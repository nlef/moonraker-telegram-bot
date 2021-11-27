import configparser
import logging
import time
from concurrent.futures import ThreadPoolExecutor

from apscheduler.schedulers.base import BaseScheduler
from telegram import ChatAction, Message
from telegram.ext import Updater

from camera import Camera
from klippy import Klippy

logger = logging.getLogger(__name__)


class Timelapse:
    def __init__(self, config: configparser.ConfigParser, klippy: Klippy, camera: Camera, scheduler: BaseScheduler, bot_updater: Updater, chat_id: int, logging_handler: logging.Handler = None,
                 debug_logging: bool = False):
        self._enabled: bool = 'timelapse' in config and camera.enabled
        self._mode_manual: bool = config.getboolean('timelapse', 'manual_mode', fallback=False)
        self._height: float = config.getfloat('timelapse', 'height', fallback=0.0)
        self._interval: int = config.getint('timelapse', 'time', fallback=0)
        self._target_fps: int = config.getint('timelapse', 'target_fps', fallback=15)
        self._min_lapse_duration: int = config.getint('timelapse', 'min_lapse_duration', fallback=0)
        self._max_lapse_duration: int = config.getint('timelapse', 'max_lapse_duration', fallback=0)
        self._last_frame_duration: int = config.getint('timelapse', 'last_frame_duration', fallback=5)

        self._after_lapse_gcode: str = config.get('timelapse', 'after_lapse_gcode', fallback='')
        self._after_lapse_send_video: bool = config.getboolean('timelapse', 'after_lapse_send_video', fallback=True)

        # Todo: use notifier?
        self._silent_progress = config.getboolean('telegram_ui', 'silent_progress', fallback=True)

        self._klippy = klippy
        self._camera = camera

        # push params to cameras instances
        self._camera.target_fps = self._target_fps
        self._camera.min_lapse_duration = self._min_lapse_duration
        self._camera.max_lapse_duration = self._max_lapse_duration
        self._camera.last_frame_duration = self._last_frame_duration

        self._sched = scheduler
        self._chat_id: int = chat_id
        self._bot_updater: Updater = bot_updater

        self._running: bool = False
        self._paused: bool = False
        self._last_height: float = 0.0

        self._executors_pool: ThreadPoolExecutor = ThreadPoolExecutor(2)

        if logging_handler:
            logger.addHandler(logging_handler)
        if debug_logging:
            logger.setLevel(logging.DEBUG)

    @property
    def enabled(self):
        return self._enabled

    @enabled.setter
    def enabled(self, new_value: bool):
        self._enabled = new_value

    @property
    def manual_mode(self):
        return self._mode_manual

    @manual_mode.setter
    def manual_mode(self, new_value: bool):
        self._mode_manual = new_value

    @property
    def interval(self):
        return self._interval

    @interval.setter
    def interval(self, new_value: int):
        if new_value == 0:
            self._interval = new_value
            self._remove_timelapse_timer()
        elif new_value > 0:
            self._interval = new_value
            self._reschedule_timelapse_timer()

    @property
    def height(self):
        return self._height

    @height.setter
    def height(self, new_value):
        if new_value >= 0:
            self._height = new_value

    @property
    def target_fps(self):
        return self._target_fps

    @target_fps.setter
    def target_fps(self, new_value: int):
        if new_value >= 1:
            self._target_fps = new_value
            self._camera.target_fps = new_value

    @property
    def min_lapse_duration(self):
        return self._min_lapse_duration

    @min_lapse_duration.setter
    def min_lapse_duration(self, new_value: int):
        if new_value >= 0:
            if new_value <= self._max_lapse_duration and not new_value == 0:
                logger.warning(f"Min lapse duration {new_value} is lower than max lapse duration {self._max_lapse_duration}")
            self._min_lapse_duration = new_value
            self._camera.min_lapse_duration = new_value

    @property
    def max_lapse_duration(self):
        return self._max_lapse_duration

    @max_lapse_duration.setter
    def max_lapse_duration(self, new_value: int):
        if new_value >= 0:
            if new_value <= self._min_lapse_duration and not new_value == 0:
                logger.warning(f"Max lapse duration {new_value} is lower than min lapse duration {self._min_lapse_duration}")
            self._max_lapse_duration = new_value
            self._camera.max_lapse_duration = new_value

    @property
    def last_frame_duration(self):
        return self._last_frame_duration

    @last_frame_duration.setter
    def last_frame_duration(self, new_value: int):
        if new_value >= 0:
            self._last_frame_duration = new_value
            self._camera.last_frame_duration = new_value

    @property
    def running(self):
        return self._running

    @running.setter
    def running(self, new_val: bool):
        self._running = new_val
        self._paused = False
        if new_val:
            self._add_timelapse_timer()
        else:
            self._remove_timelapse_timer()

    @property
    def paused(self):
        return self._paused

    @paused.setter
    def paused(self, new_val: bool):
        self._paused = new_val
        if new_val:
            self._remove_timelapse_timer()
        elif self._running:
            self._add_timelapse_timer()

    def take_lapse_photo(self, position_z: float = -1001, manually: bool = False):
        if not self._enabled:
            logger.debug(f"lapse is disabled")
            return
        elif not self._klippy.printing_filename:
            logger.debug(f"lapse is inactive for file undefined")
            return
        elif not self._running:
            logger.debug(f"lapse is not running at the moment")
            return
        elif self._paused and not manually:
            logger.debug(f"lapse is paused at the moment")
            return
        elif not self._mode_manual and self._klippy.printing_duration <= 0.0:
            logger.debug(f"lapse must not run with auto mode and zero print duration")
            return

        if 0.0 < position_z < self._last_height - self._height:
            self._last_height = position_z

        if self._height > 0.0 and round(position_z * 100) % round(self._height * 100) == 0 and position_z > self._last_height:
            self._executors_pool.submit(self._camera.take_lapse_photo)
            self._last_height = position_z
        elif position_z < -1000:
            self._executors_pool.submit(self._camera.take_lapse_photo)

    def take_test_lapse_photo(self):
        self._executors_pool.submit(self._camera.take_lapse_photo)

    def clean(self):
        self._camera.clean()

    def _add_timelapse_timer(self):
        if self._interval > 0 and not self._sched.get_job('timelapse_timer'):
            self._sched.add_job(self.take_lapse_photo, 'interval', seconds=self._interval, id='timelapse_timer')

    def _remove_timelapse_timer(self):
        if self._sched.get_job('timelapse_timer'):
            self._sched.remove_job('timelapse_timer')

    def _reschedule_timelapse_timer(self):
        if self._interval > 0 and self._sched.get_job('timelapse_timer'):
            self._sched.add_job(self.take_lapse_photo, 'interval', seconds=self._interval, id='timelapse_timer', replace_existing=True)

    def _send_lapse(self):
        if not self._enabled or not self._klippy.printing_filename:
            logger.debug(f"lapse is inactive for enabled {self.enabled} or file undefined")
        else:
            lapse_filename = self._klippy.printing_filename_with_time
            gcode_name = self._klippy.printing_filename

            info_mess: Message = self._bot_updater.bot.send_message(chat_id=self._chat_id, text=f"Starting time-lapse assembly for {gcode_name}", disable_notification=self._silent_progress)

            if self._executors_pool._work_queue.qsize() > 0:
                info_mess.edit_text(text="Waiting for the completion of tasks for photographing")

            time.sleep(5)
            while self._executors_pool._work_queue.qsize() > 0:
                time.sleep(1)

            self._bot_updater.bot.send_chat_action(chat_id=self._chat_id, action=ChatAction.RECORD_VIDEO)
            (video_bio, thumb_bio, width, height, video_path, gcode_name) = self._camera.create_timelapse(lapse_filename, gcode_name, info_mess)

            if self._after_lapse_send_video:
                info_mess.edit_text(text="Uploading time-lapse")

                if video_bio.getbuffer().nbytes > 52428800:
                    info_mess.edit_text(text=f'Telegram bots have a 50mb filesize restriction, please retrieve the timelapse from the configured folder\n{video_path}')
                else:
                    self._bot_updater.bot.send_video(self._chat_id, video=video_bio, thumb=thumb_bio, width=width, height=height, caption=f'time-lapse of {gcode_name}', timeout=120,
                                                     disable_notification=self._silent_progress)
                    self._bot_updater.bot.delete_message(self._chat_id, message_id=info_mess.message_id)
            else:
                info_mess.edit_text(text="Time-lapse creation finished")

            if self._after_lapse_gcode:
                self._klippy.execute_command(self._after_lapse_gcode.strip())

            video_bio.close()
            thumb_bio.close()

    def send_timelapse(self):
        self._sched.add_job(self._send_lapse, misfire_grace_time=None, coalesce=False, max_instances=1, replace_existing=False)

    def stop_all(self):
        self._remove_timelapse_timer()
        self._running = False
        self._paused = False
        self._last_height = 0.0
