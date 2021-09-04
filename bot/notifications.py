import logging
import time
from datetime import timedelta

from apscheduler.schedulers.base import BaseScheduler
from telegram import ChatAction
from telegram.ext import Updater, CallbackContext

from camera import Camera
from klippy import Klippy

logger = logging.getLogger(__name__)


def send_message(context: CallbackContext):
    (mess, chat_id, notify_groups, silent) = context.job.context
    context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    context.bot.send_message(chat_id, text=mess, disable_notification=silent)
    for group in notify_groups:
        context.bot.send_chat_action(chat_id=group, action=ChatAction.TYPING)
        context.bot.send_message(group, text=mess, disable_notification=silent)


def send_message_with_photo(context: CallbackContext):
    (mess, pht, chat_id, notify_groups, silent) = context.job.context
    context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)
    context.bot.send_photo(chat_id, photo=pht, caption=mess, disable_notification=silent)
    for group_ in notify_groups:
        context.bot.send_chat_action(chat_id=group_, action=ChatAction.UPLOAD_PHOTO)
        context.bot.send_photo(group_, photo=pht, caption=mess, disable_notification=silent)


class Notifier:
    def __init__(self, bot_updater: Updater, chat_id: int, klippy: Klippy, camera_wrapper: Camera, scheduler: BaseScheduler, percent: int = 5, height: int = 5, interval: int = 0,
                 interval_between: int = 0, notify_groups: list = None, debug_logging: bool = False, silent_progress: bool = False, silent_commands: bool = False, silent_status: bool = False, ):
        self._bot_updater: Updater = bot_updater
        self._chatId: int = chat_id
        self._cam_wrap: Camera = camera_wrapper
        self._sched = scheduler
        self._percent: int = percent
        self._height: int = height
        self.interval: int = interval
        self._interval_between: int = interval_between
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

    def notify(self, message: str):
        if self._cam_wrap.enabled:
            photo = self._cam_wrap.take_photo()
            self._bot_updater.job_queue.run_once(send_message_with_photo, 0, context=(message, photo, self._chatId, self.notify_groups, self.silent_progress))
        else:
            self._bot_updater.job_queue.run_once(send_message, 0, context=(message, self._chatId, self.notify_groups, self.silent_progress))

    def send_error(self, message: str):
        self._bot_updater.job_queue.run_once(send_message, 0, context=(message, self._chatId, self.notify_groups, False))

    def send_notification(self, message: str):
        self._bot_updater.job_queue.run_once(send_message, 0, context=(message, self._chatId, self.notify_groups, self.silent_status))

    def send_notification_with_photo(self, message: str):
        self._sched.add_job(self.notify, kwargs={'message': message}, misfire_grace_time=None, coalesce=False, max_instances=6, replace_existing=False)

    def reset_notifications(self) -> None:
        self._last_percent = 0
        self._last_height = 0
        self._klippy.printing_duration = 0

    def schedule_notification(self, progress: int = 0, position_z: int = 0):
        if not self._klippy.printing or self._klippy.printing_duration <= 0.0 or (self._height == 0 and self._percent == 0):
            return

        if self._interval_between > 0 and time.time() < self._last_notify_time + self._interval_between:
            return

        notifymsg = ''
        if progress != 0 and self._percent != 0:
            if progress < self._last_percent - self._percent:
                self._last_percent = progress
            if progress % self._percent == 0 and progress > self._last_percent:
                notifymsg = f"Printed {progress}%\n"
                self._last_percent = progress

        if position_z != 0 and self._height != 0:
            if position_z < self._last_height - self._height:
                self._last_height = position_z
            if position_z % self._height == 0 and position_z > self._last_height:
                notifymsg = f"Printed {position_z}mm\n"
                self._last_height = position_z

        if notifymsg:
            if self._last_message:
                notifymsg += f"{self._last_message}\n"
            notifymsg += f"{self._klippy.get_eta_message()}"

            self._last_notify_time = time.time()
            self._sched.add_job(self.notify, kwargs={'message': notifymsg}, misfire_grace_time=None, coalesce=False, max_instances=6, replace_existing=False)

    def notify_by_time(self):
        # Fixme: do we need last notify time check???
        if not self._klippy.printing or self._klippy.printing_duration <= 0.0 or (self._interval_between > 0 and time.time() < self._last_notify_time + self._interval_between):
            return

        notifymsg = f"Printing for {timedelta(seconds=round(self._klippy.printing_duration))}\n"
        if self._last_message:
            notifymsg += f"{self._last_message}\n"
        notifymsg += f"{self._klippy.get_eta_message()}"
        self.notify(notifymsg)

    def add_notifier_timer(self):
        if self.interval > 0:
            self._sched.add_job(self.notify_by_time, 'interval', seconds=self.interval, id='notifier_timer')

    def remove_notifier_timer(self):
        if self._sched.get_job('notifier_timer'):
            self._sched.remove_job('notifier_timer')
