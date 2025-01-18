import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from io import BytesIO
import logging
from pathlib import Path
import re
from typing import Dict, List, Optional, Union

from apscheduler.schedulers.base import BaseScheduler  # type: ignore
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaAudio, InputMediaDocument, InputMediaPhoto, InputMediaVideo, Message
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.helpers import escape_markdown

from camera import Camera
from configuration import ConfigWrapper
from klippy import Klippy

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
        self._notify_groups: List[int] = config.notifications.notify_groups
        self._group_only: bool = config.notifications.group_only
        self._max_upload_file_size: int = config.bot_config.max_upload_file_size

        self._progress_update_message = config.telegram_ui.progress_update_message
        self._silent_progress: bool = config.telegram_ui.silent_progress
        self._silent_commands: bool = config.telegram_ui.silent_commands
        self._silent_status: bool = config.telegram_ui.silent_status
        self._pin_status_single_message: bool = config.telegram_ui.pin_status_single_message
        self._status_message_m117_update: bool = config.telegram_ui.status_message_m117_update
        self._message_parts: List[str] = config.status_message_content.content

        self._last_height: int = 0
        self._last_percent: int = 0
        self._last_m117_status: str = ""
        self._last_tgnotify_status: str = ""

        self._status_message: Optional[Message] = None
        self._bzz_mess_id: int = 0
        self._groups_status_mesages: Dict[int, Message] = {}

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
    def m117_status(self, new_value: str):
        self._last_m117_status = new_value
        if self._klippy.printing and self._status_message_m117_update:
            self._schedule_notification()

    @property
    def tgnotify_status(self) -> str:
        return self._last_tgnotify_status

    @tgnotify_status.setter
    def tgnotify_status(self, new_value: str):
        self._last_tgnotify_status = new_value
        if self._klippy.printing:
            self._schedule_notification()

    @property
    def percent(self) -> int:
        return self._percent

    @percent.setter
    def percent(self, new_value: int):
        if new_value >= 0:
            self._percent = new_value

    @property
    def height(self) -> float:
        return self._height

    @height.setter
    def height(self, new_value: float):
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

    async def _send_message(self, message: str, silent: bool, group_only: bool = False, manual: bool = False) -> None:
        if not group_only:
            await self._bot.send_chat_action(chat_id=self._chat_id, action=ChatAction.TYPING)
            if self._status_message and not manual:
                if self._bzz_mess_id != 0:
                    try:
                        await self._bot.delete_message(self._chat_id, self._bzz_mess_id)
                    except BadRequest as badreq:
                        logger.warning("Failed deleting bzz message \n%s", badreq)
                        self._bzz_mess_id = 0

                if self._status_message.caption:
                    await self._status_message.edit_caption(caption=message, parse_mode=ParseMode.MARKDOWN_V2)
                else:
                    await self._status_message.edit_text(text=message, parse_mode=ParseMode.MARKDOWN_V2)

                if self._progress_update_message:
                    mes = await self._bot.send_message(self._chat_id, text="Status has been updated\nThis message will be deleted", disable_notification=silent)
                    self._bzz_mess_id = mes.message_id
            else:
                sent_message = await self._bot.send_message(
                    self._chat_id,
                    text=message,
                    parse_mode=ParseMode.MARKDOWN_V2,
                    disable_notification=silent,
                )
                if not self._status_message and not manual:
                    self._status_message = sent_message

        for group in self._notify_groups:
            await self._bot.send_chat_action(chat_id=group, action=ChatAction.TYPING)
            if group in self._groups_status_mesages and not manual:
                mess = self._groups_status_mesages[group]
                if mess.caption:
                    await mess.edit_caption(caption=message, parse_mode=ParseMode.MARKDOWN_V2)
                else:
                    await mess.edit_text(text=message, parse_mode=ParseMode.MARKDOWN_V2)
            else:
                sent_message = await self._bot.send_message(
                    group,
                    text=message,
                    parse_mode=ParseMode.MARKDOWN_V2,
                    disable_notification=silent,
                )
                if group in self._groups_status_mesages or manual:
                    continue
                self._groups_status_mesages[group] = sent_message

    async def _send_photo(self, group_only, manual, message, silent):
        loop = asyncio.get_running_loop()
        with await loop.run_in_executor(self._executors_pool, self._cam_wrap.take_photo) as photo:
            if not group_only:
                await self._bot.send_chat_action(chat_id=self._chat_id, action=ChatAction.UPLOAD_PHOTO)
                if self._status_message and not manual:
                    if self._bzz_mess_id != 0:
                        try:
                            await self._bot.delete_message(self._chat_id, self._bzz_mess_id)
                        except BadRequest as badreq:
                            logger.warning("Failed deleting bzz message \n%s", badreq)
                            self._bzz_mess_id = 0

                    # Fixme: check if media in message!
                    await self._status_message.edit_media(media=InputMediaPhoto(photo))
                    await self._status_message.edit_caption(caption=message, parse_mode=ParseMode.MARKDOWN_V2)

                    if self._progress_update_message:
                        mes = await self._bot.send_message(self._chat_id, text="Status has been updated\nThis message will be deleted", disable_notification=silent)
                        self._bzz_mess_id = mes.message_id

                else:
                    sent_message = await self._bot.send_photo(
                        self._chat_id,
                        photo=photo,
                        caption=message,
                        parse_mode=ParseMode.MARKDOWN_V2,
                        disable_notification=silent,
                    )
                    if not self._status_message and not manual:
                        self._status_message = sent_message

            for group in self._notify_groups:
                photo.seek(0)
                await self._bot.send_chat_action(chat_id=group, action=ChatAction.UPLOAD_PHOTO)
                if group in self._groups_status_mesages and not manual:
                    mess = self._groups_status_mesages[group]
                    await mess.edit_media(media=InputMediaPhoto(photo))
                    await mess.edit_caption(caption=message, parse_mode=ParseMode.MARKDOWN_V2)
                else:
                    sent_message = await self._bot.send_photo(
                        group,
                        photo=photo,
                        caption=message,
                        parse_mode=ParseMode.MARKDOWN_V2,
                        disable_notification=silent,
                    )
                    if group in self._groups_status_mesages or manual:
                        continue
                    self._groups_status_mesages[group] = sent_message

            photo.close()

    async def _notify(self, message: str, silent: bool, group_only: bool = False, manual: bool = False, finish: bool = False) -> None:
        try:
            if not self._cam_wrap.enabled:
                await self._send_message(message, silent, manual)
            else:
                await self._send_photo(group_only, manual, message, silent)
        except Exception as ex:
            logger.error(ex)
        finally:
            if finish:
                await self.reset_notifications()

    # manual notification methods
    def send_error(self, message: str, logs_upload: bool = False) -> None:
        if logs_upload:
            message += "\nUpload logs to analyzer /upload_logs\nSend logs to chat /logs"
        self._sched.add_job(
            self._send_message,
            kwargs={
                "message": escape_markdown(message, version=2),
                "silent": False,
                "manual": True,
            },
            misfire_grace_time=None,
            coalesce=False,
            max_instances=6,
            replace_existing=False,
        )

    def send_error_with_photo(self, message: str) -> None:
        self._sched.add_job(
            self._notify,
            kwargs={
                "message": escape_markdown(message, version=2),
                "silent": False,
                "manual": True,
            },
            misfire_grace_time=None,
            coalesce=False,
            max_instances=6,
            replace_existing=False,
        )

    def send_printer_status_notification(self, message: str) -> None:
        self._sched.add_job(
            self._send_message,
            kwargs={
                "message": escape_markdown(message, version=2),
                "silent": self._silent_status,
                "manual": True,
            },
            misfire_grace_time=None,
            coalesce=False,
            max_instances=6,
            replace_existing=False,
        )

    def send_notification(self, message: str) -> None:
        self._sched.add_job(
            self._send_message,
            kwargs={
                "message": escape_markdown(message, version=2),
                "silent": self._silent_commands,
                "manual": True,
            },
            misfire_grace_time=None,
            coalesce=False,
            max_instances=6,
            replace_existing=False,
        )

    def send_notification_with_photo(self, message: str) -> None:
        self._sched.add_job(
            self._notify,
            kwargs={
                "message": escape_markdown(message, version=2),
                "silent": self._silent_commands,
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
        self._klippy.printing_duration = 0
        self._last_m117_status = ""
        self._last_tgnotify_status = ""
        self._status_message = None
        self._groups_status_mesages = {}
        if self._bzz_mess_id != 0:
            try:
                await self._bot.delete_message(self._chat_id, self._bzz_mess_id)
            except BadRequest as badreq:
                logger.warning("Failed deleting bzz message \n%s", badreq)
            finally:
                self._bzz_mess_id = 0

    def _schedule_notification(self, message: str = "", schedule: bool = False, finish: bool = False) -> None:  # pylint: disable=W0613
        mess = escape_markdown(self._klippy.get_print_stats(message), version=2)
        if self._last_m117_status and "m117_status" in self._message_parts:
            mess += f"{escape_markdown(self._last_m117_status, version=2)}\n"
        if self._last_tgnotify_status and "tgnotify_status" in self._message_parts:
            mess += f"{escape_markdown(self._last_tgnotify_status, version=2)}\n"
        if "last_update_time" in self._message_parts:
            mess += f"_Last update at {datetime.now():%H:%M:%S}_"

        self._sched.add_job(
            self._notify,
            kwargs={"message": mess, "silent": self._silent_progress, "group_only": self._group_only, "finish": finish},
            misfire_grace_time=180,
            coalesce=False,
            max_instances=6,
            replace_existing=False,
        )

        # if schedule:
        #     self._sched.add_job(
        #         self._notify,
        #         kwargs={
        #             "message": mess,
        #             "silent": self._silent_progress,
        #             "group_only": self._group_only,
        #         },
        #         misfire_grace_time=None,
        #         coalesce=False,
        #         max_instances=6,
        #         replace_existing=False,
        #     )
        # else:
        #     self._notify(mess, self._silent_progress, self._group_only)

    def schedule_notification(self, progress: int = 0, position_z: int = 0) -> None:
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
            if position_z < self._last_height - self._height:
                self._last_height = position_z
            if position_z % self._height == 0 and position_z > self._last_height:
                self._last_height = position_z
                notify = True

        if notify:
            self._schedule_notification(schedule=True)

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

    async def _send_print_start_info(self) -> None:
        message, bio = await self._klippy.get_file_info("Printer started printing")
        if bio is not None:
            status_message = await self._bot.send_photo(
                self._chat_id,
                photo=bio,
                caption=message,
                disable_notification=self.silent_status,
            )
            for group_ in self._notify_groups:
                bio.seek(0)
                self._groups_status_mesages[group_] = await self._bot.send_photo(
                    group_,
                    photo=bio,
                    caption=message,
                    disable_notification=self.silent_status,
                )
            bio.close()
        else:
            status_message = await self._bot.send_message(self._chat_id, message, disable_notification=self.silent_status)
            for group_ in self._notify_groups:
                self._groups_status_mesages[group_] = await self._bot.send_message(group_, message, disable_notification=self.silent_status)
        self._status_message = status_message

        if self._pin_status_single_message:
            await self._bot.unpin_all_chat_messages(self._chat_id)
            await self._bot.pin_chat_message(self._chat_id, status_message.message_id, disable_notification=self.silent_status)

    def send_print_start_info(self) -> None:
        if self._enabled:
            self._sched.add_job(
                self._send_print_start_info,
                misfire_grace_time=None,
                coalesce=False,
                max_instances=1,
                replace_existing=True,
            )
        # Todo: reset something? or check if reseted by setting new filename?

    async def _send_print_finish(self) -> None:
        self._schedule_notification(message="Finished printing", finish=True)

    def send_print_finish(self) -> None:
        if self._enabled:
            self._sched.add_job(
                self._send_print_finish,
                misfire_grace_time=None,
                coalesce=False,
                max_instances=1,
                replace_existing=True,
            )

    def update_status(self) -> None:
        self._schedule_notification()

    @staticmethod
    def _parse_message(ws_message) -> str:
        message_match = re.search(r"message\s*=\s*\'(.[^\']*)\'", ws_message)
        if message_match:
            message = message_match.group(1)
        else:
            message = ""
        return message

    @staticmethod
    def _parse_path(ws_message) -> List[str]:
        path_match = re.search(r"path\s*=\s*\'(.[^\']*)\'", ws_message)
        path_list_math = re.search(r"path\s*=\s*\[(?:\,*\s*\'(.[^\']*)\'\,*\s*)+\]", ws_message)

        if path_match:
            path = [path_match.group(1)]
        elif path_list_math:
            path = list(map(lambda el: el.group(1), re.finditer(r"(?:\,*\s*\'(.[^\']*)\'\,*\s*)", path_list_math.group(0))))
        else:
            path = [""]
        return path

    async def _send_image(self, paths: List[str], message: str) -> None:
        try:
            photos_list: List[Union[InputMediaAudio, InputMediaDocument, InputMediaPhoto, InputMediaVideo]] = []
            for path in paths:
                path_obj = Path(path)
                if not path_obj.is_file():
                    await self._bot.send_message(self._chat_id, text="Provided path is not a file", disable_notification=self._silent_commands)
                    return

                bio = BytesIO()
                bio.name = path_obj.name

                with open(path_obj, "rb") as fh:
                    bio.write(fh.read())
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
                path_obj = Path(path)
                if not path_obj.is_file():
                    await self._bot.send_message(self._chat_id, text="Provided path is not a file", disable_notification=self._silent_commands)
                    return

                bio = BytesIO()
                bio.name = path_obj.name

                with open(path_obj, "rb") as fh:
                    bio.write(fh.read())
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
                path_obj = Path(path)
                if not path_obj.is_file():
                    await self._bot.send_message(self._chat_id, text="Provided path is not a file", disable_notification=self._silent_commands)
                    return

                bio = BytesIO()
                bio.name = path_obj.name

                with open(path_obj, "rb") as fh:
                    bio.write(fh.read())
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

    async def send_custom_inline_keyboard(self, message: str):
        def parse_button(mess: str):
            name = re.search(r"name\s*=\s*\'(.[^\']*)\'", mess)
            command = re.search(r"command\s*=\s*\'(.[^\']*)\'", mess)
            if name and command:
                gcode = "do_nothing" if command.group(1) == "delete" else f"gcode:{command.group(1)}"
                return InlineKeyboardButton(name.group(1), callback_data=gcode)
            else:
                logger.warning("Bad command!")
                return None

        keyboard: List[List[InlineKeyboardButton]] = list(
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
        if title_mathc:
            title = title_mathc.group(1)
        else:
            title = ""

        await self._bot.send_message(
            self._chat_id,
            text=title,
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_notification=self._silent_commands,
        )
