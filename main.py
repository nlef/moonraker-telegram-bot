import argparse
import glob
import hashlib
import itertools
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
from pathlib import Path
from urllib import request
from urllib.request import urlopen

import requests
from numpy import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatAction, ReplyKeyboardMarkup, \
    ReplyKeyboardRemove
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext, CallbackQueryHandler
import websocket

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
cameraEnabled = True
cameraHost = "localhost:8080"
chatId = 12341234
notify_percent = 5
notify_heigth = 5
flipVertically = False
flipHorisontally = False
gifDuration = 5
reduceGif = 2
poweroff_device: str
light_device: str
timelapse_heigth: float = 0.2
timelapse_enabled: bool = False
timelapse_basedir: str
debug = False

klipper_config_path: str
klippy_connected: bool = False
klippy_printing: bool = False
klippy_printing_duration: float = 0.0
klippy_printing_filename: str
ws: websocket.WebSocketApp

last_notify_heigth: int = 0
last_notify_percent: int = 0
last_timelapse_heigth: float = 0.2
last_message: str


# Define a few command handlers. These usually take the two arguments update and
# context. Error handlers also receive the raised TelegramError object in error.
def checkAuthorized(update: Update):
    if not int(update.message.chat.id) == chatId:
        logger.warning("Unauthorized message from ")
        logger.warning(update.message.chat)
        logger.warning("Message: " + update.message.text)
        return False
    return True
    
def help_command(update: Update, context: CallbackContext) -> None:
    if not checkAuthorized(update):
        return
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
    if not checkAuthorized(update):
        return
    update.message.reply_text(f"unknown command: {update.message.text}")


def chat(update: Update, _: CallbackContext) -> None:
    if not checkAuthorized(update):
        return
    update.message.reply_text(f"Chat id: {update.message.chat.id}")


def info(update: Update, context: CallbackContext) -> None:
    if not checkAuthorized(update):
        return
    response = request.urlopen(f"http://{host}/printer/info")
    update.message.reply_text(json.loads(response.read()))


def reset_notifications() -> None:
    global last_notify_percent, last_notify_heigth, klippy_printing_duration
    last_notify_percent = 0
    last_notify_heigth = 0
    klippy_printing_duration = 0


def get_status() -> str:
    response = request.urlopen(
        f"http://{host}/printer/objects/query?webhooks&print_stats=filename,total_duration,print_duration,filament_used,state,message&display_status=message")
    resp = json.loads(response.read())
    print_stats = resp['result']['status']['print_stats']
    webhook = resp['result']['status']['webhooks']
    total_time = time.strftime("%H:%M:%S", time.gmtime(print_stats['total_duration']))
    light_status = get_light_status()
    message = emoji.emojize(':robot: Printer status: ') + f"{webhook['state']}\n"
    if 'display_status' in resp['result']['status']:
        if 'message' in resp['result']['status']['display_status']:
            msg = resp['result']['status']['display_status']['message']
    if msg and msg is not None:
        message += f"{msg}\n"
    if 'state_message' in webhook:
        message += f"State message: {webhook['state_message']}\n"
    message += emoji.emojize(':mechanical_arm: Printing process status: ') + f"{print_stats['state']} \n"
    if print_stats['state'] in ('printing', 'paused', 'complete'):
        message += f"Print time: {total_time} \n" \
                   f"Printing filename: {print_stats['filename']} \n" \
                   f"Used filament: {round(print_stats['filament_used'] / 1000, 2)}m"
    if light_device:
        message += emoji.emojize(':flashlight: Light Status: ') + f"{light_status}"
    return message


def get_light_status() -> str:
    if light_device:
        response = request.urlopen(f"http://{host}/machine/device_power/status?{light_device}")
        resp = json.loads(response.read())
        status = resp['result'][f'{light_device}']
    else:
        status = "no device"

    return status


def status(update: Update, _: CallbackContext) -> None:
    if not checkAuthorized(update):
        return
    message_to_reply = update.message if update.message else update.effective_message

    message_to_reply.bot.send_chat_action(chat_id=chatId, action=ChatAction.TYPING)
    message_to_reply.reply_text(get_status())


