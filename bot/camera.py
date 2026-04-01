"""Camera backends for photo/video capture and timelapse frame storage."""

from __future__ import annotations

import abc
import contextlib
from functools import wraps
from io import BytesIO
import logging
import os
from pathlib import Path
import pickle
import subprocess
import tempfile
import threading
import time
from typing import TYPE_CHECKING, Any, Callable, ClassVar, TypeVar, cast

from assets.ffmpegcv_custom import FFmpegReaderStreamRTCustomInit
import ffmpegcv  # type: ignore[import-untyped]
from ffmpegcv import FFmpegReader
from ffmpegcv.stream_info import get_info  # type: ignore[import-untyped]
import httpx
from httpx import HTTPError
import numpy as np
from PIL import Image

if TYPE_CHECKING:
    from numpy.typing import NDArray

    from configuration import ConfigWrapper
    from klippy import Klippy, PowerDevice

try:
    import cv2
except ImportError:
    cv2 = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)


F = TypeVar("F", bound=Callable[..., Any])


def cam_light_toggle(func: F) -> F:
    @wraps(func)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        self.use_light()

        if self.light_timeout > 0 and self.light_device and not self.light_device.device_state and not self.light_lock.locked():
            self.light_timer_event.clear()
            self.light_lock.acquire()
            self.light_need_off = True
            self.light_device.turn_on_sync()
            time.sleep(self.light_timeout)
            self.light_timer_event.set()

        self.light_timer_event.wait()

        try:
            result = func(self, *args, **kwargs)
        finally:
            self.free_light()

            def delayed_light_off() -> None:
                if self.light_requests == 0:
                    if self.light_lock.locked():
                        self.light_lock.release()
                    self.light_need_off = False
                    self.light_device.turn_off_sync()
                else:
                    logger.debug("light requests count: %s", self.light_requests)

            if self.light_need_off and self.light_requests == 0:
                threading.Timer(self.light_timeout, delayed_light_off).start()

        return result

    return wrapper  # type: ignore[return-value]


def os_nice(value: int) -> None:
    with contextlib.suppress(Exception):
        os.nice(value)


def _encode_frames(
    frame_list: list[bytes],
    filepath: Path,
    fourcc: str,
    duration: float,
    transform: Callable[[Any], NDArray[Any]],
) -> None:
    res_fps = len(frame_list) / duration
    logger.debug("res fps - %s", res_fps)
    out = ffmpegcv.VideoWriter(filepath.as_posix(), codec=fourcc, fps=res_fps)
    for el in frame_list:
        frame = pickle.loads(el)
        out.write(transform(frame))
        del frame
    out.release()
    del out
    frame_list.clear()


def _read_and_cleanup_video(filepath: Path) -> BytesIO:
    video_bio = BytesIO()
    video_bio.name = "video.mp4"
    if filepath.is_file():
        with filepath.open("rb") as f:
            video_bio.write(f.read())
        filepath.unlink()
    video_bio.seek(0)
    return video_bio


def create_thumb(image: NDArray[Any]) -> tuple[BytesIO, int, int]:
    height, width = image.shape[:2]
    img = Image.fromarray(image[:, :, [2, 1, 0]])
    bio = BytesIO()
    bio.name = "thumbnail.jpeg"
    img.thumbnail((320, 320))
    img.save(bio, "JPEG", quality=100, optimize=True)
    bio.seek(0)
    img.close()
    del img
    return bio, height, width


