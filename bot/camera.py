import asyncio
import functools
from functools import wraps
import glob
from io import BytesIO
import logging
import math
import os
import pathlib
from pathlib import Path
import pickle
import threading
import time
from typing import List, Tuple

from PIL import Image, _webp  # type: ignore
from assets.ffmpegcv_custom import FFmpegReaderStreamRTCustomInit  # type: ignore
import ffmpegcv  # type: ignore
from ffmpegcv import FFmpegReader
from ffmpegcv.stream_info import get_info  # type: ignore
import httpx
from httpx import HTTPError
import numpy
from numpy import ndarray
from telegram import Message

from configuration import ConfigWrapper
from klippy import Klippy, PowerDevice

try:
    import cv2  # type: ignore
except ImportError:
    cv2 = None


logger = logging.getLogger(__name__)


def cam_light_toggle(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        self.use_light()

        if self.light_timeout > 0 and self.light_device and not self.light_device.device_state and not self.light_lock.locked():
            self.light_timer_event.clear()
            self.light_lock.acquire()
            self.light_need_off = True
            self.light_device.switch_device_sync(True)
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
                self.light_device.switch_device_sync(False)
            else:
                logger.debug("light requests count: %s", self.light_requests)

        if self.light_need_off and self.light_requests == 0:
            threading.Timer(self.light_timeout, delayed_light_off).start()

        return result

    return wrapper


def os_nice(value: int):
    try:
        os.nice(value)  # type: ignore
    except Exception:
        pass


class Camera:

    def __init__(self, config: ConfigWrapper, klippy: Klippy, logging_handler: logging.Handler):
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
        self._limit_fps: bool = False
        self._min_lapse_duration: int = 0
        self._max_lapse_duration: int = 0
        self._last_frame_duration: int = 5

        self._light_need_off: bool = False
        self._light_need_off_lock: threading.Lock = threading.Lock()

        self.light_timeout: int = config.camera.light_timeout
        self.light_device: PowerDevice = self._klippy.light_device
        self._camera_lock: threading.Lock = threading.Lock()
        self.light_lock = threading.Lock()
        self.light_timer_event: threading.Event = threading.Event()
        self.light_timer_event.set()

        self._picture_quality = config.camera.picture_quality
        self._img_extension: str
        if config.camera.picture_quality in ["low", "high"]:
            self._img_extension = "jpeg"
        else:
            self._img_extension = config.camera.picture_quality

        self._save_lapse_photos_as_images: bool = config.timelapse.save_lapse_photos_as_images
        self._raw_frame_extension: str = "npz"

        self._light_requests: int = 0
        self._light_request_lock: threading.Lock = threading.Lock()

        self._rotate_code: int
        if config.camera.rotate == "90_cw":
            self._rotate_code = 1
        elif config.camera.rotate == "90_ccw":
            self._rotate_code = 3
        elif config.camera.rotate == "180":
            self._rotate_code = 2
        else:
            self._rotate_code = -10

        self._lapse_missed_frames: int = 0

        if logging_handler:
            logger.addHandler(logging_handler)
        if config.bot_config.debug:
            logger.setLevel(logging.DEBUG)

        # fixme: check init with NO opencv in other cameras!
        # Fixme: deprecated! use T-API https://learnopencv.com/opencv-transparent-api/
        if cv2:
            if config.bot_config.debug:
                logger.debug(cv2.getBuildInformation())
                os.environ["OPENCV_VIDEOIO_DEBUG"] = "1"
            if cv2.ocl.haveOpenCL():
                logger.debug("OpenCL is available")
                cv2.ocl.setUseOpenCL(True)
                logger.debug("OpenCL in OpenCV is enabled: %s", cv2.ocl.useOpenCL())

            # self._cv2_params: List = config.camera.cv2_params
            self._cv2_params: List = []
            cv2.setNumThreads(self._threads)
            self.cam_cam = cv2.VideoCapture()
            self._set_cv2_params()

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
    def limit_fps(self) -> bool:
        return self._limit_fps

    @limit_fps.setter
    def limit_fps(self, new_value: bool) -> None:
        self._limit_fps = new_value

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

    @property
    def lapse_missed_frames(self) -> int:
        return self._lapse_missed_frames

    @lapse_missed_frames.setter
    def lapse_missed_frames(self, new_value: int) -> None:
        self._lapse_missed_frames = new_value

    @staticmethod
    def _create_thumb(image) -> BytesIO:
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

    def _init_cam(self):
        self.cam_cam.open(self._host)
        self._set_cv2_params()
        cv2.setNumThreads(self._threads)

    @cam_light_toggle
    def _take_raw_frame(self, rgb: bool = True) -> ndarray:
        with self._camera_lock:
            st_time = time.time()
            self._init_cam()
            success, image = self.cam_cam.read()
            self.cam_cam.release()
            logger.debug("_take_raw_frame cam read execution time: %s millis", (time.time() - st_time) * 1000)

            if not success:
                logger.debug("failed to get camera frame for photo")
                if rgb:
                    img = Image.open("../imgs/nosignal.png")
                    image = numpy.array(img)
                    img.close()
                    del img
                else:
                    # image is None
                    return numpy.empty(0)
            else:
                if self._flip_vertically:
                    image = numpy.flipud(image)
                if self._flip_horizontally:
                    image = numpy.fliplr(image)
                if self._rotate_code > -10:
                    image = numpy.rot90(image, k=self._rotate_code, axes=(1, 0))

            ndaarr = image[:, :, [2, 1, 0]].copy() if rgb else image.copy()
            image = None
            success = None
            del image, success

        return ndaarr

    def take_photo(self, ndarr: ndarray = None) -> BytesIO:
        img = Image.fromarray(ndarr) if ndarr is not None else Image.fromarray(self._take_raw_frame())

        os_nice(15)
        if img.mode != "RGB":
            logger.warning("img mode is %s", img.mode)
            img = img.convert("RGB")
        bio = BytesIO()
        bio.name = f"status.{self._img_extension}"
        if self._img_extension in ["jpg", "jpeg"] or self._picture_quality == "high":
            img.save(bio, "JPEG", quality=95, subsampling=0, optimize=True)
        elif self._picture_quality == "low":
            img.save(bio, "JPEG", quality=65, subsampling=0)
        # memory leaks!
        elif self._img_extension == "webp":
            # https://github.com/python-pillow/Pillow/issues/4364
            _webp.HAVE_WEBPANIM = False
            img.save(bio, "WebP", quality=0, lossless=True)
        elif self._img_extension == "png":
            img.save(bio, "PNG")
        bio.seek(0)

        img.close()
        os_nice(0)
        del img
        return bio

    @cam_light_toggle
    def take_video(self) -> Tuple[BytesIO, BytesIO, int, int]:
        def process_video_frame(frame_local):
            if self._flip_vertically:
                frame_local = numpy.flipud(frame_local)
            if self._flip_horizontally:
                frame_local = numpy.fliplr(frame_local)
            if self._rotate_code > -10:
                frame_local = numpy.rot90(frame_local, k=self._rotate_code, axes=(1, 0))
            return frame_local

        with self._camera_lock:
            os_nice(15)
            st_time = time.time()
            self._init_cam()
            success, frame = self.cam_cam.read()
            logger.debug("take_video cam read first frame execution time: %s millis", (time.time() - st_time) * 1000)

            if not success:
                logger.debug("failed to get camera frame for video")
                # Todo: get picture from imgs?

            frame = process_video_frame(frame)
            height, width, channels = frame.shape
            thumb_bio = self._create_thumb(frame)
            del frame, channels

            fps_cam = self.cam_cam.get(cv2.CAP_PROP_FPS) if self._stream_fps == 0 else self._stream_fps
            frame_time = 1.0 / fps_cam

            filepath = os.path.join("/tmp/", "video.mp4")
            frame_list = []

            t_end = time.time() + self._video_duration
            time_last_frame = time.time()
            while success and time.time() <= t_end:
                st_time = time.time()
                success, frame_loc = self.cam_cam.read()
                logger.debug("take_video cam read  frame execution time: %s millis", (time.time() - st_time) * 1000)
                if time.time() > time_last_frame + frame_time:
                    time_last_frame = time.time()
                    if success:
                        frame_list.append(pickle.dumps(frame_loc))
                del frame_loc

            self.cam_cam.release()

            res_fps = len(frame_list) / self._video_duration

            logger.debug("res fps - %s", res_fps)

            out = ffmpegcv.VideoWriter(
                filepath,
                codec=self._fourcc,
                fps=res_fps,
            )

            for el in frame_list:
                loc_loc = pickle.loads(el)
                out.write(process_video_frame(loc_loc))
                del loc_loc

            out.release()
            del out
            os_nice(0)

            frame_list.clear()
            del frame_list

        video_bio = BytesIO()
        video_bio.name = "video.mp4"
        with open(filepath, "rb") as video_file:
            video_bio.write(video_file.read())
        os.remove(filepath)
        video_bio.seek(0)
        return video_bio, thumb_bio, width, height

    def take_lapse_photo(self, gcode: str = "") -> None:
        logger.debug("Take_lapse_photo called with gcode `%s`", gcode)
        # Todo: check for space available?
        Path(self.lapse_dir).mkdir(parents=True, exist_ok=True)
        # never add self in params there!
        raw_frame = self._take_raw_frame(rgb=False)

        if gcode:
            try:
                self._klippy.execute_gcode_script_sync(gcode.strip())
            except Exception as ex:
                logger.error(ex)

        if raw_frame.size == 0:
            self._lapse_missed_frames += 1
            return

        os_nice(15)

        numpy.savez_compressed(f"{self.lapse_dir}/{time.time()}", raw=raw_frame)

        raw_frame_rgb = raw_frame[:, :, [2, 1, 0]].copy()
        raw_frame = None
        os_nice(0)

        # never add self in params there!
        if self._save_lapse_photos_as_images:
            with self.take_photo(raw_frame_rgb) as photo:
                # Fixme: jpeg_low is bad file extension!
                filename = f"{self.lapse_dir}/{time.time()}.{self._img_extension}"
                with open(filename, "wb") as outfile:
                    outfile.write(photo.getvalue())
                photo.close()

        raw_frame_rgb = None
        del raw_frame, raw_frame_rgb

    async def create_timelapse(self, printing_filename: str, gcode_name: str, info_mess: Message) -> Tuple[BytesIO, BytesIO, int, int, str, str]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(self._create_timelapse, printing_filename, gcode_name, info_mess, loop))

    async def create_timelapse_for_file(self, filename: str, info_mess: Message) -> Tuple[BytesIO, BytesIO, int, int, str, str]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(self._create_timelapse, filename, filename, info_mess, loop))

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

    def _get_frame(self, path: str):
        return numpy.load(path, allow_pickle=True)["raw"]

    def _create_timelapse(self, printing_filename: str, gcode_name: str, info_mess: Message, loop) -> Tuple[BytesIO, BytesIO, int, int, str, str]:
        if not printing_filename:
            raise ValueError("Gcode file name is empty")

        while self.light_need_off:
            time.sleep(1)

        os_nice(15)

        lapse_dir = f"{self._base_dir}/{printing_filename}"

        lock_file = Path(f"{lapse_dir}/lapse.lock")
        if not lock_file.is_file():
            lock_file.touch()

        raw_frames = glob.glob(f"{glob.escape(lapse_dir)}/*.{self._raw_frame_extension}")
        photo_count = len(raw_frames)
        if photo_count == 0:
            raise ValueError(f"Empty photos list for {printing_filename} in lapse path {lapse_dir}")

        raw_frames.sort(key=os.path.getmtime)

        asyncio.run_coroutine_threadsafe(info_mess.edit_text(text="Creating thumbnail"), loop).result()
        last_frame = raw_frames[-1]
        img = self._get_frame(last_frame)

        height, width, layers = img.shape
        thumb_bio = self._create_thumb(img)

        video_filename = Path(printing_filename).name
        video_filepath = f"{lapse_dir}/{video_filename}.mp4"
        if Path(video_filepath).is_file():
            os.remove(video_filepath)

        lapse_fps = self._calculate_fps(photo_count)
        odd_frames = 1
        if self._limit_fps and lapse_fps > self._target_fps:
            odd_frames = math.ceil(lapse_fps / self._target_fps)
            lapse_fps = self._target_fps

        with self._camera_lock:
            out = ffmpegcv.VideoWriter(
                video_filepath,
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
                    out.write(self._get_frame(filename))
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
            del out

        del raw_frames, img, layers, last_frame

        # Todo: some error handling?

        video_bio = BytesIO()
        video_bio.name = f"{video_filename}.mp4"
        target_video_file = f"{self._ready_dir}/{printing_filename}.mp4"
        with open(video_filepath, "rb") as fh:
            video_bio.write(fh.read())
        if self._ready_dir and os.path.isdir(self._ready_dir):
            asyncio.run_coroutine_threadsafe(info_mess.edit_text(text="Copy lapse to target ditectory"), loop).result()
            Path(target_video_file).parent.mkdir(parents=True, exist_ok=True)
            with open(target_video_file, "wb") as cpf:
                cpf.write(video_bio.getvalue())
        video_bio.seek(0)

        os.remove(f"{lapse_dir}/lapse.lock")

        os_nice(0)

        return video_bio, thumb_bio, width, height, video_filepath, gcode_name

    def cleanup(self, lapse_filename: str, force: bool = False) -> None:
        lapse_dir = f"{self._base_dir}/{lapse_filename}"
        if self._cleanup or force:
            for filename in glob.glob(f"{glob.escape(lapse_dir)}/*.{self._img_extension}"):
                os.remove(filename)
            for filename in glob.glob(f"{glob.escape(lapse_dir)}/*.{self._raw_frame_extension}"):
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


class FFmpegCamera(Camera):

    def __init__(self, config: ConfigWrapper, klippy: Klippy, logging_handler: logging.Handler):
        super().__init__(config, klippy, logging_handler)

        self._cam_timeout: int = 5
        self.videoinfo = get_info(self._host, self._cam_timeout)
        self.cam_cam: FFmpegReader

    def _init_cam(self):
        self.cam_cam = FFmpegReaderStreamRTCustomInit(self._host, timeout=self._cam_timeout, videoinfo=self.videoinfo)


class MjpegCamera(Camera):
    def __init__(self, config: ConfigWrapper, klippy: Klippy, logging_handler: logging.Handler):
        super().__init__(config, klippy, logging_handler)
        self._img_extension = "jpeg"
        self._raw_frame_extension: str = "jpeg"
        self._host = config.camera.host
        self._host_snapshot = config.camera.host_snapshot if config.camera.host_snapshot else self._host.replace("stream", "snapshot")

        self._rotate_code_mjpeg: Image.Transpose
        if config.camera.rotate == "90_cw":
            self._rotate_code_mjpeg = Image.Transpose.ROTATE_270
        elif config.camera.rotate == "90_ccw":
            self._rotate_code_mjpeg = Image.Transpose.ROTATE_90
        elif config.camera.rotate == "180":
            self._rotate_code_mjpeg = Image.Transpose.ROTATE_180
        else:
            self._rotate_code_mjpeg = None  # type: ignore

    def _rotate_img(self, img: Image.Image) -> Image.Image:
        if self._flip_vertically or self._flip_horizontally or self._rotate_code_mjpeg:
            if self._flip_vertically:
                img = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
            if self._flip_horizontally:
                img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            if self._rotate_code_mjpeg:
                img = img.transpose(self._rotate_code_mjpeg)
        return img

    @cam_light_toggle
    def take_photo(self, ndarr: ndarray = None, force_rotate: bool = True) -> BytesIO:
        bio = BytesIO()
        os_nice(15)
        try:
            # Todo: speedup coonections?
            response = httpx.get(f"{self._host_snapshot}", timeout=5, verify=False)

            os_nice(15)
            if response.is_success and response.headers["Content-Type"] == "image/jpeg":

                if force_rotate:
                    img = self._rotate_img(Image.open(BytesIO(response.content)).convert("RGB"))
                    img.save(bio, format="JPEG")
                    img.close()
                    del img
                else:
                    bio.write(response.content)
            else:
                response.raise_for_status()
        except HTTPError as err:
            logger.error("Streamer snapshot get failed\n%s", err)
            if force_rotate:
                with Image.open("../imgs/nosignal.png").convert("RGB") as img:
                    img.save(bio, format="JPEG")

        os_nice(0)
        bio.seek(0)
        return bio

    def take_lapse_photo(self, gcode: str = "") -> None:
        logger.debug("Take_lapse_photo called with gcode `%s`", gcode)
        # Todo: check for space available?
        Path(self.lapse_dir).mkdir(parents=True, exist_ok=True)
        with self.take_photo(force_rotate=False) as photo:
            if gcode:
                try:
                    self._klippy.execute_gcode_script_sync(gcode.strip())
                except Exception as ex:
                    logger.error(ex)

            if photo.getbuffer().nbytes > 0:
                filename = f"{self.lapse_dir}/{time.time()}.{self._img_extension}"
                with open(filename, "wb") as outfile:
                    outfile.write(photo.getvalue())
            else:
                self._lapse_missed_frames += 1

    def _image_to_frame(self, image_bio: BytesIO):
        image_bio.seek(0)
        img = self._rotate_img(Image.open(image_bio))
        res = numpy.array(img)
        img.close()
        del img
        return res[:, :, [2, 1, 0]].copy()

    # Todo: apply frames rotation during ffmpeg call!
    def _get_frame(self, path: str):
        with open(path, "rb") as image_file:
            buff = BytesIO(image_file.read())
            res = self._image_to_frame(buff)
            buff.close()
            return res

    @cam_light_toggle
    def take_video(self) -> Tuple[BytesIO, BytesIO, int, int]:

        with self._camera_lock:
            os_nice(15)
            frame = self._image_to_frame(self.take_photo(force_rotate=False))
            height, width, channels = frame.shape
            thumb_bio = self._create_thumb(frame)
            del frame, channels

            # Todo: maybe there is another way to get fps from a streamer
            fps_cam = 15 if self._stream_fps == 0 else self._stream_fps
            frame_time = 1.0 / fps_cam

            filepath = os.path.join("/tmp/", "video.mp4")
            frame_list = []

            t_end = time.time() + self._video_duration
            time_last_frame = time.time()
            while time.time() <= t_end:
                st_time = time.time()
                frame_loc = self.take_photo(force_rotate=False)
                logger.debug("take_video cam read  frame execution time: %s millis", (time.time() - st_time) * 1000)
                if time.time() > time_last_frame + frame_time:
                    time_last_frame = time.time()
                    if frame_loc.getbuffer().nbytes > 0:
                        frame_list.append(pickle.dumps(frame_loc))
                del frame_loc

            res_fps = len(frame_list) / self._video_duration

            logger.debug("res fps - %s", res_fps)

            out = ffmpegcv.VideoWriter(
                filepath,
                codec=self._fourcc,
                fps=res_fps,
            )

            for el in frame_list:
                loc_loc = pickle.loads(el)
                out.write(self._image_to_frame(loc_loc))
                del loc_loc

            out.release()
            del out
            os_nice(0)

            frame_list.clear()
            del frame_list

        video_bio = BytesIO()
        video_bio.name = "video.mp4"
        with open(filepath, "rb") as video_file:
            video_bio.write(video_file.read())
        os.remove(filepath)
        video_bio.seek(0)
        return video_bio, thumb_bio, width, height