def notify(bot, message):
    if not klippy_printing or not klippy_printing_duration > 0.0:
        return
    if cameraEnabled:
        bot.send_chat_action(chat_id=chatId, action=ChatAction.UPLOAD_PHOTO)
        bot.send_photo(chatId, photo=take_photo(), caption=message)
    else:
        bot.send_chat_action(chat_id=chatId, action=ChatAction.TYPING)
        bot.send_message(chatId, text=message)


def take_photo() -> BytesIO:
    cap = cv2.VideoCapture(cameraHost)
    success, image = cap.read()

    if not success:
        img = Image.open(random.choice(glob.glob(f'{klipper_config_path}/imgs/*.jpg')))
    else:
        img = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        if flipVertically:
            img = img.transpose(Image.FLIP_TOP_BOTTOM)
        if flipHorisontally:
            img = img.transpose(Image.FLIP_LEFT_RIGHT)

    cap.release()
    cv2.destroyAllWindows()

    bio = BytesIO()
    bio.name = 'status.jpeg'
    img.save(bio, 'JPEG')
    bio.seek(0)
    return bio


def take_lapse_photo():
    if not timelapse_enabled or not klippy_printing_filename:
        logger.debug(f"lapse is inactive for enabled {timelapse_enabled} or file undefined")
        return
    # Todo: check for space avaliable?
    lapse_dir = f'{timelapse_basedir}/{klippy_printing_filename}'
    Path(lapse_dir).mkdir(parents=True, exist_ok=True)
    filename = f'{lapse_dir}/{time.time()}.jpg'
    with open(filename, "wb") as outfile:
        outfile.write(take_photo().getbuffer())


def send_timelapse(bot):
    if not timelapse_enabled or not klippy_printing_filename:
        logger.debug(f"lapse is inactive for enabled {timelapse_enabled} or file undefined")
        return
    bot.send_chat_action(chat_id=chatId, action=ChatAction.RECORD_VIDEO)
    lapse_dir = f'{timelapse_basedir}/{klippy_printing_filename}'
    # Fixme: get single file!
    for filename in glob.glob(f'{lapse_dir}/*.jpg'):
        img = cv2.imread(filename)
        height, width, layers = img.shape
        size = (width, height)
        break

    filepath = f'{lapse_dir}/lapse.mp4'
    out = cv2.VideoWriter(filepath, fourcc=cv2.VideoWriter_fourcc(*'x264'), fps=15.0, frameSize=size)

    photos = glob.glob(f'{lapse_dir}/*.jpg')
    photos.sort(key=os.path.getmtime)
    for filename in photos:
        out.write(cv2.imread(filename))

    out.release()
    bot.send_chat_action(chat_id=chatId, action=ChatAction.UPLOAD_VIDEO)
    bio = BytesIO()
    bio.name = 'lapse.mp4'
    with open(filepath, 'rb') as fh:
        bio.write(fh.read())
    for filename in glob.glob(f'{lapse_dir}/*'):
        os.remove(filename)
    Path(lapse_dir).rmdir()
    bio.seek(0)
    bot.send_video(chatId, video=bio, width=width, height=height, caption=f'time-lapse of {klippy_printing_filename}')


def process_frame(frame, width, height) -> Image:
    image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    if flipVertically:
        image = image.transpose(Image.FLIP_TOP_BOTTOM)
    if flipHorisontally:
        image = image.transpose(Image.FLIP_LEFT_RIGHT)
    if reduceGif > 0:
        image = image.resize((int(width / reduceGif), int(height / reduceGif)))
    return image


def get_photo(update: Update, context: CallbackContext) -> None:
    if not checkAuthorized(update):
        return
    message_to_reply = update.message if update.message else update.effective_message

    message_to_reply.bot.send_chat_action(chat_id=chatId, action=ChatAction.UPLOAD_PHOTO)
    message_to_reply.reply_photo(photo=take_photo())