class Camera(abc.ABC):
    """Abstract base for all camera backends."""

    def __init__(self, config: ConfigWrapper, klippy: Klippy, logging_handler: logging.Handler) -> None:
        self.enabled: bool = bool(config.camera.enabled and config.camera.host)
        self._host: str = config.camera.host
        self._flip_vertically: bool = config.camera.flip_vertically
        self._flip_horizontally: bool = config.camera.flip_horizontally
        self._fourcc: str = config.camera.fourcc
        self._video_duration: int = config.camera.video_duration
        self._stream_fps: int = config.camera.stream_fps
        self._klippy: Klippy = klippy

        self._light_need_off: bool = False
        self._light_need_off_lock: threading.Lock = threading.Lock()

        self.light_timeout: int = config.camera.light_timeout
        self.light_device: PowerDevice | None = self._klippy.light_device
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

        self._light_requests: int = 0
        self._light_request_lock: threading.Lock = threading.Lock()

        self._rotation_count: int | None
        if config.camera.rotate == "90_cw":
            self._rotation_count = 1
        elif config.camera.rotate == "180":
            self._rotation_count = 2
        elif config.camera.rotate == "90_ccw":
            self._rotation_count = 3
        else:
            self._rotation_count = None

        if logging_handler:
            logger.addHandler(logging_handler)
        if config.bot_config.debug:
            logger.setLevel(logging.DEBUG)

    @abc.abstractmethod
    def take_photo(self) -> BytesIO: ...

    @abc.abstractmethod
    def take_video(self) -> tuple[BytesIO, BytesIO, int, int]: ...

    @abc.abstractmethod
    def take_lapse_photo(self, lapse_dir: Path) -> bool: ...

    @property
    @abc.abstractmethod
    def raw_frame_extension(self) -> str: ...

    @abc.abstractmethod
    def get_frame(self, path: Path) -> NDArray[Any]: ...

    @property
    def light_need_off(self) -> bool:
        with self._light_need_off_lock:
            return self._light_need_off

    @light_need_off.setter
    def light_need_off(self, new_value: bool) -> None:
        with self._light_need_off_lock:
            self._light_need_off = new_value

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


class NumpyCamera(Camera):
    """Camera backend using numpy arrays for frame processing. Base for OpenCV and FFmpeg cameras."""

    def __init__(self, config: ConfigWrapper, klippy: Klippy, logging_handler: logging.Handler) -> None:
        super().__init__(config, klippy, logging_handler)
        self._save_lapse_photos_as_images: bool = config.timelapse.save_lapse_photos_as_images

    @property
    def raw_frame_extension(self) -> str:
        return "npz"

    def get_frame(self, path: Path) -> NDArray[Any]:
        return cast("NDArray[Any]", np.load(path, allow_pickle=True)["raw"])

    @abc.abstractmethod
    def _open_capture(self) -> None: ...

    @abc.abstractmethod
    def _read_frame(self) -> tuple[bool, Any]: ...

    @abc.abstractmethod
    def _release_capture(self) -> None: ...

    @abc.abstractmethod
    def _get_capture_fps(self) -> float: ...

    def _transform_frame(self, frame: NDArray[Any]) -> NDArray[Any]:
        if self._flip_vertically:
            frame = np.flipud(frame)
        if self._flip_horizontally:
            frame = np.fliplr(frame)
        if self._rotation_count is not None:
            frame = np.rot90(frame, k=self._rotation_count, axes=(1, 0))
        return frame

    @cam_light_toggle
    def _take_raw_frame(self, rgb: bool = True) -> NDArray[Any]:
        with self._camera_lock:
            st_time = time.time()
            self._open_capture()
            success, image = self._read_frame()
            self._release_capture()
            logger.debug("_take_raw_frame cam read execution time: %s millis", (time.time() - st_time) * 1000)

            if not success:
                logger.debug("failed to get camera frame for photo")
                if rgb:
                    img = Image.open("../imgs/nosignal.png")
                    image = np.array(img)
                    img.close()
                    del img
                else:
                    return cast("NDArray[Any]", np.empty(0))
            else:
                image = self._transform_frame(image)

            ndaarr = image[:, :, [2, 1, 0]].copy() if rgb else image.copy()
            image = None
            del image, success

        return cast("NDArray[Any]", ndaarr)

    def _encode_image(self, ndarr: NDArray[Any]) -> BytesIO:
        img = Image.fromarray(ndarr)
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
        elif self._img_extension == "webp":
            img.save(bio, "WebP", quality=0, lossless=True)
        elif self._img_extension == "png":
            img.save(bio, "PNG")
        bio.seek(0)
        img.close()
        os_nice(0)
        del img
        return bio

    def take_photo(self) -> BytesIO:
        return self._encode_image(self._take_raw_frame())

    @cam_light_toggle
    def take_video(self) -> tuple[BytesIO, BytesIO, int, int]:
        with self._camera_lock:
            os_nice(15)
            st_time = time.time()
            self._open_capture()
            success, frame = self._read_frame()
            logger.debug("take_video cam read first frame execution time: %s millis", (time.time() - st_time) * 1000)

            if not success:
                logger.debug("failed to get camera frame for video")
                # TODO: get picture from imgs?

            frame = self._transform_frame(frame)
            thumb_bio, height, width = create_thumb(frame)
            del frame

            fps_cam = self._get_capture_fps() if self._stream_fps == 0 else self._stream_fps
            frame_time = 1.0 / fps_cam

            fd, tmp = tempfile.mkstemp(prefix="mtb_video_", suffix=".mp4")
            os.close(fd)
            filepath = Path(tmp)
            frame_list: list[bytes] = []

            t_end = time.time() + self._video_duration
            time_last_frame = time.time()
            while success and time.time() <= t_end:
                st_time = time.time()
                success, frame_loc = self._read_frame()
                logger.debug("take_video cam read  frame execution time: %s millis", (time.time() - st_time) * 1000)
                if time.time() > time_last_frame + frame_time:
                    time_last_frame = time.time()
                    if success:
                        frame_list.append(pickle.dumps(frame_loc))
                del frame_loc

            self._release_capture()
            _encode_frames(frame_list, filepath, self._fourcc, self._video_duration, self._transform_frame)
            os_nice(0)

        return _read_and_cleanup_video(filepath), thumb_bio, width, height

    def take_lapse_photo(self, lapse_dir: Path) -> bool:
        logger.debug("Take_lapse_photo called")
        # TODO: check for space available?
        lapse_dir.mkdir(parents=True, exist_ok=True)
        raw_frame = self._take_raw_frame(rgb=False)

        if raw_frame.size == 0:
            return False

        os_nice(15)
        np.savez_compressed(lapse_dir / str(time.time()), raw=raw_frame)

        if self._save_lapse_photos_as_images:
            raw_frame_rgb = raw_frame[:, :, [2, 1, 0]].copy()
            del raw_frame
            with self._encode_image(raw_frame_rgb) as photo:
                filename = lapse_dir / f"{time.time()}.{self._img_extension}"
                with filename.open("wb") as outfile:
                    outfile.write(photo.getvalue())
            del raw_frame_rgb
        else:
            del raw_frame

        os_nice(0)
        return True


