import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from io import BytesIO
import logging
import re
from typing import Dict, List, Optional, Tuple, Union

import aiofiles
import anyio
from apscheduler.schedulers.base import BaseScheduler  # type: ignore[import-untyped]
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaAudio, InputMediaDocument, InputMediaPhoto, InputMediaVideo, Message
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest

from camera import Camera
from configuration import ConfigWrapper
from klippy import Klippy, PrintState
from telegram_helper import TelegramMessageRepr

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(
        self,
        config: ConfigWrapper,
        bot: Bot,
        klippy: Klippy,
        camera_wrapper: Camera,
        scheduler: BaseScheduler,
        logging_handler: logging.Handler,
    ):
        self._bot: Bot = bot
        self._chat_id: int = config.secrets.chat_id
        self._cam_wrap: Camera = camera_wrapper

        self._sched: BaseScheduler = scheduler
        self._executors_pool: ThreadPoolExecutor = ThreadPoolExecutor(2, thread_name_prefix="notifier_pool")
        self._klippy: Klippy = klippy

        self._enabled: bool = config.notifications.enabled
        self._percent: int = config.notifications.percent
        self._height: float = config.notifications.height
        self._interval: int = config.notifications.interval
        self._notify_groups: List[Tuple[int, Optional[int]]] = config.notifications.notify_groups
        self._group_only: bool = config.notifications.group_only
        self._max_upload_file_size: int = config.bot_config.max_upload_file_size

        self._progress_update_message = config.telegram_ui.progress_update_message
        self._silent_progress: bool = config.telegram_ui.silent_progress
        self._silent_commands: bool = config.telegram_ui.silent_commands
        self._silent_status: bool = config.telegram_ui.silent_status
        self._pin_status_single_message: bool = config.telegram_ui.pin_status_single_message
        self._status_message_m117_update: bool = config.telegram_ui.status_message_m117_update
        self._use_status_update_button: bool = config.telegram_ui.status_update_button
        self._message_parts: List[str] = config.status_message_content.content

        self._last_height: float = 0
        self._below_threshold: bool = False
        self._last_percent: int = 0
        self._last_m117_status: str = ""
        self._last_tgnotify_status: str = ""

        self._status_message: Optional[Message] = None
        self._bzz_mess_id: int = 0
        self._groups_status_messages: Dict[int, Message] = {}

        if logging_handler:
            logger.addHandler(logging_handler)
        if config.bot_config.debug:
            logger.setLevel(logging.DEBUG)

    @property
    def silent_commands(self) -> bool:
        return self._silent_commands

    @property
    def silent_status(self) -> bool:
        return self._silent_status

    @property
    def m117_status(self) -> str:
        return self._last_m117_status

    @m117_status.setter
    def m117_status(self, new_value: str) -> None:
        self._last_m117_status = new_value
        if self._klippy.printing and self._status_message_m117_update:
            self._schedule_notification()

    @property
    def tgnotify_status(self) -> str:
        return self._last_tgnotify_status

    @tgnotify_status.setter
    def tgnotify_status(self, new_value: str) -> None:
        self._last_tgnotify_status = new_value
        if self._klippy.printing:
            self._schedule_notification()

    @property
    def percent(self) -> int:
        return self._percent

    @percent.setter
    def percent(self, new_value: int) -> None:
        if new_value >= 0:
            self._percent = new_value

    @property
    def height(self) -> float:
        return self._height

    @height.setter
    def height(self, new_value: float) -> None:
        if new_value >= 0:
            self._height = new_value

    @property
    def interval(self) -> int:
        return self._interval

    @interval.setter
    def interval(self, new_value: int) -> None:
        if new_value == 0:
            self._interval = new_value
            self.remove_notifier_timer()
        elif new_value > 0:
            self._interval = new_value
            self._reschedule_notifier_timer()

    def get_status_keyboard(self, state: PrintState) -> Optional[InlineKeyboardMarkup]:
        inline_keyboard = None
        if self._use_status_update_button and not state.is_finished:
            inline_keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            text="Update",
                            callback_data="updstatus",
                        )
                    ]
                ]
            )
        return inline_keyboard

    async def _send_bzz_message(self, message: TelegramMessageRepr) -> None:
        if self._progress_update_message:
            if self._bzz_mess_id != 0:
                try:
                    await self._bot.delete_message(self._chat_id, self._bzz_mess_id)
                except BadRequest as badreq:
                    logger.warning("Failed deleting bzz message \n%s", badreq)
                    self._bzz_mess_id = 0
            mes = await self._bot.send_message(self._chat_id, text="Status has been updated\nThis message will be deleted", disable_notification=message.is_silent())
            self._bzz_mess_id = mes.message_id

    async def _send_message(self, message: TelegramMessageRepr, group_only: bool = False, manual: bool = False) -> None:
        if not group_only:
            if self._status_message and not manual:
                await message.update_existing(self._status_message)
                await self._send_bzz_message(message)
            else:
                sent_message = await message.send(self._bot, self._chat_id)
                if not self._status_message and not manual:
                    self._status_message = sent_message

        for group, message_thread_id in self._notify_groups:
            await self._bot.send_chat_action(chat_id=group, message_thread_id=message_thread_id, action=ChatAction.TYPING)
            if group in self._groups_status_messages and not manual:
                mess = self._groups_status_messages[group]
                await message.update_existing(mess)
            else:
                sent_message = await message.send(self._bot, group, message_thread_id=message_thread_id)
                if group in self._groups_status_messages or manual:
                    continue
                self._groups_status_messages[group] = sent_message

    async def _send_photo(self, message: TelegramMessageRepr, group_only: bool = False, manual: bool = False) -> None:
        loop = asyncio.get_running_loop()
        with await loop.run_in_executor(self._executors_pool, self._cam_wrap.take_photo) as photo:
            if not group_only:
                if self._status_message and not manual:
                    await message.update_existing(self._status_message, photo=photo)
                    await self._send_bzz_message(message)
                else:
                    sent_message = await message.send(self._bot, self._chat_id, photo=photo)
                    if not self._status_message and not manual:
                        self._status_message = sent_message

            for group, message_thread_id in self._notify_groups:
                photo.seek(0)
                await self._bot.send_chat_action(chat_id=group, message_thread_id=message_thread_id, action=ChatAction.UPLOAD_PHOTO)
                if group in self._groups_status_messages and not manual:
                    mess = self._groups_status_messages[group]
                    await message.update_existing(mess, photo=photo)
                else:
                    sent_message = await message.send(self._bot, group, photo=photo, message_thread_id=message_thread_id)
                    if group in self._groups_status_messages or manual:
                        continue
                    self._groups_status_messages[group] = sent_message

            photo.close()

    async def _notify(self, message: TelegramMessageRepr, group_only: bool = False, manual: bool = False, state: PrintState = PrintState.PRINTING) -> None:
        if state.is_finished:
            await asyncio.sleep(5)
        try:
            if self._cam_wrap.enabled:
                await self._send_photo(message, group_only=group_only, manual=manual)
            else:
                await self._send_message(message, group_only=group_only, manual=manual)
        except Exception as ex:
            logger.exception(ex, stack_info=True)
        finally:
            if state.is_finished:
                await self.reset_notifications()

    # manual notification methods
    def send_error(self, message: str, logs_upload: bool = False, preformat_text: Optional[str] = None) -> None:
        if preformat_text:
            message += f"\n<pre>{preformat_text}</pre>"
        if logs_upload:
            message += "\nUpload logs to analyzer /logs_upload\nSend logs to chat /logs"
        tg_message = TelegramMessageRepr(text=message)
        self._sched.add_job(
            self._send_message,
            kwargs={
                "message": tg_message,
                "manual": True,
            },
            misfire_grace_time=None,
            coalesce=False,
            max_instances=6,
            replace_existing=False,
        )

    def send_error_with_photo(self, message: str) -> None:
        tg_message = TelegramMessageRepr(text=message)
        self._sched.add_job(
            self._notify,
            kwargs={
                "message": tg_message,
                "manual": True,
            },
            misfire_grace_time=None,
            coalesce=False,
            max_instances=6,
            replace_existing=False,
        )

    def send_printer_status_notification(self, message: str) -> None:
        tg_message = TelegramMessageRepr(
            text=message,
            silent=self._silent_status,
        )
        self._sched.add_job(
            self._send_message,
            kwargs={
                "message": tg_message,
                "manual": True,
            },
            misfire_grace_time=None,
            coalesce=False,
            max_instances=6,
            replace_existing=False,
        )

    def send_notification(self, message: str) -> None:
        tg_message = TelegramMessageRepr(
            text=message,
            silent=self._silent_commands,
        )
        self._sched.add_job(
            self._send_message,
            kwargs={
                "message": tg_message,
                "manual": True,
            },
            misfire_grace_time=None,
            coalesce=False,
            max_instances=6,
            replace_existing=False,
        )

    def send_notification_with_photo(self, message: str) -> None:
        tg_message = TelegramMessageRepr(
            text=message,
            silent=self._silent_commands,
        )
        self._sched.add_job(
            self._notify,
            kwargs={
                "message": tg_message,
                "manual": True,
            },
            misfire_grace_time=None,
            coalesce=False,
            max_instances=6,
            replace_existing=False,
        )

    async def reset_notifications(self) -> None:
        self._last_percent = 0
        self._last_height = 0
        self._below_threshold = False
        self._klippy.printing_duration = 0
        self._klippy.printing_height = 0.0
        self._last_m117_status = ""
        self._last_tgnotify_status = ""

        if self._status_message:
            try:
                await self._bot.unpin_chat_message(self._chat_id, self._status_message.message_id)
            except BadRequest as badreq:
                logger.warning("Failed unpining status message \n%s", badreq)

        self._status_message = None
        self._groups_status_messages = {}
        if self._bzz_mess_id != 0:
            try:
                await self._bot.delete_message(self._chat_id, self._bzz_mess_id)
            except BadRequest as badreq:
                logger.warning("Failed deleting bzz message \n%s", badreq)
            finally:
                self._bzz_mess_id = 0

    def _schedule_notification(self, state: PrintState = PrintState.PRINTING) -> None:
        mess = self._klippy.get_print_stats(state=state)
        if self._last_m117_status and "m117_status" in self._message_parts:
            mess += self._last_m117_status + "\n"
        if self._last_tgnotify_status and "tgnotify_status" in self._message_parts:
            mess += self._last_tgnotify_status + "\n"
        if "last_update_time" in self._message_parts:
            mess += f"<i>Last update at {datetime.now():%H:%M:%S}</i>"

        tg_message = TelegramMessageRepr(
            text=mess,
            silent=self._silent_progress,
            reply_markup=self.get_status_keyboard(state=state),
        )

        self._sched.add_job(
            self._notify,
            kwargs={"message": tg_message, "group_only": self._group_only, "state": state},
            misfire_grace_time=180,
            coalesce=False,
            max_instances=6,
            replace_existing=False,
        )

    def schedule_notification(self, progress: int = 0, position_z: float = 0) -> None:
        if not self._klippy.printing or self._klippy.printing_duration <= 0.0 or (self._height == 0 and self._percent == 0):
            return

        notify = False
        if progress != 0 and self._percent != 0:
            if progress < self._last_percent - self._percent:
                self._last_percent = progress
            if progress % self._percent == 0 and progress > self._last_percent:
                self._last_percent = progress
                notify = True

        if position_z != 0 and self._height != 0:
            # Z dropped significantly — reset for sequential objects or print restart
            if position_z < self._last_height - self._height:
                self._last_height = round((position_z // self._height) * self._height, 2)
                self._below_threshold = True
            # Only fire when Z rises through threshold within proximity.
            # This rejects wild Z jumps during start gcode, travel moves, etc.
            next_threshold = self._last_height + self._height
            if position_z < next_threshold:
                self._below_threshold = True
            elif self._below_threshold and position_z < next_threshold + 1.0:
                self._last_height = round((position_z // self._height) * self._height, 2)
                self._below_threshold = False
                logger.info("Height notification at Z=%.2f (threshold %.2f)", position_z, next_threshold)
                notify = True

        if notify:
            self._schedule_notification()

    def _notify_by_time(self) -> None:
        if not self._klippy.printing or self._klippy.printing_duration <= 0.0:
            return
        self._schedule_notification()

    def add_notifier_timer(self) -> None:
        if self._interval > 0:
            # Todo: maybe check if job exists?
            self._sched.add_job(
                self._notify_by_time,
                "interval",
                seconds=self._interval,
                id="notifier_timer",
                replace_existing=True,
            )

    def remove_notifier_timer(self) -> None:
        if self._sched.get_job("notifier_timer"):
            self._sched.remove_job("notifier_timer")

    def _reschedule_notifier_timer(self) -> None:
        if self._interval > 0 and self._sched.get_job("notifier_timer"):
            self._sched.add_job(
                self._notify_by_time,
                "interval",
                seconds=self._interval,
                id="notifier_timer",
                replace_existing=True,
            )

    async def stop_all(self) -> None:
        await self.reset_notifications()
        self.remove_notifier_timer()

    # Todo: refactor with TelegramMessageRepr class
    async def _send_print_start_info(self) -> None:
        message, bio = await self._klippy.get_file_info(state=PrintState.START)

        if not self._group_only:
            status_message = await self._bot.send_photo(
                self._chat_id,
                photo=bio,
                caption=message,
                parse_mode=ParseMode.HTML,
                reply_markup=self.get_status_keyboard(state=PrintState.START),
                disable_notification=self.silent_status,
            )
            self._status_message = status_message

        for group_, message_thread_id in self._notify_groups:
            bio.seek(0)
            self._groups_status_messages[group_] = await self._bot.send_photo(
                chat_id=group_,
                message_thread_id=message_thread_id,
                photo=bio,
                caption=message,
                parse_mode=ParseMode.HTML,
                reply_markup=self.get_status_keyboard(state=PrintState.START),
                disable_notification=self.silent_status,
            )
        bio.close()

        if self._pin_status_single_message and self._status_message is not None:
            await self._bot.unpin_all_chat_messages(self._chat_id)
            await self._bot.pin_chat_message(self._chat_id, self._status_message.message_id, disable_notification=self.silent_status)

    def send_print_start_info(self) -> None:
        if self._enabled:
            self._sched.add_job(
                self._send_print_start_info,
                misfire_grace_time=None,
                coalesce=False,
                max_instances=1,
                replace_existing=True,
            )
        # Todo: reset something? or check if reset by setting new filename?

    async def _send_print_finish(self) -> None:
        self._schedule_notification(state=PrintState.FINISH)

    def send_print_finish(self) -> None:
        if self._enabled:
            self._sched.add_job(
                self._send_print_finish,
                misfire_grace_time=None,
                coalesce=False,
                max_instances=1,
                replace_existing=True,
            )

    async def _update_status_on_abort(self, state: PrintState) -> None:
        self._schedule_notification(state=state)

    def update_status_on_abort(self, state: PrintState) -> None:
        if self._enabled:
            self._sched.add_job(
                self._update_status_on_abort,
                kwargs={"state": state},
                misfire_grace_time=None,
                coalesce=False,
                max_instances=1,
                replace_existing=True,
            )

    def update_status(self) -> None:
        self._schedule_notification()

    @staticmethod
    def _parse_message(ws_message: str) -> str:
        message_match = re.search(r"message\s*=\s*\'(.[^\']*)\'", ws_message)
        return message_match.group(1) if message_match else ""

    @staticmethod
    def _parse_path(ws_message: str) -> List[str]:
        path_match = re.search(r"path\s*=\s*\'(.[^\']*)\'", ws_message)
        path_list_math = re.search(r"path\s*=\s*\[(?:\,*\s*\'(.[^\']*)\'\,*\s*)+\]", ws_message)

        if path_match:
            path = [path_match.group(1)]
        elif path_list_math:
            path = [el.group(1) for el in re.finditer(r"(?:\,*\s*\'(.[^\']*)\'\,*\s*)", path_list_math.group(0))]
        else:
            path = [""]
        return path

    async def _send_image(self, paths: List[str], message: str) -> None:
        try:
            photos_list: List[Union[InputMediaAudio, InputMediaDocument, InputMediaPhoto, InputMediaVideo]] = []
            for path in paths:
                path_obj = anyio.Path(path)
                if not await path_obj.is_file():
                    await self._bot.send_message(self._chat_id, text="Provided path is not a file", disable_notification=self._silent_commands)
                    return

                bio = BytesIO()
                bio.name = path_obj.name

                async with aiofiles.open(path_obj, "rb") as fh:
                    bio.write(await fh.read())
                bio.seek(0)
                if bio.getbuffer().nbytes > 10485760:
                    await self._bot.send_message(self._chat_id, text=f"Telegram bots have a 10mb filesize restriction for images, image couldn't be uploaded: `{path}`")
                else:
                    if not photos_list:
                        photos_list.append(InputMediaPhoto(bio, filename=bio.name, caption=message))
                    else:
                        photos_list.append(InputMediaPhoto(bio, filename=bio.name))
                bio.close()

            await self._bot.send_media_group(
                self._chat_id,
                media=photos_list,
                disable_notification=self._silent_commands,
            )

        except Exception as ex:
            logger.warning(ex)
            await self._bot.send_message(self._chat_id, text=f"Error sending image: {ex}", disable_notification=self._silent_commands)

    def send_image(self, ws_message: str) -> None:
        self._sched.add_job(
            self._send_image,
            kwargs={"paths": self._parse_path(ws_message), "message": self._parse_message(ws_message)},
            misfire_grace_time=None,
            coalesce=False,
            max_instances=6,
            replace_existing=False,
        )

    async def _send_video(self, paths: List[str], message: str) -> None:
        try:
            photos_list: List[Union[InputMediaAudio, InputMediaDocument, InputMediaPhoto, InputMediaVideo]] = []
            for path in paths:
                path_obj = anyio.Path(path)
                if not await path_obj.is_file():
                    await self._bot.send_message(self._chat_id, text="Provided path is not a file", disable_notification=self._silent_commands)
                    return

                bio = BytesIO()
                bio.name = path_obj.name

                async with aiofiles.open(path_obj, "rb") as fh:
                    bio.write(await fh.read())
                bio.seek(0)
                if bio.getbuffer().nbytes > self._max_upload_file_size * 1024 * 1024:
                    await self._bot.send_message(self._chat_id, text=f"Telegram bots have a {self._max_upload_file_size}mb filesize restriction, video couldn't be uploaded: `{path}`")
                else:
                    if not photos_list:
                        photos_list.append(InputMediaVideo(bio, filename=bio.name, caption=message))
                    else:
                        photos_list.append(InputMediaVideo(bio, filename=bio.name))
                bio.close()

            await self._bot.send_media_group(
                self._chat_id,
                media=photos_list,
                disable_notification=self._silent_commands,
                write_timeout=120,
            )

        except Exception as ex:
            logger.warning(ex)
            await self._bot.send_message(self._chat_id, text=f"Error sending video: {ex}", disable_notification=self._silent_commands)

    def send_video(self, ws_message: str) -> None:
        self._sched.add_job(
            self._send_video,
            kwargs={"paths": self._parse_path(ws_message), "message": self._parse_message(ws_message)},
            misfire_grace_time=None,
            coalesce=False,
            max_instances=6,
            replace_existing=False,
        )

    async def _send_document(self, paths: List[str], message: str) -> None:
        try:
            photos_list: List[Union[InputMediaAudio, InputMediaDocument, InputMediaPhoto, InputMediaVideo]] = []
            for path in paths:
                path_obj = anyio.Path(path)
                if not await path_obj.is_file():
                    await self._bot.send_message(self._chat_id, text="Provided path is not a file", disable_notification=self._silent_commands)
                    return

                bio = BytesIO()
                bio.name = path_obj.name

                async with aiofiles.open(path_obj, "rb") as fh:
                    bio.write(await fh.read())
                bio.seek(0)
                if bio.getbuffer().nbytes > self._max_upload_file_size * 1024 * 1024:
                    await self._bot.send_message(self._chat_id, text=f"Telegram bots have a {self._max_upload_file_size}mb filesize restriction, document couldn't be uploaded: `{path}`")
                else:
                    if not photos_list:
                        photos_list.append(InputMediaDocument(bio, filename=bio.name, caption=message))
                    else:
                        photos_list.append(InputMediaDocument(bio, filename=bio.name))
                bio.close()

            await self._bot.send_media_group(
                self._chat_id,
                media=photos_list,
                disable_notification=self._silent_commands,
            )

        except Exception as ex:
            logger.warning(ex)
            await self._bot.send_message(self._chat_id, text=f"Error sending document: {ex}", disable_notification=self._silent_commands)

    def send_document(self, ws_message: str) -> None:
        self._sched.add_job(
            self._send_document,
            kwargs={"paths": self._parse_path(ws_message), "message": self._parse_message(ws_message)},
            misfire_grace_time=None,
            coalesce=False,
            max_instances=6,
            replace_existing=False,
        )

    async def parse_notification_params(self, message: str) -> None:
        mass_parts = message.split(sep=" ")
        mass_parts.pop(0)
        response = ""
        for part in mass_parts:
            try:
                if part.startswith("percent="):
                    self.percent = int(part.split(sep="=").pop())
                    response += f"percent={self.percent} "
                elif part.startswith("height="):
                    self.height = float(part.split(sep="=").pop())
                    response += f"height={self.height} "
                elif part.startswith("time="):
                    self.interval = int(part.split(sep="=").pop())
                    response += f"time={self.interval} "
                else:
                    await self._klippy.execute_gcode_script(f'RESPOND PREFIX="Notification params error" MSG="unknown param `{part}`"')
            except Exception as ex:
                await self._klippy.execute_gcode_script(f'RESPOND PREFIX="Notification params error" MSG="Failed parsing `{part}`. {ex}"')
        if response:
            full_conf = f"percent={self.percent} height={self.height} time={self.interval} "
            await self._klippy.execute_gcode_script(f'RESPOND PREFIX="Notification params" MSG="Changed Notification params: {response}"')
            await self._klippy.execute_gcode_script(f'RESPOND PREFIX="Notification params" MSG="Full Notification config: {full_conf}"')

    async def send_custom_inline_keyboard(self, message: str) -> None:
        def parse_button(mess: str) -> Optional[InlineKeyboardButton]:
            name = re.search(r"name\s*=\s*\'(.[^\']*)\'", mess)
            command = re.search(r"command\s*=\s*\'(.[^\']*)\'", mess)
            if name and command:
                gcode = "do_nothing" if command.group(1) == "delete" else f"gcode:{command.group(1)}"
                return InlineKeyboardButton(name.group(1), callback_data=gcode)
            else:
                logger.warning("Bad command!")
                return None

        keyboard: List[List[InlineKeyboardButton]] = list(  # noqa: C417
            map(
                lambda el: list(
                    filter(
                        None,
                        map(
                            parse_button,
                            re.findall(r"\{.[^\}]*\}", el),
                        ),
                    )
                ),
                re.findall(r"\[.[^\]]*\]", message),
            )
        )

        title_mathc = re.search(r"message\s*=\s*\'(.[^\']*)\'", message)
        title = title_mathc.group(1) if title_mathc else ""

        await self._bot.send_message(
            self._chat_id,
            text=title,
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_notification=self._silent_commands,
        )
