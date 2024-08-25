# Todo: class for printer states!
from datetime import datetime, timedelta
from io import BytesIO
import logging
import re
import threading
import time
from typing import List, Tuple
import urllib

from PIL import Image
import emoji
import orjson
import requests

from configuration import ConfigWrapper

logger = logging.getLogger(__name__)


class PowerDevice:
    def __new__(cls, name: str, klippy_: "Klippy"):
        if name:
            return super(PowerDevice, cls).__new__(cls)
        else:
            return None

    def __init__(self, name: str, klippy_: "Klippy"):
        self.name: str = name
        self._state_lock = threading.Lock()
        self._device_on: bool = False
        self._klippy: Klippy = klippy_

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


class Klippy:
    _DATA_MACRO = "bot_data"

    _SENSOR_PARAMS = {"temperature": "temperature", "target": "target", "power": "power", "speed": "speed", "rpm": "rpm"}

    _POWER_DEVICE_PARAMS = {"device": "device", "status": "status", "locked_while_printing": "locked_while_printing", "type": "type", "is_shutdown": "is_shutdown"}

    def __init__(
        self,
        config: ConfigWrapper,
        logging_handler: logging.Handler,
    ):
        self._protocol: str = "https" if config.bot_config.ssl else "http"
        self._host: str = f"{self._protocol}://{config.bot_config.host}:{config.bot_config.port}"
        self._ssl_validate: bool = config.bot_config.ssl_validate
        self._hidden_macros: List[str] = config.telegram_ui.hidden_macros + [self._DATA_MACRO]
        self._show_private_macros: bool = config.telegram_ui.show_private_macros
        self._message_parts: List[str] = config.status_message_content.content
        self._eta_source: str = config.telegram_ui.eta_source
        self._light_device: PowerDevice
        self._psu_device: PowerDevice
        self._sensors_list: List[str] = config.status_message_content.sensors
        self._heaters_list: List[str] = config.status_message_content.heaters
        self._fans_list: List[str] = config.status_message_content.fans

        self._devices_list: List[str] = config.status_message_content.moonraker_devices
        self._user: str = config.secrets.user
        self._passwd: str = config.secrets.passwd
        self._api_token: str = config.secrets.api_token

        self._dbname: str = "telegram-bot"

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
        self._thumbnail_path: str = ""

        self._jwt_token: str = ""
        self._refresh_token: str = ""

        # Todo: create sensors class!!
        self._objects_list: list = []
        self._sensors_dict: dict = {}
        self._power_devices: dict = {}

        if logging_handler:
            logger.addHandler(logging_handler)
        if config.bot_config.debug:
            logger.setLevel(logging.DEBUG)

        self._auth_moonraker()

    def prepare_sens_dict_subscribe(self):
        self._sensors_dict = {}
        sens_dict = {}

        for elem in self._objects_list:
            for heat in self._heaters_list:
                if elem.split(" ")[-1] == heat:
                    sens_dict[elem] = None
            for sens in self._sensors_list:
                if elem.split(" ")[-1] == sens and "sensor" in elem:  # Todo: add adc\thermistor
                    sens_dict[elem] = None
            for fan in self._fans_list:
                if elem.split(" ")[-1] == fan and "fan" in elem:
                    sens_dict[elem] = None

        return sens_dict

    def _filament_weight_used(self) -> float:
        return self.filament_weight * (self.filament_used / self.filament_total)

    @property
    def psu_device(self) -> PowerDevice:
        return self._psu_device

    @psu_device.setter
    def psu_device(self, psu_device: PowerDevice):
        self._psu_device = psu_device

    @property
    def light_device(self) -> PowerDevice:
        return self._light_device

    @light_device.setter
    def light_device(self, light_device: PowerDevice):
        self._light_device = light_device

    @property
    def connected(self) -> bool:
        return self._connected

    @connected.setter
    def connected(self, new_value: bool) -> None:
        self._connected = new_value
        self.printing = False
        self.paused = False
        self._reset_file_info()
        self._update_printer_objects()

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
        elif self._api_token:
            heads = {"X-Api-Key": self._api_token}
        return heads

    @property
    def one_shot_token(self) -> str:
        if (not self._user and not self._jwt_token) and not self._api_token:
            return ""

        resp = requests.get(f"{self._host}/access/oneshot_token", headers=self._headers, timeout=15, verify=self._ssl_validate)
        if resp.ok:
            res = f"?token={orjson.loads(resp.text)['result']}"
        else:
            logger.error(resp.reason)
            res = ""
        return res

    def _update_printer_objects(self):
        resp = self.make_request("GET", "/printer/objects/list")
        if resp.ok:
            self._objects_list = orjson.loads(resp.text)["result"]["objects"]

    def _reset_file_info(self) -> None:
        self.printing_duration = 0.0
        self.printing_progress = 0.0
        self.printing_height = 0.0
        self._printing_filename = ""
        self.file_estimated_time = 0.0
        self.file_print_start_time = 0.0
        self.vsd_progress = 0.0

        self.filament_used = 0.0
        self.filament_total = 0.0
        self.filament_weight = 0.0
        self._thumbnail_path = ""

    @property
    def printing_filename(self) -> str:
        return self._printing_filename

    @printing_filename.setter
    def printing_filename(self, new_value: str):
        if not new_value:
            logger.info("'filename' has the same value as the current: %s", new_value)
            self._reset_file_info()
            return

        response = self.make_request("GET", f"/server/files/metadata?filename={urllib.parse.quote(new_value)}")
        # Todo: add response status check!
        if not response.ok:
            logger.warning("bad response for file request %s", response.reason)
        resp = orjson.loads(response.text)["result"]
        self._printing_filename = new_value
        self.file_estimated_time = resp["estimated_time"] if resp["estimated_time"] else 0.0
        self.file_print_start_time = resp["print_start_time"] if resp["print_start_time"] else time.time()
        self.filament_total = resp["filament_total"] if "filament_total" in resp else 0.0
        self.filament_weight = resp["filament_weight_total"] if "filament_weight_total" in resp else 0.0

        if "thumbnails" in resp and "filename" in resp:
            thumb = max(resp["thumbnails"], key=lambda el: el["size"])
            file_dir = resp["filename"].rpartition("/")[0]
            if file_dir:
                self._thumbnail_path = f'{file_dir}/{thumb["relative_path"]}'
            else:
                self._thumbnail_path = thumb["relative_path"]
        else:
            if "filename" not in resp:
                logger.error('"filename" field is not present in response: %s', resp)
            if "thumbnails" not in resp:
                logger.error('"thumbnails" field is not present in response: %s', resp)

    @property
    def printing_filename_with_time(self) -> str:
        return f"{self._printing_filename}_{datetime.fromtimestamp(self.file_print_start_time):%Y-%m-%d_%H-%M}"

    def _get_full_marco_list(self) -> List[str]:
        macro_lines = list(filter(lambda it: "gcode_macro" in it, self._objects_list))
        loaded_macros = list(map(lambda el: el.split(" ")[1].upper(), macro_lines))
        return loaded_macros

    def _get_marco_list(self) -> List[str]:
        return [key for key in self._get_full_marco_list() if key not in self._hidden_macros and (True if self._show_private_macros else not key.startswith("_"))]

    def _auth_moonraker(self) -> None:
        if not self._user or not self._passwd:
            return
        # TOdo: add try catch
        res = requests.post(f"{self._host}/access/login", json={"username": self._user, "password": self._passwd}, timeout=15, verify=self._ssl_validate)
        if res.ok:
            res_result = orjson.loads(res.text)["result"]
            self._jwt_token = res_result["token"]
            self._refresh_token = res_result["refresh_token"]
        else:
            logger.error(res.reason)

    def _refresh_moonraker_token(self) -> None:
        if not self._refresh_token:
            return
        res = requests.post(f"{self._host}/access/refresh_jwt", data=orjson.dumps({"refresh_token": self._refresh_token}), timeout=15, verify=self._ssl_validate)
        if res.ok:
            logger.debug("JWT token successfully refreshed")
            self._jwt_token = orjson.loads(res.text)["result"]["token"]
        else:
            logger.error("Failed to refresh token: %s", res.reason)

    def make_request(self, method, url_path, json=None, headers=None, files=None, timeout=30, stream=None) -> requests.Response:
        _headers = headers if headers else self._headers
        res = requests.request(method, f"{self._host}{url_path}", data=json if files else orjson.dumps(json), headers=_headers, files=files, timeout=timeout, stream=stream, verify=self._ssl_validate)
        if res.status_code == 401:  # Unauthorized
            logger.debug("JWT token expired, refreshing...")
            self._refresh_moonraker_token()
            res = requests.request(
                method, f"{self._host}{url_path}", data=json if files else orjson.dumps(json), headers=_headers, files=files, timeout=timeout, stream=stream, verify=self._ssl_validate
            )
        if not res.ok:
            logger.error(res.reason)
        return res

    def check_connection(self) -> str:
        connected = False
        retries = 0
        last_reason = ""
        while not connected and retries < 10:
            try:
                response = self.make_request("GET", "/printer/info", timeout=3)
                connected = response.ok

                if connected:
                    return ""
                else:
                    last_reason = response.reason
            except Exception as ex:
                logger.error(ex, exc_info=True)
            retries += 1
            time.sleep(1)
        return f"Connection failed. {last_reason}"

    def update_sensor(self, name: str, value) -> None:
        if name not in self._sensors_dict:
            self._sensors_dict[name] = {}
        for key, val in self._SENSOR_PARAMS.items():
            if key in value:
                self._sensors_dict[name][key] = value[val]

    @staticmethod
    def _sensor_message(name: str, value) -> str:
        sens_name = re.sub(r"([A-Z]|\d|_)", r" \1", name).replace("_", "")
        message = ""

        if "power" in value:
            message = emoji.emojize(":hotsprings: ", language="alias")
        elif "speed" in value:
            message = emoji.emojize(":tornado: ", language="alias")
        elif "temperature" in value:
            message = emoji.emojize(":thermometer: ", language="alias")

        message += f"{sens_name.title()}:"

        if "temperature" in value:
            message += f" {round(value['temperature'])} \N{DEGREE SIGN}C"
        if "target" in value and value["target"] > 0.0 and abs(value["target"] - value["temperature"]) > 2:
            message += emoji.emojize(" :arrow_right: ", language="alias") + f"{round(value['target'])} \N{DEGREE SIGN}C"
        if "power" in value and value["power"] > 0.0:
            message += emoji.emojize(" :fire:", language="alias")
        if "speed" in value:
            message += f" {round(value['speed'] * 100)}%"
        if "rpm" in value and value["rpm"] is not None:
            message += f" {round(value['rpm'])} RPM"

        return message

    def update_power_device(self, name: str, value) -> None:
        if name not in self._power_devices:
            self._power_devices[name] = {}
        for key, val in self._POWER_DEVICE_PARAMS.items():
            if key in value:
                self._power_devices[name][key] = value[val]

    @staticmethod
    def _device_message(name: str, value, emoji_symbol: str = ":vertical_traffic_light:") -> str:
        message = emoji.emojize(f" {emoji_symbol} ", language="alias") + f"{name}: "
        if "status" in value:
            message += f" {value['status']} "
        if "locked_while_printing" in value and value["locked_while_printing"] == "True":
            message += emoji.emojize(" :lock: ", language="alias")
        if message:
            message += "\n"
        return message

    def _get_sensors_message(self) -> str:
        return "\n".join([self._sensor_message(n, v) for n, v in self._sensors_dict.items()]) + "\n"

    def _get_power_devices_mess(self) -> str:
        message = ""
        for name, value in self._power_devices.items():
            if name in self._devices_list:
                if name == self._light_device.name:
                    message += self._device_message(name, value, ":flashlight:")
                elif name == self._psu_device.name:
                    message += self._device_message(name, value, ":electric_plug:")
                else:
                    message += self._device_message(name, value)
        return message

    def execute_command(self, *command) -> None:
        self.make_request("POST", "/api/printer/command", json={"commands": list(map(lambda el: f"{el}", command))})

    def execute_gcode_script(self, gcode: str) -> None:
        self.make_request("GET", f"/printer/gcode/script?script={gcode}")

    def _get_eta(self) -> timedelta:
        if self._eta_source == "slicer":
            eta = int(self.file_estimated_time - self.printing_duration)
        elif self.vsd_progress > 0.0:  # eta by file
            eta = int(self.printing_duration / self.vsd_progress - self.printing_duration)
        else:
            eta = int(self.file_estimated_time)
        eta = max(eta, 0)
        return timedelta(seconds=eta)

    def _populate_with_thumb(self, thumb_path: str, message: str) -> Tuple[str, BytesIO]:
        if not thumb_path:
            img = Image.open("../imgs/nopreview.png").convert("RGB")
            logger.warning("Empty thumbnail_path")
        else:
            response = self.make_request("GET", f"/server/files/gcodes/{urllib.parse.quote(thumb_path)}", stream=True)
            if response.ok:
                response.raw.decode_content = True
                img = Image.open(response.raw).convert("RGB")
            else:
                logger.error("Thumbnail download failed for %s \n\n%s", thumb_path, response.reason)
                img = Image.open("../imgs/nopreview.png").convert("RGB")

        bio = BytesIO()
        bio.name = f"{self.printing_filename}.webp"
        img.save(bio, "JPEG", quality=95, subsampling=0, optimize=True)
        bio.seek(0)
        img.close()
        return message, bio

    def get_file_info(self, message: str = "") -> Tuple[str, BytesIO]:
        message = self.get_print_stats(message)
        return self._populate_with_thumb(self._thumbnail_path, message)

    def _get_printing_file_info(self, message_pre: str = "") -> str:
        message = f"Printing: {self.printing_filename} \n" if not message_pre else f"{message_pre}: {self.printing_filename} \n"
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
        return self._get_printing_file_info(message_pre) + self._get_sensors_message() + self._get_power_devices_mess()

    def get_status(self) -> str:
        resp = orjson.loads(self.make_request("GET", "/printer/objects/query?webhooks&print_stats&display_status").text)["result"]["status"]
        print_stats = resp["print_stats"]
        message = ""

        if print_stats["state"] == "printing":
            if not self.printing_filename:
                self.printing_filename = print_stats["filename"]
        elif print_stats["state"] == "paused":
            message += "Printing paused\n"
        elif print_stats["state"] == "complete":
            message += "Printing complete\n"
        elif print_stats["state"] == "standby":
            message += "Printer standby\n"
        elif print_stats["state"] == "error":
            message += "Printing error\n"
            if "message" in print_stats and print_stats["message"]:
                message += f"{print_stats['message']}\n"

        message += "\n"
        if self.printing_filename:
            message += self._get_printing_file_info()

        message += self._get_sensors_message()
        message += self._get_power_devices_mess()

        return message

    def get_file_info_by_name(self, filename: str, message: str) -> Tuple[str, BytesIO]:
        resp = orjson.loads(self.make_request("GET", f"/server/files/metadata?filename={urllib.parse.quote(filename)}").text)["result"]
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
                logger.error("Thumbnail relative_path and filename not found in %s", resp)

        return self._populate_with_thumb(thumb_path, message)

    def get_gcode_files(self):
        response = self.make_request("GET", "/server/files/list?root=gcodes")
        files = sorted(orjson.loads(response.text)["result"], key=lambda item: item["modified"], reverse=True)
        return files

    def upload_gcode_file(self, file: BytesIO, upload_path: str) -> bool:
        return self.make_request("POST", "/server/files/upload", files={"file": file, "root": "gcodes", "path": upload_path}).ok

    def start_printing_file(self, filename: str) -> bool:
        return self.make_request("POST", f"/printer/print/start?filename={urllib.parse.quote(filename)}").ok

    def stop_all(self) -> None:
        self._reset_file_info()

    def get_versions_info(self, bot_only: bool = False) -> str:
        response = self.make_request("GET", "/machine/update/status?refresh=false")
        if not response.ok:
            logger.warning(response.reason)
            return ""
        version_info = orjson.loads(response.text)["result"]["version_info"]
        version_message = ""
        for comp, inf in version_info.items():
            if comp == "system":
                continue
            if bot_only and comp != "moonraker-telegram-bot":
                continue
            if "full_version_string" in inf:
                version_message += f"{comp}: {inf['full_version_string']}\n"
            else:
                version_message += f"{comp}: {inf['version']}\n"
        if version_message:
            version_message += "\n"
        return version_message

    def add_bot_announcements_feed(self):
        res = self.make_request("POST", "/server/announcements/feed?name=moonraker-telegram-bot")
        if not res.ok:
            logger.warning("Failed adding announcements bot feed.\n\n%s", res.reason)

    # moonraker databse section
    def get_param_from_db(self, param_name: str):
        res = self.make_request("GET", f"/server/database/item?namespace={self._dbname}&key={param_name}")
        if res.ok:
            return orjson.loads(res.text)["result"]["value"]
        else:
            logger.error("Failed getting %s from %s \n\n%s", param_name, self._dbname, res.reason)
            # Fixme: return default value? check for 404!
            return None

    def save_param_to_db(self, param_name: str, value) -> None:
        data = {"namespace": self._dbname, "key": param_name, "value": value}
        res = self.make_request("POST", "/server/database/item", json=data)
        if not res.ok:
            logger.error("Failed saving %s to %s \n\n%s", param_name, self._dbname, res.reason)

    def delete_param_from_db(self, param_name: str) -> None:
        res = self.make_request("DELETE", f"/server/database/item?namespace={self._dbname}&key={param_name}")
        if not res.ok:
            logger.error("Failed getting %s from %s \n\n%s", param_name, self._dbname, res.reason)

    # macro data section
    def save_data_to_marco(self, lapse_size: int, filename: str, path: str) -> None:
        full_macro_list = self._get_full_marco_list()
        if self._DATA_MACRO in full_macro_list:
            self.execute_gcode_script(f"SET_GCODE_VARIABLE MACRO=bot_data VARIABLE=lapse_video_size VALUE={lapse_size}")
            self.execute_gcode_script(f"SET_GCODE_VARIABLE MACRO=bot_data VARIABLE=lapse_filename VALUE='\"{filename}\"'")
            self.execute_gcode_script(f"SET_GCODE_VARIABLE MACRO=bot_data VARIABLE=lapse_path VALUE='\"{path}\"'")

        else:
            logger.error("Marco %s not defined", self._DATA_MACRO)