class OpenCVCamera(NumpyCamera):
    """Camera backend using OpenCV VideoCapture for local devices and RTSP streams."""

    # TODO: [fixme] deprecated! use T-API https://learnopencv.com/opencv-transparent-api/

    def __init__(self, config: ConfigWrapper, klippy: Klippy, logging_handler: logging.Handler) -> None:
        super().__init__(config, klippy, logging_handler)

        if not cv2:
            logger.warning("OpenCV not available, camera disabled")
            self.enabled = False
            return

        self._threads: int = config.camera.threads

        if config.bot_config.debug:
            logger.debug(cv2.getBuildInformation())
            os.environ["OPENCV_VIDEOIO_DEBUG"] = "1"
        if cv2.ocl.haveOpenCL():
            logger.debug("OpenCL is available")
            cv2.ocl.setUseOpenCL(True)
            logger.debug("OpenCL in OpenCV is enabled: %s", cv2.ocl.useOpenCL())

        self._cv2_params: list[Any] = []
        cv2.setNumThreads(self._threads)
        self._capture = cv2.VideoCapture()
        self._set_cv2_params()

    @staticmethod
    def _isfloat(value: str) -> bool:
        try:
            float(value)
        except ValueError:
            return False
        else:
            return True

    def _set_cv2_params(self) -> None:
        self._capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        for prop_name, value in self._cv2_params:
            if prop_name.upper() == "CAP_PROP_FOURCC":
                try:
                    prop = getattr(cv2, prop_name.upper())
                    self._capture.set(prop, cv2.VideoWriter_fourcc(*value))  # type: ignore[attr-defined]
                except AttributeError:
                    logger.exception("Failed to set fourcc for camera %s", prop_name)
            else:
                val: Any
                if value.isnumeric():
                    val = int(value)
                elif self._isfloat(value):
                    val = float(value)
                else:
                    val = value
                try:
                    prop = getattr(cv2, prop_name.upper())
                    self._capture.set(prop, val)
                except AttributeError:
                    logger.exception("Failed to set fourcc for camera %s", prop_name)

    def _open_capture(self) -> None:
        device = int(self._host) if self._host.isdigit() else self._host
        self._capture.open(device)
        self._set_cv2_params()
        cv2.setNumThreads(self._threads)

    def _read_frame(self) -> tuple[bool, Any]:
        return self._capture.read()

    def _release_capture(self) -> None:
        self._capture.release()

    def _get_capture_fps(self) -> float:
        return self._capture.get(cv2.CAP_PROP_FPS)


