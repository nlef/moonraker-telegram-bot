import argparse
import logging
from numpy import random

from telegram import Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext

import websocket

try:
    import thread
except ImportError:
    import _thread as thread
import time

import json
import urllib.request

from urllib.request import urlopen
from PIL import Image
from io import BytesIO
import cv2
from pyhocon import ConfigFactory
import emoji

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)

logger = logging.getLogger(__name__)

# some global params
myId = random.randint(300000)
host = "localhost"
cameraHost = "localhost:8080"
chatId = 12341234
notify_percent = 5
notify_heigth = 5
flipVertically = False
flipHorisontally = False
reduceGif = 2
poweroff_device: str
debug = False

ws: websocket.WebSocketApp

last_notify_heigth: int = 0


# Define a few command handlers. These usually take the two arguments update and
# context. Error handlers also receive the raised TelegramError object in error.
def help_command(update: Update, context: CallbackContext) -> None:
    update.message.reply_text('Help!')


def echo(update: Update, context: CallbackContext) -> None:
    update.message.reply_text(f"unknown command: {update.message.text}")


def info(update: Update, context: CallbackContext) -> None:
    response = urllib.request.urlopen(f"http://{host}/printer/info")
    update.message.reply_text(json.loads(response.read()))


def get_status() -> str:
    response = urllib.request.urlopen(
        f"http://{host}/printer/objects/query?webhooks&print_stats=filename,total_duration,print_duration,filament_used,state,message")
    resp = json.loads(response.read())
    print_stats = resp['result']['status']['print_stats']
    webhook = resp['result']['status']['webhooks']
    total_time = time.strftime("%H:%M:%S", time.gmtime(print_stats['total_duration']))
    message = emoji.emojize(':robot: Printer status: ') + f"{webhook['state']} \n"
    if 'state_message' in webhook:
        message += f"State message: {webhook['state_message']}\n"
    message += emoji.emojize(':mechanical_arm: Printing process status: ') + f"{print_stats['state']} \n"
    if print_stats['state'] in ('printing', 'paused', 'complete'):
        message += f"Print time: {total_time} \n" \
                   f"Printing filename: {print_stats['filename']} \n" \
                   f"Used filament: {round(print_stats['filament_used'] / 1000, 2)}m"
    return message


def status(update: Update, context: CallbackContext) -> None:
    update.message.reply_text(get_status())


def take_photo() -> BytesIO:
    url = f"http://{cameraHost}/?action=snapshot"
    img = Image.open(urlopen(url))
    if flipVertically:
        img = img.transpose(Image.FLIP_TOP_BOTTOM)
    if flipHorisontally:
        img = img.transpose(Image.FLIP_LEFT_RIGHT)
    bio = BytesIO()
    bio.name = 'status.jpeg'
    img.save(bio, 'JPEG')
    bio.seek(0)
    return bio


def process_frame(frame, width, height) -> Image:
    image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    if flipVertically:
        image = image.transpose(Image.FLIP_TOP_BOTTOM)
    if flipHorisontally:
        image = image.transpose(Image.FLIP_LEFT_RIGHT)
    if reduceGif > 1:
        image = image.resize((int(width / 2), int(height / 2)))
    return image


def get_photo(update: Update, context: CallbackContext) -> None:
    update.message.reply_photo(photo=take_photo())


def get_gif(update: Update, context: CallbackContext) -> None:
    gif = []
    url = f"http://{cameraHost}/?action=stream"
    cap = cv2.VideoCapture(url)
    success, image = cap.read()
    height, width, channels = image.shape
    gif.append(process_frame(image, width, height))

    fps = 0
    # Todo: rewrite with fps & duration in seconds
    while success and len(gif) < 25:
        prev_frame_time = time.time()
        success, image_inner = cap.read()
        new_frame_time = time.time()
        gif.append(process_frame(image_inner, width, height))
        fps = 1 / (new_frame_time - prev_frame_time)

    cap.release()

    bio = BytesIO()
    bio.name = 'image.gif'
    gif[0].save(bio, format='GIF', save_all=True, optimize=True, append_images=gif[1:], duration=int(1000 / int(fps)),
                loop=0)
    bio.seek(0)
    update.message.reply_animation(animation=bio, width=width, height=height, timeout=60)

    update.message.reply_text(get_status())


# we must use filesystem or write own apiPreference
def get_video(update: Update, context: CallbackContext) -> None:
    update.message.reply_text(update.message.text)
    url = f"http://{cameraHost}/?action=stream"
    cap = cv2.VideoCapture(url)
    success, image = cap.read()
    height, width, channels = image.shape
    bio = BytesIO()
    bio.name = 'video.mp4'
    out = cv2.VideoWriter(bio, fourcc=cv2.VideoWriter_fourcc(*'mp4v'), fps=20, frameSize=(width, height))
    i = 0
    # Todo: rewrite with fps & duration in seconds
    while success and i < 25:
        success, image_inner = cap.read()
        out.write(image_inner)
        i += 1

    cap.release()
    out.release()
    cv2.destroyAllWindows()

    bio.seek(0)
    update.message.reply_video(video=bio, width=width, height=height)
    # update.message.reply_text(get_status())


def manage_printing(command: str) -> None:
    ws.send(json.dumps({"jsonrpc": "2.0", "method": f"printer.print.{command}", "id": myId}))


def pause_printing(update: Update, context: CallbackContext) -> None:
    manage_printing('pause')


def resume_printing(update: Update, context: CallbackContext) -> None:
    manage_printing('resume')


def cancel_printing(update: Update, context: CallbackContext) -> None:
    manage_printing('cancel')


