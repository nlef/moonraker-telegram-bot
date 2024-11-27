import asyncio
from concurrent.futures import ThreadPoolExecutor
import logging

from apscheduler.schedulers.base import BaseScheduler  # type: ignore
from telegram import Bot, Message
from telegram.constants import ChatAction
from telegram.error import BadRequest

from camera import Camera
from configuration import ConfigWrapper
from klippy import Klippy

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
        logging_handler: logging.Handler,
    ):
        self._enabled: bool = config.timelapse.enabled and camera.enabled
        self._mode_manual: bool = config.timelapse.mode_manual
        self._height: float = config.timelapse.height
        self._interval: int = config.timelapse.interval
        self._target_fps: int = config.timelapse.target_fps
        self._limit_fps: bool = config.timelapse.limit_fps
        self._min_lapse_duration: int = config.timelapse.min_lapse_duration
        self._max_lapse_duration: int = config.timelapse.max_lapse_duration
        self._last_frame_duration: int = config.timelapse.last_frame_duration

        self._after_lapse_gcode: str = config.timelapse.after_lapse_gcode
        self._send_finished_lapse: bool = config.timelapse.send_finished_lapse
        self._after_photo_gcode: str = config.timelapse.after_photo_gcode

        self._silent_progress: bool = config.telegram_ui.silent_progress

        self._klippy: Klippy = klippy
        self._camera: Camera = camera

        # push params to cameras instances
        self._camera.target_fps = self._target_fps
        self._camera.limit_fps = self._limit_fps
        self._camera.min_lapse_duration = self._min_lapse_duration
        self._camera.max_lapse_duration = self._max_lapse_duration
        self._camera.last_frame_duration = self._last_frame_duration

        self._sched: BaseScheduler = scheduler
        self._chat_id: int = config.secrets.chat_id
        self._bot: Bot = bot

        self._running: bool = False
        self._paused: bool = False
        self._last_height: float = 0.0

        self._executors_pool: ThreadPoolExecutor = ThreadPoolExecutor(2, thread_name_prefix="timelapse_pool")

        if logging_handler:
            logger.addHandler(logging_handler)
        if config.bot_config.debug:
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
            if new_value <= self._max_lapse_duration and new_value != 0:
                logger.warning("Min lapse duration %s is lower than max lapse duration %s", new_value, self._max_lapse_duration)
            self._min_lapse_duration = new_value
            self._camera.min_lapse_duration = new_value

    @property
    def max_lapse_duration(self) -> int:
        return self._max_lapse_duration

    @max_lapse_duration.setter
    def max_lapse_duration(self, new_value: int):
        if new_value >= 0:
            if new_value <= self._min_lapse_duration and new_value != 0:
                logger.warning("Max lapse duration %s is lower than min lapse duration %s", new_value, self._min_lapse_duration)
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
    def is_running(self) -> bool:
        return self._running

    @is_running.setter
    def is_running(self, new_val: bool) -> None:
        self._running = new_val
        self._paused = False
        if new_val:
            self._add_timelapse_timer()
            self._camera.lapse_missed_frames = 0
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

    def take_lapse_photo(self, position_z: float = -1001, manually: bool = False, gcode: bool = False) -> None:
        if not self._enabled:
            logger.debug("lapse is disabled")
            return
        elif not self._klippy.printing_filename:
            logger.debug("lapse is inactive for file undefined")
            return
        elif not self._running:
            logger.debug("lapse is not running at the moment")
            return
        elif self._paused and not manually:
            logger.debug("lapse is paused at the moment")
            return
        elif not self._mode_manual and self._klippy.printing_duration <= 0.0:
            logger.debug("lapse must not run with auto mode and zero print duration")
            return

        gcode_command = self._after_photo_gcode if gcode and self._after_photo_gcode else ""

        if self._height > 0.0 and (position_z >= self._last_height + self._height or 0.0 < position_z < self._last_height - self._height):
            self._executors_pool.submit(self._camera.take_lapse_photo, gcode=gcode_command).add_done_callback(logging_callback)
            self._last_height = position_z
        elif position_z < -1000:
            self._executors_pool.submit(self._camera.take_lapse_photo, gcode=gcode_command).add_done_callback(logging_callback)

    def take_test_lapse_photo(self) -> None:
        self._executors_pool.submit(self._camera.take_lapse_photo).add_done_callback(logging_callback)

    def clean(self) -> None:
        self._camera.clean()

    def _add_timelapse_timer(self) -> None:
        if self._interval > 0 and not self._sched.get_job("timelapse_timer"):
            self._sched.add_job(
                self.take_lapse_photo,
                "interval",
                seconds=self._interval,
                id="timelapse_timer",
            )

    def _remove_timelapse_timer(self) -> None:
        if self._sched.get_job("timelapse_timer"):
            self._sched.remove_job("timelapse_timer")

    def _reschedule_timelapse_timer(self) -> None:
        if self._interval > 0 and self._sched.get_job("timelapse_timer"):
            self._sched.add_job(
                self.take_lapse_photo,
                "interval",
                seconds=self._interval,
                id="timelapse_timer",
                replace_existing=True,
            )

    async def _send_lapse(self) -> None:
        if not self._enabled or not self._klippy.printing_filename:
            logger.debug("lapse is inactive for enabled %s or file undefined", self.enabled)
            return

        lapse_filename = self._klippy.printing_filename_with_time
        gcode_name = self._klippy.printing_filename

        info_mess: Message = await self._bot.send_message(
            chat_id=self._chat_id,
            text=f"Starting time-lapse assembly for {gcode_name}",
            disable_notification=self._silent_progress,
        )

        if self._executors_pool._work_queue.qsize() > 0:  # pylint: disable=protected-access
            await info_mess.edit_text(text="Waiting for the completion of tasks for photographing")

        await asyncio.sleep(5)
        while self._executors_pool._work_queue.qsize() > 0:  # pylint: disable=protected-access
            await asyncio.sleep(1)

        await self._bot.send_chat_action(chat_id=self._chat_id, action=ChatAction.RECORD_VIDEO)

        try:
            (
                video_bio,
                thumb_bio,
                width,
                height,
                video_path,
                gcode_name,
            ) = await self._camera.create_timelapse(lapse_filename, gcode_name, info_mess)

            if self._send_finished_lapse:
                await info_mess.edit_text(text="Uploading time-lapse")

                if video_bio.getbuffer().nbytes > 52428800:
                    await info_mess.edit_text(text=f"Telegram bots have a 50mb filesize restriction, please retrieve the timelapse from the configured folder\n{video_path}")
                else:
                    lapse_caption = f"time-lapse of {gcode_name}"
                    if self._camera.lapse_missed_frames > 0:
                        lapse_caption += f"\n{self._camera.lapse_missed_frames} frames missed"
                    await self._bot.send_video(
                        self._chat_id,
                        video=video_bio,
                        thumbnail=thumb_bio,
                        width=width,
                        height=height,
                        caption=lapse_caption,
                        write_timeout=120,
                        disable_notification=self._silent_progress,
                    )
                    try:
                        await self._bot.delete_message(self._chat_id, message_id=info_mess.message_id)
                    except BadRequest as badreq:
                        logger.warning("Failed deleting message \n%s", badreq)
                    self._camera.cleanup(lapse_filename)
            else:
                await info_mess.edit_text(text="Time-lapse creation finished")

            video_bio_nbytes = video_bio.getbuffer().nbytes
            video_bio.close()
            thumb_bio.close()

            if self._after_lapse_gcode:
                # Todo: add exception handling
                await self._klippy.save_data_to_marco(video_bio_nbytes, video_path, f"{gcode_name}.mp4")
                await self._klippy.execute_gcode_script(self._after_lapse_gcode.strip())
        except Exception as ex:
            logger.warning("Failed to send time-lapse to telegram bot: %s", ex)
            await info_mess.edit_text(text=f"Failed to send time-lapse to telegram bot: {str(ex)}")

    def send_timelapse(self) -> None:
        self._sched.add_job(
            self._send_lapse,
            misfire_grace_time=None,
            coalesce=False,
            max_instances=1,
            replace_existing=False,
        )

    def stop_all(self) -> None:
        self._remove_timelapse_timer()
        self._running = False
        self._paused = False
        self._last_height = 0.0
        self._camera.lapse_missed_frames = 0

    async def parse_timelapse_params(self, message: str) -> None:
        mass_parts = message.split(sep=" ")
        mass_parts.pop(0)
        response = ""
        for part in mass_parts:
            try:
                if part.startswith("enabled="):
                    self.enabled = bool(int(part.split(sep="=").pop()))
                    response += f"enabled={self.enabled} "
                elif part.startswith("manual_mode="):
                    self.manual_mode = bool(int(part.split(sep="=").pop()))
                    response += f"manual_mode={self.manual_mode} "
                elif part.startswith("height="):
                    self.height = float(part.split(sep="=").pop())
                    response += f"height={self.height} "
                elif part.startswith("time="):
                    self.interval = int(part.split(sep="=").pop())
                    response += f"time={self.interval} "
                elif part.startswith("target_fps="):
                    self.target_fps = int(part.split(sep="=").pop())
                    response += f"target_fps={self.target_fps} "
                elif part.startswith("last_frame_duration="):
                    self.last_frame_duration = int(part.split(sep="=").pop())
                    response += f"last_frame_duration={self.last_frame_duration} "
                elif part.startswith("min_lapse_duration="):
                    self.min_lapse_duration = int(part.split(sep="=").pop())
                    response += f"min_lapse_duration={self.min_lapse_duration} "
                elif part.startswith("max_lapse_duration="):
                    self.max_lapse_duration = int(part.split(sep="=").pop())
                    response += f"max_lapse_duration={self.max_lapse_duration} "
                elif part.startswith("after_lapse_gcode="):
                    self._after_lapse_gcode = part.split(sep="=").pop()
                    response += f"after_lapse_gcode={self._after_lapse_gcode} "
                elif part.startswith("send_finished_lapse="):
                    self._send_finished_lapse = bool(int(part.split(sep="=").pop()))
                    response += f"send_finished_lapse={self._send_finished_lapse} "
                elif part.startswith("after_photo_gcode="):
                    self._after_photo_gcode = part.split(sep="=").pop()
                    response += f"after_photo_gcode={self._after_photo_gcode} "
                else:
                    await self._klippy.execute_gcode_script(f'RESPOND PREFIX="Timelapse params error" MSG="unknown param `{part}`"')
            except Exception as ex:
                await self._klippy.execute_gcode_script(f'RESPOND PREFIX="Timelapse params error" MSG="Failed parsing `{part}`. {ex}"')
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
                f"after_lapse_gcode={self._after_lapse_gcode} "
                f"send_finished_lapse={self._send_finished_lapse} "
                f"after_photo_gcode={self._after_photo_gcode} "
            )
            await self._klippy.execute_gcode_script(f'RESPOND PREFIX="Timelapse params" MSG="Changed timelapse params: {response}"')
            await self._klippy.execute_gcode_script(f'RESPOND PREFIX="Timelapse params" MSG="Full timelapse config: {full_conf}"')