class FFmpegCamera(NumpyCamera):
    """Camera backend using FFmpeg for RTSP/stream capture."""

    def __init__(self, config: ConfigWrapper, klippy: Klippy, logging_handler: logging.Handler) -> None:
        super().__init__(config, klippy, logging_handler)

        self._cam_timeout: int = 5
        self._videoinfo = get_info(self._host, self._cam_timeout)
        self._capture: FFmpegReader | None = None

    def _open_capture(self) -> None:
        self._capture = FFmpegReaderStreamRTCustomInit(self._host, timeout=self._cam_timeout, videoinfo=self._videoinfo)

    def _read_frame(self) -> tuple[bool, Any]:
        if self._capture is None:
            return False, None
        return cast("tuple[bool, Any]", self._capture.read())

    def _release_capture(self) -> None:
        if self._capture is not None:
            self._capture.release()

    def _get_capture_fps(self) -> float:
        return cast("float", self._videoinfo.fps)


class MjpegCamera(Camera):
    """Camera backend using MJPEG snapshot/stream URLs."""

    _ROTATION_TO_TRANSPOSE: ClassVar[dict[int, Image.Transpose]] = {
        1: Image.Transpose.ROTATE_270,
        2: Image.Transpose.ROTATE_180,
        3: Image.Transpose.ROTATE_90,
    }

    def __init__(self, config: ConfigWrapper, klippy: Klippy, logging_handler: logging.Handler) -> None:
        super().__init__(config, klippy, logging_handler)
        self._img_extension = "jpeg"
        self._host = config.camera.host
        self._host_snapshot = config.camera.host_snapshot or self._host.replace("stream", "snapshot")
        self._http = httpx.Client(timeout=5, verify=False)

    @property
    def raw_frame_extension(self) -> str:
        return "jpeg"

    def _rotate_img(self, img: Image.Image) -> Image.Image:
        if self._flip_vertically:
            img = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        if self._flip_horizontally:
            img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        if self._rotation_count is not None:
            img = img.transpose(self._ROTATION_TO_TRANSPOSE[self._rotation_count])
        return img

    def _fetch_raw_snapshot(self) -> BytesIO:
        bio = BytesIO()
        os_nice(15)
        try:
            response = self._http.get(self._host_snapshot)
            os_nice(15)
            if response.is_success and response.headers["Content-Type"] == "image/jpeg":
                bio.write(response.content)
            else:
                response.raise_for_status()
        except HTTPError:
            logger.exception("Streamer snapshot get failed\n%s")
        os_nice(0)
        bio.seek(0)
        return bio

    @cam_light_toggle
    def take_photo(self) -> BytesIO:
        raw = self._fetch_raw_snapshot()
        bio = BytesIO()
        if raw.getbuffer().nbytes > 0:
            img = self._rotate_img(Image.open(raw).convert("RGB"))
            img.save(bio, format="JPEG")
            img.close()
            del img
        else:
            with Image.open("../imgs/nosignal.png").convert("RGB") as img:
                img.save(bio, format="JPEG")
        raw.close()
        bio.seek(0)
        return bio

    @cam_light_toggle
    def take_lapse_photo(self, lapse_dir: Path) -> bool:
        logger.debug("Take_lapse_photo called")
        # TODO: check for space available?
        lapse_dir.mkdir(parents=True, exist_ok=True)
        with self._fetch_raw_snapshot() as photo:
            if photo.getbuffer().nbytes > 0:
                filename = lapse_dir / f"{time.time()}.{self._img_extension}"
                with filename.open("wb") as outfile:
                    outfile.write(photo.getvalue())
                return True
            return False

    def _image_to_frame(self, image_bio: BytesIO) -> NDArray[Any]:
        image_bio.seek(0)
        img = self._rotate_img(Image.open(image_bio))
        res = np.array(img)
        img.close()
        del img
        return cast("NDArray[Any]", res[:, :, [2, 1, 0]].copy())

    # TODO: apply frames rotation during ffmpeg call!
    def get_frame(self, path: Path) -> NDArray[Any]:
        with path.open("rb") as image_file:
            buff = BytesIO(image_file.read())
            res = self._image_to_frame(buff)
            buff.close()
            return res

    @cam_light_toggle
    def take_video(self) -> tuple[BytesIO, BytesIO, int, int]:

        with self._camera_lock:
            os_nice(15)
            frame = self._image_to_frame(self._fetch_raw_snapshot())
            thumb_bio, height, width = create_thumb(frame)
            del frame

            # TODO: maybe there is another way to get fps from a streamer
            fps_cam = 15 if self._stream_fps == 0 else self._stream_fps
            frame_time = 1.0 / fps_cam

            fd, tmp = tempfile.mkstemp(prefix="mtb_video_", suffix=".mp4")
            os.close(fd)
            filepath = Path(tmp)
            frame_list: list[bytes] = []

            t_end = time.time() + self._video_duration
            time_last_frame = time.time()
            while time.time() <= t_end:
                st_time = time.time()
                frame_loc = self._fetch_raw_snapshot()
                logger.debug("take_video cam read  frame execution time: %s millis", (time.time() - st_time) * 1000)
                if time.time() > time_last_frame + frame_time:
                    time_last_frame = time.time()
                    if frame_loc.getbuffer().nbytes > 0:
                        frame_list.append(pickle.dumps(frame_loc))
                del frame_loc

            _encode_frames(frame_list, filepath, self._fourcc, self._video_duration, self._image_to_frame)
            os_nice(0)

        return _read_and_cleanup_video(filepath), thumb_bio, width, height


