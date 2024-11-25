import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import contextlib
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
import socket
import subprocess
import sys
import tarfile
import time
from typing import Dict, List, Optional, Union
from zipfile import ZipFile

from apscheduler.events import EVENT_JOB_ERROR  # type: ignore
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore
import emoji
import httpx
import orjson
import telegram
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaAudio, InputMediaDocument, InputMediaPhoto, InputMediaVideo, Message, MessageEntity, ReplyKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import Application, CallbackContext, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from camera import Camera, FFmpegCamera, MjpegCamera
from configuration import ConfigWrapper
from klippy import Klippy, PowerDevice
from notifications import Notifier
from timelapse import Timelapse
from websocket_helper import WebSocketHelper

with contextlib.suppress(ImportError):
    import uvloop  # type: ignore

    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())


sys.modules["json"] = orjson

logging.basicConfig(
    handlers=[logging.StreamHandler(sys.stdout)],
    format="%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s",
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


a_scheduler = AsyncIOScheduler(
    {
        "apscheduler.job_defaults.coalesce": "false",
        "apscheduler.job_defaults.max_instances": "4",
    }
)
a_scheduler.add_listener(errors_listener, EVENT_JOB_ERROR)


configWrap: ConfigWrapper
main_pid = os.getpid()
cameraWrap: Camera
timelapse: Timelapse
notifier: Notifier
klippy: Klippy
light_power_device: PowerDevice
psu_power_device: PowerDevice
ws_helper: WebSocketHelper
executors_pool: ThreadPoolExecutor = ThreadPoolExecutor(2, thread_name_prefix="bot_pool")


async def echo_unknown(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(f"unknown command: {update.message.text}", quote=True)


async def unknown_chat(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None:
        logger.warning("Undefined effective chat")
        return

    if update.effective_chat.id in configWrap.notifications.notify_groups:
        return

    if update.effective_chat.id < 0 or update.effective_message is None:
        return

    mess = f"Unauthorized access detected with chat_id: {update.effective_chat.id}.\n<tg-spoiler>This incident will be reported.</tg-spoiler>"
    await update.effective_message.reply_text(
        mess,
        parse_mode=ParseMode.HTML,
        quote=True,
    )
    logger.error("Unauthorized access detected from `%s` with chat_id `%s`. Message: %s", update.effective_chat.username, update.effective_chat.id, update.effective_message.to_json())


async def status(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None or update.effective_message.get_bot() is None:
        logger.warning("Undefined effective message or bot")
        return

    if klippy.printing and not configWrap.notifications.group_only:
        notifier.update_status()
        time.sleep(configWrap.camera.light_timeout + 1.5)
        await update.effective_message.delete()
    else:
        mess = await klippy.get_status()
        if cameraWrap.enabled:
            loop_loc = asyncio.get_running_loop()
            with await loop_loc.run_in_executor(executors_pool, cameraWrap.take_photo) as bio:
                await update.effective_message.get_bot().send_chat_action(chat_id=configWrap.secrets.chat_id, action=ChatAction.UPLOAD_PHOTO)
                await update.effective_message.reply_photo(
                    photo=bio,
                    caption=mess,
                    parse_mode=ParseMode.HTML,
                    disable_notification=notifier.silent_commands,
                )
                bio.close()
        else:
            await update.effective_message.get_bot().send_chat_action(chat_id=configWrap.secrets.chat_id, action=ChatAction.TYPING)
            await update.effective_message.reply_text(
                mess,
                parse_mode=ParseMode.HTML,
                disable_notification=notifier.silent_commands,
                quote=True,
            )


async def check_unfinished_lapses(bot: telegram.Bot):
    files = cameraWrap.detect_unfinished_lapses()
    if not files:
        return
    await bot.send_chat_action(chat_id=configWrap.secrets.chat_id, action=ChatAction.TYPING)
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
    await bot.send_message(
        configWrap.secrets.chat_id,
        text="Unfinished timelapses found\nBuild unfinished timelapse?",
        reply_markup=InlineKeyboardMarkup(files_keys),
        disable_notification=notifier.silent_status,
    )


async def get_ip(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None or update.effective_message.get_bot() is None:
        logger.warning("Undefined effective message or bot")
        return

    await update.effective_message.reply_text(get_local_ip(), quote=True)


async def get_video(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None or update.effective_message.get_bot() is None:
        logger.warning("Undefined effective message or bot")
        return

    if not cameraWrap.enabled:
        await update.effective_message.reply_text("camera is disabled", quote=True)
    else:
        info_reply: Message = await update.effective_message.reply_text(
            text="Starting video recording",
            disable_notification=notifier.silent_commands,
            quote=True,
        )
        await update.effective_message.get_bot().send_chat_action(chat_id=configWrap.secrets.chat_id, action=ChatAction.RECORD_VIDEO)

        loop_loc = asyncio.get_running_loop()
        (video_bio, thumb_bio, width, height) = await loop_loc.run_in_executor(executors_pool, cameraWrap.take_video)
        await info_reply.edit_text(text="Uploading video")
        if video_bio.getbuffer().nbytes > 52428800:
            await info_reply.edit_text(text="Telegram has a 50mb restriction...")
        else:
            await update.effective_message.reply_video(
                video=video_bio,
                thumbnail=thumb_bio,
                width=width,
                height=height,
                caption="",
                write_timeout=120,
                disable_notification=notifier.silent_commands,
                quote=True,
            )
            await update.effective_message.get_bot().delete_message(chat_id=configWrap.secrets.chat_id, message_id=info_reply.message_id)

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


async def command_confirm_message(update: Update, text: str, callback_mess: str) -> None:
    if update.effective_message is None or update.effective_message.get_bot() is None:
        logger.warning("Undefined effective message or bot")
        return

    await update.effective_message.get_bot().send_chat_action(chat_id=configWrap.secrets.chat_id, action=ChatAction.TYPING)
    await update.effective_message.reply_text(
        text,
        reply_markup=confirm_keyboard(callback_mess),
        disable_notification=notifier.silent_commands,
        quote=True,
    )


async def pause_printing(update: Update, __: ContextTypes.DEFAULT_TYPE) -> None:
    await command_confirm_message(update, text="Pause printing?", callback_mess="pause_printing")


async def resume_printing(update: Update, __: ContextTypes.DEFAULT_TYPE) -> None:
    await command_confirm_message(update, text="Resume printing?", callback_mess="resume_printing")


async def cancel_printing(update: Update, __: ContextTypes.DEFAULT_TYPE) -> None:
    await command_confirm_message(update, text="Cancel printing?", callback_mess="cancel_printing")


async def emergency_stop(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    await command_confirm_message(update, text="Execute emergency stop?", callback_mess="emergency_stop")


async def firmware_restart(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    await command_confirm_message(update, text="Restart klipper firmware?", callback_mess="firmware_restart")


async def shutdown_host(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    await command_confirm_message(update, text="Shutdown host?", callback_mess="shutdown_host")


async def reboot_host(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    await command_confirm_message(update, text="Reboot host?", callback_mess="reboot_host")


async def bot_restart(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    await command_confirm_message(update, text="Restart bot?", callback_mess="bot_restart")


def prepare_log_files() -> tuple[List[str], bool, Optional[str]]:
    dmesg_success = True
    dmesg_error = None

    if Path(f"{configWrap.bot_config.log_path}/dmesg.txt").exists():
        Path(f"{configWrap.bot_config.log_path}/dmesg.txt").unlink()

    dmesg_res = subprocess.run(f"dmesg -T > {configWrap.bot_config.log_path}/dmesg.txt", shell=True, executable="/bin/bash", check=False, capture_output=True)
    if dmesg_res.returncode != 0:
        logger.warning("dmesg file creation error: %s %s", dmesg_res.stdout.decode("utf-8"), dmesg_res.stderr.decode("utf-8"))
        dmesg_error = dmesg_res.stderr.decode("utf-8")
        dmesg_success = False

    if Path(f"{configWrap.bot_config.log_path}/debug.txt").exists():
        Path(f"{configWrap.bot_config.log_path}/debug.txt").unlink()

    commands = [
        "lsb_release -a",
        "uname -a",
        "find /dev/serial",
        "find /dev/v4l",
        "free -h",
        "df -h",
        "lsusb",
        "systemctl status KlipperScreen",
        "systemctl status klipper-mcu",
        "ip --details --statistics link show dev can0",
    ]
    for command in commands:
        subprocess.run(
            f'echo >> {configWrap.bot_config.log_path}/debug.txt;echo "{command}" >> {configWrap.bot_config.log_path}/debug.txt;{command} >> {configWrap.bot_config.log_path}/debug.txt',
            shell=True,
            executable="/bin/bash",
            check=False,
        )

    files = ["/boot/config.txt", "/boot/cmdline.txt", "/boot/armbianEnv.txt", "/boot/orangepiEnv.txt", "/boot/BoardEnv.txt", "/boot/env.txt"]
    with open(configWrap.bot_config.log_path + "/debug.txt", mode="a", encoding="utf-8") as debug_file:
        for file in files:
            try:
                if Path(file).exists():
                    debug_file.write(f"\n{file}\n")
                    with open(file, mode="r", encoding="utf-8") as file_obj:
                        debug_file.writelines(file_obj.readlines())
            except Exception as err:
                logger.warning(err)

    return ["telegram.log", "crowsnest.log", "moonraker.log", "klippy.log", "KlipperScreen.log", "dmesg.txt", "debug.txt"], dmesg_success, dmesg_error


async def send_logs(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None or update.effective_message.get_bot() is None:
        logger.warning("Undefined effective message or bot")
        return

    await update.effective_message.get_bot().send_chat_action(chat_id=configWrap.secrets.chat_id, action=ChatAction.UPLOAD_DOCUMENT)

    logs_list: List[Union[InputMediaAudio, InputMediaDocument, InputMediaPhoto, InputMediaVideo]] = []
    for log_file in prepare_log_files()[0]:
        try:
            if Path(f"{configWrap.bot_config.log_path}/{log_file}").exists():
                with open(f"{configWrap.bot_config.log_path}/{log_file}", "rb") as fh:
                    logs_list.append(InputMediaDocument(fh.read(), filename=log_file))
        except FileNotFoundError as err:
            logger.warning(err)

    await update.effective_message.reply_text(text=f"{await klippy.get_versions_info()}\nUpload logs to analyzer /upload_logs", disable_notification=notifier.silent_commands, quote=True)
    if logs_list:
        await update.effective_message.reply_media_group(logs_list, disable_notification=notifier.silent_commands, quote=True)
    else:
        await update.effective_message.reply_text(
            text=f"No logs found in log_path `{configWrap.bot_config.log_path}`",
            disable_notification=notifier.silent_commands,
            quote=True,
        )


async def upload_logs(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None or update.effective_message.get_bot() is None:
        logger.warning("Undefined effective message or bot")
        return

    files_list, dmesg_success, dmesg_error = prepare_log_files()
    if not dmesg_success:
        await update.effective_message.reply_text(
            text=f"Dmesg log file creation error {dmesg_error}",
            disable_notification=notifier.silent_commands,
            quote=True,
        )
        return

    if Path(f"{configWrap.bot_config.log_path}/logs.tar.xz").exists():
        Path(f"{configWrap.bot_config.log_path}/logs.tar.xz").unlink()

    with tarfile.open(f"{configWrap.bot_config.log_path}/logs.tar.xz", "w:xz") as tar:
        for file in files_list:
            if Path(f"{configWrap.bot_config.log_path}/{file}").exists():
                tar.add(Path(f"{configWrap.bot_config.log_path}/{file}"), arcname=file)

    with open(f"{configWrap.bot_config.log_path}/logs.tar.xz", "rb") as log_archive_ojb:
        resp = httpx.post(url="https://coderus.openrepos.net/klipper_logs", files={"tarfile": log_archive_ojb}, follow_redirects=False, timeout=25)
        if resp.status_code < 400:
            logs_path = resp.headers["location"]
            logger.info(logs_path)
            await update.effective_message.reply_text(
                text=f"Logs are available at https://coderus.openrepos.net{logs_path}",
                disable_notification=notifier.silent_commands,
                quote=True,
            )
        else:
            logger.error(resp.status_code)
            await update.effective_message.reply_text(
                text=f"Logs upload failed `{resp.status_code}`",
                disable_notification=notifier.silent_commands,
                quote=True,
            )


def restart_bot() -> None:
    a_scheduler.shutdown(wait=False)
    # if ws_helper.websocket:
    #     ws_helper.websocket.close()
    os.kill(main_pid, signal.SIGTERM)


async def power(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None or update.effective_message.get_bot() is None:
        logger.warning("Undefined effective message or bot")
        return

    await update.effective_message.get_bot().send_chat_action(chat_id=configWrap.secrets.chat_id, action=ChatAction.TYPING)
    if psu_power_device:
        if psu_power_device.device_state:
            await update.effective_message.reply_text(
                "Power Off printer?",
                reply_markup=confirm_keyboard("power_off_printer"),
                disable_notification=notifier.silent_commands,
                quote=True,
            )
        else:
            await update.effective_message.reply_text(
                "Power On printer?",
                reply_markup=confirm_keyboard("power_on_printer"),
                disable_notification=notifier.silent_commands,
                quote=True,
            )
    else:
        await update.effective_message.reply_text(
            "No device defined for /power command in bot config.\nPlease add a moonraker device to the bots config",
            disable_notification=notifier.silent_commands,
            quote=True,
        )


async def light_toggle(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None:
        logger.warning("Undefined effective message")
        return

    if light_power_device:
        mess = f"Device `{light_power_device.name}` toggled " + ("on" if await light_power_device.toggle_device() else "off")
        await update.effective_message.reply_text(
            mess,
            parse_mode=ParseMode.HTML,
            disable_notification=notifier.silent_commands,
            quote=True,
        )
    else:
        await update.effective_message.reply_text(
            "No light device in config!",
            disable_notification=notifier.silent_commands,
            quote=True,
        )


async def button_lapse_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None or update.effective_message.get_bot() is None or update.callback_query is None:
        logger.warning("Undefined effective message or bot or query")
        return
    query = update.callback_query
    if query.message is None or not query.message.is_accessible or not isinstance(query.message, Message):
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
    info_mess: Message = await context.bot.send_message(
        chat_id=configWrap.secrets.chat_id,
        text=f"Starting time-lapse assembly for {lapse_name}",
        disable_notification=notifier.silent_commands,
    )
    await context.bot.send_chat_action(chat_id=configWrap.secrets.chat_id, action=ChatAction.RECORD_VIDEO)
    # Todo: refactor all timelapse cals
    (
        video_bio,
        thumb_bio,
        width,
        height,
        video_path,
        _gcode_name,
    ) = await cameraWrap.create_timelapse_for_file(lapse_name, info_mess)
    await info_mess.edit_text(text="Uploading time-lapse")
    if video_bio.getbuffer().nbytes > 52428800:
        await info_mess.edit_text(text=f"Telegram bots have a 50mb filesize restriction, please retrieve the timelapse from the configured folder\n{video_path}")
    else:
        await context.bot.send_video(
            configWrap.secrets.chat_id,
            video=video_bio,
            thumbnail=thumb_bio,
            width=width,
            height=height,
            caption=f"time-lapse of {lapse_name}",
            write_timeout=120,
            disable_notification=notifier.silent_commands,
        )
        await context.bot.delete_message(chat_id=configWrap.secrets.chat_id, message_id=info_mess.message_id)
        cameraWrap.cleanup(lapse_name)

    video_bio.close()
    thumb_bio.close()
    await query.delete_message()
    await check_unfinished_lapses(context.bot)


async def print_file_dialog_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None or update.effective_message.get_bot() is None or update.callback_query is None:
        logger.warning("Undefined effective message or bot or query")
        return
    query = update.callback_query
    if query.message is None or not query.message.is_accessible or not isinstance(query.message, Message):
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
    start_pre_mess = "Start printing file:"
    message, bio = await klippy.get_file_info_by_name(pri_filename, f"{start_pre_mess}{pri_filename}?")
    await update.effective_message.reply_to_message.reply_photo(
        photo=bio,
        caption=message,
        reply_markup=InlineKeyboardMarkup(keyboard),
        disable_notification=notifier.silent_commands,
        quote=True,
        caption_entities=[MessageEntity(type="bold", offset=len(start_pre_mess), length=len(pri_filename))],
    )
    bio.close()
    await context.bot.delete_message(update.effective_message.chat_id, update.effective_message.message_id)


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None or update.effective_message.get_bot() is None or update.callback_query is None:
        logger.warning("Undefined effective message or bot or query")
        return

    query = update.callback_query

    if query.get_bot() is None:
        logger.error("Undefined bot in callback_query")
        return

    if query.message is None or not query.message.is_accessible or not isinstance(query.message, Message):
        logger.error("Undefined callback_query.message for %s", query.to_json())
        return

    if query.data is None:
        logger.error("Undefined callback_query.data for %s", query.to_json())
        return

    await context.bot.send_chat_action(chat_id=configWrap.secrets.chat_id, action=ChatAction.TYPING)

    await query.answer()
    if query.data == "do_nothing":
        if update.effective_message.reply_to_message:
            await context.bot.delete_message(
                update.effective_message.chat_id,
                update.effective_message.reply_to_message.message_id,
            )
        await query.delete_message()
    elif query.data == "emergency_stop":
        await ws_helper.emergency_stop_printer()
        await query.delete_message()
    elif query.data == "firmware_restart":
        await ws_helper.firmware_restart_printer()
        await query.delete_message()
    elif query.data == "cancel_printing":
        await ws_helper.manage_printing("cancel")
        await query.delete_message()
    elif query.data == "pause_printing":
        await ws_helper.manage_printing("pause")
        await query.delete_message()
    elif query.data == "resume_printing":
        await ws_helper.manage_printing("resume")
        await query.delete_message()
    elif query.data == "cleanup_timelapse_unfinished":
        await context.bot.send_message(chat_id=configWrap.secrets.chat_id, text="Removing unfinished timelapses data")
        cameraWrap.cleanup_unfinished_lapses()
        await query.delete_message()
    elif "gcode:" in query.data:
        await ws_helper.execute_ws_gcode_script(query.data.replace("gcode:", ""))
    elif update.effective_message.reply_to_message is None:
        logger.error("Undefined reply_to_message for %s", update.effective_message.to_json())
    elif query.data == "shutdown_host":
        await update.effective_message.reply_to_message.reply_text("Shutting down host", quote=True)
        await query.delete_message()
        await ws_helper.shutdown_pi_host()
    elif query.data == "reboot_host":
        await update.effective_message.reply_to_message.reply_text("Rebooting host", quote=True)
        await query.delete_message()
        await ws_helper.reboot_pi_host()
    elif query.data == "bot_restart":
        await update.effective_message.reply_to_message.reply_text("Restarting bot", quote=True)
        await query.delete_message()
        restart_bot()
    elif query.data == "power_off_printer":
        await psu_power_device.switch_device(False)
        await update.effective_message.reply_to_message.reply_text(
            f"Device `{psu_power_device.name}` toggled off",
            parse_mode=ParseMode.HTML,
            quote=True,
        )
        await query.delete_message()
    elif query.data == "power_on_printer":
        await psu_power_device.switch_device(True)
        await update.effective_message.reply_to_message.reply_text(
            f"Device `{psu_power_device.name}` toggled on",
            parse_mode=ParseMode.HTML,
            quote=True,
        )
        await query.delete_message()
    elif "macro:" in query.data:
        command = query.data.replace("macro:", "")
        await update.effective_message.reply_to_message.reply_text(
            f"Running macro: {command}",
            disable_notification=notifier.silent_commands,
            quote=True,
        )
        await query.delete_message()
        await ws_helper.execute_ws_gcode_script(command)
    elif "macroc:" in query.data:
        command = query.data.replace("macroc:", "")
        await query.edit_message_text(
            text=f"Execute macro {command}?",
            reply_markup=confirm_keyboard(f"macro:{command}"),
        )
    elif "gcode_files_offset:" in query.data:
        offset = int(query.data.replace("gcode_files_offset:", ""))
        await query.edit_message_text(
            "Gcode files to print:",
            reply_markup=await gcode_files_keyboard(offset),
        )
    elif "print_file" in query.data:
        if query.message.caption:
            filename = query.message.parse_caption_entity(query.message.caption_entities[0]).strip()
        else:
            filename = query.message.parse_entity(query.message.entities[0]).strip()
        if await klippy.start_printing_file(filename):
            await query.delete_message()
        else:
            if query.message.text:
                await query.edit_message_text(text=f"Failed start printing file {filename}")
            elif query.message.caption:
                await query.message.edit_caption(caption=f"Failed start printing file {filename}")
    elif "rstrt_srvc:" in query.data:
        service_name = query.data.replace("rstrt_srvc:", "")
        await query.edit_message_text(
            text=f'Restart service "{service_name}"?',
            reply_markup=confirm_keyboard(f"rstrt_srv:{service_name}"),
        )
    elif "rstrt_srv:" in query.data:
        service_name = query.data.replace("rstrt_srv:", "")
        await update.effective_message.reply_to_message.reply_text(
            f"Restarting service: {service_name}",
            disable_notification=notifier.silent_commands,
            quote=True,
        )
        await query.delete_message()
        await ws_helper.restart_system_service(service_name)
    else:
        logger.debug("unknown message from inline keyboard query: %s", query.data)
        await query.delete_message()


async def get_gcode_files(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None or update.effective_message.get_bot() is None:
        logger.warning("Undefined effective message or bot")
        return

    await update.effective_message.get_bot().send_chat_action(chat_id=configWrap.secrets.chat_id, action=ChatAction.TYPING)
    await update.effective_message.reply_text(
        "Gcode files to print:",
        reply_markup=await gcode_files_keyboard(),
        disable_notification=notifier.silent_commands,
        quote=True,
    )


async def gcode_files_keyboard(offset: int = 0):
    def create_file_button(element) -> List[InlineKeyboardButton]:
        filename = element["path"] if "path" in element else element["filename"]
        return [
            InlineKeyboardButton(
                filename,
                callback_data=hashlib.md5(filename.encode()).hexdigest() + ".gcode",
            )
        ]

    gcodes = await klippy.get_gcode_files()
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


async def services_keyboard(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    def create_service_button(element) -> List[InlineKeyboardButton]:
        return [
            InlineKeyboardButton(
                element,
                callback_data=f"rstrt_srvc:{element}" if configWrap.telegram_ui.require_confirmation_macro else f"rstrt_srv:{element}",
            )
        ]

    services = configWrap.bot_config.services
    service_keys: List[List[InlineKeyboardButton]] = list(map(create_service_button, services))
    if update.effective_message is None or update.effective_message.get_bot() is None:
        logger.warning("Undefined effective message or bot")
        return

    await update.effective_message.get_bot().send_chat_action(chat_id=configWrap.secrets.chat_id, action=ChatAction.TYPING)
    await update.effective_message.reply_text(
        "Services to operate:",
        reply_markup=InlineKeyboardMarkup(service_keys),
        disable_notification=notifier.silent_commands,
        quote=True,
    )


async def exec_gcode(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    # maybe use context.args
    if update.effective_message is None or update.effective_message.text is None:
        logger.warning("Undefined effective message or text")
        return

    if update.effective_message.text != "/gcode":
        command = update.effective_message.text.replace("/gcode ", "")
        await ws_helper.execute_ws_gcode_script(command)
    else:
        await update.effective_message.reply_text("No command provided", quote=True)


async def get_macros(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None or update.effective_message.get_bot() is None:
        logger.warning("Undefined effective message or bot")
        return

    await update.effective_message.get_bot().send_chat_action(chat_id=configWrap.secrets.chat_id, action=ChatAction.TYPING)
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

    await update.effective_message.reply_text(
        "Gcode macros:",
        reply_markup=InlineKeyboardMarkup(files_keys),
        disable_notification=notifier.silent_commands,
        quote=True,
    )


async def macros_handler(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message or update.effective_message.text is None:
        logger.warning("Undefined effective message or update.effective_message.text")
        return

    command = update.effective_message.text.replace("/", "").upper()
    if command in klippy.macros_all:
        if configWrap.telegram_ui.require_confirmation_macro:
            await update.effective_message.reply_text(
                f"Execute marco {command}?",
                reply_markup=confirm_keyboard(f"macro:{command}"),
                disable_notification=notifier.silent_commands,
                quote=True,
            )
        else:
            await ws_helper.execute_ws_gcode_script(command)
            await update.effective_message.reply_text(
                f"Running macro: {command}",
                disable_notification=notifier.silent_commands,
                quote=True,
            )
    else:
        await echo_unknown(update, _)


async def upload_file(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None or update.effective_message.get_bot() is None:
        logger.warning("Undefined effective message or bot")
        return

    await update.effective_message.get_bot().send_chat_action(chat_id=configWrap.secrets.chat_id, action=ChatAction.UPLOAD_DOCUMENT)
    doc = update.effective_message.document
    if doc is None or doc.file_name is None:
        await update.effective_message.reply_text(
            f"Document or filename is None in {update.effective_message.to_json()}",
            disable_notification=notifier.silent_commands,
            quote=True,
        )
        return

    if not doc.file_name.endswith((".gcode", ".zip", ".tar.gz", ".tar.bz2", ".tar.xz")):
        await update.effective_message.reply_text(
            f"unknown filetype in {doc.file_name}",
            disable_notification=notifier.silent_commands,
            quote=True,
        )
        return

    try:
        file_byte_array = await (await doc.get_file()).download_as_bytearray()
    except BadRequest as badreq:
        await update.effective_message.reply_text(
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
                await update.effective_message.reply_text(
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
                await update.effective_message.reply_text(
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
            await update.effective_message.reply_text(
                f"Not a gcode file {doc.file_name}",
                disable_notification=notifier.silent_commands,
                quote=True,
            )
        else:
            if await klippy.upload_gcode_file(sending_bio, configWrap.bot_config.upload_path):
                start_pre_mess = "Successfully uploaded file:"
                mess, thumb = await klippy.get_file_info_by_name(
                    f"{configWrap.bot_config.formatted_upload_path}{sending_bio.name}", f"{start_pre_mess}{configWrap.bot_config.formatted_upload_path}{sending_bio.name}"
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
                await update.effective_message.reply_photo(
                    photo=thumb,
                    caption=mess,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    disable_notification=notifier.silent_commands,
                    quote=True,
                    caption_entities=[MessageEntity(type="bold", offset=len(start_pre_mess), length=len(f"{configWrap.bot_config.formatted_upload_path}{sending_bio.name}"))],
                )
                thumb.close()
                # Todo: delete uploaded file
                # bot.delete_message(update.effective_message.chat_id, update.effective_message.message_id)
            else:
                await update.effective_message.reply_text(
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
        "ip": "send private ip of the bot installation",
        "pause": "pause printing",
        "resume": "resume printing",
        "cancel": "cancel printing",
        "files": "list available gcode files",
        "logs": "get klipper, moonraker, bot logs",
        "upload_logs": "upload logs to analyzer",
        "macros": "list all visible macros from klipper",
        "gcode": 'run any gcode command, spaces are supported. "gcode G28 Z"',
        "video": "record and upload a video",
        "power": "toggle moonraker power device from config",
        "light": "toggle light",
        "emergency": "emergency stop printing",
        "bot_restart": "restarts the bot service, useful for config updates",
        "shutdown": "shutdown bot host gracefully",
        "reboot": "reboot bot host gracefully",
    }
    return {c: a for c, a in commands.items() if c not in configWrap.telegram_ui.hidden_bot_commands}


async def help_command(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None:
        logger.warning("Undefined effective message")
        return

    ## Fixme: escape symbols???  from telegram.utils.helpers import escape
    mess = (
        await klippy.get_versions_info(bot_only=True)
        + ("\n".join([f"/{c} - {a}" for c, a in bot_commands().items()]))
        + '\n\nPlease refer to the <a href="https://github.com/nlef/moonraker-telegram-bot/wiki">wiki</a> for additional information'
    )
    await update.effective_message.reply_text(
        text=mess,
        parse_mode=ParseMode.HTML,
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


async def greeting_message(bot: telegram.Bot) -> None:
    if configWrap.secrets.chat_id == 0:
        return

    if configWrap.telegram_ui.send_greeting_message:
        response = await klippy.check_connection()
        mess = ""
        if response:
            mess += f"Bot online, no moonraker connection!\n {response} \nFailing..."
        else:
            mess += "Printer online on " + get_local_ip()
            if configWrap.configuration_errors:
                mess += await klippy.get_versions_info(bot_only=True) + configWrap.configuration_errors

        await bot.send_message(
            configWrap.secrets.chat_id,
            text=mess,
            parse_mode=ParseMode.HTML,
            reply_markup=ReplyKeyboardMarkup(create_keyboard(), resize_keyboard=True),
            disable_notification=notifier.silent_status,
        )

    await bot.set_my_commands(commands=prepare_commands_list(await klippy.get_macros_force(), configWrap.telegram_ui.include_macros_in_command_list))
    await klippy.add_bot_announcements_feed()
    await check_unfinished_lapses(bot)


def get_local_ip():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.255.255.255", 1))
        ip_address = sock.getsockname()[0]
    except:  # pylint: disable=W0702
        ip_address = "127.0.0.1"
    finally:
        sock.close()
    return ip_address


def start_bot(bot_token, socks):
    app_builder = Application.builder()
    (
        app_builder.base_url(configWrap.bot_config.api_url)
        .get_updates_connection_pool_size(4)
        .read_timeout(30)
        .write_timeout(30)
        .get_updates_read_timeout(30)
        .get_updates_write_timeout(30)
        .media_write_timeout(120)
        .token(bot_token)
    )
    if socks:
        app_builder.proxy(f"socks5://{socks}").get_updates_proxy(f"socks5://{socks}")
    application = app_builder.build()

    application.add_handler(MessageHandler(~filters.Chat(configWrap.secrets.chat_id), unknown_chat))

    application.add_handler(CallbackQueryHandler(button_lapse_handler, pattern="lapse:"))
    application.add_handler(CallbackQueryHandler(print_file_dialog_handler, pattern=re.compile("^\\S[^\\:]+\\.gcode$")))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(CommandHandler("help", help_command, block=False))
    application.add_handler(CommandHandler("status", status, block=False))
    application.add_handler(CommandHandler("ip", get_ip))
    application.add_handler(CommandHandler("video", get_video))
    application.add_handler(CommandHandler("pause", pause_printing))
    application.add_handler(CommandHandler("resume", resume_printing))
    application.add_handler(CommandHandler("cancel", cancel_printing))
    application.add_handler(CommandHandler("power", power))
    application.add_handler(CommandHandler("light", light_toggle))
    application.add_handler(CommandHandler("emergency", emergency_stop))
    application.add_handler(CommandHandler("shutdown", shutdown_host))
    application.add_handler(CommandHandler("reboot", reboot_host))
    application.add_handler(CommandHandler("bot_restart", bot_restart))
    application.add_handler(CommandHandler("fw_restart", firmware_restart))
    application.add_handler(CommandHandler("services", services_keyboard))
    application.add_handler(CommandHandler("files", get_gcode_files, block=False))
    application.add_handler(CommandHandler("macros", get_macros, block=False))
    application.add_handler(CommandHandler("gcode", exec_gcode, block=False))
    application.add_handler(CommandHandler("logs", send_logs, block=False))
    application.add_handler(CommandHandler("upload_logs", upload_logs, block=False))

    application.add_handler(MessageHandler(filters.COMMAND, macros_handler, block=False))

    application.add_handler(MessageHandler(filters.Document.ALL & (~filters.COMMAND), upload_file, block=False))

    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), echo_unknown))

    application.add_error_handler(bot_error_handler)

    return application


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

    # Todo: os.chdir(Path(sys.path[0]).parent.absolute())
    os.chdir(sys.path[0])

    configWrap = ConfigWrapper(system_args.configfile)
    configWrap.bot_config.log_path_update(system_args.logfile)
    configWrap.dump_config_to_log()

    rotating_handler = RotatingFileHandler(
        configWrap.bot_config.log_file,
        maxBytes=26214400,
        backupCount=3,
    )
    rotating_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s"))
    logger.addHandler(rotating_handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpx").addHandler(rotating_handler)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpcore").addHandler(rotating_handler)

    if configWrap.parsing_errors or configWrap.unknown_fields:
        logger.error(configWrap.parsing_errors + "\n" + configWrap.unknown_fields)

    if configWrap.bot_config.debug:
        faulthandler.enable()
        logger.setLevel(logging.DEBUG)
        logging.getLogger("apscheduler").addHandler(rotating_handler)
        logging.getLogger("apscheduler").setLevel(logging.DEBUG)

    klippy = Klippy(configWrap, rotating_handler)

    light_power_device = PowerDevice(configWrap.bot_config.light_device_name, klippy)
    psu_power_device = PowerDevice(configWrap.bot_config.poweroff_device_name, klippy)

    klippy.psu_device = psu_power_device
    klippy.light_device = light_power_device

    cameraWrap = (
        MjpegCamera(configWrap, klippy, rotating_handler)
        if configWrap.camera.cam_type == "mjpeg"
        else FFmpegCamera(configWrap, klippy, rotating_handler) if configWrap.camera.cam_type == "ffmpeg" else Camera(configWrap, klippy, rotating_handler)
    )
    bot_updater = start_bot(configWrap.secrets.token, configWrap.bot_config.socks_proxy)
    timelapse = Timelapse(configWrap, klippy, cameraWrap, a_scheduler, bot_updater.bot, rotating_handler)
    notifier = Notifier(configWrap, bot_updater.bot, klippy, cameraWrap, a_scheduler, rotating_handler)

    ws_helper = WebSocketHelper(configWrap, klippy, notifier, timelapse, a_scheduler, rotating_handler)

    a_scheduler.start()

    loop = asyncio.get_event_loop()
    loop.create_task(ws_helper.run_forever_async())

    a_scheduler.add_job(
        greeting_message,
        kwargs={"bot": bot_updater.bot},
    )

    bot_updater.run_polling(allowed_updates=Update.ALL_TYPES)

    logger.info("Shutting down the bot")
