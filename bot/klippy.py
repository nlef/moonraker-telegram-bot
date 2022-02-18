# Todo: class for printer states!
from datetime import datetime, timedelta
from io import BytesIO
import logging
import re
import time
from typing import List
import urllib

from PIL import Image
from configuration import ConfigWrapper
import emoji
from power_device import PowerDevice
import requests

logger = logging.getLogger(__name__)


class Klippy:
    _DATA_MACRO = "bot_data"

    def __init__(
        self,
        config: ConfigWrapper,
        light_device: PowerDevice,
        psu_device: PowerDevice,
        logging_handler: logging.Handler = None,
    ):
        self._host: str = config.bot.host
        self._disabled_macros: List[str] = config.telegram_ui.disabled_macros + [self._DATA_MACRO]
        self.show_hidden_macros: List[str] = config.telegram_ui.show_hidden_macros
        self._message_parts: List[str] = config.telegram_ui.status_message_content
        self._eta_source: str = config.telegram_ui.eta_source
        self._light_device = light_device
        self._psu_device = psu_device
        self._sensors_list: List[str] = config.telegram_ui.status_message_sensors
        self._heates_list: List[str] = config.telegram_ui.status_message_heaters
        self._temp_fans_list: List[str] = config.telegram_ui.status_message_temp_fans
        self._devices_list: List[str] = config.telegram_ui.status_message_devices
        self._user: str = config.bot.user
        self._passwd: str = config.bot.passwd

        self._dbname = "telegram-bot"

        self._connected: bool = False
        self.printing: bool = False
        self.paused: bool = False
        self.state: str = ""
        self.state_message: str = ""

        self.printing_duration: float = 0.0
        self.printing_progress: float = 0.0
        self.printing_height: float = 0.0
        self._printing_filename: str = ""
        self.file_estimated_time: float = 0.0
        self.file_print_start_time: float = 0.0
        self.vsd_progress: float = 0.0

        self.filament_used: float = 0.0
        self.filament_total: float = 0.0
        self.filament_weight: float = 0.0
        self._thumbnail_path = ""

        self._jwt_token: str = ""

        # Todo: create sensors class!!
        self.sensors_dict: dict = dict()

        if logging_handler:
            logger.addHandler(logging_handler)
        if config.bot.debug:
            logger.setLevel(logging.DEBUG)

        self._auth_moonraker()

    def prepare_sens_dict_subscribe(self):
        self.sensors_dict = {}
        sens_dict = {}
        for heat in self._heates_list:
            if heat in ["extruder", "heater_bed"]:
                sens_dict[heat] = None
            else:
                sens_dict[f"heater_generic {heat}"] = None

        for sens in self._sensors_list:
            sens_dict[f"temperature_sensor {sens}"] = None

        for sens in self._temp_fans_list:
            sens_dict[f"temperature_fan {sens}"] = None
        return sens_dict

    def _filament_weight_used(self) -> float:
        return self.filament_weight * (self.filament_used / self.filament_total)

    @property
    def connected(self) -> bool:
        return self._connected

    @connected.setter
    def connected(self, new_value: bool):
        self._connected = new_value
        self.printing = False
        self.paused = False
        self._reset_file_info()

    # Todo: save macros list until klippy restart
    @property
    def macros(self) -> List[str]:
        return self._get_marco_list()

    @property
    def macros_all(self) -> List[str]:
        return self._get_full_marco_list()

    @property
    def moonraker_host(self) -> str:
        return self._host

    @property
    def _headers(self):
        heads = {}
        if self._jwt_token:
            heads = {"Authorization": f"Bearer {self._jwt_token}"}

        return heads

    @property
    def one_shot_token(self) -> str:
        if not self._user and not self._jwt_token:
            return ""

        resp = requests.get(f"http://{self._host}/access/oneshot_token", headers=self._headers)
        if resp.ok:
            res = f"?token={resp.json()['result']}"
        else:
            logger.error(resp.reason)
            res = ""
        return res

    def _reset_file_info(self) -> None:
        self.printing_duration: float = 0.0
        self.printing_progress: float = 0.0
        self.printing_height: float = 0.0
        self._printing_filename: str = ""
        self.file_estimated_time: float = 0.0
        self.file_print_start_time: float = 0.0
        self.vsd_progress: float = 0.0

        self.filament_used: float = 0.0
        self.filament_total: float = 0.0
        self.filament_weight: float = 0.0
        self._thumbnail_path = ""

    @property
    def printing_filename(self) -> str:
        return self._printing_filename

    @property
    def printing_filename_with_time(self) -> str:
        return f"{self._printing_filename}_{datetime.fromtimestamp(self.file_print_start_time):%Y-%m-%d_%H-%M}"

    @printing_filename.setter
    def printing_filename(self, new_value: str):
        if not new_value:
            self._reset_file_info()
            return

        response = requests.get(
            f"http://{self._host}/server/files/metadata?filename={urllib.parse.quote(new_value)}",
            headers=self._headers,
        )
        # Todo: add response status check!
        resp = response.json()["result"]
        self._printing_filename = new_value
        self.file_estimated_time = resp["estimated_time"]
        self.file_print_start_time = resp["print_start_time"] if resp["print_start_time"] else time.time()
        self.filament_total = resp["filament_total"] if "filament_total" in resp else 0.0
        self.filament_weight = resp["filament_weight_total"] if "filament_weight_total" in resp else 0.0

        if "thumbnails" in resp and "filename" in resp:
            thumb = max(resp["thumbnails"], key=lambda el: el["size"])
            file_dir = resp["filename"].rpartition("/")[0]
            if file_dir:
                self._thumbnail_path = file_dir + "/"
            self._thumbnail_path += thumb["relative_path"]

    def _get_full_marco_list(self) -> List[str]:
        resp = requests.get(f"http://{self._host}/printer/objects/list", headers=self._headers)
        if not resp.ok:
            return []
        macro_lines = list(filter(lambda it: "gcode_macro" in it, resp.json()["result"]["objects"]))
        loaded_macros = list(map(lambda el: el.split(" ")[1], macro_lines))
        return loaded_macros

    def _get_marco_list(self) -> List[str]:
        return [
            key
            for key in self._get_full_marco_list()
            if key not in self._disabled_macros and (True if self.show_hidden_macros else not key.startswith("_"))
        ]

    def _auth_moonraker(self) -> None:
        if not self._user or not self._passwd:
            return
        # TOdo: add try catch
        res = requests.post(
            f"http://{self._host}/access/login",
            json={"username": self._user, "password": self._passwd},
        )
        if res.ok:
            # Todo: check if token refresh needed
            self._jwt_token = res.json()["result"]["token"]
        else:
            logger.error(res.reason)

    def check_connection(self) -> str:
        try:
            response = requests.get(f"http://{self._host}/printer/info", headers=self._headers, timeout=2)
            return "" if response.ok else f"Connection failed. {response.reason}"
        except Exception as ex:
            logger.error(ex, exc_info=True)
            return f"Connection failed."

    def update_sensror(self, name: str, value) -> None:
        if name in self.sensors_dict:
            if "temperature" in value:
                self.sensors_dict.get(name)["temperature"] = value["temperature"]
            if "target" in value:
                self.sensors_dict.get(name)["target"] = value["target"]
            if "power" in value:
                self.sensors_dict.get(name)["power"] = value["power"]
            if "speed" in value:
                self.sensors_dict.get(name)["speed"] = value["speed"]
        else:
            self.sensors_dict[name] = value

    @staticmethod
    def sensor_message(name: str, value) -> str:
        sens_name = re.sub(r"([A-Z]|\d|_)", r" \1", name).replace("_", "")
        if "power" in value:
            message = (
                emoji.emojize(" :hotsprings: ", use_aliases=True)
                + f"{sens_name.title()}: {round(value['temperature'])}"
            )
            if "target" in value and value["target"] > 0.0 and abs(value["target"] - value["temperature"]) > 2:
                message += emoji.emojize(" :arrow_right: ", use_aliases=True) + f"{round(value['target'])}"
            if value["power"] > 0.0:
                message += emoji.emojize(" :fire: ", use_aliases=True)
        elif "speed" in value:
            message = (
                emoji.emojize(" :tornado: ", use_aliases=True) + f"{sens_name.title()}: {round(value['temperature'])}"
            )
            if "target" in value and value["target"] > 0.0 and abs(value["target"] - value["temperature"]) > 2:
                message += emoji.emojize(" :arrow_right: ", use_aliases=True) + f"{round(value['target'])}"
        else:
            message = (
                emoji.emojize(" :thermometer: ", use_aliases=True)
                + f"{sens_name.title()}: {round(value['temperature'])}"
            )
        message += "\n"
        return message

    def _get_sensors_message(self) -> str:
        message = ""
        for name, value in self.sensors_dict.items():
            message += self.sensor_message(name, value)
        return message

    def _get_power_devices_mess(self) -> str:
        message = ""
        if self._light_device and self._light_device.name in self._devices_list:
            message += (
                emoji.emojize(" :flashlight: Light: ", use_aliases=True)
                + f"{'on' if self._light_device.device_state else 'off'}\n"
            )
        if self._psu_device and self._psu_device.name in self._devices_list:
            message += (
                emoji.emojize(" :electric_plug: PSU: ", use_aliases=True)
                + f"{'on' if self._psu_device.device_state else 'off'}\n"
            )
        return message

    def execute_command(self, *command) -> None:
        data = {"commands": list(map(lambda el: f"{el}", command))}
        res = requests.post(f"http://{self._host}/api/printer/command", json=data, headers=self._headers)
        if not res.ok:
            logger.error(res.reason)

    def _get_eta(self) -> timedelta:
        if self._eta_source == "slicer":
            eta = int(self.file_estimated_time - self.printing_duration)
        else:  # eta by file
            eta = int(self.printing_duration / self.vsd_progress - self.printing_duration)
        if eta < 0:
            eta = 0
        return timedelta(seconds=eta)

    def _populate_with_thumb(self, thumb_path: str, message: str):
        if not thumb_path:
            # Todo: resize?
            img = Image.open("../imgs/nopreview.png").convert("RGB")
        else:
            response = requests.get(
                f"http://{self._host}/server/files/gcodes/{urllib.parse.quote(thumb_path)}",
                stream=True,
                headers=self._headers,
            )
            if response.ok:
                response.raw.decode_content = True
                img = Image.open(response.raw).convert("RGB")
            else:
                logger.error(f"Thumbnail download failed for {thumb_path} \n\n{response.reason}")
                # Todo: resize?
                img = Image.open("../imgs/nopreview.png").convert("RGB")

        bio = BytesIO()
        bio.name = f"{self.printing_filename}.webp"
        img.save(bio, "WebP", quality=0, lossless=True)
        bio.seek(0)
        img.close()
        return message, bio

    def get_file_info(self, message: str = "") -> (str, BytesIO):
        message = self.get_print_stats(message)
        return self._populate_with_thumb(self._thumbnail_path, message)

    def _get_printing_file_info(self, message_pre: str = "") -> str:
        message = (
            f"Printing: {self.printing_filename} \n"
            if not message_pre
            else f"{message_pre}: {self.printing_filename} \n"
        )
        if "progress" in self._message_parts:
            message += f"Progress {round(self.printing_progress * 100, 0)}%"
        if "height" in self._message_parts:
            message += f", height: {round(self.printing_height, 2)}mm\n" if self.printing_height > 0.0 else "\n"
        if self.filament_total > 0.0:
            if "filament_length" in self._message_parts:
                message += f"Filament: {round(self.filament_used / 1000, 2)}m / {round(self.filament_total / 1000, 2)}m"
            if self.filament_weight > 0.0 and "filament_weight" in self._message_parts:
                message += f", weight: {round(self._filament_weight_used(), 2)}/{self.filament_weight}g"
            message += "\n"
        if "print_duration" in self._message_parts:
            message += f"Printing for {timedelta(seconds=round(self.printing_duration))}\n"

        eta = self._get_eta()
        if "eta" in self._message_parts:
            message += f"Estimated time left: {eta}\n"
        if "finish_time" in self._message_parts:
            message += f"Finish at {datetime.now() + eta:%Y-%m-%d %H:%M}\n"

        return message

    def get_print_stats(self, message_pre: str = "") -> str:
        message = self._get_printing_file_info(message_pre) + self._get_sensors_message()
        if "power_devices" in self._message_parts:
            message += self._get_power_devices_mess()
        return message

    def get_status(self) -> str:
        response = requests.get(
            f"http://{self._host}/printer/objects/query?webhooks&print_stats&display_status",
            headers=self._headers,
        )
        resp = response.json()["result"]["status"]
        print_stats = resp["print_stats"]
        # webhook = resp['webhooks']
        # message = emoji.emojize(':robot: Klipper status: ', use_aliases=True) + f"{webhook['state']}\n"
        message = ""

        # if 'display_status' in resp and 'message' in resp['display_status']:
        #     msg = resp['display_status']['message']
        #     if msg and msg is not None:
        #         message += f"{msg}\n"
        # if 'state_message' in webhook:
        #     message += f"State message: {webhook['state_message']}\n"

        # message += emoji.emojize(':mechanical_arm: Printing process status: ', use_aliases=True) + f"{print_stats['state']} \n"

        if print_stats["state"] == "printing":
            if not self.printing_filename:
                self.printing_filename = print_stats["filename"]
        elif print_stats["state"] == "paused":
            message += f"Printing paused\n"
        elif print_stats["state"] == "complete":
            message += f"Printing complete\n"
        elif print_stats["state"] == "standby":
            message += f"Printer standby\n"
        elif print_stats["state"] == "error":
            message += f"Printing error\n"
            if "message" in print_stats and print_stats["message"]:
                message += f"{print_stats['message']}\n"

        message += "\n"
        if self.printing_filename:
            message += self._get_printing_file_info()

        message += self._get_sensors_message()
        message += self._get_power_devices_mess()

        return message

    def get_file_info_by_name(self, filename: str, message: str):
        response = requests.get(
            f"http://{self._host}/server/files/metadata?filename={urllib.parse.quote(filename)}",
            headers=self._headers,
        )
        # Todo: add response status check!
        resp = response.json()["result"]
        message += "\n"
        if "filament_total" in resp and resp["filament_total"] > 0.0:
            message += f"Filament: {round(resp['filament_total'] / 1000, 2)}m"
            if "filament_weight_total" in resp and resp["filament_weight_total"] > 0.0:
                message += f", weight: {resp['filament_weight_total']}g"
        if "estimated_time" in resp and resp["estimated_time"] > 0.0:
            message += f"\nEstimated printing time: {timedelta(seconds=resp['estimated_time'])}"

        thumb_path = ""
        if "thumbnails" in resp:
            thumb = max(resp["thumbnails"], key=lambda el: el["size"])
            if "relative_path" in thumb and "filename" in resp:
                file_dir = resp["filename"].rpartition("/")[0]
                if file_dir:
                    thumb_path = file_dir + "/"
                thumb_path += thumb["relative_path"]
            else:
                logger.error(f"Thumbnail relative_path and filename not found in {resp}")

        return self._populate_with_thumb(thumb_path, message)

    # TOdo: add scrolling
    def get_gcode_files(self):
        response = requests.get(f"http://{self._host}/server/files/list?root=gcodes", headers=self._headers)
        resp = response.json()
        files = sorted(resp["result"], key=lambda item: item["modified"], reverse=True)[:10]
        return files

    def upload_file(self, file: BytesIO) -> bool:
        response = requests.post(
            f"http://{self._host}/server/files/upload",
            files={"file": file},
            headers=self._headers,
        )
        return response.ok

    def start_printing_file(self, filename: str) -> bool:
        response = requests.post(
            f"http://{self._host}/printer/print/start?filename={urllib.parse.quote(filename)}",
            headers=self._headers,
        )
        return response.ok

    def stop_all(self):
        self._reset_file_info()

    # moonraker databse section
    def get_param_from_db(self, param_name: str):
        res = requests.get(
            f"http://{self._host}/server/database/item?namespace={self._dbname}&key={param_name}",
            headers=self._headers,
        )
        if res.ok:
            return res.json()["result"]["value"]
        else:
            logger.error(f"Failed getting {param_name} from {self._dbname} \n\n{res.reason}")
            # Fixme: return default value? check for 404!
            return None

    def save_param_to_db(self, param_name: str, value) -> None:
        data = {"namespace": self._dbname, "key": param_name, "value": value}
        res = requests.post(
            f"http://{self._host}/server/database/item",
            json=data,
            headers=self._headers,
        )
        if not res.ok:
            logger.error(f"Failed saving {param_name} to {self._dbname} \n\n{res.reason}")

    def delete_param_from_db(self, param_name: str) -> None:
        res = requests.delete(
            f"http://{self._host}/server/database/item?namespace={self._dbname}&key={param_name}",
            headers=self._headers,
        )
        if not res.ok:
            logger.error(f"Failed getting {param_name} from {self._dbname} \n\n{res.reason}")

    # macro data section
    def save_data_to_marco(self, lapse_size: int, filename: str, path: str) -> None:
        full_macro_list = self._get_full_marco_list()
        if self._DATA_MACRO in full_macro_list:
            self.execute_command(
                f"SET_GCODE_VARIABLE MACRO=bot_data VARIABLE=lapse_video_size VALUE={lapse_size}",
                f"SET_GCODE_VARIABLE MACRO=bot_data VARIABLE=lapse_filename VALUE='\"{filename}\"'",
                f"SET_GCODE_VARIABLE MACRO=bot_data VARIABLE=lapse_path VALUE='\"{path}\"'",
            )

        else:
            logger.error(f'Marco "{self._DATA_MACRO}" not defined')
