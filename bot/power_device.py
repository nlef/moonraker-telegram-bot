import logging
import threading

import requests

from klippy import Klippy

logger = logging.getLogger(__name__)


class PowerDevice:
    def __new__(cls, name: str, klippy: Klippy):
        if name:
            return super(PowerDevice, cls).__new__(cls)
        else:
            return None

    def __init__(self, name: str, klippy: Klippy):
        self.name: str = name
        self._state_lock = threading.Lock()
        self._device_on: bool = False
        self._klippy: Klippy = klippy

    @property
    def device_state(self) -> bool:
        with self._state_lock:
            return self._device_on

    @device_state.setter
    def device_state(self, state: bool) -> None:
        with self._state_lock:
            self._device_on = state

    def toggle_device(self) -> bool:
        return self.switch_device(not self.device_state)

    # Todo: return exception?
    def switch_device(self, state: bool) -> bool:
        with self._state_lock:
            if state:

                res = self._klippy.make_request("POST", f"/machine/device_power/device?device={self.name}&action=on")
                if res.ok:
                    self._device_on = True
                    return True
                else:
                    logger.error("Power device switch failed: %s", res.reason)
                    return state
            else:
                res = self._klippy.make_request("POST", f"/machine/device_power/device?device={self.name}&action=off")
                if res.ok:
                    self._device_on = False
                    return False
                else:
                    logger.error("Power device switch failed: %s", res.reason)
                    return state
