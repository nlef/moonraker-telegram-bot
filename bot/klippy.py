"""Moonraker HTTP/REST client for printer control and status."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from enum import Enum
from io import BytesIO
import logging
import re
import time
from typing import TYPE_CHECKING, Any, Final, TypeVar
import urllib

import emoji
import httpx
from httpx import AsyncClient
import orjson
from PIL import Image

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from configuration import ConfigWrapper

T = TypeVar("T")

logger = logging.getLogger(__name__)


class PrintState(Enum):
    """Klipper print job states."""

    STANDBY = "standby"
    START = "start"
    PRINTING = "printing"
    FINISH = "finish"
    CANCELLED = "cancelled"
    ERROR = "error"

    @property
    def is_start(self) -> bool:
        return self == PrintState.START

    @property
    def is_finished(self) -> bool:
        return self in (PrintState.FINISH, PrintState.CANCELLED, PrintState.ERROR)


class PowerDevice:
    """Moonraker power device with async on/off control."""

    def __init__(self, name: str, klippy_: Klippy) -> None:
        self.name: str = name
        self._state_lock_async = asyncio.Lock()
        self._device_on: bool = False
        self._device_error: str = ""
        self._klippy: Klippy = klippy_

    @property
    def device_error(self) -> str:
        return self._device_error

    @property
    def device_state(self) -> bool:
        return self._device_on

    @device_state.setter
    def device_state(self, state: bool) -> None:
        self._device_on = state

    async def toggle_device(self) -> bool:
        if self.device_state:
            return await self.turn_off()
        return await self.turn_on()

    async def _switch_device(self, state: bool) -> bool:
        async with self._state_lock_async:
            res = await self._klippy.make_request("POST", f"/machine/device_power/device?device={self.name}&action={'on' if state else 'off'}")
            if res.is_success:
                self._device_on = state
                self._device_error = ""
            else:
                resp_json = orjson.loads(res.text)
                if "error" in resp_json and "message" in resp_json["error"]:
                    self._device_error = resp_json["error"]["message"]
                logger.error("Power device switch failed: %s", res)
            return self._device_on

    async def turn_on(self) -> bool:
        return await self._switch_device(True)

    async def turn_off(self) -> bool:
        return await self._switch_device(False)

    def turn_on_sync(self) -> bool:
        return self._klippy.call_async(self.turn_on())

    def turn_off_sync(self) -> bool:
        return self._klippy.call_async(self.turn_off())


class Klippy:
    """HTTP client for the Moonraker API."""

    _DATA_MACRO: Final = "bot_data"

    _SENSOR_PARAMS: Final = ["temperature", "humidity", "target", "power", "speed", "rpm"]

    _POWER_DEVICE_PARAMS: Final = ["device", "status", "locked_while_printing", "type", "is_shutdown"]
    _MAX_CONNECT_RETRIES: Final = 10

    def __init__(
        self,
        config: ConfigWrapper,
        logging_handler: logging.Handler,
    ) -> None:
        self._protocol: str = "https" if config.bot_config.ssl else "http"
        self._host: str = f"{self._protocol}://{config.bot_config.host}:{config.bot_config.port}"
        self._ssl_verify: bool = config.bot_config.ssl_verify
        self._hidden_macros: list[str] = [*config.telegram_ui.hidden_macros, self._DATA_MACRO]
        self._show_private_macros: bool = config.telegram_ui.show_private_macros
        self._message_parts: list[str] = config.status_message_content.content
        self._eta_source: str = config.telegram_ui.eta_source
        self._light_device: PowerDevice | None
        self._psu_device: PowerDevice | None
        self._sensors_list: list[str] = config.status_message_content.sensors
        self._heaters_list: list[str] = config.status_message_content.heaters
        self._fans_list: list[str] = config.status_message_content.fans

        self._devices_list: list[str] = config.status_message_content.moonraker_devices
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
        self.file_object_height: float = 0.0
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

        # TODO: create sensors class!!
        self._objects_list: list[str] = []
        self._sensors_dict: dict[str, dict[str, Any]] = {}
        self._power_devices: dict[str, Any] = {}

        if logging_handler:
            logger.addHandler(logging_handler)
        if config.bot_config.debug:
            logger.setLevel(logging.DEBUG)

        self._client: AsyncClient = AsyncClient(verify=self._ssl_verify)
        self._loop: asyncio.AbstractEventLoop | None = None

    async def async_init(self) -> None:
        self._loop = asyncio.get_running_loop()
        await self._auth_moonraker()

    def call_async(self, coro: Coroutine[Any, Any, T]) -> T:
        if self._loop is None:
            raise RuntimeError("Event loop not set. Call async_init() first.")
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def prepare_sens_dict_subscribe(self) -> dict[str, Any]:
        self._sensors_dict = {}
        sens_dict: dict[str, Any] = {}

        for elem in self._objects_list:
            for heat in self._heaters_list:
                if elem.split(" ")[-1] == heat:
                    sens_dict[elem] = None
            for sens in self._sensors_list:
                if elem.split(" ")[-1] == sens and "sensor" in elem:  # TODO: add adc\thermistor
                    sens_dict[elem] = None
            for fan in self._fans_list:
                if elem.split(" ")[-1] == fan and "fan" in elem:
                    sens_dict[elem] = None

        return sens_dict

    def _filament_weight_used(self) -> float:
        return self.filament_weight * (self.filament_used / self.filament_total)

    @property
    def psu_device(self) -> PowerDevice | None:
        return self._psu_device

    @psu_device.setter
    def psu_device(self, psu_device: PowerDevice | None) -> None:
        self._psu_device = psu_device

    @property
    def light_device(self) -> PowerDevice | None:
        return self._light_device

    @light_device.setter
    def light_device(self, light_device: PowerDevice | None) -> None:
        self._light_device = light_device

    @property
    def connected(self) -> bool:
        return self._connected

    async def on_connected(self) -> None:
        self._connected = True
        self.printing = False
        self.paused = False
        self._reset_file_info()
        await self._update_printer_objects()

    async def on_disconnected(self) -> None:
        self._connected = False
        self.printing = False
        self.paused = False
        self._reset_file_info()
        self._objects_list = []

    # TODO: save macros list until klippy restart
    @property
    def macros(self) -> list[str]:
        return self._get_macro_list()

    async def get_macros_force(self) -> list[str]:
        try:
            await self._update_printer_objects()
        except Exception:
            logger.exception("Failed to get macros force")
        return self._get_macro_list()

    @property
    def macros_all(self) -> list[str]:
        return self._get_full_macro_list()

    @property
    def auth_headers(self) -> dict[str, str]:
        if self._jwt_token:
            return {"Authorization": f"Bearer {self._jwt_token}"}
        if self._api_token:
            return {"X-Api-Key": self._api_token}
        return {}

    async def ensure_auth(self) -> None:
        """Refresh JWT token if using user/password auth. No-op for API token."""
        if self._refresh_token:
            await self._refresh_moonraker_token()

    async def _update_printer_objects(self) -> None:
        resp = await self.make_request("GET", "/printer/objects/list")
        if resp.is_success:
            self._objects_list = orjson.loads(resp.text)["result"]["objects"]

    def _reset_file_info(self) -> None:
        self.printing_duration = 0.0
        self.printing_progress = 0.0
        self.printing_height = 0.0
        self.file_object_height = 0.0
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

    async def set_printing_filename(self, new_value: str) -> None:
        if new_value == self._printing_filename:
            logger.info("'filename' has the same value as the current: %s", new_value)
            self._reset_file_info()
            return

        self._printing_filename = new_value
        response = await self.make_request("GET", f"/server/files/metadata?filename={urllib.parse.quote(new_value)}")
        if not response.is_success:
            logger.warning("bad response for file request %s", response.status_code)
            self.file_print_start_time = time.time()
            self.filament_total = 0.0
            self.filament_weight = 0.0
            return

        resp = orjson.loads(response.text)["result"]
        self.file_estimated_time = resp.get("estimated_time", 0.0)
        self.file_print_start_time = resp.get("print_start_time") or time.time()
        self.filament_total = resp.get("filament_total", 0.0)
        self.filament_weight = resp.get("filament_weight_total", 0.0)
        self.file_object_height = resp.get("object_height", 0.0)

        if "thumbnails" in resp and "filename" in resp:
            thumb = max(resp["thumbnails"], key=lambda el: el["size"])
            file_dir = resp["filename"].rpartition("/")[0]
            if file_dir:
                self._thumbnail_path = f"{file_dir}/{thumb['relative_path']}"
            else:
                self._thumbnail_path = thumb["relative_path"]
        else:
            if "filename" not in resp:
                logger.error('"filename" field is not present in response: %s', resp)
            if "thumbnails" not in resp:
                logger.info("No thumbnails in file metadata for %s", resp.get("filename", "unknown"))

    @property
    def printing_filename_with_time(self) -> str:
        return f"{self._printing_filename}_{datetime.fromtimestamp(self.file_print_start_time):%Y-%m-%d_%H-%M}"

    def _get_full_macro_list(self) -> list[str]:
        macro_lines = list(filter(lambda it: "gcode_macro" in it, self._objects_list))
        return [el.split(" ")[1].upper() for el in macro_lines]

    def _get_macro_list(self) -> list[str]:
        return [key for key in self._get_full_macro_list() if key not in self._hidden_macros and (True if self._show_private_macros else not key.startswith("_"))]

    async def _auth_moonraker(self) -> None:
        if not self._user or not self._passwd:
            return

        res = await self._client.post(f"{self._host}/access/login", json={"username": self._user, "password": self._passwd}, timeout=15)

        try:
            res.raise_for_status()
            res_result = orjson.loads(res.text)["result"]
            self._jwt_token = res_result["token"]
            self._refresh_token = res_result["refresh_token"]
        except httpx.HTTPError:
            logger.exception("Failed to auth moonraker and refresh token")

    async def _refresh_moonraker_token(self) -> None:
        if not self._refresh_token:
            return
        res = await self._client.post(f"{self._host}/access/refresh_jwt", content=orjson.dumps({"refresh_token": self._refresh_token}), timeout=15)

        try:
            res.raise_for_status()
            logger.debug("JWT token successfully refreshed")
            self._jwt_token = orjson.loads(res.text)["result"]["token"]
        except httpx.HTTPError:
            logger.exception("Failed to refresh token")

    async def make_request(self, method: str, url_path: str, json: Any = None, files: Any = None, timeout: int = 30, *, log_errors: bool = True) -> httpx.Response:
        headers = {**self.auth_headers, "Content-Type": "application/json"} if json else self.auth_headers
        content = orjson.dumps(json) if json else None
        res = await self._client.request(method, f"{self._host}{url_path}", content=content, headers=headers, files=files, timeout=timeout)
        if res.status_code == httpx.codes.UNAUTHORIZED:
            logger.debug("JWT token expired, refreshing...")
            await self._refresh_moonraker_token()
            headers = {**self.auth_headers, "Content-Type": "application/json"} if json else self.auth_headers
            res = await self._client.request(method, f"{self._host}{url_path}", content=content, headers=headers, files=files, timeout=timeout)

        if log_errors:
            try:
                res.raise_for_status()
            except httpx.HTTPError:
                logger.exception("Failed to make request asynchronously")

        return res

    async def check_connection(self) -> str:
        connected = False
        retries = 0
        last_reason = ""
        while not connected and retries < self._MAX_CONNECT_RETRIES:
            try:
                response = await self.make_request("GET", "/printer/info", timeout=3)
                connected = response.is_success

                if connected:
                    return ""
                # TODO: get reason from error handler
                last_reason = f"{response.status_code}"
            except Exception:
                logger.exception("Failed to check connection")

            retries += 1
            await asyncio.sleep(1)
        return f"Connection failed. {last_reason}"

    def update_sensor(self, name: str, value: dict[str, Any]) -> None:
        if name not in self._sensors_dict:
            self._sensors_dict[name] = {}
        for key in self._SENSOR_PARAMS:
            if key in value:
                self._sensors_dict[name][key] = value[key]

    @staticmethod
    def _sensor_message(name: str, value: dict[str, Any]) -> str:
        temp_display_threshold: Final = 2
        display_name = re.sub(r"([a-z])([A-Z])", r"\1 \2", name).replace("_", " ")
        message = ""

        if "power" in value:
            message = emoji.emojize(":hotsprings: ", language="alias")
        elif "speed" in value:
            message = emoji.emojize(":tornado: ", language="alias")
        elif "temperature" in value:
            message = emoji.emojize(":thermometer: ", language="alias")

        message += f"{display_name.title()}:"

        if "temperature" in value:
            message += f" {round(value['temperature'])} \N{DEGREE SIGN}C"
        if "target" in value and value["target"] > 0.0 and abs(value["target"] - value["temperature"]) > temp_display_threshold:
            message += emoji.emojize(" :arrow_right: ", language="alias") + f"{round(value['target'])} \N{DEGREE SIGN}C"
        if "power" in value and value["power"] > 0.0:
            message += emoji.emojize(" :fire:", language="alias")
        if "speed" in value:
            message += f" {round(value['speed'] * 100)}%"
        if "rpm" in value and value["rpm"] is not None:
            message += f" {round(value['rpm'])} RPM"
        if "humidity" in value:
            message += emoji.emojize(" :droplet: ", language="alias") + f"{round(value['humidity'])}%"

        return message

    def update_power_device(self, name: str, value: dict[str, Any]) -> None:
        if name not in self._power_devices:
            self._power_devices[name] = {}
        for key in self._POWER_DEVICE_PARAMS:
            if key in value:
                self._power_devices[name][key] = value[key]

    @staticmethod
    def _device_message(name: str, value: dict[str, Any], emoji_symbol: str = ":vertical_traffic_light:") -> str:
        message = emoji.emojize(f" {emoji_symbol} ", language="alias") + f"{name}: "
        if "status" in value:
            message += f" {value['status']} "
        if value.get("locked_while_printing"):
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
                if self._light_device and name == self._light_device.name:
                    message += self._device_message(name, value, ":flashlight:")
                elif self._psu_device and name == self._psu_device.name:
                    message += self._device_message(name, value, ":electric_plug:")
                else:
                    message += self._device_message(name, value)
        return message

    async def execute_gcode_script(self, gcode: str) -> None:
        await self.make_request("GET", f"/printer/gcode/script?script={gcode}")

    def execute_gcode_script_sync(self, gcode: str) -> None:
        self.call_async(self.execute_gcode_script(gcode))

    def _get_eta(self) -> timedelta:
        if self._eta_source == "slicer":
            eta = int(self.file_estimated_time - self.printing_duration)
        elif self.vsd_progress > 0.0:  # eta by file
            eta = int(self.printing_duration / self.vsd_progress - self.printing_duration)
        else:
            eta = int(self.file_estimated_time)
        eta = max(eta, 0)
        return timedelta(seconds=eta)

    async def _populate_with_thumb(self, thumb_path: str, message: str) -> tuple[str, BytesIO]:
        if not thumb_path:
            img = Image.open("../imgs/nopreview.png").convert("RGB")
            logger.debug("Empty thumbnail_path")
        else:
            response = await self.make_request("GET", f"/server/files/gcodes/{urllib.parse.quote(thumb_path)}")
            try:
                response.raise_for_status()
                img = Image.open(BytesIO(response.content)).convert("RGB")
            except httpx.HTTPError:
                logger.exception("Thumbnail download failed for %s", thumb_path)
                img = Image.open("../imgs/nopreview.png").convert("RGB")

        bio = BytesIO()
        bio.name = f"{self.printing_filename}.webp"
        img.save(bio, "JPEG", quality=95, subsampling=0, optimize=True)
        bio.seek(0)
        img.close()
        return message, bio

    async def get_file_info(self, state: PrintState = PrintState.PRINTING) -> tuple[str, BytesIO]:
        message = self.get_print_stats(state=state)
        return await self._populate_with_thumb(self._thumbnail_path, message)

    _STATE_TITLES: Final = {
        PrintState.START: "Printer started printing",
        PrintState.PRINTING: "Printing",
        PrintState.FINISH: "Finished printing",
        PrintState.CANCELLED: "Cancelled printing",
        PrintState.ERROR: "Printing was interrupted with an error",
    }

    def _get_printing_file_info(self, state: PrintState = PrintState.PRINTING) -> str:
        result = f"<b>{self._STATE_TITLES[state]}: {self.printing_filename}</b>\n"
        if "progress" in self._message_parts:
            result += f"Progress {int(self.printing_progress * 100)}%"
        if "height" in self._message_parts:
            show_current_height = state == PrintState.PRINTING
            if show_current_height and self.printing_height > 0.0 and self.file_object_height > 0.0:
                result += f", height: {round(self.printing_height, 2)} / {round(self.file_object_height, 2)}mm\n"
            elif self.file_object_height > 0.0:
                result += f", print height: {round(self.file_object_height, 2)}mm\n"
            elif show_current_height and self.printing_height > 0.0:
                result += f", height: {round(self.printing_height, 2)}mm\n"
            else:
                result += "\n"
        if self.filament_total > 0.0:
            if "filament_length" in self._message_parts:
                if state.is_start:
                    result += f"Filament: {round(self.filament_total / 1000, 2)}m"
                elif state.is_finished:
                    result += f"Filament used: {round(self.filament_used / 1000, 2)}m"
                else:
                    result += f"Filament: {round(self.filament_used / 1000, 2)}m / {round(self.filament_total / 1000, 2)}m"
            if self.filament_weight > 0.0 and "filament_weight" in self._message_parts:
                if state.is_start:
                    result += f", weight: {self.filament_weight}g"
                elif state.is_finished:
                    result += f", weight: {round(self._filament_weight_used(), 2)}g"
                else:
                    result += f", weight: {round(self._filament_weight_used(), 2)} / {self.filament_weight}g"
            result += "\n"
        if "print_duration" in self._message_parts and not state.is_start:
            duration_prefix = "Printed for" if state.is_finished else "Printing for"
            result += f"{duration_prefix} {timedelta(seconds=round(self.printing_duration))}\n"

        if state in (PrintState.START, PrintState.PRINTING):
            eta = self._get_eta()
            if "eta" in self._message_parts:
                result += f"Estimated time left: {eta}\n"
            if "finish_time" in self._message_parts:
                result += f"Finish at {datetime.now() + eta:%Y-%m-%d %H:%M}\n"

        return result

    def get_print_stats(self, state: PrintState = PrintState.PRINTING) -> str:
        return self._get_printing_file_info(state=state) + self._get_sensors_message() + self._get_power_devices_mess()

    async def get_status(self) -> str:
        try:
            resp = await self.make_request("GET", "/printer/objects/query?webhooks&print_stats&display_status")
            if not resp.is_success:
                resp.raise_for_status()
        except httpx.HTTPError as err:
            logger.exception("Get status failed")
            return f"Failed to get status: `{err}`"

        resp_json = orjson.loads(resp.text)
        print_stats = resp_json["result"]["status"]["print_stats"]
        message = ""

        # TODO: refactor!
        if print_stats["state"] == "printing":
            if not self.printing_filename:
                await self.set_printing_filename(print_stats["filename"])
        elif print_stats["state"] == "paused":
            message += "Printing paused\n"
        elif print_stats["state"] == "cancelled":
            message += "Printing cancelled\n"
        elif print_stats["state"] == "complete":
            message += "Printing complete\n"
        elif print_stats["state"] == "standby":
            message += "Printer standby\n"
        elif print_stats["state"] == "error":
            message += "Printing error\n"
            if print_stats.get("message"):
                message += f"{print_stats['message']}\n"

        message += "\n"
        if self.printing_filename:
            message += self._get_printing_file_info()

        message += self._get_sensors_message()
        message += self._get_power_devices_mess()

        return message

    async def get_file_info_by_name(self, filename: str, message: str) -> tuple[str, BytesIO]:
        resp = orjson.loads((await self.make_request("GET", f"/server/files/metadata?filename={urllib.parse.quote(filename)}")).text)["result"]
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

        return await self._populate_with_thumb(thumb_path, message)

    async def get_gcode_files(self) -> list[dict[str, Any]]:
        response = await self.make_request("GET", "/server/files/list?root=gcodes")
        return sorted(orjson.loads(response.text)["result"], key=lambda item: item["modified"], reverse=True)

    async def upload_gcode_file(self, file: BytesIO, upload_path: str) -> bool:
        return (await self.make_request("POST", "/server/files/upload", files={"file": file, "root": "gcodes", "path": upload_path})).is_success

    async def start_printing_file(self, filename: str) -> bool:
        return (await self.make_request("POST", f"/printer/print/start?filename={urllib.parse.quote(filename)}")).is_success

    def stop_all(self) -> None:
        self._reset_file_info()

    async def get_versions_info(self, bot_only: bool = False) -> str:
        version_message = ""
        try:
            response = await self.make_request("GET", "/machine/update/status?refresh=false")
            if not response.is_success:
                return ""
            version_info = orjson.loads(response.text)["result"]["version_info"]

            for comp, inf in version_info.items():
                if comp == "system":
                    continue
                if bot_only and comp != "moonraker-telegram-bot":
                    continue
                if "full_version_string" in inf:
                    version_message += f"{comp}: {inf['full_version_string']}\n"
                else:
                    version_message += f"{comp}: {inf['version']}\n"
        except Exception:
            logger.exception("Failed to get versions info from moonraker")

        if version_message:
            version_message += "\n"
        return version_message

    async def add_bot_announcements_feed(self) -> None:
        await self.make_request("POST", "/server/announcements/feed?name=moonraker-telegram-bot")

    # moonraker database section
    async def get_param_from_db(self, param_name: str) -> Any:
        res = await self.make_request("GET", f"/server/database/item?namespace={self._dbname}&key={param_name}", log_errors=False)
        if res.is_success:
            return orjson.loads(res.text)["result"]["value"]
        if res.status_code == httpx.codes.NOT_FOUND:
            return None
        logger.error("Failed getting %s from database: %s", param_name, res.status_code)
        return None

    async def save_param_to_db(self, param_name: str, value: Any) -> None:
        await self.make_request("POST", f"/server/database/item?namespace={self._dbname}&key={param_name}", json={"value": value})

    async def delete_param_from_db(self, param_name: str) -> None:
        res = await self.make_request("DELETE", f"/server/database/item?namespace={self._dbname}&key={param_name}", log_errors=False)
        if not res.is_success and res.status_code != httpx.codes.NOT_FOUND:
            logger.error("Failed deleting %s from database: %s", param_name, res.status_code)

    # macro data section
    async def save_data_to_macro(self, lapse_size: int, filename: str, path: str) -> None:
        full_macro_list = self._get_full_macro_list()
        if self._DATA_MACRO in full_macro_list:
            await self.execute_gcode_script(f"SET_GCODE_VARIABLE MACRO=bot_data VARIABLE=lapse_video_size VALUE={lapse_size}")
            await self.execute_gcode_script(f"SET_GCODE_VARIABLE MACRO=bot_data VARIABLE=lapse_filename VALUE='\"{filename}\"'")
            await self.execute_gcode_script(f"SET_GCODE_VARIABLE MACRO=bot_data VARIABLE=lapse_path VALUE='\"{path}\"'")

        else:
            logger.error("Macro %s not defined", self._DATA_MACRO)