class RawStreamCamera(MjpegCamera):
    """Camera backend for direct H.264/snapshot passthrough without re-encoding."""

    def __init__(self, config: ConfigWrapper, klippy: Klippy, logging_handler: logging.Handler) -> None:
        super().__init__(config, klippy, logging_handler)

        if self._flip_vertically or self._flip_horizontally or self._rotation_count is not None:
            logger.warning("raw_stream camera: flip/rotate not supported for video (stream copy). Use type=ffmpeg if you need video transforms.")

    @cam_light_toggle
    def take_video(self) -> tuple[BytesIO, BytesIO, int, int]:
        with self._camera_lock:
            os_nice(15)

            thumb_frame = self._image_to_frame(self._fetch_raw_snapshot())
            thumb_bio, height, width = create_thumb(thumb_frame)
            del thumb_frame

            fd, tmp = tempfile.mkstemp(prefix="mtb_video_", suffix=".mp4")
            os.close(fd)
            filepath = Path(tmp)
            host = str(self._host)

            cmd = ["ffmpeg", "-y"]
            if host.startswith("rtsp://"):
                cmd.extend(["-rtsp_transport", "tcp"])
            cmd.extend(["-i", host, "-t", str(self._video_duration), "-c:v", "copy", "-an", "-avoid_negative_ts", "make_zero", filepath.as_posix()])

            logger.debug("RawStreamCamera ffmpeg cmd: %s", " ".join(cmd))

            try:
                result = subprocess.run(cmd, capture_output=True, timeout=self._video_duration + 30, check=False)
                if result.returncode != 0:
                    logger.error("ffmpeg stream copy failed (rc=%d): %s", result.returncode, result.stderr.decode("utf-8", errors="replace"))
            except subprocess.TimeoutExpired:
                logger.exception("ffmpeg stream copy timed out after %d seconds", self._video_duration + 30)

            os_nice(0)

        return _read_and_cleanup_video(filepath), thumb_bio, width, height
