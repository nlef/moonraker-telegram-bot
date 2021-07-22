import logging
import time

from telegram import ChatAction
from telegram.ext import Updater, CallbackContext

from camera import Camera
from klippy import Klippy

logger = logging.getLogger(__name__)


def send_message(context: CallbackContext):
    (mess, chatId, notify_groups, silent) = context.job.context
    context.bot.send_chat_action(chat_id=chatId, action=ChatAction.TYPING)
    context.bot.send_message(chatId, text=mess, disable_notification=silent)
    for group in notify_groups:
        context.bot.send_chat_action(chat_id=group, action=ChatAction.TYPING)
        context.bot.send_message(group, text=mess, disable_notification=silent)


class Notifier:
    def __init__(self, bot_updater: Updater, chat_id: int, klippy: Klippy, camera_wrapper: Camera, percent: int = 5, height: int = 5, interval: int = 0,
                 notify_groups: list = None, debug_logging: bool = False, silent_progress: bool = False, silent_commands: bool = False, silent_status: bool = False, ):
        self._bot_updater: Updater = bot_updater
        self._chatId: int = chat_id
        self._cam_wrap: Camera = camera_wrapper
        self._percent: int = percent
        self._height: int = height
        self._interval: int = interval
        self.notify_groups: list = notify_groups if notify_groups else list()

        self.silent_progress = silent_progress
        self.silent_commands = silent_commands
        self.silent_status = silent_status

        self._last_height: int = 0
        self._last_percent: int = 0
        self._last_message: str = ''
        self._last_notify_time: int = 0
        self._klippy: Klippy = klippy
        if debug_logging:
            logger.setLevel(logging.DEBUG)

    @property
    def message(self):
        return self._last_message

    @message.setter
    def message(self, new: str):
        self._last_message = new

    def notify(self, progress: int = 0, position_z: int = 0):
        def send_notification(context: CallbackContext):
            if self._cam_wrap.enabled:
                (mess, chatId, notify_groups, silent) = context.job.context
                photo = self._cam_wrap.take_photo()
                context.bot.send_chat_action(chat_id=chatId, action=ChatAction.UPLOAD_PHOTO)
                context.bot.send_photo(chatId, photo=photo, caption=mess, disable_notification=silent)
                for group_ in notify_groups:
                    context.bot.send_chat_action(chat_id=group_, action=ChatAction.UPLOAD_PHOTO)
                    context.bot.send_photo(group_, photo=photo, caption=mess, disable_notification=silent)
                photo.close()
            else:
                send_message(context)

        if not self._klippy.printing or not self._klippy.printing_duration > 0.0 or (self._height == 0 + self._percent == 0) or (
                time.time() < self._last_notify_time + self._interval):
            return

        notifymsg = ''
        if progress != 0 and self._percent != 0:
            if progress < self._last_percent - self._percent:
                self._last_percent = progress
            if progress % self._percent == 0 and progress > self._last_percent:
                notifymsg = f"Printed {progress}%"
                if self._last_message:
                    notifymsg += f"\n{self._last_message}"
                if self._klippy.printing_duration > 0:
                    notifymsg += f"\n{self._klippy.get_eta_message()}"
                self._last_percent = progress

        if position_z != 0 and self._height != 0:
            if position_z < self._last_height - self._height:
                self._last_height = position_z
            if position_z % self._height == 0 and position_z > self._last_height:
                notifymsg = f"Printed {position_z}mm"
                if self._last_message:
                    notifymsg += f"\n{self._last_message}"
                self._last_height = position_z

        if notifymsg:
            self._last_notify_time = time.time()
            self._bot_updater.job_queue.run_once(send_notification, 0, context=(notifymsg, self._chatId, self.notify_groups, self.silent_progress))

    def send_error(self, message: str):
        self._bot_updater.job_queue.run_once(send_message, 0, context=(message, self._chatId, self.notify_groups, False))

    def send_notification(self, message: str):
        self._bot_updater.job_queue.run_once(send_message, 0, context=(message, self._chatId, self.notify_groups, self.silent_status))

    def reset_notifications(self) -> None:
        self._last_percent = 0
        self._last_height = 0
        self._klippy.printing_duration = 0