def get_gif(update: Update, context: CallbackContext) -> None:
    if not checkAuthorized(update):
        return
    message_to_reply = update.message if update.message else update.effective_message
    if not cameraEnabled:
        message_to_reply.reply_text("camera is disabled")
        return
    message_to_reply.bot.send_chat_action(chat_id=chatId, action=ChatAction.RECORD_VIDEO)
    gif = []
    cap = cv2.VideoCapture(cameraHost)
    success, image = cap.read()

    if not success:
        message_to_reply.reply_text("camera connection failed!")
        return

    height, width, channels = image.shape
    gif.append(process_frame(image, width, height))

    fps = 0
    t_end = time.time() + gifDuration
    # TOdo: calc frame count
    while success and time.time() < t_end:
        prev_frame_time = time.time()
        success, image_inner = cap.read()
        new_frame_time = time.time()
        gif.append(process_frame(image_inner, width, height))
        fps = 1 / (new_frame_time - prev_frame_time)

    cap.release()
    cv2.destroyAllWindows()

    message_to_reply.bot.send_chat_action(chat_id=chatId, action=ChatAction.UPLOAD_VIDEO)

    bio = BytesIO()
    bio.name = 'image.gif'
    gif[0].save(bio, format='GIF', save_all=True, optimize=True, append_images=gif[1:], duration=int(1000 / int(fps)),
                loop=0)
    bio.seek(0)
    message_to_reply.reply_animation(animation=bio, width=width, height=height, timeout=60, disable_notification=True,
                                     caption=get_status())

    if debug:
        message_to_reply.reply_text(f"measured fps is {fps}", disable_notification=True)


def process_video_frame(frame):
    if flipVertically and flipHorisontally:
        frame = cv2.flip(frame, -1)
    elif flipHorisontally:
        frame = cv2.flip(frame, 1)
    elif flipVertically:
        frame = cv2.flip(frame, 0)

    return frame


def get_video(update: Update, context: CallbackContext) -> None:
    if not checkAuthorized(update):
        return
    message_to_reply = update.message if update.message else update.effective_message
    if not cameraEnabled:
        message_to_reply.reply_text("camera is disabled")
        return

    message_to_reply.bot.send_chat_action(chat_id=chatId, action=ChatAction.RECORD_VIDEO)
    cap = cv2.VideoCapture(cameraHost)
    success, frame = cap.read()

    if not success:
        message_to_reply.reply_text("camera connection failed!")
        return

    height, width, channels = frame.shape
    fps_video = cap.get(cv2.CAP_PROP_FPS)
    fps = 10
    filepath = os.path.join('/tmp/', 'video.mp4')
    out = cv2.VideoWriter(filepath, fourcc=cv2.VideoWriter_fourcc(*'x264'), fps=fps_video, frameSize=(width, height))
    t_end = time.time() + gifDuration * 2
    while success and time.time() < t_end:
        prev_frame_time = time.time()
        success, frame_inner = cap.read()
        out.write(process_video_frame(frame_inner))
        fps = 1 / (time.time() - prev_frame_time)

    cap.release()
    out.set(cv2.CAP_PROP_FPS, fps)
    out.release()
    cv2.destroyAllWindows()

    message_to_reply.bot.send_chat_action(chat_id=chatId, action=ChatAction.UPLOAD_VIDEO)

    bio = BytesIO()
    bio.name = 'video.mp4'
    with open(filepath, 'rb') as fh:
        bio.write(fh.read())

    os.remove(filepath)
    bio.seek(0)
    message_to_reply.reply_video(video=bio, width=width, height=height)
    if debug:
        message_to_reply.reply_text(f"measured fps is {fps}, video fps {fps_video}", disable_notification=True)


def manage_printing(command: str) -> None:
    ws.send(json.dumps({"jsonrpc": "2.0", "method": f"printer.print.{command}", "id": myId}))


def pause_printing(update: Update, context: CallbackContext) -> None:
    if not checkAuthorized(update):
        return
    manage_printing('pause')


def resume_printing(update: Update, context: CallbackContext) -> None:
    if not checkAuthorized(update):
        return
    manage_printing('resume')


def cancel_printing(update: Update, context: CallbackContext) -> None:
    if not checkAuthorized(update):
        return
    manage_printing('cancel')


def power_off(update: Update, context: CallbackContext) -> None:
    if not checkAuthorized(update):
        return
    message_to_reply = update.message if update.message else update.effective_message
    if poweroff_device:
        ws.send(json.dumps({"jsonrpc": "2.0", "method": "machine.device_power.off", "id": myId,
                            "params": {f"{poweroff_device}": None}}))
    else:
        message_to_reply.reply_text("No power device in config!")