def power_off(update: Update, context: CallbackContext) -> None:
    if poweroff_device:
        ws.send(json.dumps({"jsonrpc": "2.0", "method": "machine.device_power.off", "id": myId,
                            "params": {f"{poweroff_device}": None}}))
    else:
        update.message.reply_text("No power device in config!")


def start_bot(token):
    # Create the Updater and pass it your bot's token.
    updater = Updater(token, workers=1)  # we have too small ram on oPi zero...

    # Get the dispatcher to register handlers
    dispatcher = updater.dispatcher

    # on different commands - answer in Telegram
    dispatcher.add_handler(CommandHandler("help", help_command))
    dispatcher.add_handler(CommandHandler("status", status))
    dispatcher.add_handler(CommandHandler("photo", get_photo))
    dispatcher.add_handler(CommandHandler("gif", get_gif))
    dispatcher.add_handler(CommandHandler("pause", pause_printing))
    dispatcher.add_handler(CommandHandler("resume", resume_printing))
    dispatcher.add_handler(CommandHandler("cancel", cancel_printing))
    dispatcher.add_handler(CommandHandler("poweroff", power_off))

    # on noncommand i.e message - echo the message on Telegram
    dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, echo))

    # Start the Bot
    updater.start_polling()

    return updater


def on_error(ws, error):
    print(error)


def on_close(ws):
    print("### closed ###")


def on_open(ws):
    # Todo: get WebSocket Id from server
    # add subscription on printer objects changes
    ws.send(
        json.dumps({"jsonrpc": "2.0",
                    "method": "printer.objects.subscribe",
                    "params": {
                        "objects": {
                            "print_stats": ["filename", "state"],
                            "display_status": ['progress', 'message'],
                            'toolhead': ['position']
                        }
                    },
                    "id": myId}))


def websocket_to_message(ws_message, botUpdater):
    json_message = json.loads(ws_message)
    if debug:
        print(ws_message)

    if 'id' in json_message and 'result' in json_message:
        if 'status' in json_message['result']:
            return
        botUpdater.bot.send_message(chatId, text=f"{json_message['result']}")
    if 'id' in json_message and 'error' in json_message:
        botUpdater.bot.send_message(chatId, text=f"{json_message['error']['message']}")

    # if ws_message["method"] == "notify_gcode_response":
    #     val = ws_message["params"][0]
    #     # Todo: add global state for mcu disconnects!
    #     if 'Lost communication with MCU' not in ws_message["params"][0]:
    #         botUpdater.dispatcher.bot.send_message(chatId, ws_message["params"])
    if json_message["method"] == "notify_status_update":
        if 'display_status' in json_message["params"][0]:
            progress = json_message["params"][0]['display_status']['progress']
            if notify_percent != 0 and int(progress * 100) % notify_percent == 0:
                botUpdater.bot.send_photo(chatId, photo=take_photo(), disable_notification=True)
                botUpdater.bot.send_message(chatId, text=f"Printed {int(progress * 100)}%")
        if 'toolhead' in json_message["params"][0] and 'position' in json_message["params"][0]['toolhead']:
            position = json_message["params"][0]['toolhead']['position'][2]
            global last_notify_heigth
            ##when we print objects in series
            if int(position) < last_notify_heigth - notify_heigth * 2:
                last_notify_heigth = int(position)
            if notify_heigth != 0 and int(position) % notify_heigth == 0 and int(position) > last_notify_heigth:
                botUpdater.bot.send_photo(chatId, photo=take_photo(), disable_notification=True)
                botUpdater.bot.send_message(chatId, text=f"Printed {round(position, 2)}mm")
                last_notify_heigth = int(position)
        if 'print_stats' in json_message['params'][0]:
            message = ""
            state = ""
            filename = ""
            if 'filename' in json_message['params'][0]['print_stats']:
                filename = json_message['params'][0]['print_stats']['filename']
            if 'state' in json_message['params'][0]['print_stats']:
                state = json_message['params'][0]['print_stats']['state']

            if state and filename:
                if state == "printing":
                    message += f"Printer started printing: {filename} \n"
            elif state:
                message += f"Printer state change: {json_message['params'][0]['print_stats']['state']} \n"

            if message:
                botUpdater.bot.send_message(chatId, text=message)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Moonraker Telegram Bot")
    parser.add_argument(
        "-c", "--configfile", default="application.conf",
        metavar='<configfile>',
        help="Location of moonraker tlegram bot configuration file")
    system_args = parser.parse_args()

    conf = ConfigFactory.parse_file(system_args.configfile)
    host = conf.get_string('server')
    token = conf.get_string('bot_token')
    chatId = conf.get_string('chat_id')
    notify_percent = conf.get_int('notify.percent', 5)
    notify_heigth = conf.get_int('notify.heigth', 5)
    flipHorisontally = conf.get_bool('camera.flipHorisontally', False)
    flipVertically = conf.get_bool('camera.flipVertically', False)
    reduceGif = conf.get_int('camera.reduceGif', 0)
    cameraHost = conf.get_string('camera.host', f"{host}:8080")
    poweroff_device = conf.get_string('poweroff_device', "")
    debug = conf.get_bool('debug', False)

    botUpdater = start_bot(token)


    # websocket communication
    def on_message(ws, message):
        websocket_to_message(message, botUpdater)


    if debug:
        websocket.enableTrace(True)

    ws = websocket.WebSocketApp(f"ws://{host}/websocket", on_message=on_message, on_error=on_error, on_close=on_close)
    ws.on_open = on_open

    botUpdater.bot.send_message(chatId, text=get_status())

    ws.run_forever()
    print("Exiting! Moonraker connection lost!")
    botUpdater.stop()
