import argparse
import configparser
import faulthandler
import hashlib
import itertools
import logging
import urllib
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import RotatingFileHandler
import os
import sys
from pathlib import Path
from zipfile import ZipFile

import requests
from numpy import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatAction, ReplyKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext, CallbackQueryHandler
import websocket

from camera import Camera
from klippy import Klippy
from notifications import Notifier

try:
    import thread
except ImportError:
    import _thread as thread
import time
import json

from io import BytesIO
import cv2
import emoji
import threading

logging.basicConfig(
    handlers=[
        logging.StreamHandler(sys.stdout)
    ],
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

logger = logging.getLogger(__name__)

# some global params
myId = random.randint(300000)
host = "localhost"
chatId: int = 12341234
poweroff_device: str
poweroff_device_on: bool = False

debug: bool = False
hidden_methods: list = list()

timelapse_enabled: bool = False
timelapse_mode_manual: bool = False
timelapse_running: bool = False

bot_updater: Updater
executors_pool: ThreadPoolExecutor = ThreadPoolExecutor(4)
cameraWrap: Camera
notifier: Notifier
ws: websocket.WebSocketApp
klippy: Klippy


def help_command(update: Update, _: CallbackContext) -> None:
    update.message.reply_text('The following commands are known:\n\n'
                              '/status - send klipper status\n'
                              '/pause - pause printing\n'
                              '/resume - resume printing\n'
                              '/cancel - cancel printing\n'
                              '/files - list last 5 files( you can start printing one from menu)\n'
                              '/photo - capture & send me a photo\n'
                              '/gif - let\'s make some gif from printer cam\n'
                              '/video - will take mp4 video from camera\n'
                              '/poweroff - turn off moonraker power device from config\n'
                              '/light - toggle light\n'
                              '/emergency - emergency stop printing',
                              '/shutdown - shutdown Pi gracefully')


def echo(update: Update, _: CallbackContext) -> None:
    update.message.reply_text(f"unknown command: {update.message.text}")


def unknown_chat(update: Update, _: CallbackContext) -> None:
    update.message.reply_text(f"Unauthorized access: {update.message.text} and {update.message.chat_id}")


def send_print_start_info(context: CallbackContext):
    message = context.job.context
    send_file_info(context.bot, notifier.silent_status, message)


def send_file_info(bot, silent: bool, message: str = ''):
    message, bio = klippy.get_file_info(message)
    if bio is not None:
        bot.send_photo(chatId, photo=bio, caption=message, disable_notification=silent)
    else:
        bot.send_message(chatId, message, disable_notification=silent)


def status(update: Update, _: CallbackContext) -> None:
    message_to_reply = update.message if update.message else update.effective_message
    mess = klippy.get_status()
    message_to_reply.bot.send_chat_action(chat_id=chatId, action=ChatAction.TYPING)
    message_to_reply.reply_text(mess, disable_notification=notifier.silent_commands)
    if klippy.printing_filename:
        message_to_reply.bot.send_chat_action(chat_id=chatId, action=ChatAction.TYPING)
        send_file_info(message_to_reply.bot, notifier.silent_commands)


def create_keyboard():
    custom_keyboard = [
        '/status', '/pause', '/cancel', '/resume', '/files',
        '/photo', '/video', '/gif', '/emergency', '/macros', '/shutdown'
    ]
    if poweroff_device:
        custom_keyboard.append('/poweroff')
    if light_device:
        custom_keyboard.append('/light')
    filtered = [key for key in custom_keyboard if key not in hidden_methods]
    keyboard = [filtered[i:i + 4] for i in range(0, len(filtered), 4)]
    return keyboard


def greeting_message():
    if klippy.check_connection():
        mess = 'Printer online'
    else:
        mess = 'Bot online, no moonraker connection! Failing...'
    reply_markup = ReplyKeyboardMarkup(create_keyboard(), resize_keyboard=True)
    bot_updater.bot.send_message(chatId, text=mess, reply_markup=reply_markup, disable_notification=notifier.silent_status)
    check_unfinished_lapses()


def check_unfinished_lapses():
    files = cameraWrap.detect_unfinished_lapses()
    if not files:
        return
    bot_updater.bot.send_chat_action(chat_id=chatId, action=ChatAction.TYPING)
    files_keys = list(map(list, zip(map(lambda el: InlineKeyboardButton(el, callback_data=f'lapse:{el}'), files))))
    files_keys.append([InlineKeyboardButton(emoji.emojize(':no_entry_sign: ', use_aliases=True), callback_data='do_nothing')])
    reply_markup = InlineKeyboardMarkup(files_keys)
    bot_updater.bot.send_message(chatId, text='Unfinished timelapses found\nBuild unfinished timelapse?', reply_markup=reply_markup, disable_notification=notifier.silent_status)


# Todo: vase mode calcs
def take_lapse_photo(position_z: float = -1001):
    if not timelapse_enabled:
        logger.debug(f"lapse is disabled")
        return
    elif not klippy.printing_filename:
        logger.debug(f"lapse is inactive for file undefined")
        return
    elif not timelapse_running:
        logger.debug(f"lapse is not running at the moment")
        return

    if timelapse_height > 0 and position_z % timelapse_height == 0:
        executors_pool.submit(cameraWrap.take_lapse_photo)
    elif position_z < -1000:
        executors_pool.submit(cameraWrap.take_lapse_photo)


def send_video(bot, bio: BytesIO, width, height, caption: str = '', err_mess: str = ''):
    if bio.getbuffer().nbytes > 52428800:
        bot.send_message(chatId, text=err_mess, disable_notification=notifier.silent_commands)
    else:
        bot.send_chat_action(chat_id=chatId, action=ChatAction.UPLOAD_VIDEO)
        bot.send_video(chatId, video=bio, width=width, height=height, caption=caption, timeout=120,
                       disable_notification=notifier.silent_commands)


def send_timelapse(context: CallbackContext):
    if not timelapse_enabled or not klippy.printing_filename:
        logger.debug(f"lapse is inactive for enabled {timelapse_enabled} or file undefined")
    else:
        context.bot.send_chat_action(chat_id=chatId, action=ChatAction.RECORD_VIDEO)
        (bio, width, height, video_path) = cameraWrap.create_timelapse()
        send_video(context.bot, bio, width, height, f'time-lapse of {klippy.printing_filename}',
                   f'Telegram bots have a 50mb filesize restriction, please retrieve the timelapse from the configured folder\n{video_path}')


def get_photo(update: Update, _: CallbackContext) -> None:
    message_to_reply = update.message if update.message else update.effective_message
    if not cameraEnabled:
        message_to_reply.reply_text("camera is disabled")
        return

    message_to_reply.bot.send_chat_action(chat_id=chatId, action=ChatAction.UPLOAD_PHOTO)
    message_to_reply.reply_photo(photo=cameraWrap.take_photo(), disable_notification=notifier.silent_commands)


def get_gif(update: Update, _: CallbackContext) -> None:
    message_to_reply = update.message if update.message else update.effective_message
    if not cameraEnabled:
        message_to_reply.reply_text("camera is disabled")
        return
    message_to_reply.bot.send_chat_action(chat_id=chatId, action=ChatAction.RECORD_VIDEO)

    (bio, width, height) = cameraWrap.take_gif()

    message_to_reply.bot.send_chat_action(chat_id=chatId, action=ChatAction.UPLOAD_VIDEO)
    message_to_reply.reply_animation(animation=bio, width=width, height=height, timeout=60, disable_notification=notifier.silent_commands,
                                     caption=klippy.get_status())


def get_video(update: Update, _: CallbackContext) -> None:
    message_to_reply = update.message if update.message else update.effective_message
    if not cameraEnabled:
        message_to_reply.reply_text("camera is disabled")
    else:
        message_to_reply.bot.send_chat_action(chat_id=chatId, action=ChatAction.RECORD_VIDEO)
        (bio, width, height) = cameraWrap.take_video()
        send_video(message_to_reply.bot, bio, width, height, err_mess='Telegram has a 50mb restriction...')


def manage_printing(command: str) -> None:
    ws.send(json.dumps({"jsonrpc": "2.0", "method": f"printer.print.{command}", "id": myId}))


def toggle_power_device(device: str, enable: bool):
    ws.send(json.dumps({"jsonrpc": "2.0",
                        "method": "machine.device_power.on" if enable else "machine.device_power.off",
                        "id": myId,
                        "params": {f"{device}": None}
                        }))


def emergency_stop_printer():
    ws.send(json.dumps({"jsonrpc": "2.0", "method": f"printer.emergency_stop", "id": myId}))


def shutdown_pi_host():
    ws.send(json.dumps({"jsonrpc": "2.0", "method": f"machine.shutdown", "id": myId}))


def confirm_keyboard(callback_mess: str) -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(emoji.emojize(':white_check_mark: ', use_aliases=True), callback_data=callback_mess),
            InlineKeyboardButton(emoji.emojize(':no_entry_sign: ', use_aliases=True), callback_data='do_nothing'),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def pause_printing(update: Update, __: CallbackContext) -> None:
    update.message.bot.send_chat_action(chat_id=chatId, action=ChatAction.TYPING)
    update.message.reply_text('Pause printing?', reply_markup=confirm_keyboard('pause_printing'), disable_notification=notifier.silent_commands)


def resume_printing(_: Update, __: CallbackContext) -> None:
    manage_printing('resume')


def cancel_printing(update: Update, __: CallbackContext) -> None:
    update.message.bot.send_chat_action(chat_id=chatId, action=ChatAction.TYPING)
    update.message.reply_text('Cancel printing?', reply_markup=confirm_keyboard('cancel_printing'), disable_notification=notifier.silent_commands)


def emergency_stop(update: Update, _: CallbackContext) -> None:
    update.message.bot.send_chat_action(chat_id=chatId, action=ChatAction.TYPING)
    update.message.reply_text('Execute emergency stop?', reply_markup=confirm_keyboard('emergency_stop'), disable_notification=notifier.silent_commands)


def shutdown_host(update: Update, _: CallbackContext) -> None:
    update.message.bot.send_chat_action(chat_id=chatId, action=ChatAction.TYPING)
    update.message.reply_text('Shutdown host?', reply_markup=confirm_keyboard('shutdown_host'), disable_notification=notifier.silent_commands)


def power_off(update: Update, _: CallbackContext) -> None:
    message_to_reply = update.message if update.message else update.effective_message
    message_to_reply.bot.send_chat_action(chat_id=chatId, action=ChatAction.TYPING)
    if poweroff_device:
        message_to_reply.reply_text('Power Off printer?', reply_markup=confirm_keyboard('power_off_printer'), disable_notification=notifier.silent_commands)
    else:
        message_to_reply.reply_text("No power device in config!", disable_notification=notifier.silent_commands)


def light_toggle(update: Update, _: CallbackContext) -> None:
    message_to_reply = update.message if update.message else update.effective_message
    if cameraWrap.light_device:
        cameraWrap.togle_light_device()
    else:
        message_to_reply.reply_text("No light device in config!", disable_notification=notifier.silent_commands)


def button_handler(update: Update, context: CallbackContext) -> None:
    context.bot.send_chat_action(chat_id=chatId, action=ChatAction.TYPING)
    query = update.callback_query
    query.answer()
    # Todo: maybe regex check?
    if query.data == 'do_nothing':
        query.delete_message()
    elif query.data == 'emergency_stop':
        emergency_stop_printer()
        query.delete_message()
    elif query.data == 'shutdown_host':
        shutdown_pi_host()
        query.delete_message()
    elif query.data == 'cancel_printing':
        manage_printing('cancel')
        query.delete_message()
    elif query.data == 'pause_printing':
        manage_printing('pause')
        query.delete_message()
    elif query.data == 'power_off_printer':
        toggle_power_device(poweroff_device, False)
        query.delete_message()
    elif 'gmacro:' in query.data:
        klippy.execute_command(query.data.replace('gmacro:', ''))
        query.delete_message()
    elif '.gcode' in query.data and ':' not in query.data:
        keyboard_keys = dict((x['callback_data'], x['text']) for x in
                             itertools.chain.from_iterable(query.message.reply_markup.to_dict()['inline_keyboard']))
        filename = keyboard_keys[query.data]
        keyboard = [
            [
                InlineKeyboardButton(emoji.emojize(':robot: print file', use_aliases=True), callback_data=f'print_file:{query.data}'),
                InlineKeyboardButton(emoji.emojize(':cross_mark: cancel printing', use_aliases=True), callback_data='cancel_file'),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        query.edit_message_text(text=f"Start printing file:{filename}", reply_markup=reply_markup)
    elif 'print_file' in query.data:
        filename = query.message.text.split(':')[-1].replace('?', '').replace(' ', '')
        response = requests.post(f"http://{host}/printer/print/start?filename={urllib.parse.quote(filename)}")
        if not response.ok:
            query.edit_message_text(text=f"Failed start printing file {filename}")
        else:
            query.delete_message()
    elif 'lapse:' in query.data:
        lapse_name = query.data.replace('lapse:', '')
        query.bot.send_chat_action(chat_id=chatId, action=ChatAction.RECORD_VIDEO)
        (bio, width, height, video_path) = cameraWrap.create_timelapse_for_file(lapse_name)
        send_video(context.bot, bio, width, height, f'time-lapse of {lapse_name}',
                   f'Telegram bots have a 50mb filesize restriction, please retrieve the timelapse from the configured folder\n{video_path}')

        query.delete_message()
        check_unfinished_lapses()
    else:
        logger.debug(f"unknown message from inline keyboard query: {query.data}")
        query.delete_message()


def get_gcode_files(update: Update, _: CallbackContext) -> None:
    def create_file_button(element) -> InlineKeyboardButton:
        if 'path' in element:
            filename = element['path']
        else:
            filename = element['filename']

        return InlineKeyboardButton(filename, callback_data=hashlib.md5(filename.encode()).hexdigest() + '.gcode')

    update.message.bot.send_chat_action(chat_id=chatId, action=ChatAction.TYPING)
    response = requests.get(f"http://{host}/server/files/list?root=gcodes")
    resp = response.json()
    files = sorted(resp['result'], key=lambda item: item['modified'], reverse=True)[:5]
    files_keys = list(map(list, zip(map(create_file_button, files))))
    reply_markup = InlineKeyboardMarkup(files_keys)

    update.message.reply_text('Gcode files to print:', reply_markup=reply_markup, disable_notification=notifier.silent_commands)


def get_macros(update: Update, _: CallbackContext) -> None:
    update.message.bot.send_chat_action(chat_id=chatId, action=ChatAction.TYPING)
    files_keys = list(map(list, zip(map(lambda el: InlineKeyboardButton(el, callback_data=f'gmacro:{el}'), klippy.macros))))
    reply_markup = InlineKeyboardMarkup(files_keys)

    update.message.reply_text('Gcode macros:', reply_markup=reply_markup, disable_notification=notifier.silent_commands)


def upload_file(update: Update, _: CallbackContext) -> None:
    update.message.bot.send_chat_action(chat_id=chatId, action=ChatAction.UPLOAD_DOCUMENT)
    doc = update.message.document
    if not doc.file_name.endswith(('.gcode', '.zip')):
        update.message.reply_text(f"unknown filetype in {doc.file_name}", disable_notification=notifier.silent_commands)
        return

    try:
        file_byte_array = doc.get_file().download_as_bytearray()
    except BadRequest as badreq:
        update.message.reply_text(f"Bad request: {badreq.message}", disable_notification=notifier.silent_commands)
        return

    uploaded_bio = BytesIO()
    uploaded_bio.name = doc.file_name
    uploaded_bio.write(file_byte_array)
    uploaded_bio.seek(0)

    sending_bio = BytesIO()
    if doc.file_name.endswith('.gcode'):
        sending_bio = uploaded_bio
    elif doc.file_name.endswith('.zip'):
        with ZipFile(uploaded_bio) as my_zip_file:
            if len(my_zip_file.namelist()) > 1:
                update.message.reply_text(f"Multiple files in archive {doc.file_name}", disable_notification=notifier.silent_commands)
                return

            contained_file = my_zip_file.open(my_zip_file.namelist()[0])
            sending_bio.name = contained_file.name
            sending_bio.write(contained_file.read())
            sending_bio.seek(0)

    res = requests.post(f"http://{host}/server/files/upload", files={'file': sending_bio})
    if res.ok:
        filehash = hashlib.md5(doc.file_name.encode()).hexdigest() + '.gcode'
        keyboard = [
            [
                InlineKeyboardButton(emoji.emojize(':robot: print file', use_aliases=True), callback_data=f'print_file:{filehash}'),
                InlineKeyboardButton(emoji.emojize(':cross_mark: do nothing', use_aliases=True), callback_data='do_nothing'),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        update.message.reply_text(f"Successfully uploaded file: {sending_bio.name}", reply_markup=reply_markup, disable_notification=notifier.silent_commands)
    else:
        update.message.reply_text(f"Failed uploading file: {sending_bio.name}", disable_notification=notifier.silent_commands)


def bot_error_handler(_: object, context: CallbackContext) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)


def start_bot(bot_token):
    updater = Updater(bot_token, workers=4)

    dispatcher = updater.dispatcher

    dispatcher.add_handler(MessageHandler(~Filters.chat(chatId), unknown_chat))

    dispatcher.add_handler(CallbackQueryHandler(button_handler))
    dispatcher.add_handler(CommandHandler("help", help_command, run_async=True))
    dispatcher.add_handler(CommandHandler("status", status, run_async=True))
    dispatcher.add_handler(CommandHandler("photo", get_photo, run_async=True))
    dispatcher.add_handler(CommandHandler("gif", get_gif, run_async=True))
    dispatcher.add_handler(CommandHandler("video", get_video, run_async=True))
    dispatcher.add_handler(CommandHandler("pause", pause_printing))
    dispatcher.add_handler(CommandHandler("resume", resume_printing))
    dispatcher.add_handler(CommandHandler("cancel", cancel_printing))
    dispatcher.add_handler(CommandHandler("poweroff", power_off))
    dispatcher.add_handler(CommandHandler("light", light_toggle))
    dispatcher.add_handler(CommandHandler("emergency", emergency_stop))
    dispatcher.add_handler(CommandHandler("shutdown", shutdown_host))
    dispatcher.add_handler(CommandHandler("files", get_gcode_files, run_async=True))
    dispatcher.add_handler(CommandHandler("macros", get_macros, run_async=True))

    dispatcher.add_handler(MessageHandler(Filters.document & ~Filters.command, upload_file, run_async=True))

    dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, echo))

    dispatcher.add_error_handler(bot_error_handler)

    updater.start_polling()

    return updater


def on_close(_, close_status_code, close_msg):
    logger.info("WebSocket closed")
    if close_status_code or close_msg:
        logger.error("WebSocket close status code: " + str(close_status_code))
        logger.error("WebSocket close message: " + str(close_msg))


def on_error(_, error):
    logger.error(error)


def subscribe(websock):
    websock.send(
        json.dumps({'jsonrpc': '2.0',
                    'method': 'printer.objects.subscribe',
                    'params': {
                        'objects': {
                            'print_stats': ['filename', 'state', 'print_duration'],
                            'display_status': ['progress', 'message'],
                            'toolhead': ['position'],
                            'gcode_move': ['position', 'gcode_position'],
                            'virtual_sdcard': ['progress']
                        }
                    },
                    'id': myId}))


def on_open(websock):
    websock.send(
        json.dumps({'jsonrpc': '2.0',
                    'method': 'printer.info',
                    'id': myId}))
    websock.send(
        json.dumps({'jsonrpc': '2.0',
                    'method': 'machine.device_power.devices',
                    'id': myId}))


def reshedule():
    while True:
        if not klippy.connected and ws.keep_running is True:
            on_open(ws)
        time.sleep(1)


def timelapse_sheduler(interval: int):
    while True:
        take_lapse_photo()
        time.sleep(interval)


def websocket_to_message(ws_loc, ws_message):
    json_message = json.loads(ws_message)
    if debug:
        logger.debug(ws_message)

    global poweroff_device_on, timelapse_running

    if 'error' in json_message:
        return

    if 'id' in json_message:
        if 'id' in json_message and 'result' in json_message:
            if 'status' in json_message['result']:
                if 'print_stats' in json_message['result']['status'] and json_message['result']['status']['print_stats']['state'] == "printing":
                    klippy.printing = True
                    klippy.printing_filename = json_message['result']['status']['print_stats']['filename']
                    klippy.printing_duration = json_message['result']['status']['print_stats']['print_duration']
                if 'display_status' in json_message['result']['status']:
                    notifier.message = json_message['result']['status']['display_status']['message']
                    klippy.printing_progress = json_message['result']['status']['display_status']['progress']
                if 'virtual_sdcard' in json_message['result']['status']:
                    klippy.vsd_progress = json_message['result']['status']['virtual_sdcard']['progress']
                return

            if 'state' in json_message['result']:
                if json_message['result']['state'] == 'ready':
                    if ws_loc.keep_running:
                        klippy.connected = True
                        subscribe(ws_loc)
                else:
                    klippy.connected = False
                return
            if 'devices' in json_message['result']:
                for dev in json_message['result']['devices']:
                    device_name = dev["device"]
                    device_state = True if dev["status"] == 'on' else False
                    if poweroff_device == device_name:
                        poweroff_device_on = device_state
                    if cameraWrap.light_device == device_name:
                        cameraWrap.light_state = device_state
                return
            if debug:
                bot_updater.bot.send_message(chatId, text=f"{json_message['result']}")
        if 'id' in json_message and 'error' in json_message:
            notifier.send_error(f"{json_message['error']['message']}")

        # if json_message["method"] == "notify_gcode_response":
        #     val = ws_message["params"][0]
        #     # Todo: add global state for mcu disconnects!
        #     if 'Lost communication with MCU' not in ws_message["params"][0]:
        #         botUpdater.dispatcher.bot.send_message(chatId, ws_message["params"])
        #
    else:
        if json_message["method"] == "notify_gcode_response":
            if timelapse_mode_manual:
                if 'timelapse start' in json_message["params"]:
                    if not klippy.printing_filename:
                        klippy.get_status()
                    cameraWrap.clean()
                    timelapse_running = True

                if 'timelapse stop' in json_message["params"]:
                    timelapse_running = False
                if 'timelapse pause' in json_message["params"]:
                    timelapse_running = False
                if 'timelapse continue' in json_message["params"]:
                    timelapse_running = True
                if 'timelapse create' in json_message["params"]:
                    bot_updater.job_queue.run_once(send_timelapse, 1)

            if 'timelapse photo' in json_message["params"]:
                take_lapse_photo()
            if json_message["params"][0].startswith('tgnotify'):
                notifier.send_notification(json_message["params"][0][9:])
            if json_message["params"][0].startswith('tgalarm'):
                notifier.send_error(json_message["params"][0][8:])
        if json_message["method"] in ["notify_klippy_shutdown", "notify_klippy_disconnected"]:
            logger.warning(f"klippy disconnect detected with message: {json_message['method']}")
            klippy.connected = False

        # Todo: check for multiple device state change
        if json_message["method"] == "notify_power_changed":
            device_name = json_message["params"][0]["device"]
            device_state = True if json_message["params"][0]["status"] == 'on' else False
            if poweroff_device == device_name:
                poweroff_device_on = device_state
            if cameraWrap.light_device == device_name:
                cameraWrap.light_state = device_state
        if json_message["method"] == "notify_status_update":
            if 'display_status' in json_message["params"][0]:
                if 'message' in json_message["params"][0]['display_status']:
                    notifier.message = json_message['params'][0]['display_status']['message']
                if 'progress' in json_message["params"][0]['display_status']:
                    notifier.notify(progress=int(json_message["params"][0]['display_status']['progress'] * 100))
                    klippy.printing_progress = json_message["params"][0]['display_status']['progress']
            if 'toolhead' in json_message["params"][0] and 'position' in json_message["params"][0]['toolhead']:
                # position_z = json_message["params"][0]['toolhead']['position'][2]
                pass
            if 'gcode_move' in json_message["params"][0] and 'position' in json_message["params"][0]['gcode_move']:
                position_z = json_message["params"][0]['gcode_move']['gcode_position'][2]
                notifier.notify(position_z=int(position_z))
                take_lapse_photo(position_z)
            if 'virtual_sdcard' in json_message['params'][0] and 'progress' in json_message['params'][0]['virtual_sdcard']:
                klippy.vsd_progress = json_message['params'][0]['virtual_sdcard']['progress']
            if 'print_stats' in json_message['params'][0]:
                message = ""
                state = ""
                if 'filename' in json_message['params'][0]['print_stats']:
                    klippy.printing_filename = json_message['params'][0]['print_stats']['filename']
                if 'state' in json_message['params'][0]['print_stats']:
                    state = json_message['params'][0]['print_stats']['state']
                # Fixme: reset notify percent & heigth on finish/cancel/start
                if 'print_duration' in json_message['params'][0]['print_stats']:
                    klippy.printing_duration = json_message['params'][0]['print_stats']['print_duration']
                if state == "printing":
                    klippy.paused = False
                    if not timelapse_mode_manual:
                        timelapse_running = True
                    if not klippy.printing:
                        klippy.printing = True
                        notifier.reset_notifications()
                        if not klippy.printing_filename:
                            klippy.get_status()
                        if not timelapse_mode_manual:
                            cameraWrap.clean()
                        bot_updater.job_queue.run_once(send_print_start_info, 0, context=f"Printer started printing: {klippy.printing_filename} \n")
                elif state == "paused":
                    klippy.paused = True
                    if not timelapse_mode_manual:
                        timelapse_running = False
                # Todo: cleanup timelapse dir on cancel print!
                elif state == 'complete':
                    klippy.printing = False
                    if not timelapse_mode_manual:
                        timelapse_running = False
                        bot_updater.job_queue.run_once(send_timelapse, 5)
                    message += f"Finished printing {klippy.printing_filename} \n"
                elif state == 'error':
                    klippy.printing = False
                    if not timelapse_mode_manual:
                        timelapse_running = False
                    notifier.send_error(f"Printer state change error: {json_message['params'][0]['print_stats']['state']} \n")
                elif state:
                    klippy.printing = False
                    message += f"Printer state change: {json_message['params'][0]['print_stats']['state']} \n"

                if message:
                    notifier.send_notification(message)


def parselog():
    with open('telegram.log') as f:
        lines = f.readlines()

    wslines = list(filter(lambda it: ' - {' in it, lines))
    tt = list(map(lambda el: el.split(' - ')[-1].replace('\n', ''), wslines))

    for mes in tt:
        websocket_to_message(ws, mes)
    print('lalal')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Moonraker Telegram Bot")
    parser.add_argument(
        "-c", "--configfile", default="./telegram.conf",
        metavar='<configfile>',
        help="Location of moonraker telegram bot configuration file")
    system_args = parser.parse_args()
    klipper_config_path = system_args.configfile[:system_args.configfile.rfind('/')]
    conf = configparser.ConfigParser()
    conf.read(system_args.configfile)
    host = conf.get('bot', 'server', fallback='localhost')
    token = conf.get('bot', 'bot_token')
    chatId = conf.getint('bot', 'chat_id')
    notify_percent = conf.getint('progress_notification', 'percent', fallback=0)
    notify_height = conf.getint('progress_notification', 'height', fallback=0)
    notify_interval = conf.getint('progress_notification', 'time', fallback=0)
    notify_delay_interval = conf.getint('progress_notification', 'min_delay_between_notifications', fallback=0)
    notify_groups = conf.get('progress_notification', 'groups').split(',') if 'progress_notification' in conf and 'groups' in conf['progress_notification'] else list()
    timelapse_height = conf.getfloat('timelapse', 'height', fallback=0.0)
    timelapse_enabled = 'timelapse' in conf
    timelapse_basedir = conf.get('timelapse', 'basedir', fallback='/tmp/timelapse')
    timelapse_cleanup = conf.getboolean('timelapse', 'cleanup', fallback=True)
    timelapse_interval_time = conf.getint('timelapse', 'time', fallback=0)
    timelapse_fps = conf.getint('timelapse', 'target_fps', fallback=15)
    timelapse_mode_manual = conf.getboolean('timelapse', 'manual_mode', fallback=False)

    cameraEnabled = 'camera' in conf
    flipHorisontally = conf.getboolean('camera', 'flipHorizontally', fallback=False)
    flipVertically = conf.getboolean('camera', 'flipVertically', fallback=False)
    gifDuration = conf.getint('camera', 'gifDuration', fallback=5)
    videoDuration = conf.getint('camera', 'videoDuration', fallback=5)
    reduceGif = conf.getint('camera', 'reduceGif', fallback=2)
    cameraHost = conf.get('camera', 'host', fallback=f"http://{host}:8080/?action=stream")
    video_fourcc = conf.get('camera', 'fourcc', fallback='x264')
    camera_threads = conf.getint('camera', 'threads', fallback=int(os.cpu_count() / 2))
    camera_light_timeout = conf.getint('camera', 'light_control_timeout', fallback=0)
    camera_picture_quality = conf.get('camera', 'picture_quality', fallback='webp')

    poweroff_device = conf.get('bot', 'power_device', fallback='')
    light_device = conf.get('bot', 'light_device', fallback="")
    debug = conf.getboolean('bot', 'debug', fallback=False)
    log_path = conf.get('bot', 'log_path', fallback='/tmp')
    eta_source = conf.get('bot', 'eta_source', fallback='slicer')

    hidden_methods = conf.get('telegram_ui', 'hidden_methods').split(',') if 'telegram_ui' in conf and 'hidden_methods' in conf['telegram_ui'] else list()
    disabled_macros = conf.get('telegram_ui', 'disabled_macros').split(',') if 'telegram_ui' in conf and 'disabled_macros' in conf['telegram_ui'] else list()

    silent_progress = conf.getboolean('telegram_ui', 'silent_progress', fallback=True)
    silent_commands = conf.getboolean('telegram_ui', 'silent_commands', fallback=True)
    silent_status = conf.getboolean('telegram_ui', 'silent_status', fallback=True)

    if not log_path == '/tmp':
        Path(log_path).mkdir(parents=True, exist_ok=True)

    rotatingHandler = RotatingFileHandler(os.path.join(f'{log_path}/', 'telegram.log'), maxBytes=26214400, backupCount=3)
    rotatingHandler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(rotatingHandler)

    if debug:
        faulthandler.enable()
        logger.setLevel(logging.DEBUG)

    klippy = Klippy(host, disabled_macros, eta_source)
    cameraWrap = Camera(host, klippy, cameraEnabled, cameraHost, camera_threads, light_device, camera_light_timeout, flipVertically, flipHorisontally, video_fourcc, gifDuration,
                        reduceGif, videoDuration, klipper_config_path, timelapse_basedir, timelapse_cleanup, timelapse_fps, debug, camera_picture_quality)
    bot_updater = start_bot(token)
    notifier = Notifier(bot_updater, chatId, klippy, cameraWrap, notify_percent, notify_height, notify_delay_interval, notify_groups, debug, silent_progress, silent_commands,
                        silent_status)

    ws = websocket.WebSocketApp(f"ws://{host}/websocket", on_message=websocket_to_message, on_open=on_open, on_error=on_error, on_close=on_close)

    # debug reasons only
    # parselog()

    greeting_message()

    # TOdo: rewrite using ApScheduler
    threading.Thread(target=reshedule, daemon=True, name='Connection_scheduler').start()
    if timelapse_interval_time > 0:
        threading.Thread(target=timelapse_sheduler, args=(timelapse_interval_time,), daemon=True).start()

    ws.run_forever(skip_utf8_validation=True)
    logger.info("Exiting! Moonraker connection lost!")

    cv2.destroyAllWindows()
    bot_updater.stop()
