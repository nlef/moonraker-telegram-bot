import argparse
import faulthandler
import hashlib
import itertools
import logging
import urllib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
import os
import sys
from urllib import request
from urllib.request import urlopen

import requests
from numpy import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatAction, ReplyKeyboardMarkup
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext, CallbackQueryHandler
import websocket

from camera import Camera

try:
    import thread
except ImportError:
    import _thread as thread
import time
import json

from PIL import Image
from io import BytesIO
import cv2
from pyhocon import ConfigFactory
import emoji
import threading

# Enable logging
logging.basicConfig(
    handlers=[
        RotatingFileHandler(os.path.join('/tmp/', 'telegram.log'), maxBytes=26214400, backupCount=3),
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
notify_percent: int = 5
notify_heigth: int = 5
notify_interval: int = 0
notify_groups: list = list()
poweroff_device: str
poweroff_device_on: bool = False

timelapse_heigth: float = 0.2
timelapse_enabled: bool = False

debug: bool = False
hidden_methods: list = list()

bot_updater: Updater
executors_pool: ThreadPoolExecutor = ThreadPoolExecutor(4)
cameraWrap: Camera
ws: websocket.WebSocketApp

# Todo: class for printer states!
klippy_connected: bool = False
klippy_printing: bool = False
klippy_printing_duration: float = 0.0
klippy_printing_progress: float = 0.0
klippy_printing_filename: str = ''

last_notify_heigth: int = 0
last_notify_percent: int = 0
last_message: str = ''
last_notify_time: int = 0


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
                              '/light - toggle light')


def echo(update: Update, _: CallbackContext) -> None:
    update.message.reply_text(f"unknown command: {update.message.text}")


def unknown_chat(update: Update, _: CallbackContext) -> None:
    update.message.reply_text(f"Unauthorized access: {update.message.text} and {update.message.chat_id}")


def info(update: Update, _: CallbackContext) -> None:
    response = request.urlopen(f"http://{host}/printer/info")
    update.message.reply_text(json.loads(response.read()))


def reset_notifications() -> None:
    global last_notify_percent, last_notify_heigth, klippy_printing_duration
    last_notify_percent = 0
    last_notify_heigth = 0
    klippy_printing_duration = 0


def get_status() -> (str, str):
    response = request.urlopen(
        f"http://{host}/printer/objects/query?webhooks&print_stats=filename,total_duration,print_duration,filament_used,state,message&display_status=message")
    resp = json.loads(response.read())
    print_stats = resp['result']['status']['print_stats']
    webhook = resp['result']['status']['webhooks']
    message = emoji.emojize(':robot: Printer status: ') + f"{webhook['state']}\n"
    if 'display_status' in resp['result']['status']:
        if 'message' in resp['result']['status']['display_status']:
            msg = resp['result']['status']['display_status']['message']
    if msg and msg is not None:
        message += f"{msg}\n"
    if 'state_message' in webhook:
        message += f"State message: {webhook['state_message']}\n"
    message += emoji.emojize(':mechanical_arm: Printing process status: ') + f"{print_stats['state']} \n"
    printing_filename = ''
    if print_stats['state'] in ('printing', 'paused', 'complete'):
        printing_filename = print_stats['filename']
        message += f"Printing filename: {printing_filename} \n"
    if cameraWrap.light_device:
        message += emoji.emojize(':flashlight: Light Status: ') + f"{'on' if cameraWrap.light_state else 'off'}"
    return message, printing_filename


def send_print_start_info(context: CallbackContext):
    file = context.job.context
    send_file_info(context.bot, file, f"Printer started printing: {file} \n")


def send_file_info(bot, filename, message: str = ''):
    response = request.urlopen(
        f"http://{host}/server/files/metadata?filename={urllib.parse.quote(filename)}"
    )
    resp = json.loads(response.read())['result']
    eta = int(resp['estimated_time'] * (1 - klippy_printing_progress))
    message += f"Filament: {round(resp['filament_total'] / 1000, 2)}m, weigth: {resp['filament_weight_total']}g"
    message += f"\nPrint duration: {timedelta(seconds=eta)}"
    message += f"\nFinish at {datetime.now() + timedelta(seconds=eta):%Y-%m-%d %H:%M}"

    if 'thumbnails' in resp:
        thumb = max(resp['thumbnails'], key=lambda el: el['size'])
        img = Image.open(
            urlopen(f"http://{host}/server/files/gcodes/{urllib.parse.quote(thumb['relative_path'])}")
        ).convert('RGB')

        bio = BytesIO()
        bio.name = f'{filename}.png'
        img.save(bio, 'PNG')
        bio.seek(0)
        bot.send_photo(chatId, photo=bio, caption=message)
        for group in notify_groups:
            bot.send_chat_action(chat_id=group, action=ChatAction.TYPING)
            bot.send_photo(group, photo=bio, caption=message)
    else:
        bot.send_message(chatId, message)
        for group in notify_groups:
            bot.send_chat_action(chat_id=group, action=ChatAction.TYPING)
            bot.send_message(group, text=message)


def status(update: Update, _: CallbackContext) -> None:
    message_to_reply = update.message if update.message else update.effective_message
    (mess, filename) = get_status()
    message_to_reply.bot.send_chat_action(chat_id=chatId, action=ChatAction.TYPING)
    message_to_reply.reply_text(mess)
    if filename:
        message_to_reply.bot.send_chat_action(chat_id=chatId, action=ChatAction.TYPING)
        send_file_info(message_to_reply.bot, filename)


def create_keyboard():
    custom_keyboard = [
        '/status', '/pause', '/cancel', '/resume', '/files',
        '/photo', '/video', '/gif', '/emergency'
    ]
    if poweroff_device:
        custom_keyboard.append('/poweroff')
    if light_device:
        custom_keyboard.append('/light')
    filtered = [key for key in custom_keyboard if key not in hidden_methods]
    keyboard = [filtered[i:i + 4] for i in range(0, len(filtered), 4)]
    return keyboard


def greeting_message():
    (mess, filename) = get_status()
    reply_markup = ReplyKeyboardMarkup(create_keyboard(), resize_keyboard=True)
    bot_updater.bot.send_message(chatId, text=mess, reply_markup=reply_markup)
    if filename:
        send_file_info(bot_updater.bot, filename)


def send_messsage(context: CallbackContext):
    context.bot.send_chat_action(chat_id=chatId, action=ChatAction.TYPING)
    context.bot.send_message(chatId, text=context.job.context)
    for group in notify_groups:
        context.bot.send_chat_action(chat_id=group, action=ChatAction.TYPING)
        context.bot.send_message(group, text=context.job.context)


def notify(progress: int = 0, position_z: int = 0):
    global last_notify_percent, last_notify_heigth, last_notify_time, klippy_printing

    if not klippy_printing or not klippy_printing_duration > 0.0 or (notify_heigth == 0 + notify_percent == 0) or (
            time.time() < last_notify_time + notify_interval):
        return

    notifymsg = ''
    if progress != 0 and notify_percent != 0:
        if progress < last_notify_percent - notify_percent:
            last_notify_percent = progress
        if progress % notify_percent == 0 and progress > last_notify_percent:
            notifymsg = f"Printed {progress}%"
            if last_message:
                notifymsg += f"\n{last_message}"
            if klippy_printing_duration > 0:
                estimated_time = int((klippy_printing_duration * 100 / progress) - klippy_printing_duration)
                notifymsg += f"\nEstimated time {timedelta(seconds=estimated_time)}"
                notifymsg += f"\nFinish at {datetime.now() + timedelta(seconds=estimated_time):%Y-%m-%d %H:%M}"
            last_notify_percent = progress

    if position_z != 0 and notify_heigth != 0:
        if position_z < last_notify_heigth - notify_heigth:
            last_notify_heigth = position_z
        if position_z % notify_heigth == 0 and position_z > last_notify_heigth:
            notifymsg = f"Printed {position_z}mm"
            if last_message:
                notifymsg += f"\n{last_message}"
            last_notify_heigth = position_z

    def send_notification(context: CallbackContext):
        if cameraEnabled:
            photo = cameraWrap.take_photo()
            context.bot.send_chat_action(chat_id=chatId, action=ChatAction.UPLOAD_PHOTO)
            context.bot.send_photo(chatId, photo=photo, caption=notifymsg)
            for group_ in notify_groups:
                context.bot.send_chat_action(chat_id=group_, action=ChatAction.UPLOAD_PHOTO)
                context.bot.send_photo(group_, photo=photo, caption=notifymsg)
        else:
            context.bot.send_chat_action(chat_id=chatId, action=ChatAction.TYPING)
            context.bot.send_message(chatId, text=notifymsg)
            for group in notify_groups:
                context.bot.send_chat_action(chat_id=group, action=ChatAction.TYPING)
                context.bot.send_message(group, text=notifymsg)

    if notifymsg:
        last_notify_time = time.time()
        bot_updater.job_queue.run_once(send_notification, 0)


# Todo: vase mode calcs
def take_lapse_photo(position_z: float = -1):
    if not timelapse_enabled or not klippy_printing_filename:
        logger.debug(f"lapse is inactive for enabled {timelapse_enabled} or file undefined")
        return
    if (timelapse_heigth > 0 and position_z % timelapse_heigth == 0) or position_z < 0:
        executors_pool.submit(cameraWrap.take_lapse_photo)


def send_timelapse(context: CallbackContext):
    if not timelapse_enabled or not klippy_printing_filename:
        logger.debug(f"lapse is inactive for enabled {timelapse_enabled} or file undefined")
        return
    context.bot.send_chat_action(chat_id=chatId, action=ChatAction.RECORD_VIDEO)
    (bio, width, height) = cameraWrap.create_timelapse()
    context.bot.send_chat_action(chat_id=chatId, action=ChatAction.UPLOAD_VIDEO)
    context.bot.send_video(chatId, video=bio, width=width, height=height, caption=f'time-lapse of {klippy_printing_filename}', timeout=120)


def get_photo(update: Update, _: CallbackContext) -> None:
    message_to_reply = update.message if update.message else update.effective_message
    if not cameraEnabled:
        message_to_reply.reply_text("camera is disabled")
        return

    message_to_reply.bot.send_chat_action(chat_id=chatId, action=ChatAction.UPLOAD_PHOTO)
    message_to_reply.reply_photo(photo=cameraWrap.take_photo())


def get_gif(update: Update, _: CallbackContext) -> None:
    message_to_reply = update.message if update.message else update.effective_message
    if not cameraEnabled:
        message_to_reply.reply_text("camera is disabled")
        return
    message_to_reply.bot.send_chat_action(chat_id=chatId, action=ChatAction.RECORD_VIDEO)

    (bio, width, height) = cameraWrap.take_gif()

    message_to_reply.bot.send_chat_action(chat_id=chatId, action=ChatAction.UPLOAD_VIDEO)
    message_to_reply.reply_animation(animation=bio, width=width, height=height, timeout=60, disable_notification=True,
                                     caption=get_status()[0])
    # if debug:
    #     message_to_reply.reply_text(f"measured fps is {fps}", disable_notification=True)


def get_video(update: Update, _: CallbackContext) -> None:
    message_to_reply = update.message if update.message else update.effective_message
    if not cameraEnabled:
        message_to_reply.reply_text("camera is disabled")
        return

    message_to_reply.bot.send_chat_action(chat_id=chatId, action=ChatAction.RECORD_VIDEO)
    (bio, width, height) = cameraWrap.take_video()
    message_to_reply.bot.send_chat_action(chat_id=chatId, action=ChatAction.UPLOAD_VIDEO)
    message_to_reply.reply_video(video=bio, width=width, height=height)
    # if debug:
    #     message_to_reply.reply_text(f"measured fps is {fps}, video fps {fps_video}", disable_notification=True)


def manage_printing(command: str) -> None:
    ws.send(json.dumps({"jsonrpc": "2.0", "method": f"printer.print.{command}", "id": myId}))


def togle_power_device(device: str, enable: bool):
    ws.send(json.dumps({"jsonrpc": "2.0",
                        "method": "machine.device_power.on" if enable else "machine.device_power.off",
                        "id": myId,
                        "params": {f"{device}": None}
                        }))


def emergency_stop_printer():
    ws.send(json.dumps({"jsonrpc": "2.0", "method": f"printer.emergency_stop", "id": myId}))


def pause_printing(update: Update, __: CallbackContext) -> None:
    update.message.bot.send_chat_action(chat_id=chatId, action=ChatAction.TYPING)
    keyboard = [
        [
            InlineKeyboardButton(emoji.emojize(':robot: Yes. Pause print'), callback_data=f'pause_printing'),
            InlineKeyboardButton(emoji.emojize(':cross_mark: cancel'), callback_data='do_nothing'),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    update.message.reply_text('Pause printing?', reply_markup=reply_markup)


def resume_printing(_: Update, __: CallbackContext) -> None:
    manage_printing('resume')


def cancel_printing(update: Update, __: CallbackContext) -> None:
    update.message.bot.send_chat_action(chat_id=chatId, action=ChatAction.TYPING)
    keyboard = [
        [
            InlineKeyboardButton(emoji.emojize(':robot: Yes. Cancel print'), callback_data=f'cancel_printing'),
            InlineKeyboardButton(emoji.emojize(':cross_mark: cancel'), callback_data='do_nothing'),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    update.message.reply_text('Cancel printing?', reply_markup=reply_markup)


def emergency_stop(update: Update, _: CallbackContext) -> None:
    update.message.bot.send_chat_action(chat_id=chatId, action=ChatAction.TYPING)
    keyboard = [
        [
            InlineKeyboardButton(emoji.emojize(':robot: Yes. Emergency Stop'), callback_data=f'emergency_stop'),
            InlineKeyboardButton(emoji.emojize(':cross_mark: cancel'), callback_data='do_nothing'),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    update.message.reply_text('Execute emergency stop?', reply_markup=reply_markup)


def power_off(update: Update, _: CallbackContext) -> None:
    message_to_reply = update.message if update.message else update.effective_message
    message_to_reply.bot.send_chat_action(chat_id=chatId, action=ChatAction.TYPING)
    if poweroff_device:

        keyboard = [
            [
                InlineKeyboardButton(emoji.emojize(':robot: Yes. power off'), callback_data=f'power_off_printer'),
                InlineKeyboardButton(emoji.emojize(':cross_mark: cancel'), callback_data='do_nothing'),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        message_to_reply.reply_text('Power Off printer?', reply_markup=reply_markup)
    else:
        message_to_reply.reply_text("No power device in config!")


def light_toggle(update: Update, _: CallbackContext) -> None:
    message_to_reply = update.message if update.message else update.effective_message
    if cameraWrap.light_device:
        cameraWrap.togle_ligth_device()
    else:
        message_to_reply.reply_text("No light device in config!")


@DeprecationWarning
def start(update: Update, _: CallbackContext) -> None:
    update.message.bot.send_chat_action(chat_id=chatId, action=ChatAction.TYPING)
    keyboard = [
        [
            InlineKeyboardButton(emoji.emojize(':robot: status'), callback_data='status'),
            InlineKeyboardButton(emoji.emojize(':pause_button: pause'), callback_data='pause'),
            InlineKeyboardButton(emoji.emojize(':cross_mark: cancel'), callback_data='cancel'),
        ],
        [
            InlineKeyboardButton(emoji.emojize(':camera: photo'), callback_data='photo'),
            InlineKeyboardButton(emoji.emojize(':movie_camera: video'), callback_data='video'),
            InlineKeyboardButton("gif", callback_data='gif'),
        ],
        [
            InlineKeyboardButton(emoji.emojize(':electric_plug: power off'), callback_data='power_off'),
            InlineKeyboardButton(emoji.emojize(':flashlight: light toggle'), callback_data='light_toggle')
        ],
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    update.message.reply_text('Bot commands:', reply_markup=reply_markup)


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
    elif query.data == 'cancel_printing':
        manage_printing('cancel')
        query.delete_message()
    elif query.data == 'pause_printing':
        manage_printing('pause')
        query.delete_message()
    elif query.data == 'power_off_printer':
        togle_power_device(poweroff_device, False)
        query.delete_message()
    elif '.gcode' in query.data and ':' not in query.data:
        keyboard_keys = dict((x['callback_data'], x['text']) for x in
                             itertools.chain.from_iterable(query.message.reply_markup.to_dict()['inline_keyboard']))
        filename = keyboard_keys[query.data]
        keyboard = [
            [
                InlineKeyboardButton(emoji.emojize(':robot: print file'), callback_data=f'print_file:{query.data}'),
                InlineKeyboardButton(emoji.emojize(':cross_mark: cancel printing'), callback_data='cancel_file'),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        query.edit_message_text(text=f"Start printing file:{filename}", reply_markup=reply_markup)
    elif 'print_file' in query.data:
        filename = query.message.text.split(':')[-1].replace('?', '').replace(' ', '')
        response = requests.post(
            f"http://{host}/printer/print/start?filename={urllib.parse.quote(filename)}")
        if not response.ok:
            query.edit_message_text(text=f"Failed start printing file {filename}")
        else:
            query.delete_message()

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
    response = request.urlopen(f"http://{host}/server/files/list?root=gcodes")
    resp = json.loads(response.read())
    files = sorted(resp['result'], key=lambda item: item['modified'], reverse=True)[:5]
    files_keys = list(map(list, zip(map(create_file_button, files))))
    reply_markup = InlineKeyboardMarkup(files_keys)

    update.message.reply_text('Gcode files to print:', reply_markup=reply_markup)


def upload_file(update: Update, _: CallbackContext) -> None:
    update.message.bot.send_chat_action(chat_id=chatId, action=ChatAction.UPLOAD_DOCUMENT)
    doc = update.message.document
    if '.gcode' in doc.file_name:
        bio = BytesIO()
        bio.name = doc.file_name
        bio.write(doc.get_file().download_as_bytearray())
        bio.seek(0)
        files = {'file': bio}
        res = requests.post(f"http://{host}/server/files/upload", files=files)
        if res.ok:
            filehash = hashlib.md5(doc.file_name.encode()).hexdigest() + '.gcode'
            keyboard = [
                [
                    InlineKeyboardButton(emoji.emojize(':robot: print file'), callback_data=f'print_file:{filehash}'),
                    InlineKeyboardButton(emoji.emojize(':cross_mark: do nothing'), callback_data='do_nothing'),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            update.message.reply_text(f"successfully uploaded file: {doc.file_name}", reply_markup=reply_markup)
        else:
            update.message.reply_text(f"failed uploading file: {doc.file_name}")
    else:
        update.message.reply_text(f"unknown filetype in {doc.file_name}")


def bot_error_handler(update: object, context: CallbackContext) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)


def start_bot(token):
    updater = Updater(token, workers=4)  # we have too small ram on oPi zero...

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
    dispatcher.add_handler(CommandHandler("files", get_gcode_files, run_async=True))

    dispatcher.add_handler(MessageHandler(Filters.document & ~Filters.command, upload_file, run_async=True))

    dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, echo))

    dispatcher.add_error_handler(bot_error_handler)

    updater.start_polling()

    return updater


def on_close(ws, close_status_code, close_msg):
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
                            'gcode_move': ['position'],

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
        if not klippy_connected and ws.keep_running is True:
            on_open(ws)
        time.sleep(1)


def timelapse_sheduler(interval: int):
    while True:
        take_lapse_photo()
        time.sleep(interval)


def websocket_to_message(ws_message):
    json_message = json.loads(ws_message)
    if debug:
        logger.debug(ws_message)

    global klippy_printing_filename, klippy_printing, klippy_printing_duration, klippy_printing_progress
    global klippy_connected, last_notify_percent, last_notify_heigth, last_message, poweroff_device_on

    if 'error' in json_message:
        return

    if 'id' in json_message:
        if 'id' in json_message and 'result' in json_message:
            if 'status' in json_message['result']:
                if 'print_stats' in json_message['result']['status'] and json_message['result']['status']['print_stats']['state'] == "printing":
                    klippy_printing = True
                    klippy_printing_filename = json_message['result']['status']['print_stats']['filename']
                    cameraWrap.filename = klippy_printing_filename
                    klippy_printing_duration = json_message['result']['status']['print_stats']['print_duration']
                return
            if 'display_status' in json_message['result']:
                last_message = json_message['result']['display_status']['message']
                klippy_printing_progress = json_message['result']['display_status']['progress']
            if 'state' in json_message['result']:
                if json_message['result']['state'] == 'ready':
                    if ws.keep_running:
                        klippy_connected = True
                        subscribe(ws)
                else:
                    klippy_connected = False
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
            bot_updater.job_queue.run_once(send_messsage, 0, context=f"{json_message['error']['message']}")

        # if json_message["method"] == "notify_gcode_response":
        #     val = ws_message["params"][0]
        #     # Todo: add global state for mcu disconnects!
        #     if 'Lost communication with MCU' not in ws_message["params"][0]:
        #         botUpdater.dispatcher.bot.send_message(chatId, ws_message["params"])
        #
    else:
        if json_message["method"] == "notify_gcode_response":
            if 'timelapse photo' in json_message["params"]:
                take_lapse_photo()
            if json_message["params"][0].startswith('tgnotify'):
                bot_updater.bot.send_message(chatId, json_message["params"][0][9:])
            if json_message["params"][0].startswith('tgerror'):
                bot_updater.bot.send_message(chatId, json_message["params"][0][8:])
        if json_message["method"] in ["notify_klippy_shutdown", "notify_klippy_disconnected"]:
            logger.warning(f"klippy disconnect detected with message: {json_message['method']}")
            klippy_connected = False

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
                    last_message = json_message['params'][0]['display_status']['message']
                if 'progress' in json_message["params"][0]['display_status']:
                    notify(progress=int(json_message["params"][0]['display_status']['progress'] * 100))
                    klippy_printing_progress = json_message["params"][0]['display_status']['progress']
            if 'toolhead' in json_message["params"][0] and 'position' in json_message["params"][0]['toolhead']:
                # position_z = json_message["params"][0]['toolhead']['position'][2]
                pass
            if 'gcode_move' in json_message["params"][0] and 'position' in json_message["params"][0]['gcode_move']:
                position_z = json_message["params"][0]['gcode_move']['position'][2]  # Todo: use gcode_position instead
                notify(position_z=int(position_z))
                take_lapse_photo(position_z)
            if 'print_stats' in json_message['params'][0]:
                message = ""
                state = ""
                if 'filename' in json_message['params'][0]['print_stats']:
                    klippy_printing_filename = json_message['params'][0]['print_stats']['filename']
                    cameraWrap.filename = klippy_printing_filename
                if 'state' in json_message['params'][0]['print_stats']:
                    state = json_message['params'][0]['print_stats']['state']
                # Fixme: reset notify percent & heigth on finish/caancel/start
                if 'print_duration' in json_message['params'][0]['print_stats']:
                    klippy_printing_duration = json_message['params'][0]['print_stats']['print_duration']
                if state == "printing":
                    klippy_printing = True
                    reset_notifications()
                    if not klippy_printing_filename:
                        klippy_printing_filename = get_status()[1]
                        cameraWrap.filename = klippy_printing_filename
                    cameraWrap.clean()
                    bot_updater.job_queue.run_once(send_print_start_info, 0, context=klippy_printing_filename)
                # Todo: cleanup timelapse dir on cancel print!
                elif state == 'complete':
                    klippy_printing = False
                    bot_updater.job_queue.run_once(send_timelapse, 5)
                elif state:
                    klippy_printing = False
                    message += f"Printer state change: {json_message['params'][0]['print_stats']['state']} \n"

                if message:
                    bot_updater.job_queue.run_once(send_messsage, 0, context=message)


def parselog():
    with open('telegram.log') as f:
        lines = f.readlines()

    wslines = list(filter(lambda it: ' - {' in it, lines))
    tt = list(map(lambda el: el.split(' - ')[-1].replace('\n', ''), wslines))

    for mes in tt:
        websocket_to_message(mes)
    print('lalal')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Moonraker Telegram Bot")
    parser.add_argument(
        "-c", "--configfile", default="./telegram.conf",
        metavar='<configfile>',
        help="Location of moonraker tlegram bot configuration file")
    system_args = parser.parse_args()
    klipper_config_path = system_args.configfile[:system_args.configfile.rfind('/')]
    conf = ConfigFactory.parse_file(system_args.configfile)
    host = conf.get_string('server', 'localhost')
    token = conf.get_string('bot_token')
    chatId = int(conf.get_string('chat_id'))
    notify_percent = conf.get_int('notify.percent', 0)
    notify_heigth = conf.get_int('notify.heigth', 0)
    notify_interval = conf.get_int('notify.interval', 0)
    notify_groups = conf.get_list('notify.groups', list())
    timelapse_heigth = conf.get_float('timelapse.heigth', 0.0)
    timelapse_enabled = conf.get_bool('timelapse.enabled', False)
    timelapse_basedir = conf.get_string('timelapse.basedir', '/tmp/timelapse')
    timelapse_cleanup = conf.get_bool('timelapse.cleanup', True)
    timelapse_interval_time = conf.get_int('timelapse.interval_time', 0)
    timelapse_fps = conf.get_int('timelapse.fps', 15)

    cameraEnabled = conf.get_bool('camera.enabled', True)
    flipHorisontally = conf.get_bool('camera.flipHorisontally', False)
    flipVertically = conf.get_bool('camera.flipVertically', False)
    gifDuration = conf.get_int('camera.gifDuration', 5)
    videoDuration = conf.get_int('camera.videoDuration', gifDuration * 2)
    reduceGif = conf.get_int('camera.reduceGif', 0)
    cameraHost = conf.get_string('camera.host', f"http://{host}:8080/?action=stream")
    video_fourcc = conf.get_string('camera.fourcc', 'x264')
    camera_threads = conf.get_int('camera.threads', int(os.cpu_count() / 2))
    camera_light_enable = conf.get_bool('camera.light.enable', False)
    camera_light_timeout = conf.get_int('camera.light.timeout', 0)

    poweroff_device = conf.get_string('poweroff_device', "")
    light_device = conf.get_string('light_device', "")
    debug = conf.get_bool('debug', False)
    hidden_methods = conf.get_list('hidden_methods', list())

    if debug:
        faulthandler.enable()
        logger.setLevel(logging.DEBUG)

    cameraWrap = Camera(host, cameraHost, camera_threads, light_device, camera_light_enable, camera_light_timeout, flipVertically, flipHorisontally, video_fourcc, gifDuration,
                        reduceGif, videoDuration, klipper_config_path, timelapse_basedir, timelapse_cleanup, timelapse_fps)

    bot_updater = start_bot(token)


    # websocket communication
    def on_message(ws, message):
        websocket_to_message(message)


    ws = websocket.WebSocketApp(f"ws://{host}/websocket", on_message=on_message, on_open=on_open, on_error=on_error, on_close=on_close)

    # debug reasons only
    # parselog()

    greeting_message()

    # TOdo: rewrite using ApShecduller
    threading.Thread(target=reshedule, daemon=True, name='Connection_shedul').start()
    if timelapse_interval_time > 0:
        threading.Thread(target=timelapse_sheduler, args=(timelapse_interval_time,), daemon=True).start()

    ws.run_forever(skip_utf8_validation=True)
    logger.info("Exiting! Moonraker connection lost!")

    cv2.destroyAllWindows()
    bot_updater.stop()
