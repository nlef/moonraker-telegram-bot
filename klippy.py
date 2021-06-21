# Todo: class for printer states!
import logging

import emoji
import requests

logger = logging.getLogger(__name__)


class Klippy():
    def __init__(self, moonraker_host: str, disabled_macros: list):
        self._host = moonraker_host
        self._disabled_macros = disabled_macros
        self.connected: bool = False
        self.printing: bool = False
        self.printing_duration: float = 0.0
        self.printing_progress: float = 0.0
        self.printing_filename: str = ''

    @property
    def macros(self):
        return self._get_marco_list()

    def _get_marco_list(self) -> list:
        resp = requests.get(f'http://{self._host}/printer/objects/list')
        if not resp.ok:
            return list()
        macro_lines = list(filter(lambda it: 'gcode_macro' in it, resp.json()['result']['objects']))
        loaded_macros = list(map(lambda el: el.split(' ')[1], macro_lines))
        return [key for key in loaded_macros if key not in self._disabled_macros]

    def check_connection(self) -> bool:
        try:
            response = requests.get(f"http://{self._host}/printer/info")
            return True if response.ok else False
        except Exception as err:
            return False

    def get_status(self) -> (str, str):
        response = requests.get(
            f"http://{self._host}/printer/objects/query?webhooks&print_stats=filename,total_duration,print_duration,filament_used,state,message&display_status=message")
        resp = response.json()
        print_stats = resp['result']['status']['print_stats']
        webhook = resp['result']['status']['webhooks']
        message = emoji.emojize(':robot: Klipper status: ', use_aliases=True) + f"{webhook['state']}\n"
        if 'display_status' in resp['result']['status']:
            if 'message' in resp['result']['status']['display_status']:
                msg = resp['result']['status']['display_status']['message']
                if msg and msg is not None:
                    message += f"{msg}\n"
        if 'state_message' in webhook:
            message += f"State message: {webhook['state_message']}\n"
        message += emoji.emojize(':mechanical_arm: Printing process status: ', use_aliases=True) + f"{print_stats['state']} \n"
        printing_filename = ''
        # if print_stats['state'] in ('printing', 'paused', 'complete'):
        if print_stats['state'] == 'printing':
            printing_filename = print_stats['filename']
            message += f"Printing filename: {printing_filename} \n"
        elif print_stats['state'] == 'paused':
            message += f"Printing paused\n"
        elif print_stats['state'] == 'complete':
            pass
        # Todo: use powerdevice classes
        # if cameraWrap.light_device:
        #     message += emoji.emojize(':flashlight: Light Status: ', use_aliases=True) + f"{'on' if cameraWrap.light_state else 'off'}"
        return message, printing_filename

    def execute_command(self, command: str):
        data = {'commands': [f'{command}']}
        res = requests.post(f"http://{self._host}/api/printer/command", json=data)
        if not res.ok:
            logger.error(res.reason)
