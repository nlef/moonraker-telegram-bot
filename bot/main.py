import argparse
import configparser
import faulthandler
import hashlib
from io import BytesIO
import itertools
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import signal
import sys
import tarfile
import time
from typing import Dict, List, Union
from zipfile import ZipFile

from apscheduler.events import EVENT_JOB_ERROR  # type: ignore
from apscheduler.schedulers.background import BackgroundScheduler  # type: ignore
import emoji
import telegram
from telegram import (
    BotCommand,
    ChatAction,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
    MessageEntity,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import PARSEMODE_HTML
from telegram.error import BadRequest
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler, Filters, MessageHandler, Updater
from telegram.utils.helpers import escape
from websocket_helper import WebSocketHelper

from camera import Camera
from configuration import ConfigWrapper
from klippy import Klippy
from notifications import Notifier
from power_device import PowerDevice
from timelapse import Timelapse

logging.basicConfig(
    handlers=[logging.StreamHandler(sys.stdout)],
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    logger.error(
        "Uncaught exception",
        exc_info=(exc_type, exc_value, exc_traceback),
        stack_info=True,
    )


sys.excepthook = handle_exception


# some global params
def errors_listener(event):
    exception_info = f"Job {event.job_id} raised"
    if hasattr(event.exception, "message"):
        exception_info += f"{event.exception.message}\n"
    else:
        exception_info += f"{event.exception}\n"
    logger.error(
        exception_info,
        exc_info=(
            type(event.exception),
            event.exception,
            event.exception.__traceback__,
        ),
    )
    # logger.error(exception_info, exc_info=True, stack_info=True)


scheduler = BackgroundScheduler(
    {
        "apscheduler.executors.default": {
            "class": "apscheduler.executors.pool:ThreadPoolExecutor",
            "max_workers": "10",
        },
        "apscheduler.job_defaults.coalesce": "false",
        "apscheduler.job_defaults.max_instances": "1",
    },
    daemon=True,
)
scheduler.add_listener(errors_listener, EVENT_JOB_ERROR)

configWrap: ConfigWrapper
main_pid = os.getpid()
cameraWrap: Camera
timelapse: Timelapse
notifier: Notifier
klippy: Klippy
light_power_device: PowerDevice
psu_power_device: PowerDevice
ws_helper: WebSocketHelper


def echo_unknown(update: Update, _: CallbackContext) -> None:
    if update.message is None:
        return
    update.message.reply_text(f"unknown command: {update.message.text}", quote=True)


def unknown_chat(update: Update, _: CallbackContext) -> None:
    if update.effective_chat is None:
        logger.warning("Undefined effective chat")
        return

    if update.effective_chat.id in configWrap.notifications.notify_groups:
        return

    if update.effective_chat.id < 0 or update.effective_message is None:
        return

    mess = f"Unauthorized access detected with chat_id: {update.effective_chat.id}.\n<tg-spoiler>This incident will be reported.</tg-spoiler>"
    update.effective_message.reply_text(
        mess,
        parse_mode=PARSEMODE_HTML,
        quote=True,
    )
    logger.error("Unauthorized access detected from `%s` with chat_id `%s`. Message: %s", update.effective_chat.username, update.effective_chat.id, update.effective_message.to_json())


def status(update: Update, _: CallbackContext) -> None:
    if update.effective_message is None or update.effective_message.bot is None:
        logger.warning("Undefined effective message or bot")
        return

    if klippy.printing and not configWrap.notifications.group_only:
        notifier.update_status()
        time.sleep(configWrap.camera.light_timeout + 1.5)
        update.effective_message.delete()
    else:
        mess = escape(klippy.get_status())
        if cameraWrap.enabled:
            with cameraWrap.take_photo() as bio:
                update.effective_message.bot.send_chat_action(chat_id=configWrap.secrets.chat_id, action=ChatAction.UPLOAD_PHOTO)
                update.effective_message.reply_photo(
                    photo=bio,
                    caption=mess,
                    parse_mode=PARSEMODE_HTML,
                    disable_notification=notifier.silent_commands,
                )
                bio.close()
        else:
            update.effective_message.bot.send_chat_action(chat_id=configWrap.secrets.chat_id, action=ChatAction.TYPING)
            update.effective_message.reply_text(
                mess,
                parse_mode=PARSEMODE_HTML,
                disable_notification=notifier.silent_commands,
                quote=True,
            )


def check_unfinished_lapses(bot: telegram.Bot):
    files = cameraWrap.detect_unfinished_lapses()
    if not files:
        return
    bot.send_chat_action(chat_id=configWrap.secrets.chat_id, action=ChatAction.TYPING)
    files_keys: List[List[InlineKeyboardButton]] = list(
        map(
            lambda el: [
                InlineKeyboardButton(
                    text=el,
                    callback_data=f"lapse:{hashlib.md5(el.encode()).hexdigest()}",
                )
            ],
            files,
        )
    )
    files_keys.append(
        [
            InlineKeyboardButton(
                emoji.emojize(":no_entry_sign: ", language="alias"),
                callback_data="do_nothing",
            )
        ]
    )
    files_keys.append(
        [
            InlineKeyboardButton(
                emoji.emojize(":wastebasket: Cleanup unfinished", language="alias"),
                callback_data="cleanup_timelapse_unfinished",
            )
        ]
    )
    reply_markup = InlineKeyboardMarkup(files_keys)
    bot.send_message(
        configWrap.secrets.chat_id,
        text="Unfinished timelapses found\nBuild unfinished timelapse?",
        reply_markup=reply_markup,
        disable_notification=notifier.silent_status,
    )


def get_video(update: Update, _: CallbackContext) -> None:
    if update.effective_message is None or update.effective_message.bot is None:
        logger.warning("Undefined effective message or bot")
        return

    if not cameraWrap.enabled:
        update.effective_message.reply_text("camera is disabled", quote=True)
    else:
        info_reply: Message = update.effective_message.reply_text(
            text="Starting video recording",
            disable_notification=notifier.silent_commands,
            quote=True,
        )
        update.effective_message.bot.send_chat_action(chat_id=configWrap.secrets.chat_id, action=ChatAction.RECORD_VIDEO)
        with cameraWrap.take_video_generator() as (video_bio, thumb_bio, width, height):
            info_reply.edit_text(text="Uploading video")
            if video_bio.getbuffer().nbytes > 52428800:
                info_reply.edit_text(text="Telegram has a 50mb restriction...")
            else:
                update.effective_message.reply_video(
                    video=video_bio,
                    thumb=thumb_bio,
                    width=width,
                    height=height,
                    caption="",
                    timeout=120,
                    disable_notification=notifier.silent_commands,
                    quote=True,
                )
                update.effective_message.bot.delete_message(chat_id=configWrap.secrets.chat_id, message_id=info_reply.message_id)

            video_bio.close()
            thumb_bio.close()


def confirm_keyboard(callback_mess: str) -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(
                emoji.emojize(":white_check_mark: ", language="alias"),
                callback_data=callback_mess,
            ),
            InlineKeyboardButton(
                emoji.emojize(":no_entry_sign: ", language="alias"),
                callback_data="do_nothing",
            ),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def command_confirm_message(update: Update, text: str, callback_mess: str) -> None:
    if update.effective_message is None or update.effective_message.bot is None:
        logger.warning("Undefined effective message or bot")
        return

    update.effective_message.bot.send_chat_action(chat_id=configWrap.secrets.chat_id, action=ChatAction.TYPING)
    update.effective_message.reply_text(
        text,
        reply_markup=confirm_keyboard(callback_mess),
        disable_notification=notifier.silent_commands,
        quote=True,
    )


def pause_printing(update: Update, __: CallbackContext) -> None:
    command_confirm_message(update, text="Pause printing?", callback_mess="pause_printing")


def resume_printing(update: Update, __: CallbackContext) -> None:
    command_confirm_message(update, text="Resume printing?", callback_mess="resume_printing")


def cancel_printing(update: Update, __: CallbackContext) -> None:
    command_confirm_message(update, text="Cancel printing?", callback_mess="cancel_printing")


def emergency_stop(update: Update, _: CallbackContext) -> None:
    command_confirm_message(update, text="Execute emergency stop?", callback_mess="emergency_stop")


def firmware_restart(update: Update, _: CallbackContext) -> None:
    command_confirm_message(update, text="Restart klipper firmware?", callback_mess="firmware_restart")


def shutdown_host(update: Update, _: CallbackContext) -> None:
    command_confirm_message(update, text="Shutdown host?", callback_mess="shutdown_host")


def reboot_host(update: Update, _: CallbackContext) -> None:
    command_confirm_message(update, text="Reboot host?", callback_mess="reboot_host")


def bot_restart(update: Update, _: CallbackContext) -> None:
    command_confirm_message(update, text="Restart bot?", callback_mess="bot_restart")


def send_logs(update: Update, _: CallbackContext) -> None:
    if update.effective_message is None or update.effective_message.bot is None:
        logger.warning("Undefined effective message or bot")
        return

    update.effective_message.bot.send_chat_action(chat_id=configWrap.secrets.chat_id, action=ChatAction.UPLOAD_DOCUMENT)
    update.effective_message.reply_text(text=klippy.get_versions_info(), disable_notification=notifier.silent_commands, quote=True)
    logs_list: List[Union[InputMediaAudio, InputMediaDocument, InputMediaPhoto, InputMediaVideo]] = []
    if Path(configWrap.bot_config.log_file).exists():
        with open(configWrap.bot_config.log_file, "rb") as fh:
            logs_list.append(InputMediaDocument(fh.read(), filename="telegram.log"))
    if Path(f"{configWrap.bot_config.log_path}/klippy.log").exists():
        with open(f"{configWrap.bot_config.log_path}/klippy.log", "rb") as fh:
            logs_list.append(InputMediaDocument(fh.read(), filename="klippy.log"))
    if Path(f"{configWrap.bot_config.log_path}/moonraker.log").exists():
        with open(f"{configWrap.bot_config.log_path}/moonraker.log", "rb") as fh:
            logs_list.append(InputMediaDocument(fh.read(), filename="moonraker.log"))
    if logs_list:
        update.effective_message.reply_media_group(logs_list, disable_notification=notifier.silent_commands, quote=True)
    else:
        update.effective_message.reply_text(
            text="No logs found in log_path",
            disable_notification=notifier.silent_commands,
            quote=True,
        )


def restart_bot() -> None:
    scheduler.shutdown(wait=False)
    if ws_helper.websocket:
        ws_helper.websocket.close()
    os.kill(main_pid, signal.SIGTERM)


def power(update: Update, _: CallbackContext) -> None:
    if update.effective_message is None or update.effective_message.bot is None:
        logger.warning("Undefined effective message or bot")
        return

    update.effective_message.bot.send_chat_action(chat_id=configWrap.secrets.chat_id, action=ChatAction.TYPING)
    if psu_power_device:
        if psu_power_device.device_state:
            update.effective_message.reply_text(
                "Power Off printer?",
                reply_markup=confirm_keyboard("power_off_printer"),
                disable_notification=notifier.silent_commands,
                quote=True,
            )
        else:
            update.effective_message.reply_text(
                "Power On printer?",
                reply_markup=confirm_keyboard("power_on_printer"),
                disable_notification=notifier.silent_commands,
                quote=True,
            )
    else:
        update.effective_message.reply_text(
            "No power device in config!",
            disable_notification=notifier.silent_commands,
            quote=True,
        )


def light_toggle(update: Update, _: CallbackContext) -> None:
    if update.effective_message is None:
        logger.warning("Undefined effective message")
        return

    if light_power_device:
        mess = f"Device `{light_power_device.name}` toggled " + ("on" if light_power_device.toggle_device() else "off")
        update.effective_message.reply_text(
            mess,
            parse_mode=PARSEMODE_HTML,
            disable_notification=notifier.silent_commands,
            quote=True,
        )
    else:
        update.effective_message.reply_text(
            "No light device in config!",
            disable_notification=notifier.silent_commands,
            quote=True,
        )


def button_lapse_handler(update: Update, context: CallbackContext) -> None:
    if update.effective_message is None or update.effective_message.bot is None or update.callback_query is None:
        logger.warning("Undefined effective message or bot or query")
        return
    query = update.callback_query
    if query.message is None:
        logger.error("Undefined callback_query.message for %s", query.to_json())
        return
    if query.message.reply_markup is None:
        logger.error("Undefined query.message.reply_markup in %s", query.message.to_json())
        return

    lapse_name = next(
        filter(
            lambda el: el[0].callback_data == query.data,
            query.message.reply_markup.inline_keyboard,
        )
    )[0].text
    info_mess: Message = context.bot.send_message(
        chat_id=configWrap.secrets.chat_id,
        text=f"Starting time-lapse assembly for {lapse_name}",
        disable_notification=notifier.silent_commands,
    )
    context.bot.send_chat_action(chat_id=configWrap.secrets.chat_id, action=ChatAction.RECORD_VIDEO)
    # Todo: refactor all timelapse cals
    (
        video_bio,
        thumb_bio,
        width,
        height,
        video_path,
        _gcode_name,
    ) = cameraWrap.create_timelapse_for_file(lapse_name, info_mess)
    info_mess.edit_text(text="Uploading time-lapse")
    if video_bio.getbuffer().nbytes > 52428800:
        info_mess.edit_text(text=f"Telegram bots have a 50mb filesize restriction, please retrieve the timelapse from the configured folder\n{video_path}")
    else:
        context.bot.send_video(
            configWrap.secrets.chat_id,
            video=video_bio,
            thumb=thumb_bio,
            width=width,
            height=height,
            caption=f"time-lapse of {lapse_name}",
            timeout=120,
            disable_notification=notifier.silent_commands,
        )
        context.bot.delete_message(chat_id=configWrap.secrets.chat_id, message_id=info_mess.message_id)
        cameraWrap.cleanup(lapse_name)

    video_bio.close()
    thumb_bio.close()
    query.delete_message()
    check_unfinished_lapses(context.bot)


def print_file_dialog_handler(update: Update, context: CallbackContext) -> None:
    if update.effective_message is None or update.effective_message.bot is None or update.callback_query is None:
        logger.warning("Undefined effective message or bot or query")
        return
    query = update.callback_query
    if query.message is None:
        logger.error("Undefined callback_query.message for %s", query.to_json())
        return
    if query.message.reply_markup is None:
        logger.error("Undefined query.message.reply_markup in %s", query.message.to_json())
        return
    if update.effective_message.reply_to_message is None:
        logger.error("Undefined reply_to_message for %s", update.effective_message.to_json())
        return
    keyboard_keys = dict((x["callback_data"], x["text"]) for x in itertools.chain.from_iterable(query.message.reply_markup.to_dict()["inline_keyboard"]))
    pri_filename = keyboard_keys[query.data]
    keyboard = [
        [
            InlineKeyboardButton(
                emoji.emojize(":robot: print file", language="alias"),
                callback_data=f"print_file:{query.data}",
            ),
            InlineKeyboardButton(
                emoji.emojize(":cross_mark: cancel", language="alias"),
                callback_data="cancel_file",
            ),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    start_pre_mess = "Start printing file:"
    message, bio = klippy.get_file_info_by_name(pri_filename, f"{start_pre_mess}{pri_filename}?")
    update.effective_message.reply_to_message.reply_photo(
        photo=bio,
        caption=message,
        reply_markup=reply_markup,
        disable_notification=notifier.silent_commands,
        quote=True,
        caption_entities=[MessageEntity(type="bold", offset=len(start_pre_mess), length=len(pri_filename))],
    )
    bio.close()
    context.bot.delete_message(update.effective_message.chat_id, update.effective_message.message_id)


def button_handler(update: Update, context: CallbackContext) -> None:
    if update.effective_message is None or update.effective_message.bot is None or update.callback_query is None:
        logger.warning("Undefined effective message or bot or query")
        return

    query = update.callback_query

    if query.bot is None:
        logger.error("Undefined bot in callback_query")
        return

    if query.message is None:
        logger.error("Undefined callback_query.message for %s", query.to_json())
        return

    if query.data is None:
        logger.error("Undefined callback_query.data for %s", query.to_json())
        return

    context.bot.send_chat_action(chat_id=configWrap.secrets.chat_id, action=ChatAction.TYPING)

    query.answer()
    if query.data == "do_nothing":
        if update.effective_message.reply_to_message:
            context.bot.delete_message(
                update.effective_message.chat_id,
                update.effective_message.reply_to_message.message_id,
            )
        query.delete_message()
    elif query.data == "emergency_stop":
        ws_helper.emergency_stop_printer()
        query.delete_message()
    elif query.data == "firmware_restart":
        ws_helper.firmware_restart_printer()
        query.delete_message()
    elif query.data == "cancel_printing":
        ws_helper.manage_printing("cancel")
        query.delete_message()
    elif query.data == "pause_printing":
        ws_helper.manage_printing("pause")
        query.delete_message()
    elif query.data == "resume_printing":
        ws_helper.manage_printing("resume")
        query.delete_message()
    elif query.data == "cleanup_timelapse_unfinished":
        context.bot.send_message(chat_id=configWrap.secrets.chat_id, text="Removing unfinished timelapses data")
        cameraWrap.cleanup_unfinished_lapses()
        query.delete_message()
    elif "gcode:" in query.data:
        ws_helper.execute_ws_gcode_script(query.data.replace("gcode:", ""))
    elif update.effective_message.reply_to_message is None:
        logger.error("Undefined reply_to_message for %s", update.effective_message.to_json())
    elif query.data == "shutdown_host":
        update.effective_message.reply_to_message.reply_text("Shutting down host", quote=True)
        query.delete_message()
        ws_helper.shutdown_pi_host()
    elif query.data == "reboot_host":
        update.effective_message.reply_to_message.reply_text("Rebooting host", quote=True)
        query.delete_message()
        ws_helper.reboot_pi_host()
    elif query.data == "bot_restart":
        update.effective_message.reply_to_message.reply_text("Restarting bot", quote=True)
        query.delete_message()
        restart_bot()
    elif query.data == "power_off_printer":
        psu_power_device.switch_device(False)
        update.effective_message.reply_to_message.reply_text(
            f"Device `{psu_power_device.name}` toggled off",
            parse_mode=PARSEMODE_HTML,
            quote=True,
        )
        query.delete_message()
    elif query.data == "power_on_printer":
        psu_power_device.switch_device(True)
        update.effective_message.reply_to_message.reply_text(
            f"Device `{psu_power_device.name}` toggled on",
            parse_mode=PARSEMODE_HTML,
            quote=True,
        )
        query.delete_message()
    elif "macro:" in query.data:
        command = query.data.replace("macro:", "")
        update.effective_message.reply_to_message.reply_text(
            f"Running macro: {command}",
            disable_notification=notifier.silent_commands,
            quote=True,
        )
        query.delete_message()
        ws_helper.execute_ws_gcode_script(command)
    elif "macroc:" in query.data:
        command = query.data.replace("macroc:", "")
        query.edit_message_text(
            text=f"Execute macro {command}?",
            reply_markup=confirm_keyboard(f"macro:{command}"),
        )
    elif "gcode_files_offset:" in query.data:
        offset = int(query.data.replace("gcode_files_offset:", ""))
        query.edit_message_text(
            "Gcode files to print:",
            reply_markup=gcode_files_keyboard(offset),
        )
    elif "print_file" in query.data:
        if query.message.caption:
            filename = query.message.parse_caption_entity(query.message.caption_entities[0]).strip()
        else:
            filename = query.message.parse_entity(query.message.entities[0]).strip()
        if klippy.start_printing_file(filename):
            query.delete_message()
        else:
            if query.message.text:
                query.edit_message_text(text=f"Failed start printing file {filename}")
            elif query.message.caption:
                query.message.edit_caption(caption=f"Failed start printing file {filename}")
    elif "rstrt_srvc:" in query.data:
        service_name = query.data.replace("rstrt_srvc:", "")
        query.edit_message_text(
            text=f'Restart service "{service_name}"?',
            reply_markup=confirm_keyboard(f"rstrt_srv:{service_name}"),
        )
    elif "rstrt_srv:" in query.data:
        service_name = query.data.replace("rstrt_srv:", "")
        update.effective_message.reply_to_message.reply_text(
            f"Restarting service: {service_name}",
            disable_notification=notifier.silent_commands,
            quote=True,
        )
        query.delete_message()
        ws_helper.restart_system_service(service_name)
    else:
        logger.debug("unknown message from inline keyboard query: %s", query.data)
        query.delete_message()


def get_gcode_files(update: Update, _: CallbackContext) -> None:
    if update.effective_message is None or update.effective_message.bot is None:
        logger.warning("Undefined effective message or bot")
        return

    update.effective_message.bot.send_chat_action(chat_id=configWrap.secrets.chat_id, action=ChatAction.TYPING)
    update.effective_message.reply_text(
        "Gcode files to print:",
        reply_markup=gcode_files_keyboard(),
        disable_notification=notifier.silent_commands,
        quote=True,
    )


def gcode_files_keyboard(offset: int = 0):
    def create_file_button(element) -> List[InlineKeyboardButton]:
        filename = element["path"] if "path" in element else element["filename"]
        return [
            InlineKeyboardButton(
                filename,
                callback_data=hashlib.md5(filename.encode()).hexdigest() + ".gcode",
            )
        ]

    gcodes = klippy.get_gcode_files()
    files_keys: List[List[InlineKeyboardButton]] = list(map(create_file_button, gcodes[offset : offset + 10]))
    if len(gcodes) > 10:
        arrows = []
        if offset >= 10:
            arrows.append(
                InlineKeyboardButton(
                    emoji.emojize(":arrow_backward:previous", language="alias"),
                    callback_data=f"gcode_files_offset:{offset - 10}",
                )
            )
        arrows.append(
            InlineKeyboardButton(
                emoji.emojize(":no_entry_sign: ", language="alias"),
                callback_data="do_nothing",
            )
        )
        if offset + 10 <= len(gcodes):
            arrows.append(
                InlineKeyboardButton(
                    emoji.emojize("next:arrow_forward:", language="alias"),
                    callback_data=f"gcode_files_offset:{offset + 10}",
                )
            )

        files_keys += [arrows]

    return InlineKeyboardMarkup(files_keys)


def services_keyboard(update: Update, _: CallbackContext) -> None:
    def create_service_button(element) -> List[InlineKeyboardButton]:
        return [
            InlineKeyboardButton(
                element,
                callback_data=f"rstrt_srvc:{element}" if configWrap.telegram_ui.require_confirmation_macro else f"rstrt_srv:{element}",
            )
        ]

    services = configWrap.bot_config.services
    service_keys: List[List[InlineKeyboardButton]] = list(map(create_service_button, services))
    if update.effective_message is None or update.effective_message.bot is None:
        logger.warning("Undefined effective message or bot")
        return

    update.effective_message.bot.send_chat_action(chat_id=configWrap.secrets.chat_id, action=ChatAction.TYPING)
    update.effective_message.reply_text(
        "Services to operate:",
        reply_markup=InlineKeyboardMarkup(service_keys),
        disable_notification=notifier.silent_commands,
        quote=True,
    )


def exec_gcode(update: Update, _: CallbackContext) -> None:
    # maybe use context.args
    if update.effective_message is None or update.effective_message.text is None:
        logger.warning("Undefined effective message or text")
        return

    if update.effective_message.text != "/gcode":
        command = update.effective_message.text.replace("/gcode ", "")
        ws_helper.execute_ws_gcode_script(command)
    else:
        update.effective_message.reply_text("No command provided", quote=True)


def get_macros(update: Update, _: CallbackContext) -> None:
    if update.effective_message is None or update.effective_message.bot is None:
        logger.warning("Undefined effective message or bot")
        return

    update.effective_message.bot.send_chat_action(chat_id=configWrap.secrets.chat_id, action=ChatAction.TYPING)
    files_keys: List[List[InlineKeyboardButton]] = list(
        map(
            lambda el: [
                InlineKeyboardButton(
                    el,
                    callback_data=f"macroc:{el}" if configWrap.telegram_ui.require_confirmation_macro else f"macro:{el}",
                )
            ],
            klippy.macros,
        )
    )
    reply_markup = InlineKeyboardMarkup(files_keys)

    update.effective_message.reply_text(
        "Gcode macros:",
        reply_markup=reply_markup,
        disable_notification=notifier.silent_commands,
        quote=True,
    )


def macros_handler(update: Update, _: CallbackContext) -> None:
    if not update.effective_message or update.effective_message.text is None:
        logger.warning("Undefined effective message or update.effective_message.text")
        return

    command = update.effective_message.text.replace("/", "").upper()
    if command in klippy.macros_all:
        if configWrap.telegram_ui.require_confirmation_macro:
            update.effective_message.reply_text(
                f"Execute marco {command}?",
                reply_markup=confirm_keyboard(f"macro:{command}"),
                disable_notification=notifier.silent_commands,
                quote=True,
            )
        else:
            ws_helper.execute_ws_gcode_script(command)
            update.effective_message.reply_text(
                f"Running macro: {command}",
                disable_notification=notifier.silent_commands,
                quote=True,
            )
    else:
        echo_unknown(update, _)


def upload_file(update: Update, _: CallbackContext) -> None:
    if update.effective_message is None or update.effective_message.bot is None:
        logger.warning("Undefined effective message or bot")
        return

    update.effective_message.bot.send_chat_action(chat_id=configWrap.secrets.chat_id, action=ChatAction.UPLOAD_DOCUMENT)
    doc = update.effective_message.document
    if doc is None or doc.file_name is None:
        update.effective_message.reply_text(
            f"Document or filename is None in {update.effective_message.to_json()}",
            disable_notification=notifier.silent_commands,
            quote=True,
        )
        return

    if not doc.file_name.endswith((".gcode", ".zip", ".tar.gz", ".tar.bz2", ".tar.xz")):
        update.effective_message.reply_text(
            f"unknown filetype in {doc.file_name}",
            disable_notification=notifier.silent_commands,
            quote=True,
        )
        return

    try:
        file_byte_array = doc.get_file().download_as_bytearray()
    except BadRequest as badreq:
        update.effective_message.reply_text(
            f"Bad request: {badreq.message}",
            disable_notification=notifier.silent_commands,
            quote=True,
        )
        return

    # Todo: add context managment!
    uploaded_bio = BytesIO()
    uploaded_bio.name = doc.file_name
    uploaded_bio.write(file_byte_array)
    uploaded_bio.seek(0)

    sending_bio = BytesIO()
    if doc.file_name.endswith(".gcode"):
        sending_bio = uploaded_bio
    elif doc.file_name.endswith(".zip"):
        with ZipFile(uploaded_bio) as my_zip_file:
            if len(my_zip_file.namelist()) > 1:
                update.effective_message.reply_text(
                    f"Multiple files in archive {doc.file_name}",
                    disable_notification=notifier.silent_commands,
                    quote=True,
                )
            else:
                with my_zip_file.open(my_zip_file.namelist()[0]) as contained_file:
                    sending_bio.name = contained_file.name
                    sending_bio.write(contained_file.read())
                    sending_bio.seek(0)

    elif doc.file_name.endswith((".tar.gz", ".tar.bz2", ".tar.xz")):
        with tarfile.open(fileobj=uploaded_bio, mode="r:*") as tararch:
            if len(tararch.getmembers()) > 1:
                update.effective_message.reply_text(
                    f"Multiple files in archive {doc.file_name}",
                    disable_notification=notifier.silent_commands,
                    quote=True,
                )
            else:
                archived_file = tararch.getmembers()[0]
                extracted_f = tararch.extractfile(archived_file)
                if extracted_f:
                    sending_bio.name = archived_file.name
                    sending_bio.write(extracted_f.read())
                    sending_bio.seek(0)

    if sending_bio.name:
        if not sending_bio.name.endswith(".gcode"):
            update.effective_message.reply_text(
                f"Not a gcode file {doc.file_name}",
                disable_notification=notifier.silent_commands,
                quote=True,
            )
        else:
            if klippy.upload_gcode_file(sending_bio, configWrap.bot_config.upload_path):
                start_pre_mess = "Successfully uploaded file:"
                mess, thumb = klippy.get_file_info_by_name(
                    f"{configWrap.bot_config.formated_upload_path}{sending_bio.name}", f"{start_pre_mess}{configWrap.bot_config.formated_upload_path}{sending_bio.name}"
                )
                filehash = hashlib.md5(doc.file_name.encode()).hexdigest() + ".gcode"
                keyboard = [
                    [
                        InlineKeyboardButton(
                            emoji.emojize(":robot: print file", language="alias"),
                            callback_data=f"print_file:{filehash}",
                        ),
                        InlineKeyboardButton(
                            emoji.emojize(":cross_mark: do nothing", language="alias"),
                            callback_data="do_nothing",
                        ),
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                update.effective_message.reply_photo(
                    photo=thumb,
                    caption=mess,
                    reply_markup=reply_markup,
                    disable_notification=notifier.silent_commands,
                    quote=True,
                    caption_entities=[MessageEntity(type="bold", offset=len(start_pre_mess), length=len(f"{configWrap.bot_config.formated_upload_path}{sending_bio.name}"))],
                )
                thumb.close()
                # Todo: delete uploaded file
                # bot.delete_message(update.effective_message.chat_id, update.effective_message.message_id)
            else:
                update.effective_message.reply_text(
                    f"Failed uploading file: {sending_bio.name}",
                    disable_notification=notifier.silent_commands,
                    quote=True,
                )

    uploaded_bio.close()
    sending_bio.close()


def bot_error_handler(_: object, context: CallbackContext) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)


def create_keyboard():
    if not configWrap.telegram_ui.buttons_default:
        return configWrap.telegram_ui.buttons

    custom_keyboard = []
    if cameraWrap.enabled:
        custom_keyboard.append("/video")
    if psu_power_device:
        custom_keyboard.append("/power")
    if light_power_device:
        custom_keyboard.append("/light")

    keyboard = configWrap.telegram_ui.buttons
    if len(custom_keyboard) > 0:
        keyboard.append(custom_keyboard)
    return keyboard


def bot_commands() -> Dict[str, str]:
    commands = {
        "help": "list bot commands",
        "status": "send klipper status",
        "pause": "pause printing",
        "resume": "resume printing",
        "cancel": "cancel printing",
        "files": "list gcode files. you can start printing one from menu",
        "logs": "get klipper, moonraker, bot logs",
        "macros": "list all visible macros from klipper",
        "gcode": 'run any gcode command, spaces are supported. "gcode G28 Z"',
        "video": "will take mp4 video from camera",
        "power": "toggle moonraker power device from config",
        "light": "toggle light",
        "emergency": "emergency stop printing",
        "bot_restart": "restarts the bot service, useful for config updates",
        "shutdown": "shutdown Pi gracefully",
        "reboot": "reboot Pi gracefully",
    }
    return {c: a for c, a in commands.items() if c not in configWrap.telegram_ui.hidden_bot_commands}


def help_command(update: Update, _: CallbackContext) -> None:
    if update.effective_message is None:
        logger.warning("Undefined effective message")
        return
    mess = (
        escape(klippy.get_versions_info(bot_only=True))
        + escape("\n".join([f"/{c} - {a}" for c, a in bot_commands().items()]))
        + '\n\nPlease refer to the <a href="https://github.com/nlef/moonraker-telegram-bot/wiki">wiki</a> for additional information'
    )
    update.effective_message.reply_text(
        text=mess,
        parse_mode=PARSEMODE_HTML,
        quote=True,
    )


def prepare_command(marco: str):
    if re.match("^[a-zA-Z0-9_]{1,32}$", marco):
        try:
            return BotCommand(marco.lower(), marco)
        except Exception as ex:
            logger.error("Bad macro name '%s'\n%s", marco, ex)
            return None
    else:
        logger.warning("Bad macro name '%s'", marco)
        return None


def prepare_commands_list(macros: List[str], add_macros: bool):
    commands = list(bot_commands().items())
    if add_macros:
        commands += list(filter(lambda el: el, map(prepare_command, macros)))
        if len(commands) >= 100:
            logger.warning("Commands list too large!")
            commands = commands[0:99]
    return commands


def greeting_message(bot: telegram.Bot) -> None:
    if configWrap.secrets.chat_id == 0:
        return
    response = klippy.check_connection()
    mess = ""
    if response:
        mess += escape(f"Bot online, no moonraker connection!\n {response} \nFailing...")
    else:
        mess += "Printer online"
        if configWrap.configuration_errors:
            mess += escape(klippy.get_versions_info(bot_only=True)) + configWrap.configuration_errors

    reply_markup = ReplyKeyboardMarkup(create_keyboard(), resize_keyboard=True)
    bot.send_message(
        configWrap.secrets.chat_id,
        text=mess,
        parse_mode=PARSEMODE_HTML,
        reply_markup=reply_markup,
        disable_notification=notifier.silent_status,
    )
    bot.set_my_commands(commands=prepare_commands_list(klippy.macros, configWrap.telegram_ui.include_macros_in_command_list))
    klippy.add_bot_announcements_feed()
    check_unfinished_lapses(bot)


def start_bot(bot_token, socks):
    request_kwargs = {
        "read_timeout": 15,
    }

    if socks:
        request_kwargs["proxy_url"] = f"socks5://{socks}"

    updater = Updater(
        token=bot_token,
        base_url=configWrap.bot_config.api_url,
        workers=4,
        request_kwargs=request_kwargs,
    )

    dispatcher = updater.dispatcher

    dispatcher.add_handler(MessageHandler(~Filters.chat(configWrap.secrets.chat_id), unknown_chat))

    dispatcher.add_handler(CallbackQueryHandler(button_lapse_handler, pattern="lapse:"))
    dispatcher.add_handler(CallbackQueryHandler(print_file_dialog_handler, pattern=re.compile("^\\S[^\\:]+\\.gcode$")))
    dispatcher.add_handler(CallbackQueryHandler(button_handler))
    dispatcher.add_handler(CommandHandler("help", help_command, run_async=True))
    dispatcher.add_handler(CommandHandler("status", status, run_async=True))
    dispatcher.add_handler(CommandHandler("video", get_video))
    dispatcher.add_handler(CommandHandler("pause", pause_printing))
    dispatcher.add_handler(CommandHandler("resume", resume_printing))
    dispatcher.add_handler(CommandHandler("cancel", cancel_printing))
    dispatcher.add_handler(CommandHandler("power", power))
    dispatcher.add_handler(CommandHandler("light", light_toggle))
    dispatcher.add_handler(CommandHandler("emergency", emergency_stop))
    dispatcher.add_handler(CommandHandler("shutdown", shutdown_host))
    dispatcher.add_handler(CommandHandler("reboot", reboot_host))
    dispatcher.add_handler(CommandHandler("bot_restart", bot_restart))
    dispatcher.add_handler(CommandHandler("fw_restart", firmware_restart))
    dispatcher.add_handler(CommandHandler("services", services_keyboard))
    dispatcher.add_handler(CommandHandler("files", get_gcode_files, run_async=True))
    dispatcher.add_handler(CommandHandler("macros", get_macros, run_async=True))
    dispatcher.add_handler(CommandHandler("gcode", exec_gcode, run_async=True))
    dispatcher.add_handler(CommandHandler("logs", send_logs, run_async=True))

    dispatcher.add_handler(MessageHandler(Filters.command, macros_handler, run_async=True))

    dispatcher.add_handler(MessageHandler(Filters.document & ~Filters.command, upload_file, run_async=True))

    dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, echo_unknown))

    dispatcher.add_error_handler(bot_error_handler)

    updater.start_polling()

    return updater


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Moonraker Telegram Bot")
    parser.add_argument(
        "-c",
        "--configfile",
        default="./telegram.conf",
        metavar="<configfile>",
        help="Location of moonraker telegram bot configuration file",
    )
    parser.add_argument(
        "-l",
        "--logfile",
        metavar="<logfile>",
        help="Location of moonraker telegram bot log file",
    )
    system_args = parser.parse_args()
    conf = configparser.ConfigParser(allow_no_value=True, inline_comment_prefixes=(";", "#"))

    # Todo: os.chdir(Path(sys.path[0]).parent.absolute())
    os.chdir(sys.path[0])

    conf.read(system_args.configfile)
    configWrap = ConfigWrapper(conf)
    configWrap.bot_config.log_path_update(system_args.logfile)

    with open(configWrap.bot_config.log_file, "a", encoding="utf-8") as f:
        f.write("\n*******************************************************************\n")
        f.write("Current Moonraker telegram bot config\n")
        conf.remove_option("bot", "bot_token")
        conf.remove_option("bot", "chat_id")
        conf.write(f)
        f.write("\n*******************************************************************\n")

    rotatingHandler = RotatingFileHandler(
        configWrap.bot_config.log_file,
        maxBytes=26214400,
        backupCount=3,
    )
    rotatingHandler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    logger.addHandler(rotatingHandler)

    logger.error(configWrap.parsing_errors + "\n" + configWrap.unknown_fields)

    if configWrap.bot_config.debug:
        faulthandler.enable()
        logger.setLevel(logging.DEBUG)
        logging.getLogger("apscheduler").addHandler(rotatingHandler)
        logging.getLogger("apscheduler").setLevel(logging.DEBUG)

    light_power_device = PowerDevice(configWrap.bot_config.light_device_name, configWrap.bot_config.host)
    psu_power_device = PowerDevice(configWrap.bot_config.poweroff_device_name, configWrap.bot_config.host)

    klippy = Klippy(configWrap, light_power_device, psu_power_device, rotatingHandler)
    cameraWrap = Camera(configWrap, klippy, light_power_device, rotatingHandler)
    bot_updater = start_bot(configWrap.secrets.token, configWrap.bot_config.socks_proxy)
    timelapse = Timelapse(configWrap, klippy, cameraWrap, scheduler, bot_updater.bot, rotatingHandler)
    notifier = Notifier(configWrap, bot_updater.bot, klippy, cameraWrap, scheduler, rotatingHandler)

    ws_helper = WebSocketHelper(configWrap, klippy, notifier, timelapse, scheduler, light_power_device, psu_power_device, rotatingHandler)

    scheduler.start()

    greeting_message(bot_updater.bot)

    ws_helper.run_forever()

    logger.info("Exiting! Moonraker connection lost!")

    bot_updater.stop()
