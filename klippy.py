# Todo: class for printer states!
import logging
import time

import emoji
import requests
import urllib
from datetime import datetime, timedelta
from urllib.request import urlopen
from PIL import Image
from io import BytesIO

from power_device import PowerDevice

logger = logging.getLogger(__name__)


class Klippy:
    def __init__(self, moonraker_host: str, disabled_macros: list, eta_source: str, light_device: PowerDevice, psu_device: PowerDevice):
        self._host = moonraker_host
        self._disabled_macros = disabled_macros
        self._eta_source: str = eta_source
        self._light_devvice = light_device
        self._psu_device = psu_device
        self.connected: bool = False
        self.printing: bool = False
        self.paused: bool = False
        self.printing_duration: float = 0.0
        self.printing_progress: float = 0.0
        self._printing_filename: str = ''
        self.file_estimated_time: float = 0.0
        self.file_print_start_time: float = 0.0
        self.vsd_progress: float = 0.0

    @property
    def macros(self):
        return self._get_marco_list()

    @property
    def printing_filename(self):
        return self._printing_filename

    @property
    def printing_filename_with_time(self):
        return f"{self._printing_filename}_{datetime.fromtimestamp(self.file_print_start_time):%Y-%m-%d_%H-%M}"  # Todo: maybe add seconds?

    @printing_filename.setter
    def printing_filename(self, new_value: str):
        if not new_value:
            self._printing_filename = ''
            self.file_estimated_time = 0.0
            self.file_print_start_time = 0.0
            return
        response = requests.get(f"http://{self._host}/server/files/metadata?filename={urllib.parse.quote(new_value)}")
        resp = response.json()['result']
        self._printing_filename = new_value
        self.file_estimated_time = resp['estimated_time']
        self.file_print_start_time = resp['print_start_time'] if resp['print_start_time'] else time.time()

    def _get_marco_list(self) -> list:
        resp = requests.get(f'http://{self._host}/printer/objects/list')
        if not resp.ok:
            return list()
        macro_lines = list(filter(lambda it: 'gcode_macro' in it and ' _' not in it, resp.json()['result']['objects']))
        loaded_macros = list(map(lambda el: el.split(' ')[1], macro_lines))
        return [key for key in loaded_macros if key not in self._disabled_macros]

    def check_connection(self) -> bool:
        try:
            response = requests.get(f"http://{self._host}/printer/info")
            return True if response.ok else False
        except Exception:
            return False

    def get_status(self) -> str:
        response = requests.get(f"http://{self._host}/printer/objects/query?webhooks&print_stats&display_status&extruder&heater_bed")
        resp = response.json()['result']['status']
        print_stats = resp['print_stats']
        webhook = resp['webhooks']
        message = emoji.emojize(':robot: Klipper status: ', use_aliases=True) + f"{webhook['state']}\n"
        if 'display_status' in resp:
            if 'message' in resp['display_status']:
                msg = resp['display_status']['message']
                if msg and msg is not None:
                    message += f"{msg}\n"
        if 'state_message' in webhook:
            message += f"State message: {webhook['state_message']}\n"
        message += emoji.emojize(':mechanical_arm: Printing process status: ', use_aliases=True) + f"{print_stats['state']} \n"
        # if print_stats['state'] in ('printing', 'paused', 'complete'):
        message += f"Extruder temp.: {round(resp['extruder']['temperature'])}, Bed temp.: {round(resp['heater_bed']['temperature'])}\n"
        if print_stats['state'] == 'printing':
            if not self.printing_filename:
                self.printing_filename = print_stats['filename']
            message += f"Printing filename: {self.printing_filename} \n"
        elif print_stats['state'] == 'paused':
            message += f"Printing paused\n"
        elif print_stats['state'] == 'complete':
            pass
        if self._light_devvice:
            message += emoji.emojize(':flashlight: Light Status: ', use_aliases=True) + f"{'on' if self._light_devvice.device_state else 'off'} \n"
        if self._psu_device:
            message += emoji.emojize(':electric_plug: PSU Status: ', use_aliases=True) + f"{'on' if self._psu_device.device_state else 'off'} \n"
        return message

    def execute_command(self, command: str):
        data = {'commands': [f'{command}']}
        res = requests.post(f"http://{self._host}/api/printer/command", json=data)
        if not res.ok:
            logger.error(res.reason)

    def get_eta(self) -> timedelta:
        if self._eta_source == 'slicer':
            eta = int(self.file_estimated_time - self.printing_duration)
        else:  # eta by file
            eta = int(self.printing_duration / self.vsd_progress - self.printing_duration)
        # eta_vsd = int(resp['estimated_time'] * (1 - klippy.vsd_progress))
        if eta < 0:
            eta = 0
        return timedelta(seconds=eta)

    def get_eta_message(self):
        eta = self.get_eta()
        return f"Estimated time left: {eta}\nFinish at {datetime.now() + eta:%Y-%m-%d %H:%M}\n"

    def get_file_info(self, message: str = '') -> (str, BytesIO):
        response = requests.get(f"http://{self._host}/server/files/metadata?filename={urllib.parse.quote(self.printing_filename)}")
        resp = response.json()['result']
        self.file_estimated_time = resp['estimated_time']

        filemanet_lenght = round(resp['filament_total'] / 1000, 2)
        message += f"Printed {round(self.printing_progress * 100, 0)}%\n"
        message += f"Filament: {round(filemanet_lenght * self.printing_progress, 2)}m / {filemanet_lenght}m, weight: {resp['filament_weight_total']}g\n"
        message += self.get_eta_message()

        if 'thumbnails' in resp:
            thumb = max(resp['thumbnails'], key=lambda el: el['size'])
            img = Image.open(urlopen(f"http://{self._host}/server/files/gcodes/{urllib.parse.quote(thumb['relative_path'])}")).convert('RGB')

            bio = BytesIO()
            bio.name = f'{self.printing_filename}.webp'
            img.save(bio, 'WebP', quality=0, lossless=True)
            bio.seek(0)

            return message, bio
        else:
            return message, None
