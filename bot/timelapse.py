from concurrent.futures import ThreadPoolExecutor
import logging
import time

from apscheduler.schedulers.base import BaseScheduler
from camera import Camera
from configuration import ConfigWrapper
from klippy import Klippy
from telegram import Bot, ChatAction, Message

logger = logging.getLogger(__name__)


def logging_callback(future):
    exc = future.exception()

    if exc is None:
        return

    logger.error(exc, exc_info=(type(exc), exc, exc.__traceback__))


class Timelapse:
    def __init__(
        self,
        config: ConfigWrapper,
        klippy: Klippy,
        camera: Camera,
        scheduler: BaseScheduler,
        bot: Bot,
        logging_handler: logging.Handler = None,
    ):
        self._enabled: bool = config.timelapse.enabled and camera.enabled
        self._mode_manual: bool = config.timelapse.mode_manual
        self._height: float = config.timelapse.height
        self._interval: int = config.timelapse.interval
        self._target_fps: int = config.timelapse.target_fps
        self._min_lapse_duration: int = config.timelapse.min_lapse_duration
        self._max_lapse_duration: int = config.timelapse.max_lapse_duration
        self._last_frame_duration: int = config.timelapse.last_frame_duration

        # Todo: add to runtime params section!
        self._after_lapse_gcode: str = config.timelapse.after_lapse_gcode
        self._send_finished_lapse: bool = config.timelapse.send_finished_lapse
        self._after_photo_gcode: str = config.timelapse.after_photo_gcode

        self._silent_progress: bool = config.telegram_ui.silent_progress

        self._klippy = klippy
        self._camera = camera

        # push params to cameras instances
        self._camera.target_fps = self._target_fps
        self._camera.min_lapse_duration = self._min_lapse_duration
        self._camera.max_lapse_duration = self._max_lapse_duration
        self._camera.last_frame_duration = self._last_frame_duration

        self._sched = scheduler
        self._chat_id: int = config.bot.chat_id
        self._bot: Bot = bot

        self._running: bool = False
        self._paused: bool = False
        self._last_height: float = 0.0

        self._executors_pool: ThreadPoolExecutor = ThreadPoolExecutor(2, thread_name_prefix="timelapse_pool")

        if logging_handler:
            logger.addHandler(logging_handler)
        if config.bot.debug:
            logger.setLevel(logging.DEBUG)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, new_value: bool):
        self._enabled = new_value

    @property
    def manual_mode(self) -> bool:
        return self._mode_manual

    @manual_mode.setter
    def manual_mode(self, new_value: bool):
        self._mode_manual = new_value

    @property
    def interval(self) -> int:
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
    def height(self) -> float:
        return self._height

    @height.setter
    def height(self, new_value: float):
        if new_value >= 0:
            self._height = new_value

    @property
    def target_fps(self) -> int:
        return self._target_fps

    @target_fps.setter
    def target_fps(self, new_value: int):
        if new_value >= 1:
            self._target_fps = new_value
            self._camera.target_fps = new_value

    @property
    def min_lapse_duration(self) -> int:
        return self._min_lapse_duration

    @min_lapse_duration.setter
    def min_lapse_duration(self, new_value: int):
        if new_value >= 0:
            if new_value <= self._max_lapse_duration and not new_value == 0:
                logger.warning(
                    f"Min lapse duration {new_value} is lower than max lapse duration {self._max_lapse_duration}"
                )
            self._min_lapse_duration = new_value
            self._camera.min_lapse_duration = new_value

    @property
    def max_lapse_duration(self) -> int:
        return self._max_lapse_duration

    @max_lapse_duration.setter
    def max_lapse_duration(self, new_value: int):
        if new_value >= 0:
            if new_value <= self._min_lapse_duration and not new_value == 0:
                logger.warning(
                    f"Max lapse duration {new_value} is lower than min lapse duration {self._min_lapse_duration}"
                )
            self._max_lapse_duration = new_value
            self._camera.max_lapse_duration = new_value

    @property
    def last_frame_duration(self) -> int:
        return self._last_frame_duration

    @last_frame_duration.setter
    def last_frame_duration(self, new_value: int):
        if new_value >= 0:
            self._last_frame_duration = new_value
            self._camera.last_frame_duration = new_value

    @property
    def running(self) -> bool:
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
    def paused(self) -> bool:
        return self._paused

    @paused.setter
    def paused(self, new_val: bool):
        self._paused = new_val
        if new_val:
            self._remove_timelapse_timer()
        elif self._running:
            self._add_timelapse_timer()

    def take_lapse_photo(self, position_z: float = -1001, manually: bool = False, gcode: bool = False):
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

        gcode_command = self._after_photo_gcode if gcode and self._after_photo_gcode else ""

        if (
            self._height > 0.0
            and round(position_z * 100) % round(self._height * 100) == 0
            and position_z > self._last_height
        ):
            self._executors_pool.submit(self._camera.take_lapse_photo, gcode=gcode_command).add_done_callback(
                logging_callback
            )
            self._last_height = position_z
        elif position_z < -1000:
            self._executors_pool.submit(self._camera.take_lapse_photo, gcode=gcode_command).add_done_callback(
                logging_callback
            )

    def take_test_lapse_photo(self):
        self._executors_pool.submit(self._camera.take_lapse_photo).add_done_callback(logging_callback)

    def clean(self):
        self._camera.clean()

    def _add_timelapse_timer(self):
        if self._interval > 0 and not self._sched.get_job("timelapse_timer"):
            self._sched.add_job(
                self.take_lapse_photo,
                "interval",
                seconds=self._interval,
                id="timelapse_timer",
            )

    def _remove_timelapse_timer(self):
        if self._sched.get_job("timelapse_timer"):
            self._sched.remove_job("timelapse_timer")

    def _reschedule_timelapse_timer(self):
        if self._interval > 0 and self._sched.get_job("timelapse_timer"):
            self._sched.add_job(
                self.take_lapse_photo,
                "interval",
                seconds=self._interval,
                id="timelapse_timer",
                replace_existing=True,
            )

    def _send_lapse(self):
        if not self._enabled or not self._klippy.printing_filename:
            logger.debug(f"lapse is inactive for enabled {self.enabled} or file undefined")
        else:
            lapse_filename = self._klippy.printing_filename_with_time
            gcode_name = self._klippy.printing_filename

            info_mess: Message = self._bot.send_message(
                chat_id=self._chat_id,
                text=f"Starting time-lapse assembly for {gcode_name}",
                disable_notification=self._silent_progress,
            )

            if self._executors_pool._work_queue.qsize() > 0:
                info_mess.edit_text(text="Waiting for the completion of tasks for photographing")

            time.sleep(5)
            while self._executors_pool._work_queue.qsize() > 0:
                time.sleep(1)

            self._bot.send_chat_action(chat_id=self._chat_id, action=ChatAction.RECORD_VIDEO)
            (
                video_bio,
                thumb_bio,
                width,
                height,
                video_path,
                gcode_name,
            ) = self._camera.create_timelapse(lapse_filename, gcode_name, info_mess)

            if self._send_finished_lapse:
                info_mess.edit_text(text="Uploading time-lapse")

                if video_bio.getbuffer().nbytes > 52428800:
                    info_mess.edit_text(
                        text=f"Telegram bots have a 50mb filesize restriction, please retrieve the timelapse from the configured folder\n{video_path}"
                    )
                else:
                    self._bot.send_video(
                        self._chat_id,
                        video=video_bio,
                        thumb=thumb_bio,
                        width=width,
                        height=height,
                        caption=f"time-lapse of {gcode_name}",
                        timeout=120,
                        disable_notification=self._silent_progress,
                    )
                    self._bot.delete_message(self._chat_id, message_id=info_mess.message_id)
            else:
                info_mess.edit_text(text="Time-lapse creation finished")

            video_bio.close()
            thumb_bio.close()

            if self._after_lapse_gcode:
                # Todo: add exception handling
                self._klippy.save_data_to_marco(video_bio.getbuffer().nbytes, video_path, f"{gcode_name}.mp4")
                self._klippy.execute_command(self._after_lapse_gcode.strip())

    def send_timelapse(self):
        self._sched.add_job(
            self._send_lapse,
            misfire_grace_time=None,
            coalesce=False,
            max_instances=1,
            replace_existing=False,
        )

    def stop_all(self):
        self._remove_timelapse_timer()
        self._running = False
        self._paused = False
        self._last_height = 0.0

    def parse_timelapse_params(self, message: str):
        mass_parts = message.split(sep=" ")
        mass_parts.pop(0)
        response = ""
        for part in mass_parts:
            try:
                if "enabled" in part:
                    self.enabled = bool(int(part.split(sep="=").pop()))
                    response += f"enabled={self.enabled} "
                elif "manual_mode" in part:
                    self.manual_mode = bool(int(part.split(sep="=").pop()))
                    response += f"manual_mode={self.manual_mode} "
                elif "height" in part:
                    self.height = float(part.split(sep="=").pop())
                    response += f"height={self.height} "
                elif "time" in part:
                    self.interval = int(part.split(sep="=").pop())
                    response += f"time={self.interval} "
                elif "target_fps" in part:
                    self.target_fps = int(part.split(sep="=").pop())
                    response += f"target_fps={self.target_fps} "
                elif "last_frame_duration" in part:
                    self.last_frame_duration = int(part.split(sep="=").pop())
                    response += f"last_frame_duration={self.last_frame_duration} "
                elif "min_lapse_duration" in part:
                    self.min_lapse_duration = int(part.split(sep="=").pop())
                    response += f"min_lapse_duration={self.min_lapse_duration} "
                elif "max_lapse_duration" in part:
                    self.max_lapse_duration = int(part.split(sep="=").pop())
                    response += f"max_lapse_duration={self.max_lapse_duration} "
                else:
                    self._klippy.execute_command(
                        f'RESPOND PREFIX="Timelapse params error" MSG="unknown param `{part}`"'
                    )
            except Exception as ex:
                self._klippy.execute_command(
                    f'RESPOND PREFIX="Timelapse params error" MSG="Failed parsing `{part}`. {ex}"'
                )
        if response:
            full_conf = (
                f"enabled={self.enabled} "
                f"manual_mode={self.manual_mode} "
                f"height={self.height} "
                f"time={self.interval} "
                f"target_fps={self.target_fps} "
                f"last_frame_duration={self.last_frame_duration} "
                f"min_lapse_duration={self.min_lapse_duration} "
                f"max_lapse_duration={self.max_lapse_duration} "
            )
            self._klippy.execute_command(
                f'RESPOND PREFIX="Timelapse params" MSG="Changed timelapse params: {response}"'
            )
            self._klippy.execute_command(f'RESPOND PREFIX="Timelapse params" MSG="Full timelapse config: {full_conf}"')