def light_toggle(update: Update, context: CallbackContext) -> None:
    if not checkAuthorized(update):
        return
    message_to_reply = update.message if update.message else update.effective_message
    if light_device:
        status = get_light_status()
        if status == 'on':
            ws.send(json.dumps({"jsonrpc": "2.0", "method": "machine.device_power.off", "id": myId,
                                "params": {f"{light_device}": None}}))
        else:
            ws.send(json.dumps({"jsonrpc": "2.0", "method": "machine.device_power.on", "id": myId,
                                "params": {f"{light_device}": None}}))
    else:
        message_to_reply.reply_text("No light device in config!")


def start(update: Update, _: CallbackContext) -> None:
    if not checkAuthorized(update):
        return
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


def keyboard(update: Update, _: CallbackContext) -> None:
    if not checkAuthorized(update):
        return
    update.message.bot.send_chat_action(chat_id=chatId, action=ChatAction.TYPING)
    custom_keyboard = [
        ['/status', '/pause', '/cancel', '/files'],
        ['/photo', '/video', '/gif'],
        ['/poweroff', '/keyoff', '/start']
    ]
    reply_markup = ReplyKeyboardMarkup(custom_keyboard, resize_keyboard=True)
    update.message.bot.send_message(chat_id=chatId,
                                    text="Custom Keyboard",
                                    reply_markup=reply_markup)


def keyboard_off(update: Update, _: CallbackContext) -> None:
    if not checkAuthorized(update):
        return
    update.message.bot.send_chat_action(chat_id=chatId, action=ChatAction.TYPING)
    reply_markup = ReplyKeyboardRemove()
    update.message.bot.send_message(chat_id=chatId, text="disable keyboard", reply_markup=reply_markup)


def button(update: Update, context: CallbackContext) -> None:
    if not checkAuthorized(update):
        return
    context.bot.send_chat_action(chat_id=chatId, action=ChatAction.TYPING)
    query = update.callback_query
    query.answer()
    # Todo: maybe regex check?
    if '.gcode' in query.data and ':' not in query.data:
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
        query.edit_message_text(text=f"Start printing file:{filename}?", reply_markup=reply_markup)
    elif 'print_file' in query.data:
        filename = query.message.text.split(':')[-1].replace('?', '').replace(' ', '')
        response = requests.post(f"http://{host}/printer/print/start?filename={filename}")
        if not response.ok:
            query.edit_message_text(text=f"Failed start printing file {filename}?")
        else:
            query.delete_message()
    else:
        if query.data == 'status':
            status(update, context)
        elif query.data == 'pause':
            pause_printing(update, context)
        elif query.data == 'cancel':
            cancel_printing(update, context)
        elif query.data == 'photo':
            get_photo(update, context)
        elif query.data == 'gif':
            get_gif(update, context)
        elif query.data == 'video':
            get_video(update, context)
        elif query.data == 'power_off':
            power_off(update, context)
        elif query.data == 'light_toggle':
            light_toggle(update, context)
        query.delete_message()


def get_gcode_files(update: Update, context: CallbackContext) -> None:
    if not checkAuthorized(update):
        return
    update.message.bot.send_chat_action(chat_id=chatId, action=ChatAction.TYPING)
    response = request.urlopen(f"http://{host}/server/files/list?root=gcodes")
    resp = json.loads(response.read())
    files = sorted(resp['result'], key=lambda item: item['modified'], reverse=True)[:5]
    files_keys = list(
        map(list,
            zip(
                map(lambda el: InlineKeyboardButton(el['filename'],
                                                    callback_data=hashlib.md5(
                                                        el['filename'].encode()).hexdigest() + '.gcode'),
                    files)
            )
            )
    )
    reply_markup = InlineKeyboardMarkup(files_keys)

    update.message.reply_text('Gcode files to print:', reply_markup=reply_markup)


def upload_file(update: Update, context: CallbackContext) -> None:
    if not checkAuthorized(update):
        return
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


