import argparse
import configparser
import faulthandler
import hashlib
import itertools
import logging
import urllib
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
from power_device import PowerDevice
from timelapse import Timelapse

try:
    import thread
except ImportError:
    import _thread as thread
import json

from io import BytesIO
import cv2
import emoji
from apscheduler.schedulers.background import BackgroundScheduler

logging.basicConfig(
    handlers=[
        logging.StreamHandler(sys.stdout)
    ],
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

logger = logging.getLogger(__name__)


def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    logger.error("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))


sys.excepthook = handle_exception

# some global params
myId = random.randint(300000)
host = "localhost"
chatId: int = 12341234
debug: bool = False
hidden_methods: list = list()

bot_updater: Updater
scheduler = BackgroundScheduler({
    'apscheduler.executors.default': {
        'class': 'apscheduler.executors.pool:ThreadPoolExecutor',
        'max_workers': '10'
    },
    'apscheduler.job_defaults.coalesce': 'false',
    'apscheduler.job_defaults.max_instances': '1',
}, daemon=True)
cameraWrap: Camera
timelapse: Timelapse
notifier: Notifier
ws: websocket.WebSocketApp
klippy: Klippy
light_power_device: PowerDevice
psu_power_device: PowerDevice


def help_command(update: Update, _: CallbackContext) -> None:
    update.message.reply_text('The following commands are known:\n\n'
                              '/status - send klipper status\n'
                              '/pause - pause printing\n'
                              '/resume - resume printing\n'
                              '/cancel - cancel printing\n'
                              '/files - list last 5 files( you can start printing one from menu)\n'
                              '/photo - capture & send me a photo\n'
                              '/video - will take mp4 video from camera\n'
                              '/power - toggle moonraker power device from config\n'
                              '/light - toggle light\n'
                              '/emergency - emergency stop printing\n'
                              '/restart - restart bot\n'
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
        bio.close()
    else:
        bot.send_message(chatId, message, disable_notification=silent)


def status(update: Update, _: CallbackContext) -> None:
    message_to_reply = update.message if update.message else update.effective_message
    mess = klippy.get_status()
    message_to_reply.bot.send_chat_action(chat_id=chatId, action=ChatAction.TYPING)
    message_to_reply.reply_text(mess, disable_notification=notifier.silent_commands)
    if klippy.printing_filename:
        message_to_reply.bot.send_chat_action(chat_id=chatId, action=ChatAction.TYPING)
        send_file_info(message_to_reply.bot, notifier.silent_commands, f"Printing: {klippy.printing_filename} \n")


def create_keyboard():
    custom_keyboard = [
        '/status', '/pause', '/cancel', '/resume', '/files',
        '/photo', '/video', '/emergency', '/macros', '/shutdown'
    ]
    if psu_power_device:
        custom_keyboard.append('/power')
    if light_power_device:
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


def send__video(bot, video_bio: BytesIO, thumb_bio: BytesIO, width, height, caption: str = '', err_mess: str = ''):
    if video_bio.getbuffer().nbytes > 52428800:
        bot.send_message(chatId, text=err_mess, disable_notification=notifier.silent_commands)
    else:
        bot.send_video(chatId, video=video_bio, thumb=thumb_bio, width=width, height=height, caption=caption, timeout=120, disable_notification=notifier.silent_commands)

    video_bio.close()
    thumb_bio.close()


def get_photo(update: Update, _: CallbackContext) -> None:
    message_to_reply = update.message if update.message else update.effective_message
    if not cameraWrap.enabled:
        message_to_reply.reply_text("camera is disabled")
        return

    message_to_reply.bot.send_chat_action(chat_id=chatId, action=ChatAction.UPLOAD_PHOTO)
    with cameraWrap.take_photo() as bio:
        message_to_reply.reply_photo(photo=bio, disable_notification=notifier.silent_commands)
        bio.close()


def get_video(update: Update, _: CallbackContext) -> None:
    message_to_reply = update.message if update.message else update.effective_message
    if not cameraWrap.enabled:
        message_to_reply.reply_text("camera is disabled")
    else:
        message_to_reply.bot.send_chat_action(chat_id=chatId, action=ChatAction.RECORD_VIDEO)
        with cameraWrap.take_video_generator() as (video_bio, thumb_bio, width, height):
            send__video(message_to_reply.bot, video_bio, thumb_bio, width, height, err_mess='Telegram has a 50mb restriction...')


def manage_printing(command: str) -> None:
    ws.send(json.dumps({"jsonrpc": "2.0", "method": f"printer.print.{command}", "id": myId}))


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


def power(update: Update, _: CallbackContext) -> None:
    message_to_reply = update.message if update.message else update.effective_message
    message_to_reply.bot.send_chat_action(chat_id=chatId, action=ChatAction.TYPING)
    if psu_power_device:
        if psu_power_device.device_state:
            message_to_reply.reply_text('Power Off printer?', reply_markup=confirm_keyboard('power_off_printer'), disable_notification=notifier.silent_commands)
        else:
            message_to_reply.reply_text('Power On printer?', reply_markup=confirm_keyboard('power_on_printer'), disable_notification=notifier.silent_commands)
    else:
        message_to_reply.reply_text("No power device in config!", disable_notification=notifier.silent_commands)


def light_toggle(update: Update, _: CallbackContext) -> None:
    message_to_reply = update.message if update.message else update.effective_message
    if light_power_device:
        light_power_device.toggle_device()
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
        psu_power_device.switch_device(False)
        query.delete_message()
    elif query.data == 'power_on_printer':
        psu_power_device.switch_device(True)
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
        query.edit_message_text(text=f"Start printing file:{filename}?", reply_markup=reply_markup)
    elif 'print_file' in query.data:
        filename = query.message.text.split(':')[-1].replace('?', '').strip()
        if klippy.start_printing_file(filename):
            query.delete_message()
        else:
            query.edit_message_text(text=f"Failed start printing file {filename}")
    elif 'lapse:' in query.data:
        lapse_name = query.data.replace('lapse:', '')
        query.bot.send_chat_action(chat_id=chatId, action=ChatAction.RECORD_VIDEO)
        (video_bio, thumb_bio, width, height, video_path, gcode_name) = cameraWrap.create_timelapse_for_file(lapse_name)
        send__video(context.bot, video_bio, thumb_bio, width, height, f'time-lapse of {lapse_name}',
                    f'Telegram bots have a 50mb filesize restriction, please retrieve the timelapse from the configured folder\n{video_path}')

        query.delete_message()
        check_unfinished_lapses()
    else:
        logger.debug(f"unknown message from inline keyboard query: {query.data}")
        query.delete_message()


def get_gcode_files(update: Update, _: CallbackContext) -> None:
    def create_file_button(element) -> InlineKeyboardButton:
        filename = element['path'] if 'path' in element else element['filename']
        return InlineKeyboardButton(filename, callback_data=hashlib.md5(filename.encode()).hexdigest() + '.gcode')

    update.message.bot.send_chat_action(chat_id=chatId, action=ChatAction.TYPING)
    files_keys = list(map(list, zip(map(create_file_button, klippy.get_gcode_files()))))
    reply_markup = InlineKeyboardMarkup(files_keys)

    update.message.reply_text('Gcode files to print:', reply_markup=reply_markup, disable_notification=notifier.silent_commands)


def exec_gcode(update: Update, _: CallbackContext) -> None:
    # maybe use context.args
    message = update.message if update.message else update.effective_message
    if not message.text == '/gcode':
        command = message.text.replace('/gcode ', '')
        klippy.execute_command(command)
    else:
        message.reply_text('No command provided')


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

    # Todo: add context managment!
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

    if klippy.upload_file(sending_bio):
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

    uploaded_bio.close()
    sending_bio.close()


# Todo: some case sensitive checks?
def macros_handler(update: Update, _: CallbackContext) -> None:
    command = update.message.text.replace('/', '').upper()
    if command in klippy.macros:
        print(command)
        klippy.execute_command(command)


def restart(update: Update, _: CallbackContext) -> None:
    ws.close()
    update.message.reply_text("Restarting bot")
    os._exit(1)


def bot_error_handler(_: object, context: CallbackContext) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)


def start_bot(bot_token, socks):
    request_kwargs = {}
    if socks:
        request_kwargs['proxy_url'] = f'socks5://{socks}'

    updater = Updater(bot_token, workers=4, request_kwargs=request_kwargs)

    dispatcher = updater.dispatcher

    dispatcher.add_handler(MessageHandler(~Filters.chat(chatId), unknown_chat))

    dispatcher.add_handler(CallbackQueryHandler(button_handler))
    dispatcher.add_handler(CommandHandler("help", help_command, run_async=True))
    dispatcher.add_handler(CommandHandler("status", status, run_async=True))
    dispatcher.add_handler(CommandHandler("photo", get_photo))
    dispatcher.add_handler(CommandHandler("video", get_video))
    dispatcher.add_handler(CommandHandler("pause", pause_printing))
    dispatcher.add_handler(CommandHandler("resume", resume_printing))
    dispatcher.add_handler(CommandHandler("cancel", cancel_printing))
    dispatcher.add_handler(CommandHandler("power", power))
    dispatcher.add_handler(CommandHandler("light", light_toggle))
    dispatcher.add_handler(CommandHandler("emergency", emergency_stop))
    dispatcher.add_handler(CommandHandler("shutdown", shutdown_host))
    dispatcher.add_handler(CommandHandler("restart", restart))
    dispatcher.add_handler(CommandHandler("files", get_gcode_files, run_async=True))
    dispatcher.add_handler(CommandHandler("macros", get_macros, run_async=True))
    dispatcher.add_handler(CommandHandler("gcode", exec_gcode, run_async=True))

    dispatcher.add_handler(MessageHandler(Filters.command, macros_handler, run_async=True))

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
                            'print_stats': ['filename', 'state', 'print_duration', 'filament_used'],
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
    if not klippy.connected and ws.keep_running:
        on_open(ws)


def stop_all():
    klippy.stop_all()
    notifier.stop_all()
    timelapse.stop_all()


def status_reponse(message_result):
    if 'print_stats' in message_result['status']:
        print_stats = message_result['status']['print_stats']
        if print_stats['state'] in ['printing', 'paused']:
            klippy.printing = True
            klippy.printing_filename = print_stats['filename']
            klippy.printing_duration = print_stats['print_duration']
            klippy.filament_used = print_stats['filament_used']
            # Todo: maybe get print start time and set start interval for job?
            notifier.add_notifier_timer()
            if not timelapse.manual_mode:
                timelapse.running = True
                timelapse.paused = False
                # TOdo: manual timelapse start check?

        # Fixme: some logic error with states for klippy.paused and printing
        if print_stats['state'] == "printing":
            klippy.paused = False
        if print_stats['state'] == "paused":
            klippy.paused = True
            if not timelapse.manual_mode:
                timelapse.paused = True

    if 'display_status' in message_result['status']:
        notifier.message = message_result['status']['display_status']['message']
        klippy.printing_progress = message_result['status']['display_status']['progress']
    if 'virtual_sdcard' in message_result['status']:
        klippy.vsd_progress = message_result['status']['virtual_sdcard']['progress']


def notify_gcode_reponse(message_params):
    if timelapse.manual_mode:
        if 'timelapse start' in message_params:
            if not klippy.printing_filename:
                klippy.get_status()
            timelapse.clean()
            timelapse.running = True

        if 'timelapse stop' in message_params:
            timelapse.running = False
        if 'timelapse pause' in message_params:
            timelapse.paused = True
        if 'timelapse resume' in message_params:
            timelapse.paused = False
        if 'timelapse create' in message_params:
            timelapse.send_timelapse()
    if 'timelapse photo' in message_params:
        timelapse.take_lapse_photo(manually=True)
    if message_params[0].startswith('tgnotify '):
        notifier.send_notification(message_params[0][9:])
    if message_params[0].startswith('tgnotify_photo '):
        notifier.send_notification_with_photo(message_params[0][15:])
    if message_params[0].startswith('tgalarm '):
        notifier.send_error(message_params[0][8:])
    if message_params[0].startswith('tgalarm_photo '):
        notifier.send_error_with_photo(message_params[0][14:])


def notify_status_update(message_params):
    if 'display_status' in message_params[0]:
        if 'message' in message_params[0]['display_status']:
            notifier.message = message_params[0]['display_status']['message']
        if 'progress' in message_params[0]['display_status']:
            notifier.schedule_notification(progress=int(message_params[0]['display_status']['progress'] * 100))
            klippy.printing_progress = message_params[0]['display_status']['progress']

    if 'toolhead' in message_params[0] and 'position' in message_params[0]['toolhead']:
        # position_z = json_message["params"][0]['toolhead']['position'][2]
        pass
    if 'gcode_move' in message_params[0] and 'position' in message_params[0]['gcode_move']:
        position_z = message_params[0]['gcode_move']['gcode_position'][2]
        notifier.schedule_notification(position_z=int(position_z))
        timelapse.take_lapse_photo(position_z)

    if 'virtual_sdcard' in message_params[0] and 'progress' in message_params[0]['virtual_sdcard']:
        klippy.vsd_progress = message_params[0]['virtual_sdcard']['progress']

    if 'print_stats' in message_params[0]:
        parse_print_stats(message_params)


def parse_print_stats(message_params):
    message = ""
    state = ""
    # Fixme:  maybe do not parse without state? history data may not be avaliable
    # Message with filename will be sent before printing is started
    if 'filename' in message_params[0]['print_stats']:
        klippy.printing_filename = message_params[0]['print_stats']['filename']
    if 'filament_used' in message_params[0]['print_stats']:
        klippy.filament_used = message_params[0]['print_stats']['filament_used']
    if 'state' in message_params[0]['print_stats']:
        state = message_params[0]['print_stats']['state']
    # Fixme: reset notify percent & height on finish/cancel/start
    if 'print_duration' in message_params[0]['print_stats']:
        klippy.printing_duration = message_params[0]['print_stats']['print_duration']
    if state == 'printing':
        klippy.paused = False
        if not klippy.printing:
            klippy.printing = True
            notifier.reset_notifications()
            notifier.add_notifier_timer()
            if not klippy.printing_filename:
                klippy.get_status()
            if not timelapse.manual_mode:
                timelapse.clean()
                timelapse.running = True
                timelapse.paused = False
            # Todo: refactor!
            bot_updater.job_queue.run_once(send_print_start_info, 0, context=f"Printer started printing: {klippy.printing_filename} \n")

        if not timelapse.manual_mode:
            timelapse.paused = False
    elif state == 'paused':
        klippy.paused = True
        if not timelapse.manual_mode:
            timelapse.paused = True
    # Todo: cleanup timelapse dir on cancel print!
    elif state == 'complete':
        klippy.printing = False
        notifier.remove_notifier_timer()
        if not timelapse.manual_mode:
            timelapse.running = False
            timelapse.send_timelapse()
        message += f"Finished printing {klippy.printing_filename} \n"
    elif state == 'error':
        klippy.printing = False
        timelapse.running = False
        notifier.remove_notifier_timer()
        notifier.send_error(f"Printer state change error: {message_params[0]['print_stats']['state']} \n")
    elif state == 'standby':
        klippy.printing = False
        notifier.remove_notifier_timer()
        # Fixme: check manual mode
        timelapse.running = False

        message += f"Printer state change: {message_params[0]['print_stats']['state']} \n"
    elif state:
        logger.error(f"Unknown state: {state}")
    if message:
        notifier.send_notification(message)


def websocket_to_message(ws_loc, ws_message):
    json_message = json.loads(ws_message)
    logger.debug(ws_message)

    if 'error' in json_message:
        return

    if 'id' in json_message:
        if 'id' in json_message and 'result' in json_message:
            message_result = json_message['result']

            if 'status' in message_result:
                status_reponse(message_result)
                return

            if 'state' in message_result:
                klippy_state = message_result['state']
                if klippy_state == 'ready':
                    if ws_loc.keep_running:
                        klippy.connected = True
                        subscribe(ws_loc)
                        if scheduler.get_job('ws_reschedule'):
                            scheduler.remove_job('ws_reschedule')
                elif klippy_state in ["error", "shutdown", "startup"]:
                    klippy.connected = False
                    if not scheduler.get_job('ws_reschedule'):
                        scheduler.add_job(reshedule, 'interval', seconds=2, id='ws_reschedule')
                else:
                    logger.error(f"UnKnown klippy state: {klippy_state}")
                    klippy.connected = False
                    scheduler.add_job(reshedule, 'interval', seconds=2, id='ws_reschedule')
                return

            if 'devices' in message_result:
                for dev in message_result['devices']:
                    device_name = dev["device"]
                    device_state = True if dev["status"] == 'on' else False
                    if psu_power_device and psu_power_device.name == device_name:
                        psu_power_device.device_state = device_state
                    if light_power_device and light_power_device.name == device_name:
                        light_power_device.device_state = device_state
                return

            if debug:
                bot_updater.bot.send_message(chatId, text=f"{message_result}")

        if 'id' in json_message and 'error' in json_message:
            notifier.send_error(f"{json_message['error']['message']}")

    else:
        message_method = json_message['method']
        if message_method in ["notify_klippy_shutdown", "notify_klippy_disconnected"]:
            logger.warning(f"klippy disconnect detected with message: {json_message['method']}")
            stop_all()
            klippy.connected = False
            if not scheduler.get_job('ws_reschedule'):
                scheduler.add_job(reshedule, 'interval', seconds=2, id='ws_reschedule')

        if 'params' not in json_message:
            return

        message_params = json_message['params']

        if message_method == 'notify_gcode_response':
            notify_gcode_reponse(message_params)

        # Todo: check for multiple device state change
        if message_method == 'notify_power_changed':
            device_name = message_params[0]["device"]
            device_state = True if message_params[0]["status"] == 'on' else False
            if psu_power_device and psu_power_device.name == device_name:
                psu_power_device.device_state = device_state
            if light_power_device and light_power_device.name == device_name:
                light_power_device.device_state = device_state

        if message_method == 'notify_status_update':
            notify_status_update(message_params)


def parselog():
    with open('../telegram.log') as f:
        lines = f.readlines()

    wslines = list(filter(lambda it: ' - {' in it, lines))
    tt = list(map(lambda el: el.split(' - ')[-1].replace('\n', ''), wslines))

    for mes in tt:
        websocket_to_message(ws, mes)
        # time.sleep(0.01)
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
    socks_proxy = conf.get('bot', 'socks_proxy', fallback='')
    token = conf.get('bot', 'bot_token')
    chatId = conf.getint('bot', 'chat_id')

    debug = conf.getboolean('bot', 'debug', fallback=False)
    log_parser = conf.getboolean('bot', 'log_parser', fallback=False)
    log_path = conf.get('bot', 'log_path', fallback='/tmp')

    hidden_methods = [el.strip() for el in conf.get('telegram_ui', 'hidden_methods').split(',')] if 'telegram_ui' in conf and 'hidden_methods' in conf['telegram_ui'] else list()

    if not log_path == '/tmp':
        Path(log_path).mkdir(parents=True, exist_ok=True)

    rotatingHandler = RotatingFileHandler(os.path.join(f'{log_path}/', 'telegram.log'), maxBytes=26214400, backupCount=3)
    rotatingHandler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(rotatingHandler)

    if debug:
        faulthandler.enable()
        logger.setLevel(logging.DEBUG)
        logging.getLogger('apscheduler').addHandler(rotatingHandler)

    poweroff_device_name = conf.get('bot', 'power_device', fallback='')
    light_device_name = conf.get('bot', 'light_device', fallback="")
    light_power_device = PowerDevice(light_device_name, host)
    psu_power_device = PowerDevice(poweroff_device_name, host)

    klippy = Klippy(conf, light_power_device, psu_power_device, rotatingHandler, debug)
    cameraWrap = Camera(conf, klippy, light_power_device, klipper_config_path, rotatingHandler, debug)
    bot_updater = start_bot(token, socks_proxy)
    timelapse = Timelapse(conf, klippy, cameraWrap, scheduler, bot_updater, chatId, rotatingHandler, debug)
    notifier = Notifier(conf, bot_updater, chatId, klippy, cameraWrap, scheduler, rotatingHandler, debug)

    ws = websocket.WebSocketApp(f"ws://{host}/websocket", on_message=websocket_to_message, on_open=on_open, on_error=on_error, on_close=on_close)

    scheduler.start()

    # debug reasons only
    if log_parser:
        parselog()

    greeting_message()

    scheduler.add_job(reshedule, 'interval', seconds=2, id='ws_reschedule')

    ws.run_forever(skip_utf8_validation=True)
    logger.info("Exiting! Moonraker connection lost!")

    cv2.destroyAllWindows()
    bot_updater.stop()
