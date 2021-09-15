import configparser
import logging
import os
import pathlib
import threading
import time
import glob
from io import BytesIO
from pathlib import Path
from typing import List

from numpy import random
import cv2
from PIL import Image

from klippy import Klippy
from power_device import PowerDevice

logger = logging.getLogger(__name__)


def cam_light_toggle(func):
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
                logger.debug(f"light requests count: {self.light_requests}")

        if self.light_need_off and self.light_requests == 0:
            threading.Timer(1.5, delayed_light_off).start()

        return result

    return wrapper


class Camera:
    def __init__(self, config: configparser.ConfigParser, klippy: Klippy, light_device: PowerDevice, imgs_path: str = "", logging_handler: logging.Handler = None, debug_logging: bool = False):
        camera_host = config.get('camera', 'host', fallback=f"http://{klippy.moonraker_host}:8080/?action=stream")  # Todo: remove default host?
        self._host = int(camera_host) if str.isdigit(camera_host) else camera_host
        self.enabled: bool = 'camera' in config
        self._threads: int = config.getint('camera', 'threads', fallback=int(os.cpu_count() / 2))
        self._flipVertically: bool = config.getboolean('camera', 'flipVertically', fallback=False)
        self._flipHorizontally: bool = config.getboolean('camera', 'flipHorizontally', fallback=False)
        self._fourcc: str = config.get('camera', 'fourcc', fallback='x264')
        self._videoDuration: int = config.getint('camera', 'videoDuration', fallback=5)
        self._imgs_path: str = imgs_path
        self._klippy: Klippy = klippy
        self._base_dir: str = config.get('timelapse', 'basedir', fallback='/tmp/timelapse')  # Fixme: relative path failed! ~/timelapse
        self._ready_dir: str = config.get('timelapse', 'copy_finished_timelapse_dir', fallback='')  # Fixme: relative path failed! ~/timelapse
        self._cleanup: bool = config.getboolean('timelapse', 'cleanup', fallback=True)
        self._fps: int = config.getint('timelapse', 'target_fps', fallback=15)
        self._light_need_off: bool = False
        self._light_need_off_lock = threading.Lock()

        self.light_timeout: int = config.getint('camera', 'light_control_timeout', fallback=0)
        self.light_device: PowerDevice = light_device
        self._camera_lock = threading.Lock()
        self.light_lock = threading.Lock()
        self.light_timer_event = threading.Event()
        self.light_timer_event.set()

        self._hw_accel: bool = False

        picture_quality = config.get('camera', 'picture_quality', fallback='high')
        if picture_quality == 'low':
            self._img_extension: str = 'jpeg'
        elif picture_quality == 'high':
            self._img_extension: str = 'webp'
        else:
            self._img_extension: str = picture_quality

        self._light_requests: int = 0
        self._light_request_lock = threading.Lock()

        if self._flipVertically and self._flipHorizontally:
            self._flip = -1
        elif self._flipHorizontally:
            self._flip = 1
        elif self._flipVertically:
            self._flip = 0

        if logging_handler:
            logger.addHandler(logging_handler)
        if debug_logging:
            logger.setLevel(logging.DEBUG)
            logger.debug(cv2.getBuildInformation())
            os.environ["OPENCV_VIDEOIO_DEBUG"] = "1"
        # Fixme: deprecated! use T-API https://learnopencv.com/opencv-transparent-api/
        if cv2.ocl.haveOpenCL():
            logger.debug('OpenCL is available')
            cv2.ocl.setUseOpenCL(True)
            logger.debug(f'OpenCL in OpenCV is enabled: {cv2.ocl.useOpenCL()}')

        cv2.setNumThreads(self._threads)
        self.cam_cam = cv2.VideoCapture()
        self.cam_cam.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    @property
    def light_need_off(self) -> bool:
        with self._light_need_off_lock:
            return self._light_need_off

    @light_need_off.setter
    def light_need_off(self, new: bool):
        with self._light_need_off_lock:
            self._light_need_off = new

    @property
    def lapse_dir(self) -> str:
        return f'{self._base_dir}/{self._klippy.printing_filename_with_time}'

    @property
    def light_requests(self) -> int:
        with self._light_request_lock:
            return self._light_requests

    def use_light(self):
        with self._light_request_lock:
            self._light_requests += 1

    def free_light(self):
        with self._light_request_lock:
            self._light_requests -= 1

    @staticmethod
    def _create_thumb(image) -> BytesIO:
        img = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        bio = BytesIO()
        bio.name = 'thumb.jpeg'
        img.save(bio, 'JPEG', quality=60, subsampling=2, optimize=True)
        bio.seek(0)
        img.close()
        del img
        return bio

    @cam_light_toggle
    def take_photo(self) -> BytesIO:
        with self._camera_lock:
            self.cam_cam.open(self._host)
            self.cam_cam.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            success, image = self.cam_cam.read()

            if not success:
                logger.debug("failed to get camera frame for photo")
                img = Image.open(random.choice(glob.glob(f'{self._imgs_path}/imgs/*')))
            else:
                if self._hw_accel:
                    image_um = cv2.UMat(image)
                    if self._flipVertically or self._flipHorizontally:
                        image_um = cv2.flip(image_um, self._flip)
                    img = Image.fromarray(cv2.UMat.get(cv2.cvtColor(image_um, cv2.COLOR_BGR2RGB)))
                    image_um = None
                    del image_um
                else:
                    if self._flipVertically or self._flipHorizontally:
                        image = cv2.flip(image, self._flip)
                    img = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))

            image = None
            del image, success

        bio = BytesIO()
        bio.name = f'status.{self._img_extension}'
        if self._img_extension in ['jpg', 'jpeg']:
            img.save(bio, 'JPEG', quality=80, subsampling=0)
        elif self._img_extension == 'webp':
            img.save(bio, 'WebP', quality=0, lossless=True)
        elif self._img_extension == 'png':
            img.save(bio, 'PNG')
        bio.seek(0)

        img.close()
        del img
        return bio

    @cam_light_toggle
    def take_video(self) -> (BytesIO, BytesIO, int, int):
        def process_video_frame(frame_local):
            if self._flipVertically or self._flipHorizontally:
                if self._hw_accel:
                    frame_loc_ = cv2.UMat(frame_local)
                    frame_loc_ = cv2.flip(frame_loc_, self._flip)
                    frame_local = cv2.UMat.get(frame_loc_)
                    del frame_loc_
                else:
                    frame_local = cv2.flip(frame_local, self._flip)
            return frame_local

        with self._camera_lock:
            cv2.setNumThreads(self._threads)  # TOdo: check self set and remove!
            self.cam_cam.open(self._host)
            self.cam_cam.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            success, frame = self.cam_cam.read()

            if not success:
                logger.debug("failed to get camera frame for video")
                # Todo: get picture from imgs?

            height, width, channels = frame.shape
            thumb_bio = self._create_thumb(process_video_frame(frame))
            del frame, channels
            fps_cam = self.cam_cam.get(cv2.CAP_PROP_FPS)
            fps = 10
            filepath = os.path.join('/tmp/', 'video.mp4')
            out = cv2.VideoWriter(filepath, fourcc=cv2.VideoWriter_fourcc(*self._fourcc), fps=fps_cam, frameSize=(width, height))
            t_end = time.time() + self._videoDuration
            while success and time.time() < t_end:
                prev_frame_time = time.time()
                success, frame_loc = self.cam_cam.read()
                out.write(process_video_frame(frame_loc))
                frame_loc = None
                del frame_loc
                fps = 1 / (time.time() - prev_frame_time)

            logger.debug(f"Measured video fps is {fps}, while camera fps {fps_cam}")
            out.set(cv2.CAP_PROP_FPS, fps)
            out.release()

        video_bio = BytesIO()
        video_bio.name = 'video.mp4'
        with open(filepath, 'rb') as fh:
            video_bio.write(fh.read())
        os.remove(filepath)
        video_bio.seek(0)
        return video_bio, thumb_bio, width, height

    def take_lapse_photo(self) -> None:
        # Todo: check for space available?
        Path(self.lapse_dir).mkdir(parents=True, exist_ok=True)
        # never add self in params there!
        with self.take_photo() as photo:
            filename = f'{self.lapse_dir}/{time.time()}.{self._img_extension}'
            with open(filename, "wb") as outfile:
                outfile.write(photo.getvalue())

    def create_timelapse(self) -> (BytesIO, BytesIO, int, int, str):
        return self._create_timelapse(self.lapse_dir, self._klippy.printing_filename_with_time)

    def create_timelapse_for_file(self, filename: str) -> (BytesIO, BytesIO, int, int, str):
        return self._create_timelapse(f'{self._base_dir}/{filename}', filename)

    def _create_timelapse(self, lapse_dir: str, printing_filename: str) -> (BytesIO, BytesIO, int, int, str):
        while self.light_need_off:
            time.sleep(1)

        if not Path(f'{lapse_dir}/lapse.lock').is_file():
            os.mknod(f'{lapse_dir}/lapse.lock')  # Fixme: fail on windows hosts!

        # Todo: check for nonempty photos!
        photos = glob.glob(f'{glob.escape(lapse_dir)}/*.{self._img_extension}')
        photos.sort(key=os.path.getmtime)

        filename = photos[-1]
        img = cv2.imread(filename)
        height, width, layers = img.shape
        thumb_bio = self._create_thumb(img)

        video_filepath = f'{lapse_dir}/{printing_filename}.mp4'
        if Path(video_filepath).is_file():
            os.remove(video_filepath)

        with self._camera_lock:
            cv2.setNumThreads(self._threads)  # TOdo: check self set and remove!
            out = cv2.VideoWriter(video_filepath, fourcc=cv2.VideoWriter_fourcc(*self._fourcc), fps=self._fps, frameSize=(width, height))

            for filename in photos:
                out.write(cv2.imread(filename))

            out.release()
            cv2.destroyAllWindows()
            del out

        del photos, img, layers

        # Todo: some error handling?

        video_bio = BytesIO()
        video_bio.name = f'{printing_filename}.mp4'
        with open(video_filepath, 'rb') as fh:
            video_bio.write(fh.read())
        if self._ready_dir and os.path.isdir(self._ready_dir):
            with open(f"{self._ready_dir}/{printing_filename}.mp4", 'wb') as cpf:
                cpf.write(video_bio.getvalue())
        video_bio.seek(0)

        os.remove(f'{lapse_dir}/lapse.lock')

        if self._cleanup:
            for filename in glob.glob(f'{glob.escape(lapse_dir)}/*'):
                os.remove(filename)
            Path(lapse_dir).rmdir()

        return video_bio, thumb_bio, width, height, video_filepath

    def clean(self) -> None:
        if self._cleanup and self._klippy.printing_filename and os.path.isdir(self.lapse_dir):
            for filename in glob.glob(f'{glob.escape(self.lapse_dir)}/*'):
                os.remove(filename)

    def detect_unfinished_lapses(self) -> List[str]:
        # Todo: detect unstarted timelapse builds? folder with pics and no mp4 files
        return list(map(lambda el: pathlib.PurePath(el).parent.name, glob.glob(f'{self._base_dir}/*/*.lock')))