def start_bot(token):
    # Create the Updater and pass it your bot's token.
    updater = Updater(token, workers=1)  # we have too small ram on oPi zero...

    # Get the dispatcher to register handlers
    dispatcher = updater.dispatcher

    # on different commands - answer in Telegram
    dispatcher.add_handler(CommandHandler('start', start))
    dispatcher.add_handler(CallbackQueryHandler(button))
    dispatcher.add_handler(CommandHandler('keyboard', keyboard))
    dispatcher.add_handler(CommandHandler('keyoff', keyboard_off))
    dispatcher.add_handler(CommandHandler("help", help_command))
    dispatcher.add_handler(CommandHandler("status", status))
    dispatcher.add_handler(CommandHandler("photo", get_photo))
    dispatcher.add_handler(CommandHandler("gif", get_gif))
    dispatcher.add_handler(CommandHandler("video", get_video))
    dispatcher.add_handler(CommandHandler("pause", pause_printing))
    dispatcher.add_handler(CommandHandler("resume", resume_printing))
    dispatcher.add_handler(CommandHandler("cancel", cancel_printing))
    dispatcher.add_handler(CommandHandler("chat", chat))
    dispatcher.add_handler(CommandHandler("poweroff", power_off))
    dispatcher.add_handler(CommandHandler("light", light_toggle))
    dispatcher.add_handler(CommandHandler("files", get_gcode_files))

    # on noncommand i.e message - echo the message on Telegram
    dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, echo))
    dispatcher.add_handler(MessageHandler(Filters.document & ~Filters.command, upload_file))

    # Start the Bot
    updater.start_polling()

    return updater


def on_error(ws, error):
    logger.error(error)


def on_close(ws):
    logger.info("### ws closed ###")


def subscribe(ws):
    ws.send(
        json.dumps({"jsonrpc": "2.0",
                    "method": "printer.objects.subscribe",
                    "params": {
                        "objects": {
                            "print_stats": ["filename", "state", "print_duration"],
                            "display_status": ['progress', 'message'],
                            'toolhead': ['position'],
                            'gcode_move': ['position']
                        }
                    },
                    "id": myId}))


def on_open(ws):
    ws.send(
        json.dumps({"jsonrpc": "2.0",
                    "method": "printer.info",
                    "id": myId}))


def reshedule():
    while True:
        if not klippy_connected and ws.keep_running is True:
            on_open(ws)
        time.sleep(1)


