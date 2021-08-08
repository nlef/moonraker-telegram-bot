import logging
import threading

import requests

logger = logging.getLogger(__name__)


class PowerDevice(object):
    def __new__(cls, name: str, moonraker_host: str):
        if name:
            return super(PowerDevice, cls).__new__(cls)
        else:
            return None

    def __init__(self, name: str, moonraker_host: str):
        self.name = name
        self._moonraker_host = moonraker_host
        self._state_lock = threading.Lock()
        self._device_on: bool = False

    @property
    def device_state(self) -> bool:
        with self._state_lock:
            return self._device_on

    @device_state.setter
    def device_state(self, state: bool):
        with self._state_lock:
            self._device_on = state

    def toggle_device(self):
        self.switch_device(not self.device_state)

    def switch_device(self, state: bool):
        with self._state_lock:
            if state:
                res = requests.post(f"http://{self._moonraker_host}/machine/device_power/device?device={self.name}&action=on")
                if res.ok:
                    self._device_on = True
                else:
                    logger.error(f'Power device switch failed: {res.reason}')
            else:
                res = requests.post(f"http://{self._moonraker_host}/machine/device_power/device?device={self.name}&action=off")
                if res.ok:
                    self._device_on = False
                else:
                    logger.error(f'Power device switch failed: {res.reason}')

# Maybe use websocket?
# def toggle_power_device(device: str, enable: bool):
#     ws.send(json.dumps({"jsonrpc": "2.0",
#                         "method": "machine.device_power.on" if enable else "machine.device_power.off",
#                         "id": myId,
#                         "params": {f"{device}": None}
#                         }))
