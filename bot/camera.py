from contextlib import contextmanager
from functools import wraps
import glob
from io import BytesIO
import logging
import math
import os
import pathlib
from pathlib import Path
from queue import Queue
import threading
import time
from typing import Any, Callable, List, Optional, Tuple

from PIL import Image, _webp  # type: ignore
import cv2  # type: ignore
from telegram import Message
from telegram.error import RetryAfter

from configuration import ConfigWrapper
from klippy import Klippy
from power_device import PowerDevice

logger = logging.getLogger(__name__)


def cam_light_toggle(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        self.use_light()

        if self.light_timeout > 0 and self.light_device and not self.light_device.device_state and not self.light_lock.locked():
            self.light_timer_event.clear()
            self.light_lock.acquire()
            self.light_need_off = True
            self.light_device.switch_device(True)
            time.sleep(self.light_timeout)
            self.light_timer_event.set()

        self.light_timer_event.wait()

        # Todo: maybe add try block?
        result = func(self, *args, **kwargs)

        self.free_light()

        def delayed_light_off():
            if self.light_requests == 0:
                if self.light_lock.locked():
                    self.light_lock.release()
                self.light_need_off = False
                self.light_device.switch_device(False)
            else:
                logger.debug("light requests count: %s", self.light_requests)

        if self.light_need_off and self.light_requests == 0:
            threading.Timer(self.light_timeout, delayed_light_off).start()

        return result

    return wrapper


class Camera:
    def __init__(
        self,
        config: ConfigWrapper,
        klippy: Klippy,
        light_device: PowerDevice,
        logging_handler: logging.Handler = None,
    ):
        self.enabled: bool = bool(config.camera.enabled and config.camera.host)
        self._host = int(config.camera.host) if str.isdigit(config.camera.host) else config.camera.host
        self._threads: int = config.camera.threads
        self._flip_vertically: bool = config.camera.flip_vertically
        self._flip_horizontally: bool = config.camera.flip_horizontally
        self._fourcc: str = config.camera.fourcc
        self._video_duration: int = config.camera.video_duration
        self._video_buffer_size: int = config.camera.video_buffer_size
        self._stream_fps: int = config.camera.stream_fps
        self._klippy: Klippy = klippy

        # Todo: refactor into timelapse class
        self._base_dir: str = config.timelapse.base_dir
        self._ready_dir: str = config.timelapse.ready_dir
        self._cleanup: bool = config.timelapse.cleanup

        self._target_fps: int = 15
        self._min_lapse_duration: int = 0
        self._max_lapse_duration: int = 0
        self._last_frame_duration: int = 5

        self._light_need_off: bool = False
        self._light_need_off_lock: threading.Lock = threading.Lock()

        self.light_timeout: int = config.camera.light_timeout
        self.light_device: PowerDevice = light_device
        self._camera_lock: threading.Lock = threading.Lock()
        self.light_lock = threading.Lock()
        self.light_timer_event: threading.Event = threading.Event()
        self.light_timer_event.set()

        self._hw_accel: bool = False

        self._img_extension: str
        if config.camera.picture_quality == "low":
            self._img_extension = "jpeg"
        elif config.camera.picture_quality == "high":
            self._img_extension = "webp"
        else:
            self._img_extension = config.camera.picture_quality

        self._light_requests: int = 0
        self._light_request_lock: threading.Lock = threading.Lock()

        if self._flip_vertically and self._flip_horizontally:
            self._flip = -1
        elif self._flip_horizontally:
            self._flip = 1
        elif self._flip_vertically:
            self._flip = 0

        self._rotate_code: int
        if config.camera.rotate == "90_cw":
            self._rotate_code = cv2.ROTATE_90_CLOCKWISE
        elif config.camera.rotate == "90_ccw":
            self._rotate_code = cv2.ROTATE_90_COUNTERCLOCKWISE
        elif config.camera.rotate == "180":
            self._rotate_code = cv2.ROTATE_180
        else:
            self._rotate_code = -10

        if logging_handler:
            logger.addHandler(logging_handler)
        if config.bot.debug:
            logger.setLevel(logging.DEBUG)
            logger.debug(cv2.getBuildInformation())
            os.environ["OPENCV_VIDEOIO_DEBUG"] = "1"
        # Fixme: deprecated! use T-API https://learnopencv.com/opencv-transparent-api/
        if cv2.ocl.haveOpenCL():
            logger.debug("OpenCL is available")
            cv2.ocl.setUseOpenCL(True)
            logger.debug("OpenCL in OpenCV is enabled: %s", cv2.ocl.useOpenCL())

        self._cv2_params: List = config.camera.cv2_params
        cv2.setNumThreads(self._threads)
        self.cam_cam = cv2.VideoCapture()
        self._set_cv2_params()

        self.retryable_notification_minimum_time = time.time()

    @property
    def light_need_off(self) -> bool:
        with self._light_need_off_lock:
            return self._light_need_off

    @light_need_off.setter
    def light_need_off(self, new_value: bool):
        with self._light_need_off_lock:
            self._light_need_off = new_value

    @property
    def lapse_dir(self) -> str:
        return f"{self._base_dir}/{self._klippy.printing_filename_with_time}"

    @property
    def light_requests(self) -> int:
        with self._light_request_lock:
            return self._light_requests

    def use_light(self) -> None:
        with self._light_request_lock:
            self._light_requests += 1

    def free_light(self) -> None:
        with self._light_request_lock:
            self._light_requests -= 1

    @property
    def target_fps(self) -> int:
        return self._target_fps

    @target_fps.setter
    def target_fps(self, new_value: int) -> None:
        self._target_fps = new_value

    @property
    def min_lapse_duration(self) -> int:
        return self._min_lapse_duration

    @min_lapse_duration.setter
    def min_lapse_duration(self, new_value: int):
        if new_value >= 0:
            self._min_lapse_duration = new_value

    @property
    def max_lapse_duration(self) -> int:
        return self._max_lapse_duration

    @max_lapse_duration.setter
    def max_lapse_duration(self, new_value: int) -> None:
        if new_value >= 0:
            self._max_lapse_duration = new_value

    @property
    def last_frame_duration(self) -> int:
        return self._last_frame_duration

    @last_frame_duration.setter
    def last_frame_duration(self, new_value: int) -> None:
        if new_value >= 0:
            self._last_frame_duration = new_value

    @staticmethod
    def _create_thumb(image) -> BytesIO:
        # cv2.cvtColor cause segfaults!
        img = Image.fromarray(image[:, :, [2, 1, 0]])
        bio = BytesIO()
        bio.name = "thumbnail.jpeg"
        img.thumbnail((320, 320))
        img.save(bio, "JPEG", quality=100, optimize=True)
        bio.seek(0)
        img.close()
        del img
        return bio

    @staticmethod
    def _isfloat(value: str) -> bool:
        try:
            float(value)
            return True
        except ValueError:
            return False

    def _set_cv2_params(self):
        self.cam_cam.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        for prop_name, value in self._cv2_params:
            if prop_name.upper() == "CAP_PROP_FOURCC":
                try:
                    prop = getattr(cv2, prop_name.upper())
                    self.cam_cam.set(prop, cv2.VideoWriter_fourcc(*value))
                except AttributeError as err:
                    logger.error(err, err)
            else:
                if value.isnumeric():
                    val = int(value)
                elif self._isfloat(value):
                    val = float(value)
                else:
                    val = value
                try:
                    prop = getattr(cv2, prop_name.upper())
                    self.cam_cam.set(prop, val)
                except AttributeError as err:
                    logger.error(err, err)

    @cam_light_toggle
    def take_photo(self) -> BytesIO:
        with self._camera_lock:
            self.cam_cam.open(self._host)
            self._set_cv2_params()
            success, image = self.cam_cam.read()
            self.cam_cam.release()

            if not success:
                logger.debug("failed to get camera frame for photo")
                # Todo: resize to cam resolution!
                img = Image.open("../imgs/nosignal.png")
            else:
                if self._hw_accel:
                    image_um = cv2.UMat(image)
                    if self._flip_vertically or self._flip_horizontally:
                        image_um = cv2.flip(image_um, self._flip)
                    img = Image.fromarray(cv2.UMat.get(cv2.cvtColor(image_um, cv2.COLOR_BGR2RGB)))
                    image_um = None
                    del image_um
                else:
                    if self._flip_vertically or self._flip_horizontally:
                        image = cv2.flip(image, self._flip)
                    # Todo: check memory leaks
                    if self._rotate_code > -10:
                        image = cv2.rotate(image, rotateCode=self._rotate_code)
                    # # cv2.cvtColor cause segfaults!
                    # rgb = image[:, :, ::-1]
                    rgb = image[:, :, [2, 1, 0]]
                    img = Image.fromarray(rgb)
                    rgb = None
                    del rgb

            image = None
            del image, success

        bio = BytesIO()
        bio.name = f"status.{self._img_extension}"
        if self._img_extension in ["jpg", "jpeg"]:
            img.save(bio, "JPEG", quality=80, subsampling=0)
        elif self._img_extension == "webp":
            # https://github.com/python-pillow/Pillow/issues/4364
            _webp.HAVE_WEBPANIM = False
            img.save(bio, "WebP", quality=0, lossless=True)
        elif self._img_extension == "png":
            img.save(bio, "PNG")
        bio.seek(0)

        img.close()
        del img
        return bio

    @contextmanager
    def take_video_generator(self):
        (video_bio, thumb_bio, width, height) = self.take_video()
        try:
            yield video_bio, thumb_bio, width, height
        finally:
            video_bio.close()
            thumb_bio.close()

    @cam_light_toggle
    def take_video(self) -> Tuple[BytesIO, BytesIO, int, int]:
        def process_video_frame(frame_local):
            if self._flip_vertically or self._flip_horizontally:
                if self._hw_accel:
                    frame_loc_ = cv2.UMat(frame_local)
                    frame_loc_ = cv2.flip(frame_loc_, self._flip)
                    frame_local = cv2.UMat.get(frame_loc_)
                    del frame_loc_
                else:
                    frame_local = cv2.flip(frame_local, self._flip)
            # Todo: check memory leaks
            if self._rotate_code > -10:
                frame_local = cv2.rotate(frame_local, rotateCode=self._rotate_code)
            return frame_local

        def write_video():
            cv2.setNumThreads(self._threads)
            out = cv2.VideoWriter(
                filepath,
                fourcc=cv2.VideoWriter_fourcc(*self._fourcc),
                fps=fps_cam,
                frameSize=(width, height),
            )
            while video_lock.locked():
                try:
                    frame_local = frame_queue.get(block=False)
                except Exception as exc:
                    logger.warning("Reading video frames queue exception %s", exc)
                    frame_local = frame_queue.get()

                out.write(process_video_frame(frame_local))
                frame_local = None
                del frame_local

            while not frame_queue.empty():
                frame_local = frame_queue.get()
                out.write(process_video_frame(frame_local))
                frame_local = None
                del frame_local

            out.release()
            video_written_event.set()

        with self._camera_lock:
            cv2.setNumThreads(self._threads)  # TOdo: check self set and remove!
            self.cam_cam.open(self._host)
            self._set_cv2_params()
            success, frame = self.cam_cam.read()

            if not success:
                logger.debug("failed to get camera frame for video")
                # Todo: get picture from imgs?

            frame = process_video_frame(frame)
            height, width, channels = frame.shape
            thumb_bio = self._create_thumb(frame)
            del frame, channels
            fps_cam = self.cam_cam.get(cv2.CAP_PROP_FPS) if self._stream_fps == 0 else self._stream_fps

            filepath = os.path.join("/tmp/", "video.mp4")
            frame_queue: Queue = Queue(fps_cam * self._video_buffer_size)
            video_lock = threading.Lock()
            video_written_event = threading.Event()
            video_written_event.clear()
            with video_lock:
                threading.Thread(target=write_video, args=()).start()
                t_end = time.time() + self._video_duration
                while success and time.time() <= t_end:
                    success, frame_loc = self.cam_cam.read()
                    try:
                        frame_queue.put(frame_loc, block=False)
                    except Exception as ex:
                        logger.warning("Writing video frames queue exception %s", ex.with_traceback)
                        frame_queue.put(frame_loc)
                    frame_loc = None
                    del frame_loc
            video_written_event.wait()

        self.cam_cam.release()
        video_bio = BytesIO()
        video_bio.name = "video.mp4"
        with open(filepath, "rb") as video_file:
            video_bio.write(video_file.read())
        os.remove(filepath)
        video_bio.seek(0)
        return video_bio, thumb_bio, width, height

    def take_lapse_photo(self, gcode: str = "") -> None:
        # Todo: check for space available?
        Path(self.lapse_dir).mkdir(parents=True, exist_ok=True)
        # never add self in params there!
        with self.take_photo() as photo:
            filename = f"{self.lapse_dir}/{time.time()}.{self._img_extension}"
            if gcode:
                try:
                    self._klippy.execute_command(gcode.strip())
                except Exception as ex:
                    logger.error(ex)
            with open(filename, "wb") as outfile:
                outfile.write(photo.getvalue())
            photo.close()

    def create_timelapse(self, printing_filename: str, gcode_name: str, info_mess: Message) -> Tuple[BytesIO, BytesIO, int, int, str, str]:
        return self._create_timelapse(printing_filename, gcode_name, info_mess)

    def create_timelapse_for_file(self, filename: str, info_mess: Message) -> Tuple[BytesIO, BytesIO, int, int, str, str]:
        return self._create_timelapse(filename, filename, info_mess)

    def _calculate_fps(self, frames_count: int) -> int:
        actual_duration = frames_count / self._target_fps

        # Todo: check _max_lapse_duration > _min_lapse_duration
        if (
            (self._min_lapse_duration == 0 and self._max_lapse_duration == 0)
            or (self._min_lapse_duration <= actual_duration <= self._max_lapse_duration and self._max_lapse_duration > 0)
            or (actual_duration > self._min_lapse_duration and self._max_lapse_duration == 0)
        ):
            return self._target_fps
        elif actual_duration < self._min_lapse_duration and self._min_lapse_duration > 0:
            fps = math.ceil(frames_count / self._min_lapse_duration)
            return fps if fps >= 1 else 1
        elif actual_duration > self._max_lapse_duration > 0:
            return math.ceil(frames_count / self._max_lapse_duration)
        else:
            logger.error("Unknown fps calculation state for durations min:%s and max:%s and actual:%s", self._min_lapse_duration, self._max_lapse_duration, actual_duration)
            return self._target_fps

    def retryable_notification(self, required: bool, func: Callable[[], Any], debug_info: Optional[str] = None) -> None:
        if not required and time.time() < self.retryable_notification_minimum_time:
            logger.debug("Proactively skipping not required message due to previous throttle request%s", (": " + debug_info) if debug_info else "")
            return

        try:
            func()
        except RetryAfter as exc:
            self.retryable_notification_minimum_time = time.time() + exc.retry_after

            if not required:
                logger.debug("Skipping not required Telegram notification due to throttle%s", (": " + debug_info) if debug_info else "")
                return

            logger.debug("Sleeping for %i seconds because of Telegram's throttling", exc.retry_after)
            time.sleep(exc.retry_after)
            self.retryable_notification(required=required, func=func)

    def _create_timelapse(self, printing_filename: str, gcode_name: str, info_mess: Message) -> Tuple[BytesIO, BytesIO, int, int, str, str]:
        if not printing_filename:
            raise ValueError("Gcode file name is empty")

        while self.light_need_off:
            time.sleep(1)

        lapse_dir = f"{self._base_dir}/{printing_filename}"

        lock_file = Path(f"{lapse_dir}/lapse.lock")
        if not lock_file.is_file():
            lock_file.touch()

        # Todo: check for nonempty photos!
        photos = glob.glob(f"{glob.escape(lapse_dir)}/*.{self._img_extension}")
        photos.sort(key=os.path.getmtime)
        photo_count = len(photos)

        if photo_count == 0:
            raise ValueError(f"Empty photos list for {printing_filename} in lapse path {lapse_dir}")

        info_mess.edit_text(text="Creating thumbnail")
        last_photo = photos[-1]
        img = cv2.imread(last_photo)
        height, width, layers = img.shape
        thumb_bio = self._create_thumb(img)

        video_filename = Path(printing_filename).name
        video_filepath = f"{lapse_dir}/{video_filename}.mp4"
        if Path(video_filepath).is_file():
            os.remove(video_filepath)

        lapse_fps = self._calculate_fps(photo_count)

        with self._camera_lock:
            cv2.setNumThreads(self._threads)  # TOdo: check self set and remove!
            out = cv2.VideoWriter(
                video_filepath,
                fourcc=cv2.VideoWriter_fourcc(*self._fourcc),
                fps=lapse_fps,
                frameSize=(width, height),
            )

            info_mess.edit_text(text="Images recoding")
            for fnum, filename in enumerate(photos):
                text = f"Images recoded {fnum}/{photo_count}"
                self.retryable_notification(required=False, func=lambda: info_mess.edit_text(text=text), debug_info=text)  # pylint: disable=cell-var-from-loop
                # it's a blocking call so the value won't be different while lambda is being executed

                out.write(cv2.imread(filename))

            text = f"Repeating last image for {self._last_frame_duration} seconds"
            self.retryable_notification(required=True, func=lambda: info_mess.edit_text(text=text), debug_info=text)

            for _ in range(lapse_fps * self._last_frame_duration):
                out.write(img)

            out.release()
            cv2.destroyAllWindows()
            del out

        del photos, img, layers

        # Todo: some error handling?

        video_bio = BytesIO()
        video_bio.name = f"{video_filename}.mp4"
        target_video_file = f"{self._ready_dir}/{printing_filename}.mp4"
        with open(video_filepath, "rb") as fh:
            video_bio.write(fh.read())
        if self._ready_dir and os.path.isdir(self._ready_dir):
            info_mess.edit_text(text="Copy lapse to target ditectory")
            Path(target_video_file).parent.mkdir(parents=True, exist_ok=True)
            with open(target_video_file, "wb") as cpf:
                cpf.write(video_bio.getvalue())
        video_bio.seek(0)

        os.remove(f"{lapse_dir}/lapse.lock")

        return video_bio, thumb_bio, width, height, video_filepath, gcode_name

    def cleanup(self, lapse_filename: str, force: bool = False) -> None:
        lapse_dir = f"{self._base_dir}/{lapse_filename}"
        if self._cleanup or force:
            for filename in glob.glob(f"{glob.escape(lapse_dir)}/*.{self._img_extension}"):
                os.remove(filename)
            for filename in glob.glob(f"{glob.escape(lapse_dir)}/*"):
                os.remove(filename)
            Path(lapse_dir).rmdir()

    def clean(self) -> None:
        if self._cleanup and self._klippy.printing_filename and os.path.isdir(self.lapse_dir):
            for filename in glob.glob(f"{glob.escape(self.lapse_dir)}/*"):
                os.remove(filename)

    # Todo: check if lapse was in subfolder ( alike gcode folders)
    # Todo: refactor into timelapse class
    # Todo: check for 64 symbols length in lapse names
    def detect_unfinished_lapses(self) -> List[str]:
        # Todo: detect unstarted timelapse builds? folder with pics and no mp4 files
        return list(
            map(
                lambda el: pathlib.PurePath(el).parent.name,
                glob.glob(f"{self._base_dir}/*/*.lock"),
            )
        )

    def cleanup_unfinished_lapses(self):
        for lapse_name in self.detect_unfinished_lapses():
            self.cleanup(lapse_name, force=True)
