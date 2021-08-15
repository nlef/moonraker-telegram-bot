import logging
from concurrent.futures import ThreadPoolExecutor

from camera import Camera
from klippy import Klippy

logger = logging.getLogger(__name__)


class Timelapse:
    def __init__(self, enabled: bool, manual: bool, height: float, klippy: Klippy, camera: Camera, debug_logging: bool = False, ):
        self._enabled: bool = enabled
        self._mode_manual: bool = manual
        self._running: bool = False
        self._height: float = height
        self._last_height: float = 0.0
        self._klippy = klippy
        self._camera = camera

        self._executors_pool: ThreadPoolExecutor = ThreadPoolExecutor(4)
        if debug_logging:
            logger.setLevel(logging.DEBUG)

    @property
    def enabled(self):
        return self._enabled

    @property
    def manual_mode(self):
        return self._mode_manual

    @property
    def running(self):
        return self._running

    @running.setter
    def running(self, new_val: bool):
        self._running = new_val

    # Todo: vase mode calcs
    def take_lapse_photo(self, position_z: float = -1001):
        if not self._enabled:
            logger.debug(f"lapse is disabled")
            return
        elif not self._klippy.printing_filename:
            logger.debug(f"lapse is inactive for file undefined")
            return
        elif not self._running:
            logger.debug(f"lapse is not running at the moment")
            return

        if 0.0 < position_z < self._last_height - self._height:
            self._last_height = position_z

        if self._height > 0.0 and round(position_z * 100) % round(self._height * 100) == 0 and position_z > self._last_height:
            self._executors_pool.submit(self._camera.take_lapse_photo)
            self._last_height = position_z
        elif position_z < -1000:
            self._executors_pool.submit(self._camera.take_lapse_photo)