def websocket_to_message(ws_message, botUpdater):
    json_message = json.loads(ws_message)
    if debug:
        logger.debug(ws_message)

    global klippy_printing_filename, klippy_printing, klippy_connected, last_notify_percent, last_notify_heigth, klippy_printing_duration, last_message

    if 'error' in json_message:
        return

    if 'id' in json_message and 'result' in json_message:
        if 'status' in json_message['result']:
            if 'print_stats' in json_message['result']['status'] and \
                    json_message['result']['status']['print_stats']['state'] == "printing":
                klippy_printing = True
                klippy_printing_filename = json_message['result']['status']['print_stats']['filename']
                klippy_printing_duration = json_message['result']['status']['print_stats']['print_duration']
            return
        if 'state' in json_message['result']:
            if json_message['result']['state'] == 'ready':
                klippy_connected = True
                subscribe(ws)
            else:
                klippy_connected = False
            return
        botUpdater.bot.send_message(chatId, text=f"{json_message['result']}")
    if 'id' in json_message and 'error' in json_message:
        botUpdater.bot.send_message(chatId, text=f"{json_message['error']['message']}")

    # if json_message["method"] == "notify_gcode_response":
    #     val = ws_message["params"][0]
    #     # Todo: add global state for mcu disconnects!
    #     if 'Lost communication with MCU' not in ws_message["params"][0]:
    #         botUpdater.dispatcher.bot.send_message(chatId, ws_message["params"])
    #

    if json_message["method"] == "notify_gcode_response":
        if 'timelapse photo' in json_message["params"]:
            take_lapse_photo()
    if json_message["method"] in ["notify_klippy_shutdown", "notify_klippy_disconnected"]:
        logger.warning(f"klippy disconnect detected with message: {json_message['method']}")
        klippy_connected = False

    if json_message["method"] == "notify_status_update":
        if 'display_status' in json_message["params"][0]:
            if 'message' in json_message["params"][0]['display_status']:
                last_message = f"{json_message['params'][0]['display_status']['message']}"
            if 'progress' in json_message["params"][0]['display_status']:
                progress = int(json_message["params"][0]['display_status']['progress'] * 100)
                if progress < last_notify_percent - notify_percent:
                    last_notify_percent = progress
                if notify_percent != 0 and progress % notify_percent == 0 and progress > last_notify_percent:
                    notifymsg = f"Printed {progress}%"
                    if last_message:
                        notifymsg += f"\n{last_message}"
                    notify(botUpdater.bot, notifymsg)
                    last_notify_percent = progress
        if 'toolhead' in json_message["params"][0] and 'position' in json_message["params"][0]['toolhead']:
            position_z = json_message["params"][0]['toolhead']['position'][2]

        if 'gcode_move' in json_message["params"][0] and 'position' in json_message["params"][0]['gcode_move']:
            position_z = json_message["params"][0]['gcode_move']['position'][2]  # Todo: use gcode_position instead
            if int(position_z) < last_notify_heigth - notify_heigth:
                last_notify_heigth = int(position_z)
            if notify_heigth != 0 and int(position_z) % notify_heigth == 0 and int(position_z) > last_notify_heigth:
                notifymsg = f"Printed {round(position_z, 2)}mm"
                if last_message:
                    notifymsg += f"\n{last_message}"
                notify(botUpdater.bot, notifymsg)
                last_notify_heigth = int(position_z)
            if position_z % timelapse_heigth == 0:  # check vase mode!
                take_lapse_photo()
        if 'print_stats' in json_message['params'][0]:
            message = ""
            state = ""
            if 'filename' in json_message['params'][0]['print_stats']:
                klippy_printing_filename = json_message['params'][0]['print_stats']['filename']
            if 'state' in json_message['params'][0]['print_stats']:
                state = json_message['params'][0]['print_stats']['state']
            # Fixme: reset notify percent & heigth on finish/caancel/start
            if 'print_duration' in json_message['params'][0]['print_stats']:
                klippy_printing_duration = json_message['params'][0]['print_stats']['print_duration']
            if state == "printing":
                klippy_printing = True
                reset_notifications()
                message += f"Printer started printing: {klippy_printing_filename} \n"
            # Todo: cleanup timelapse dir on cancel print!
            elif state == 'complete':
                klippy_printing = False
                send_timelapse(botUpdater.bot)
            elif state:
                klippy_printing = False
                message += f"Printer state change: {json_message['params'][0]['print_stats']['state']} \n"

            if message:
                botUpdater.bot.send_chat_action(chat_id=chatId, action=ChatAction.TYPING)
                botUpdater.bot.send_message(chatId, text=message)


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
    chatId = conf.get_string('chat_id')
    notify_percent = conf.get_int('notify.percent', 5)
    notify_heigth = conf.get_int('notify.heigth', 5)
    timelapse_heigth = conf.get_float('timelapse.heigth', 0.2)
    timelapse_enabled = conf.get_bool('timelapse.enabled', False)
    timelapse_basedir = conf.get_string('timelapse.basedir', '/tmp/timelapse')

    cameraEnabled = conf.get_bool('camera.enabled', True)
    flipHorisontally = conf.get_bool('camera.flipHorisontally', False)
    flipVertically = conf.get_bool('camera.flipVertically', False)
    gifDuration = conf.get_int('camera.gifDuration', 5)
    reduceGif = conf.get_int('camera.reduceGif', 0)
    cameraHost = conf.get_string('camera.host', f"http://{host}:8080/?action=stream")
    poweroff_device = conf.get_string('poweroff_device', "")
    light_device = conf.get_string('light_device', "")
    debug = conf.get_bool('debug', False)

    if debug:
        logger.setLevel(logging.DEBUG)

    botUpdater = start_bot(token)


    # websocket communication
    def on_message(ws, message):
        websocket_to_message(message, botUpdater)


    ws = websocket.WebSocketApp(f"ws://{host}/websocket", on_message=on_message, on_error=on_error, on_close=on_close)
    ws.on_open = on_open

    botUpdater.bot.send_message(chatId, text=get_status())

    threading.Thread(target=reshedule, daemon=True).start()

    ws.run_forever(ping_interval=10, ping_timeout=2)
    logger.info("Exiting! Moonraker connection lost!")
    botUpdater.stop()
