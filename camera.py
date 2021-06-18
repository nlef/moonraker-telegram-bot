import logging
import os
import threading
import time
import glob
from io import BytesIO
from pathlib import Path

import requests
from numpy import random
import cv2
from PIL import Image

from klippy import Klippy

logger = logging.getLogger(__name__)


def cam_ligth_toogle(func):
    def wrapper(self, *args, **kwargs):
        if self.light_timeout > 0 and self.light_device and not self.light_state and not self.light_lock.locked():
            self.light_timer_event.clear()
            self.light_lock.acquire()
            self.light_need_off = True
            self.switch_ligth_device(True)
            time.sleep(self.light_timeout)
            self.light_timer_event.set()

        self.light_timer_event.wait()

        result = func(self, *args, **kwargs)

        if self.light_need_off:
            if self.light_lock.locked():
                self.light_lock.release()
            if not self.camera_lock.locked() and not self.light_lock.locked():
                self.light_need_off = False
                self.switch_ligth_device(False)

        return result

    return wrapper


class Camera:
    def __init__(self, moonraker_host: str, klippy: Klippy, camera_enabled: bool, camera_host: str, threads: int = 0, light_device: str = "",
                 light_timeout: int = 0, flip_vertically: bool = False, flip_horisontally: bool = False, fourcc: str = 'x264', gif_duration: int = 5, reduce_gif: int = 2,
                 video_duration: int = 10, imgs: str = "", timelapse_base_dir: str = "", timelapse_cleanup: bool = False, timelapse_fps: int = 10, debug_logging: bool = False,
                 picture_quality: str = 'low'):
        self._host: str = camera_host
        self.enabled: bool = camera_enabled
        self._threads: int = threads
        self._flipVertically: bool = flip_vertically
        self._flipHorisontally: bool = flip_horisontally
        self._fourcc: str = fourcc
        self._gifDuration: int = gif_duration
        self._reduceGif: int = reduce_gif
        self._videoDuration: int = video_duration
        self._imgs: str = imgs
        self._moonraker_host: str = moonraker_host
        self._klippy: Klippy = klippy
        self._light_state_lock = threading.Lock()
        self._light_device_on: bool = False
        self._base_dir: str = timelapse_base_dir
        self._cleanup: bool = timelapse_cleanup
        self._fps: int = timelapse_fps
        self._light_need_off: bool = False
        self._light_need_off_lock = threading.Lock()

        self.light_timeout: int = light_timeout
        # Todo: make class for power device
        self.light_device: str = light_device
        self.camera_lock = threading.Lock()
        self.light_lock = threading.Lock()
        self.light_timer_event = threading.Event()
        self.light_timer_event.set()
        if debug_logging:
            logger.setLevel(logging.DEBUG)
        if picture_quality == 'low':
            self._img_extension: str = 'jpeg'
        elif picture_quality == 'high':
            self._img_extension: str = 'png'
        else:
            self._img_extension: str = picture_quality

        # Fixme: deprecated! use T-API https://learnopencv.com/opencv-transparent-api/
        if cv2.ocl.haveOpenCL():
            logger.debug('OpenCL is available')
            cv2.ocl.setUseOpenCL(True)
            logger.debug(f'OpenCL in OpenCV is enabled: {cv2.ocl.useOpenCL()}')

    @property
    def light_state(self) -> bool:
        with self._light_state_lock:
            return self._light_device_on

    @light_state.setter
    def light_state(self, state: bool):
        with self._light_state_lock:
            self._light_device_on = state

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
        return f'{self._base_dir}/{self._klippy.printing_filename}'

    def togle_ligth_device(self):
        self.switch_ligth_device(not self.light_state)

    def switch_ligth_device(self, state: bool):
        with self._light_state_lock:
            if state:
                res = requests.post(f"http://{self._moonraker_host}/machine/device_power/device?device={self.light_device}&action=on")
                if res.ok:
                    self._light_device_on = True
            else:
                res = requests.post(f"http://{self._moonraker_host}/machine/device_power/device?device={self.light_device}&action=off")
                if res.ok:
                    self._light_device_on = False

    @cam_ligth_toogle
    def take_photo(self) -> BytesIO:
        with self.camera_lock:
            cap = cv2.VideoCapture(self._host)

            success, image = cap.read()

            if not success:
                logger.debug("failed to get camera frame for photo")
                img = Image.open(random.choice(glob.glob(f'{self._imgs}/imgs/*')))
            else:
                image = cv2.UMat(image)
                if self._flipVertically and self._flipHorisontally:
                    image = cv2.flip(image, -1)
                elif self._flipHorisontally:
                    image = cv2.flip(image, 1)
                elif self._flipVertically:
                    image = cv2.flip(image, 0)
                image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
                img = Image.fromarray(cv2.UMat.get(image))

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
        return bio

    @cam_ligth_toogle
    def take_video(self):
        def process_video_frame(frame_loc):
            frame_loc = cv2.UMat(frame_loc)
            if self._flipVertically and self._flipHorisontally:
                frame_loc = cv2.flip(frame_loc, -1)
            elif self._flipHorisontally:
                frame_loc = cv2.flip(frame_loc, 1)
            elif self._flipVertically:
                frame_loc = cv2.flip(frame_loc, 0)

            return cv2.UMat.get(frame_loc)

        with self.camera_lock:
            cv2.setNumThreads(self._threads)
            cap = cv2.VideoCapture(self._host)
            success, frame = cap.read()
            if not success:
                logger.debug("failed to get camera frame for video")
                # Todo: get picture from imgs?

            # height, width, channels = frame.shape
            height, width, channels = frame.shape
            fps_cam = cap.get(cv2.CAP_PROP_FPS)
            fps = 10
            filepath = os.path.join('/tmp/', 'video.mp4')
            out = cv2.VideoWriter(filepath, fourcc=cv2.VideoWriter_fourcc(*self._fourcc), fps=fps_cam, frameSize=(width, height))
            t_end = time.time() + self._videoDuration
            while success and time.time() < t_end:
                prev_frame_time = time.time()
                success, frame_inner = cap.read()
                out.write(process_video_frame(frame_inner))
                fps = 1 / (time.time() - prev_frame_time)

            logger.debug(f"Measured video fps is {fps}, while camera fps {fps_cam}")
            out.set(cv2.CAP_PROP_FPS, fps)
            out.release()

        bio = BytesIO()
        bio.name = 'video.mp4'
        with open(filepath, 'rb') as fh:
            bio.write(fh.read())

        os.remove(filepath)
        bio.seek(0)

        return bio, width, height

    @cam_ligth_toogle
    def take_gif(self):
        def process_frame(frame) -> Image:
            frame = cv2.UMat(frame)
            if self._flipVertically and self._flipHorisontally:
                frame = cv2.flip(frame, -1)
            elif self._flipHorisontally:
                frame = cv2.flip(frame, 1)
            elif self._flipVertically:
                frame = cv2.flip(frame, 0)
            if self._reduceGif > 0:
                frame = cv2.resize(frame, (int(width / self._reduceGif), int(height / self._reduceGif)))

            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            return Image.fromarray(cv2.UMat.get(frame))

        gif = []
        fps = 0
        with self.camera_lock:
            cv2.setNumThreads(self._threads)
            cap = cv2.VideoCapture(self._host)
            success, image = cap.read()

            if not success:
                logger.debug("failed to get camera frame for gif")

            height, width, channels = image.shape
            fps_cam = cap.get(cv2.CAP_PROP_FPS)
            gif.append(process_frame(image))

            t_end = time.time() + self._gifDuration
            # TOdo: calc frame count
            while success and time.time() < t_end:
                prev_frame_time = time.time()
                success, image_inner = cap.read()
                new_frame_time = time.time()
                gif.append(process_frame(image_inner))
                fps = 1 / (new_frame_time - prev_frame_time)

        logger.debug(f"Measured gif fps is {fps}, while camera fps {fps_cam}")
        if fps <= 0:
            fps = 1
        bio = BytesIO()
        bio.name = 'image.gif'
        gif[0].save(bio, format='GIF', save_all=True, optimize=True, append_images=gif[1:], duration=int(1000 / int(fps)), loop=0)
        bio.seek(0)

        return bio, width, height

    def take_lapse_photo(self):
        # Todo: check for space available?
        Path(self.lapse_dir).mkdir(parents=True, exist_ok=True)
        filename = f'{self.lapse_dir}/{time.time()}.{self._img_extension}'
        with open(filename, "wb") as outfile:
            # never add self in params there!
            photo = self.take_photo()
            outfile.write(photo.getbuffer())

    def create_timelapse(self):

        while self.light_need_off:
            time.sleep(1)

        # Fixme: get single file!
        for filename in glob.glob(f'{self.lapse_dir}/*.{self._img_extension}'):
            img = cv2.imread(filename)
            height, width, layers = img.shape
            size = (width, height)
            break

        filepath = f'{self.lapse_dir}/lapse.mp4'
        # Todo: check ligth & timer locks?
        with self.camera_lock:
            cv2.setNumThreads(self._threads)
            out = cv2.VideoWriter(filepath, fourcc=cv2.VideoWriter_fourcc(*self._fourcc), fps=self._fps, frameSize=size)

            # Todo: check for nonempty photos!
            photos = glob.glob(f'{self.lapse_dir}/*.{self._img_extension}')
            photos.sort(key=os.path.getmtime)
            for filename in photos:
                out.write(cv2.imread(filename))

            out.release()

        bio = BytesIO()
        bio.name = 'lapse.mp4'
        with open(filepath, 'rb') as fh:
            bio.write(fh.read())
        bio.seek(0)

        if self._cleanup:
            for filename in glob.glob(f'{self.lapse_dir}/*'):
                os.remove(filename)
            Path(self.lapse_dir).rmdir()

        return bio, width, height

    def clean(self):
        if self._cleanup and self._klippy.printing_filename:
            if os.path.isdir(self.lapse_dir):
                for filename in glob.glob(f'{self.lapse_dir}/*'):
                    os.remove(filename)
