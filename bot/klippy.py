# Todo: class for printer states!
import configparser
import logging
import re
import time

import emoji
import requests
import urllib
from datetime import datetime, timedelta
from PIL import Image
from io import BytesIO

from power_device import PowerDevice

logger = logging.getLogger(__name__)


class Klippy:
    def __init__(self, config: configparser.ConfigParser, light_device: PowerDevice, psu_device: PowerDevice, logging_handler: logging.Handler = None, debug_logging: bool = False):
        self._host = config.get('bot', 'server', fallback='localhost')
        self._disabled_macros = [el.strip() for el in config.get('telegram_ui', 'disabled_macros').split(',')] if 'telegram_ui' in config and 'disabled_macros' in config['telegram_ui'] else list()
        self._eta_source: str = config.get('bot', 'eta_source', fallback='slicer')
        self._light_device = light_device
        self._psu_device = psu_device
        self._sensors_list: list = [el.strip() for el in config.get('bot', 'sensors').split(',')] if 'bot' in config and 'sensors' in config['bot'] else []
        self._heates_list: list = [el.strip() for el in config.get('bot', 'heaters').split(',')] if 'bot' in config and 'heaters' in config['bot'] else []
        self._sensors_dict: dict = self._prepare_sens_dict()
        self._sensors_query = '&' + '&'.join(self._sensors_dict.values())
        self._user = config.get('bot', 'user', fallback='')
        self._passwd = config.get('bot', 'password', fallback='')

        self._dbname = 'telegram-bot'

        self.connected: bool = False
        self.printing: bool = False
        self.paused: bool = False
        self.state: str = ''
        self.state_message: str = ''

        self.printing_duration: float = 0.0
        self.printing_progress: float = 0.0
        self._printing_filename: str = ''
        self.file_estimated_time: float = 0.0
        self.file_print_start_time: float = 0.0
        self.vsd_progress: float = 0.0

        self.filament_used: float = 0.0
        self.filament_total: float = 0.0
        self.filament_weight: float = 0.0
        self._thumbnail_path = ''

        self._jwt_token: str = ''

        if logging_handler:
            logger.addHandler(logging_handler)
        if debug_logging:
            logger.setLevel(logging.DEBUG)

    def _prepare_sens_dict(self):
        sens_dict = {}
        for heat in self._heates_list:
            if heat in ['extruder', 'heater_bed']:
                sens_dict[heat] = heat
            else:
                sens_dict[heat] = f"heater_generic {heat}"

        for sens in self._sensors_list:
            sens_dict[sens] = f"temperature_sensor {sens}"
        return sens_dict

    @property
    def macros(self):
        return self._get_marco_list()

    @property
    def macros_all(self):
        return self._get_full_marco_list()

    @property
    def printing_filename(self):
        return self._printing_filename

    @property
    def printing_filename_with_time(self):
        return f"{self._printing_filename}_{datetime.fromtimestamp(self.file_print_start_time):%Y-%m-%d_%H-%M}"

    @property
    def moonraker_host(self):
        return self._host

    @property
    def _headers(self):
        heads = {}
        if self._jwt_token:
            heads = {'Authorization': f"Bearer {self._jwt_token}"}

        return heads

    @property
    def one_shot_token(self) -> str:
        if not self._user and not self._jwt_token:
            return ''

        resp = requests.get(f'http://{self._host}/access/oneshot_token', headers=self._headers)
        if resp.ok:
            res = f"?token={resp.json()['result']}"
        else:
            logger.error(resp.reason)
            res = ''
        return res

    def _reset_file_info(self) -> None:
        self._printing_filename = ''
        self.file_estimated_time = 0.0
        self.file_print_start_time = 0.0
        self.filament_total = 0.0
        self.filament_weight = 0.0
        self._thumbnail_path = ''

    @printing_filename.setter
    def printing_filename(self, new_value: str):
        if not new_value:
            self._reset_file_info()
            return

        response = requests.get(f"http://{self._host}/server/files/metadata?filename={urllib.parse.quote(new_value)}", headers=self._headers)
        # Todo: add response status check!
        resp = response.json()['result']
        self._printing_filename = new_value
        self.file_estimated_time = resp['estimated_time']
        self.file_print_start_time = resp['print_start_time'] if resp['print_start_time'] else time.time()
        self.filament_total = resp['filament_total'] if 'filament_total' in resp else 0.0
        self.filament_weight = resp['filament_weight_total'] if 'filament_weight_total' in resp else 0.0

        if 'thumbnails' in resp:
            thumb = max(resp['thumbnails'], key=lambda el: el['size'])
            self._thumbnail_path = thumb['relative_path']

    def _get_full_marco_list(self) -> list:
        resp = requests.get(f'http://{self._host}/printer/objects/list', headers=self._headers)
        if not resp.ok:
            return list()
        macro_lines = list(filter(lambda it: 'gcode_macro' in it, resp.json()['result']['objects']))
        loaded_macros = list(map(lambda el: el.split(' ')[1], macro_lines))
        return loaded_macros

    def _get_marco_list(self) -> list:
        return [key for key in self._get_full_marco_list() if key not in self._disabled_macros]

    def auth_moonraker(self) -> str:
        if not self._user or not self._passwd:
            return ''

        res = requests.post(f"http://{self._host}/access/login", json={'username': self._user, 'password': self._passwd})
        if res.ok:
            # Todo: check if token refresh needed
            self._jwt_token = res.json()['result']['token']
            return ''
        else:
            logger.error(res.reason)
            return f"Auth failed.\n {res.reason}"

    def check_connection(self) -> str:
        auth = self.auth_moonraker()
        if auth:
            return auth

        try:
            response = requests.get(f"http://{self._host}/printer/info", headers=self._headers, timeout=2)
            return '' if response.ok else f"Connection failed. {response.reason}"
        except Exception as ex:
            logger.error(ex, exc_info=True)
            return f"Connection failed."

    @staticmethod
    def sensor_message(sensor: str, sens_key: str, response) -> str:
        if sens_key not in response or not response[sens_key]:
            return ''

        sens_name = re.sub(r"([A-Z]|\d|_)", r" \1", sensor).replace('_', '')
        message = f"{sens_name.title()}: {round(response[sens_key]['temperature'])}"
        if 'target' in response[sens_key]:
            if response[sens_key]['target'] > 0.0:
                message += emoji.emojize(' :arrow_right: ', use_aliases=True) + f"{round(response[sens_key]['target'])}"
            if response[sens_key]['power'] > 0.0:
                message += emoji.emojize(' :fire: ', use_aliases=True)
        message += '\n'
        return message

    def get_status(self) -> str:
        response = requests.get(f"http://{self._host}/printer/objects/query?webhooks&print_stats&display_status{self._sensors_query}", headers=self._headers)
        resp = response.json()['result']['status']
        print_stats = resp['print_stats']
        webhook = resp['webhooks']
        message = emoji.emojize(':robot: Klipper status: ', use_aliases=True) + f"{webhook['state']}\n"

        if 'display_status' in resp and 'message' in resp['display_status']:
            msg = resp['display_status']['message']
            if msg and msg is not None:
                message += f"{msg}\n"
        if 'state_message' in webhook:
            message += f"State message: {webhook['state_message']}\n"

        message += emoji.emojize(':mechanical_arm: Printing process status: ', use_aliases=True) + f"{print_stats['state']} \n"

        if print_stats['state'] == 'printing':
            if not self.printing_filename:
                self.printing_filename = print_stats['filename']
            message += f"Printing filename: {self.printing_filename} \n"
        elif print_stats['state'] == 'paused':
            message += f"Printing paused\n"
        elif print_stats['state'] == 'complete':
            pass

        for sens, s_key in self._sensors_dict.items():
            message += self.sensor_message(sens, s_key, resp)

        if self._light_device:
            message += emoji.emojize(':flashlight: Light: ', use_aliases=True) + f"{'on' if self._light_device.device_state else 'off'}\n"
        if self._psu_device:
            message += emoji.emojize(':electric_plug: PSU: ', use_aliases=True) + f"{'on' if self._psu_device.device_state else 'off'}\n"

        return message

    def execute_command(self, command: str):
        data = {'commands': [f'{command}']}
        res = requests.post(f"http://{self._host}/api/printer/command", json=data, headers=self._headers)
        if not res.ok:
            logger.error(res.reason)

    def _get_eta(self) -> timedelta:
        if self._eta_source == 'slicer':
            eta = int(self.file_estimated_time - self.printing_duration)
        else:  # eta by file
            eta = int(self.printing_duration / self.vsd_progress - self.printing_duration)
        if eta < 0:
            eta = 0
        return timedelta(seconds=eta)

    def get_eta_message(self) -> str:
        eta = self._get_eta()
        return f"Estimated time left: {eta}\nFinish at {datetime.now() + eta:%Y-%m-%d %H:%M}\n"

    def _populate_with_thumb(self, thumb_path: str, message: str):
        response = requests.get(f"http://{self._host}/server/files/gcodes/{urllib.parse.quote(thumb_path)}", stream=True, headers=self._headers)
        if response.ok:
            response.raw.decode_content = True
            img = Image.open(response.raw).convert('RGB')

            bio = BytesIO()
            bio.name = f'{self.printing_filename}.webp'
            img.save(bio, 'WebP', quality=0, lossless=True)
            bio.seek(0)
            img.close()
            return message, bio
        else:
            logger.error(f"Thumbnail download failed for {thumb_path} \n\n{response.reason}")
            return message, None

    def get_file_info(self, message: str = '') -> (str, BytesIO):
        message += f"Printed {round(self.printing_progress * 100, 0)}%\n"
        if self.filament_total > 0.0:
            message += f"Filament: {round(self.filament_used / 1000, 2)}m / {round(self.filament_total / 1000, 2)}m"
            if self.filament_weight > 0.0:
                message += f", weight: {self.filament_weight}g"
        message += '\n'

        message += self.get_eta_message()

        if self._thumbnail_path:
            return self._populate_with_thumb(self._thumbnail_path, message)
        else:
            return message, None

    def get_file_info_by_name(self, filename: str, message: str):
        response = requests.get(f"http://{self._host}/server/files/metadata?filename={urllib.parse.quote(filename)}", headers=self._headers)
        # Todo: add response status check!
        resp = response.json()['result']
        message += '\n'
        if 'filament_total' in resp and resp['filament_total'] > 0.0:
            message += f"Filament: {round(resp['filament_total'] / 1000, 2)}m"
            if 'filament_weight_total' in resp and resp['filament_weight_total'] > 0.0:
                message += f", weight: {resp['filament_weight_total']}g"
        if 'estimated_time' in resp and resp['estimated_time'] > 0.0:
            message += f"\nEstimated printing time: {timedelta(seconds=resp['estimated_time'])}"

        if 'thumbnails' in resp:
            thumb = max(resp['thumbnails'], key=lambda el: el['size'])
            if 'relative_path' in thumb:
                return self._populate_with_thumb(thumb['relative_path'], message)
            else:
                logger.error(f"Thumbnail relative_path not found in {resp}")
                return message, None
        else:
            return message, None

    def get_gcode_files(self):
        response = requests.get(f"http://{self._host}/server/files/list?root=gcodes", headers=self._headers)
        resp = response.json()
        files = sorted(resp['result'], key=lambda item: item['modified'], reverse=True)[:10]
        return files

    def upload_file(self, file: BytesIO) -> bool:
        response = requests.post(f"http://{self._host}/server/files/upload", files={'file': file}, headers=self._headers)
        return response.ok

    def start_printing_file(self, filename: str) -> bool:
        response = requests.post(f"http://{self._host}/printer/print/start?filename={urllib.parse.quote(filename)}", headers=self._headers)
        return response.ok

    def stop_all(self):
        self._reset_file_info()

    # moonraker databse section
    def get_param_from_db(self, param_name: str):
        res = requests.get(f"http://{self._host}/server/database/item?namespace={self._dbname}&key={param_name}", headers=self._headers)
        if res.ok:
            return res.json()['result']['value']
        else:
            logger.error(f"Failed getting {param_name} from {self._dbname} \n\n{res.reason}")
            # Fixme: return default value? check for 404!
            return None

    def save_param_to_db(self, param_name: str, value):
        data = {
            "namespace": self._dbname,
            "key": param_name,
            "value": value
        }
        res = requests.post(f"http://{self._host}/server/database/item", json=data, headers=self._headers)
        if not res.ok:
            logger.error(f"Failed saving {param_name} to {self._dbname} \n\n{res.reason}")

    def delete_param_from_db(self, param_name: str):
        res = requests.delete(f"http://{self._host}/server/database/item?namespace={self._dbname}&key={param_name}", headers=self._headers)
        if not res.ok:
            logger.error(f"Failed getting {param_name} from {self._dbname} \n\n{res.reason}")

    # [gcode_macro bot_data]
    # variable_homyak_test: 0
    # gcode:
    # M118 Homyak: {homyak_test}
    #
    #
    # [gcode_macro bot_data_update]
    # gcode:
    # { % set    homyak_test = params.homyak_test | default(40) | float %}
    # SET_GCODE_VARIABLE    MACRO = bot_data    VARIABLE = homyak_test    VALUE = {homyak_test}

    # Todo: add macro example into docs.
    # bot variables will be saved to this macro and become usable from klipper macros!
    def save_data_to_marco(self):
        # lapse_duration, lapse_video_size, lapse_path
        command = f'bot_data_update homyak_test=55'
        self.execute_command(command)
