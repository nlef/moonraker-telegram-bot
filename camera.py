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
            threading.Timer(.5, delayed_light_off).start()

        return result

    return wrapper


class Camera:
    def __init__(self, klippy: Klippy, camera_enabled: bool, camera_host: str, light_device: PowerDevice, threads: int = 0, light_timeout: int = 0, flip_vertically: bool = False,
                 flip_horizontally: bool = False, fourcc: str = 'x264', gif_duration: int = 5, reduce_gif: int = 2, video_duration: int = 10, imgs: str = "", timelapse_base_dir: str = "",
                 copy_finished_timelapse_dir: str = "", timelapse_cleanup: bool = False, timelapse_fps: int = 10, logging_handler: logging.Handler = None, debug_logging: bool = False,
                 picture_quality: str = 'low'):
        self._host: str = camera_host
        self.enabled: bool = camera_enabled
        self._threads: int = threads
        self._flipVertically: bool = flip_vertically
        self._flipHorizontally: bool = flip_horizontally
        self._fourcc: str = fourcc
        self._gifDuration: int = gif_duration
        self._reduceGif: int = reduce_gif
        self._videoDuration: int = video_duration
        self._imgs: str = imgs
        self._klippy: Klippy = klippy
        self._base_dir: str = timelapse_base_dir  # Fixme: relative path failed! ~/timelapse
        self._ready_dir: str = copy_finished_timelapse_dir  # Fixme: relative path failed! ~/timelapse
        self._cleanup: bool = timelapse_cleanup
        self._fps: int = timelapse_fps
        self._light_need_off: bool = False
        self._light_need_off_lock = threading.Lock()

        self.light_timeout: int = light_timeout
        self.light_device: PowerDevice = light_device
        self._camera_lock = threading.Lock()
        self.light_lock = threading.Lock()
        self.light_timer_event = threading.Event()
        self.light_timer_event.set()

        self._hw_accel: bool = False
        if picture_quality == 'low':
            self._img_extension: str = 'jpeg'
        elif picture_quality == 'high':
            self._img_extension: str = 'png'
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
        # Fixme: deprecated! use T-API https://learnopencv.com/opencv-transparent-api/
        if cv2.ocl.haveOpenCL():
            logger.debug('OpenCL is available')
            cv2.ocl.setUseOpenCL(True)
            logger.debug(f'OpenCL in OpenCV is enabled: {cv2.ocl.useOpenCL()}')

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
    def _create_thumb(image):
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
            cap = cv2.VideoCapture(int(self._host)) if str.isdigit(self._host) else cv2.VideoCapture(self._host)

            success, image = cap.read()

            if not success:
                logger.debug("failed to get camera frame for photo")
                img = Image.open(random.choice(glob.glob(f'{self._imgs}/imgs/*')))
            else:
                if self._flipVertically or self._flipHorizontally:
                    if self._hw_accel:
                        image_um = cv2.UMat(image)
                        image_um = cv2.flip(image_um, self._flip)
                        image = cv2.UMat.get(cv2.cvtColor(image_um, cv2.COLOR_BGR2RGB))
                        del image_um
                    else:
                        image = cv2.flip(image, self._flip)
                        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
                else:  # Todo: add hw accel?
                    image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

                img = Image.fromarray(image)

            cap.release()
            cv2.destroyAllWindows()
            del image, cap

        bio = BytesIO()
        bio.name = f'status.{self._img_extension}'
        # Todo: some quality params?
        if self._img_extension in ['jpg', 'jpeg']:
            img.save(bio, 'JPEG', quality=80, subsampling=0)
        elif self._img_extension == 'png':
            img.save(bio, 'PNG')
        elif self._img_extension == 'webp':
            img.save(bio, 'WebP', quality=0, lossless=True)
        bio.seek(0)
        img.close()
        del img
        return bio

    @cam_light_toggle
    def take_video(self):
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
            cv2.setNumThreads(self._threads)
            cap = cv2.VideoCapture(int(self._host)) if str.isdigit(self._host) else cv2.VideoCapture(self._host)
            success, frame = cap.read()
            if not success:
                logger.debug("failed to get camera frame for video")
                # Todo: get picture from imgs?

            height, width, channels = frame.shape
            thumb_bio = self._create_thumb(process_video_frame(frame))
            del frame, channels
            fps_cam = cap.get(cv2.CAP_PROP_FPS)
            fps = 10
            filepath = os.path.join('/tmp/', 'video.mp4')
            out = cv2.VideoWriter(filepath, fourcc=cv2.VideoWriter_fourcc(*self._fourcc), fps=fps_cam, frameSize=(width, height))
            t_end = time.time() + self._videoDuration
            while success and time.time() < t_end:
                prev_frame_time = time.time()
                success, frame_loc = cap.read()
                out.write(process_video_frame(frame_loc))
                del frame_loc
                fps = 1 / (time.time() - prev_frame_time)

            logger.debug(f"Measured video fps is {fps}, while camera fps {fps_cam}")
            out.set(cv2.CAP_PROP_FPS, fps)
            out.release()
            cap.release()
            cv2.destroyAllWindows()
            del out, cap

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
        photo = self.take_photo()
        filename = f'{self.lapse_dir}/{time.time()}.{self._img_extension}'
        with open(filename, "wb") as outfile:
            outfile.write(photo.getbuffer())
        photo.close()

    def create_timelapse(self):
        return self._create_timelapse(self.lapse_dir, self._klippy.printing_filename_with_time)

    def create_timelapse_for_file(self, filename: str):
        return self._create_timelapse(f'{self._base_dir}/{filename}', filename)

    def _create_timelapse(self, lapse_dir: str, printing_filename: str):
        while self.light_need_off:
            time.sleep(1)

        if not Path(f'{lapse_dir}/lapse.lock').is_file():
            os.mknod(f'{lapse_dir}/lapse.lock')  # Fixme: fail on windows hosts!

        # Todo: check for nonempty photos!
        photos = glob.glob(f'{lapse_dir}/*.{self._img_extension}')
        photos.sort(key=os.path.getmtime)

        filename = photos[-1]
        img = cv2.imread(filename)
        height, width, layers = img.shape
        thumb_bio = self._create_thumb(img)

        video_filepath = f'{lapse_dir}/{printing_filename}.mp4'
        if Path(video_filepath).is_file():
            os.remove(video_filepath)

        with self._camera_lock:
            cv2.setNumThreads(self._threads)
            out = cv2.VideoWriter(video_filepath, fourcc=cv2.VideoWriter_fourcc(*self._fourcc), fps=self._fps, frameSize=(width, height))

            for filename in photos:
                out.write(cv2.imread(filename))

            out.release()
            cv2.destroyAllWindows()
            del out

        del photos, img, layers

        video_bio = BytesIO()
        video_bio.name = f'{printing_filename}.mp4'
        with open(video_filepath, 'rb') as fh:
            video_bio.write(fh.read())
            # Fixme: move to method with error handling!
            if self._ready_dir and os.path.isdir(self._ready_dir):
                with open(f"{self._ready_dir}/{printing_filename}.mp4", 'wb') as cpf:
                    cpf.write(video_bio.getbuffer())
        video_bio.seek(0)

        os.remove(f'{lapse_dir}/lapse.lock')

        if self._cleanup:
            for filename in glob.glob(f'{lapse_dir}/*'):
                os.remove(filename)
            Path(lapse_dir).rmdir()

        return video_bio, thumb_bio, width, height, video_filepath

    def clean(self) -> None:
        if self._cleanup and self._klippy.printing_filename:
            if os.path.isdir(self.lapse_dir):
                for filename in glob.glob(f'{self.lapse_dir}/*'):
                    os.remove(filename)

    def detect_unfinished_lapses(self) -> List[str]:
        # Todo: detect unstarted timelapse builds? folder with pics and no mp4 files
        return list(map(lambda el: pathlib.PurePath(el).parent.name, glob.glob(f'{self._base_dir}/*/*.lock')))
