"""Timelapse video generation from frames captured during prints."""

from __future__ import annotations

import asyncio
from concurrent.futures import Future, ThreadPoolExecutor
import functools
import gc
import logging
import math
import os
from pathlib import Path
import time
from typing import TYPE_CHECKING, Any, Final

import ffmpegcv  # type: ignore[import-untyped]
from telegram import InputFile
from telegram.constants import ChatAction
from telegram.error import BadRequest

from camera import create_thumb, os_nice

if TYPE_CHECKING:
    from apscheduler.schedulers.base import BaseScheduler  # type: ignore[import-untyped]
    from telegram import Bot, Message

    from camera import Camera
    from configuration import ConfigWrapper
    from klippy import Klippy

logger = logging.getLogger(__name__)


_DB_KEY: Final = "timelapse_state"
_STATE_RUNNING: Final = "running"
_STATE_PAUSED: Final = "paused"
_STATE_LAST_HEIGHT: Final = "last_height"


class Timelapse:
    """Captures frames during prints and encodes timelapse videos."""

    def __init__(
        self,
        config: ConfigWrapper,
        klippy: Klippy,
        camera: Camera,
        scheduler: BaseScheduler,
        bot: Bot,
        logging_handler: logging.Handler,
    ) -> None:
        self._enabled: bool = config.timelapse.enabled and camera.enabled
        self._mode_manual: bool = config.timelapse.mode_manual
        self._height: float = config.timelapse.height
        self._interval: int = config.timelapse.interval
        self._target_fps: int = config.timelapse.target_fps
        self._limit_fps: bool = config.timelapse.limit_fps
        self._min_lapse_duration: int = config.timelapse.min_lapse_duration
        self._max_lapse_duration: int = config.timelapse.max_lapse_duration
        self._max_upload_file_size: int = config.bot_config.max_upload_file_size
        self._last_frame_duration: int = config.timelapse.last_frame_duration

        self._after_lapse_gcode: str = config.timelapse.after_lapse_gcode
        self._send_finished_lapse: bool = config.timelapse.send_finished_lapse
        self._after_photo_gcode: str = config.timelapse.after_photo_gcode
        self._fourcc: str = config.camera.fourcc

        self._silent_progress: bool = config.telegram_ui.silent_progress

        self._klippy: Klippy = klippy
        self._camera: Camera = camera

        self._base_dir: Path = config.timelapse.base_dir
        self._ready_dir: Path | None = config.timelapse.ready_dir
        self._cleanup: bool = config.timelapse.cleanup
        self._lapse_missed_frames: int = 0

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
    def _lapse_dir(self) -> Path:
        return self._base_dir / self._klippy.printing_filename_with_time

    # timelapse lifecycle

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, new_value: bool) -> None:
        self._enabled = new_value

    @property
    def manual_mode(self) -> bool:
        return self._mode_manual

    @manual_mode.setter
    def manual_mode(self, new_value: bool) -> None:
        self._mode_manual = new_value

    @property
    def interval(self) -> int:
        return self._interval

    @interval.setter
    def interval(self, new_value: int) -> None:
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
    def height(self, new_value: float) -> None:
        if new_value >= 0:
            self._height = new_value

    @property
    def target_fps(self) -> int:
        return self._target_fps

    @target_fps.setter
    def target_fps(self, new_value: int) -> None:
        if new_value >= 1:
            self._target_fps = new_value

    @property
    def min_lapse_duration(self) -> int:
        return self._min_lapse_duration

    @min_lapse_duration.setter
    def min_lapse_duration(self, new_value: int) -> None:
        if new_value >= 0:
            if new_value <= self._max_lapse_duration and new_value != 0:
                logger.warning("Min lapse duration %s is lower than max lapse duration %s", new_value, self._max_lapse_duration)
            self._min_lapse_duration = new_value

    @property
    def max_lapse_duration(self) -> int:
        return self._max_lapse_duration

    @max_lapse_duration.setter
    def max_lapse_duration(self, new_value: int) -> None:
        if new_value >= 0:
            if new_value <= self._min_lapse_duration and new_value != 0:
                logger.warning("Max lapse duration %s is lower than min lapse duration %s", new_value, self._min_lapse_duration)
            self._max_lapse_duration = new_value

    @property
    def last_frame_duration(self) -> int:
        return self._last_frame_duration

    @last_frame_duration.setter
    def last_frame_duration(self, new_value: int) -> None:
        if new_value >= 0:
            self._last_frame_duration = new_value

    @property
    def is_running(self) -> bool:
        return self._running

    @is_running.setter
    def is_running(self, new_val: bool) -> None:
        if new_val != self._running:
            logger.debug("Timelapse is_running: %s -> %s", self._running, new_val)
        self._running = new_val
        self._paused = False
        if new_val:
            self._add_timelapse_timer()
            self._lapse_missed_frames = 0
        else:
            self._remove_timelapse_timer()
        self._schedule_save()

    @property
    def paused(self) -> bool:
        return self._paused

    @paused.setter
    def paused(self, new_val: bool) -> None:
        self._paused = new_val
        if new_val:
            self._remove_timelapse_timer()
        elif self._running:
            self._add_timelapse_timer()
        self._schedule_save()

    def _lapse_photo_callback(self, future: Future[bool]) -> None:
        exc = future.exception()
        if exc is not None:
            logger.error(exc, exc_info=(type(exc), exc, exc.__traceback__))
            return
        if not future.result():
            self._lapse_missed_frames += 1

    def _take_lapse_and_gcode(self, lapse_dir: Path, after_gcode: str | None) -> bool:
        result = self._camera.take_lapse_photo(lapse_dir)
        if after_gcode:
            try:
                self._klippy.execute_gcode_script_sync(after_gcode.strip())
            except Exception:
                logger.exception("Failed to execute gcode after timelapse shot")
        return result

    def take_lapse_photo(self, position_z: float | None = None, manually: bool = False, with_after_gcode: bool = False) -> None:
        if not self._enabled:
            logger.debug("lapse is disabled")
            return
        if not self._klippy.printing_filename:
            logger.debug("lapse is inactive for file undefined")
            return
        if not self._running:
            logger.debug("lapse is not running at the moment")
            return
        if self._paused and not manually:
            logger.debug("lapse is paused at the moment")
            return
        if not self._mode_manual and self._klippy.printing_duration <= 0.0:
            logger.debug("lapse must not run with auto mode and zero print duration")
            return

        after_gcode = self._after_photo_gcode if with_after_gcode and self._after_photo_gcode else None

        if position_z is None:
            logger.debug("Taking lapse photo (no position)")
            self._executors_pool.submit(self._take_lapse_and_gcode, self._lapse_dir, after_gcode).add_done_callback(self._lapse_photo_callback)
        elif self._height > 0.0 and (position_z >= self._last_height + self._height or 0.0 < position_z < self._last_height - self._height):
            logger.debug("Taking lapse photo at Z=%.2f (last=%.2f, threshold=%.2f)", position_z, self._last_height, self._height)
            self._executors_pool.submit(self._take_lapse_and_gcode, self._lapse_dir, after_gcode).add_done_callback(self._lapse_photo_callback)
            self._last_height = position_z
            self._schedule_save()
        else:
            logger.debug("Skipping lapse photo at Z=%.2f (last=%.2f, threshold=%.2f)", position_z, self._last_height, self._height)

    def clean(self) -> None:
        if self._cleanup and self._klippy.printing_filename and self._lapse_dir.is_dir():
            for filename in self._lapse_dir.iterdir():
                filename.unlink()

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

    async def upload_timelapse(self, lapse_filename: str, info_mess: Message, gcode_name_out: str | None = None) -> None:
        try:
            gcode_name = lapse_filename if gcode_name_out is None else gcode_name_out
            (
                video_bytes,
                thumb_bytes,
                width,
                height,
                video_path,
            ) = await self._create_timelapse(lapse_filename, info_mess)

            if self._send_finished_lapse:
                await info_mess.edit_text(text="Uploading time-lapse")

                if len(video_bytes) > self._max_upload_file_size * 1024 * 1024:
                    await info_mess.edit_text(text=f"Telegram bots have a {self._max_upload_file_size}mb filesize restriction, please retrieve the timelapse from the configured folder\n{video_path}")
                else:
                    lapse_caption = f"time-lapse of {gcode_name}"
                    if self._lapse_missed_frames > 0:
                        lapse_caption += f"\n{self._lapse_missed_frames} frames missed"
                    await self._bot.send_video(
                        self._chat_id,
                        video=InputFile(video_bytes, filename=f"{gcode_name}.mp4"),
                        thumbnail=thumb_bytes,
                        width=width,
                        height=height,
                        caption=lapse_caption,
                        write_timeout=600,
                        disable_notification=self._silent_progress,
                    )
                    try:
                        await self._bot.delete_message(self._chat_id, message_id=info_mess.message_id)
                    except BadRequest as badreq:
                        logger.warning("Failed deleting message \n%s", badreq)
                    self._cleanup_lapse(lapse_filename)
            else:
                await info_mess.edit_text(text="Time-lapse creation finished")
            logger.info("Timelapse assembly complete for %s", gcode_name)

            video_bio_nbytes = len(video_bytes)
            del video_bytes, thumb_bytes
            gc.collect()

            if self._after_lapse_gcode and gcode_name_out is not None:
                # TODO: add exception handling
                await self._klippy.save_data_to_macro(video_bio_nbytes, video_path, f"{gcode_name}.mp4")
                await self._klippy.execute_gcode_script(self._after_lapse_gcode.strip())
        except Exception as ex:
            logger.warning("Failed to send time-lapse to telegram bot: %s", ex)
            await info_mess.edit_text(text=f"Failed to send time-lapse to telegram bot: {ex!s}")

    async def _send_lapse(self) -> None:
        if not self._enabled or not self._klippy.printing_filename:
            logger.debug("lapse is inactive for enabled %s or file undefined", self.enabled)
            return

        lapse_filename = self._klippy.printing_filename_with_time
        gcode_name = self._klippy.printing_filename
        logger.info("Starting timelapse assembly for %s", gcode_name)

        info_mess: Message = await self._bot.send_message(
            chat_id=self._chat_id,
            text=f"Starting time-lapse assembly for {gcode_name}",
            disable_notification=self._silent_progress,
        )

        if self._executors_pool._work_queue.qsize() > 0:  # noqa: SLF001
            await info_mess.edit_text(text="Waiting for the completion of tasks for photographing")

        await asyncio.sleep(5)

        while self._executors_pool._work_queue.qsize() > 0:  # noqa: ASYNC110, SLF001
            await asyncio.sleep(1)

        await self._bot.send_chat_action(chat_id=self._chat_id, action=ChatAction.RECORD_VIDEO)

        await self.upload_timelapse(lapse_filename, info_mess, gcode_name)

    def send_timelapse(self) -> None:
        self._sched.add_job(
            self._send_lapse,
            misfire_grace_time=None,
            coalesce=False,
            max_instances=1,
            replace_existing=False,
        )

    # timelapse assembly

    async def _create_timelapse(self, printing_filename: str, info_mess: Any) -> tuple[bytes, bytes, int, int, str]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(self._assemble_timelapse, printing_filename, info_mess, loop))

    def _calculate_fps(self, frames_count: int) -> int:
        actual_duration = frames_count / self._target_fps
        if (
            (self._min_lapse_duration == 0 and self._max_lapse_duration == 0)
            or (self._min_lapse_duration <= actual_duration <= self._max_lapse_duration and self._max_lapse_duration > 0)
            or (actual_duration > self._min_lapse_duration and self._max_lapse_duration == 0)
        ):
            return self._target_fps
        if actual_duration < self._min_lapse_duration and self._min_lapse_duration > 0:
            fps = math.ceil(frames_count / self._min_lapse_duration)
            return max(fps, 1)
        if actual_duration > self._max_lapse_duration > 0:
            return math.ceil(frames_count / self._max_lapse_duration)
        logger.error("Unknown fps calculation state for durations min:%s and max:%s and actual:%s", self._min_lapse_duration, self._max_lapse_duration, actual_duration)
        return self._target_fps

    def _assemble_timelapse(self, printing_filename: str, info_mess: Any, loop: asyncio.AbstractEventLoop) -> tuple[bytes, bytes, int, int, str]:
        if not printing_filename:
            msg = "Gcode file name is empty"
            raise ValueError(msg)

        while self._camera.light_need_off:
            time.sleep(1)

        os_nice(15)

        lapse_dir = self._base_dir / printing_filename
        raw_ext = self._camera.raw_frame_extension

        raw_frames = list(lapse_dir.glob(f"*.{raw_ext}"))
        photo_count = len(raw_frames)
        if photo_count == 0:
            msg = f"Empty photos list for {printing_filename} in lapse path {lapse_dir}"
            raise ValueError(msg)

        lock_file = lapse_dir / "lapse.lock"
        if not lock_file.is_file():
            lock_file.touch()

        raw_frames.sort(key=os.path.getmtime)

        asyncio.run_coroutine_threadsafe(info_mess.edit_text(text="Creating thumbnail"), loop).result()
        last_frame = raw_frames[-1]
        img = self._camera.get_frame(last_frame)

        thumb_bio, height, width = create_thumb(img)

        video_filepath = lapse_dir / f"{Path(printing_filename).name}.mp4"
        if video_filepath.is_file():
            video_filepath.unlink()

        lapse_fps = self._calculate_fps(photo_count)
        odd_frames = 1
        if self._limit_fps and lapse_fps > self._target_fps:
            odd_frames = math.ceil(lapse_fps / self._target_fps)
            lapse_fps = self._target_fps

        out = ffmpegcv.VideoWriter(
            video_filepath.as_posix(),
            codec=self._fourcc,
            fps=lapse_fps,
        )

        asyncio.run_coroutine_threadsafe(info_mess.edit_text(text="Images recoding"), loop).result()
        last_update_time = time.time()
        frames_skipped = 0
        frames_recorded = 0
        for fnum, filename in enumerate(raw_frames):
            if time.time() >= last_update_time + 10:
                if self._limit_fps:
                    asyncio.run_coroutine_threadsafe(info_mess.edit_text(text=f"Images processed: {fnum}/{photo_count}, recorded: {frames_recorded}, skipped: {frames_skipped}"), loop).result()
                else:
                    asyncio.run_coroutine_threadsafe(info_mess.edit_text(text=f"Images recoded {fnum}/{photo_count}"), loop).result()
                last_update_time = time.time()

            if not self._limit_fps or fnum % odd_frames == 0:
                out.write(self._camera.get_frame(filename))
                frames_recorded += 1
            else:
                frames_skipped += 1

        if self._last_frame_duration > 0:
            asyncio.run_coroutine_threadsafe(info_mess.edit_text(text=f"Repeating last image for {self._last_frame_duration} seconds"), loop).result()
            for _ in range(lapse_fps * self._last_frame_duration):
                out.write(img)

        if self._limit_fps:
            asyncio.run_coroutine_threadsafe(info_mess.edit_text(text=f"Images recorded: {frames_recorded}, skipped: {frames_skipped}"), loop).result()

        out.release()
        del out, raw_frames, img, last_frame

        # TODO: some error handling?
        video_bytes: bytes = b""
        with video_filepath.open("rb") as fh:
            video_bytes = fh.read()
        if self._ready_dir and self._ready_dir.is_dir():
            asyncio.run_coroutine_threadsafe(info_mess.edit_text(text="Copy lapse to target ditectory"), loop).result()
            target_video_file = self._ready_dir / f"{printing_filename}.mp4"
            target_video_file.parent.mkdir(parents=True, exist_ok=True)
            with target_video_file.open("wb") as cpf:
                cpf.write(video_bytes)

        (lapse_dir / "lapse.lock").unlink(missing_ok=True)
        os_nice(0)

        res_thumb_bytes = thumb_bio.getvalue()
        thumb_bio.close()
        del thumb_bio

        return video_bytes, res_thumb_bytes, width, height, str(video_filepath)

    def _cleanup_lapse(self, lapse_filename: str, *, force: bool = False) -> None:
        lapse_dir = self._base_dir / lapse_filename
        if self._cleanup or force:
            for filename in lapse_dir.iterdir():
                filename.unlink()
            lapse_dir.rmdir()

    # TODO: check if lapse was in subfolder (alike gcode folders)
    # TODO: check for 64 symbols length in lapse names
    def detect_unfinished_lapses(self) -> list[str]:
        # TODO: detect unstarted timelapse builds? folder with pics and no mp4 files
        return [el.parent.name for el in self._base_dir.rglob("*.lock")]

    def cleanup_unfinished_lapses(self) -> None:
        for lapse_name in self.detect_unfinished_lapses():
            self._cleanup_lapse(lapse_name, force=True)

    def stop_all(self) -> None:
        self._remove_timelapse_timer()
        self._running = False
        self._paused = False
        self._last_height = 0.0
        self._lapse_missed_frames = 0
        self._schedule_save()

    def _schedule_save(self) -> None:
        """Schedule state save without blocking the event loop."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._save_state())  # noqa: RUF006
        except RuntimeError:
            pass

    async def _save_state(self) -> None:
        """Persist timelapse state to Moonraker database."""
        if not self._running:
            await self._clear_state()
            return
        state = {_STATE_RUNNING: self._running, _STATE_PAUSED: self._paused, _STATE_LAST_HEIGHT: self._last_height}
        await self._klippy.save_param_to_db(_DB_KEY, state)

    async def _clear_state(self) -> None:
        await self._klippy.delete_param_from_db(_DB_KEY)

    async def restore_state(self) -> None:
        """Restore timelapse state from database after reconnect."""
        state = await self._klippy.get_param_from_db(_DB_KEY)
        if state is None:
            return
        self._running = state.get(_STATE_RUNNING, False)
        self._paused = state.get(_STATE_PAUSED, False)
        self._last_height = state.get(_STATE_LAST_HEIGHT, 0.0)
        if self._running:
            logger.info("Restored timelapse state: running=%s, paused=%s, last_height=%.2f", self._running, self._paused, self._last_height)
            if not self._paused:
                self._add_timelapse_timer()

    async def parse_timelapse_params(self, message: str) -> None:
        mass_parts = message.split(sep=" ")
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
            except Exception as ex:  # noqa: PERF203
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
