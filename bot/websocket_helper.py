from functools import wraps
import logging
import os
import random
import ssl
import time
from typing import Any, Callable, Dict, List, Optional, TypeVar

os.environ.setdefault("WEBSOCKETS_MAX_LOG_SIZE", "1048576")  # pylint: disable=C0413
os.environ.setdefault("WEBSOCKETS_BACKOFF_MAX_DELAY", "15.0")  # pylint: disable=C0413

from apscheduler.schedulers.base import BaseScheduler  # type: ignore[import-untyped]
import orjson
from websockets.asyncio.client import ClientConnection, connect
from websockets.protocol import State

from configuration import ConfigWrapper
from klippy import Klippy, PrintState
from notifications import Notifier
from timelapse import Timelapse

JSONRPC_METHOD_NOT_FOUND = -32601

# Methods that may not be available depending on Moonraker configuration
_OPTIONAL_METHODS = frozenset({"machine.device_power.devices"})

logger = logging.getLogger(__name__)


F = TypeVar("F", bound=Callable[..., Any])


def websocket_alive(func: F) -> F:
    @wraps(func)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        if self.websocket is None:
            logger.warning("Websocket call `%s` on non initialized ws", func.__name__)
            return None
        else:
            return func(self, *args, **kwargs)

    return wrapper  # type: ignore[return-value]


class WebSocketHelper:
    def __init__(
        self,
        config: ConfigWrapper,
        klippy: Klippy,
        notifier: Notifier,
        timelapse: Timelapse,
        scheduler: BaseScheduler,
        logging_handler: logging.Handler,
    ):
        self._host: str = config.bot_config.host
        self._port = config.bot_config.port
        self._protocol: str = "wss" if config.bot_config.ssl else "ws"
        self._ssl_context = ssl.create_default_context() if config.bot_config.ssl else None
        if config.bot_config.ssl_verify is False and self._ssl_context is not None:
            self._ssl_context.verify_mode = ssl.CERT_NONE
            self._ssl_context.check_hostname = False

        self._klippy: Klippy = klippy
        self._notifier: Notifier = notifier
        self._timelapse: Timelapse = timelapse
        self._scheduler: BaseScheduler = scheduler
        self._log_parser: bool = config.bot_config.log_parser

        self._ws: ClientConnection
        self._pending_requests: dict[int, str] = {}

        if config.bot_config.debug:
            logger.setLevel(logging.DEBUG)

        if logging_handler:
            logger.addHandler(logging_handler)

    @staticmethod
    def on_error(error: Exception) -> None:
        logger.error(error)

    @property
    def _next_request_id(self) -> int:
        return random.randint(0, 300000)

    async def _send_jsonrpc(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        request_id = self._next_request_id
        self._pending_requests[request_id] = method
        msg = {"jsonrpc": "2.0", "method": method, "id": request_id}
        if params:
            msg["params"] = params
        await self._ws.send(orjson.dumps(msg))

    async def subscribe(self) -> None:
        subscribe_objects = {
            "print_stats": None,
            "display_status": None,
            "toolhead": ["position"],
            "gcode_move": ["position", "gcode_position"],
            "virtual_sdcard": ["progress"],
        }

        sensors = self._klippy.prepare_sens_dict_subscribe()
        if sensors:
            subscribe_objects.update(sensors)

        await self._send_jsonrpc("printer.objects.subscribe", {"objects": subscribe_objects})

    async def on_open(self) -> None:
        await self._send_jsonrpc("printer.info")
        await self._send_jsonrpc("machine.device_power.devices")

    async def reschedule(self) -> None:
        if not self._klippy.connected and self._ws.state is State.OPEN:
            await self.on_open()

    async def stop_all(self) -> None:
        self._klippy.stop_all()
        await self._notifier.stop_all()
        self._timelapse.stop_all()

    async def status_response(self, status_resp: Dict[str, Any]) -> None:
        if "print_stats" in status_resp:
            print_stats = status_resp["print_stats"]
            if print_stats["state"] in ["printing", "paused"]:
                self._klippy.printing = True
                await self._klippy.set_printing_filename(print_stats["filename"])
                self._klippy.printing_duration = print_stats["print_duration"]
                self._klippy.filament_used = print_stats["filament_used"]
                # Todo: maybe get print start time and set start interval for job?
                self._notifier.add_notifier_timer()
                if not self._timelapse.manual_mode:
                    self._timelapse.is_running = True
                    # TOdo: manual timelapse start check?

            # Fixme: some logic error with states for klippy.paused and printing
            if print_stats["state"] == "printing":
                self._klippy.paused = False
                if not self._timelapse.manual_mode:
                    self._timelapse.paused = False
            if print_stats["state"] == "paused":
                self._klippy.paused = True
                if not self._timelapse.manual_mode:
                    self._timelapse.paused = True
        if "display_status" in status_resp:
            self._notifier.m117_status = status_resp["display_status"]["message"]
            self._klippy.printing_progress = status_resp["display_status"]["progress"]
        if "virtual_sdcard" in status_resp:
            self._klippy.vsd_progress = status_resp["virtual_sdcard"]["progress"]

        self.parse_sensors(status_resp)

    async def notify_gcode_response(self, message_params: List[str]) -> None:
        if self._timelapse.manual_mode:
            if "timelapse start" in message_params:
                if not self._klippy.printing_filename:
                    await self._klippy.get_status()
                self._timelapse.clean()
                self._timelapse.is_running = True

            if "timelapse stop" in message_params:
                self._timelapse.is_running = False
            if "timelapse pause" in message_params:
                self._timelapse.paused = True
            if "timelapse resume" in message_params:
                self._timelapse.paused = False
            if "timelapse create" in message_params:
                self._timelapse.send_timelapse()
        if "timelapse photo_and_gcode" in message_params:
            self._timelapse.take_lapse_photo(manually=True, gcode=True)
        if "timelapse photo" in message_params:
            self._timelapse.take_lapse_photo(manually=True)

        message_params_loc = message_params[0]
        if message_params_loc.startswith("tgnotify "):
            self._notifier.send_notification(message_params_loc[9:])
        if message_params_loc.startswith("tgnotify_photo "):
            self._notifier.send_notification_with_photo(message_params_loc[15:])
        if message_params_loc.startswith("tgalarm "):
            self._notifier.send_error(message_params_loc[8:])
        if message_params_loc.startswith("tgalarm_photo "):
            self._notifier.send_error_with_photo(message_params_loc[14:])
        if message_params_loc.startswith("tgnotify_status "):
            self._notifier.tgnotify_status = message_params_loc[16:]

        if message_params_loc.startswith("set_timelapse_params "):
            await self._timelapse.parse_timelapse_params(message_params_loc)
        if message_params_loc.startswith("set_notify_params "):
            await self._notifier.parse_notification_params(message_params_loc)
        if message_params_loc.startswith("tgcustom_keyboard "):
            await self._notifier.send_custom_inline_keyboard(message_params_loc)

        if message_params_loc.startswith("tg_send_image"):
            self._notifier.send_image(message_params_loc)
        if message_params_loc.startswith("tg_send_video"):
            self._notifier.send_video(message_params_loc)
        if message_params_loc.startswith("tg_send_document"):
            self._notifier.send_document(message_params_loc)

    async def notify_status_update(self, message_params: List[Dict[str, Any]]) -> None:
        message_params_loc = message_params[0]
        if "display_status" in message_params_loc:
            if "message" in message_params_loc["display_status"]:
                self._notifier.m117_status = message_params_loc["display_status"]["message"]
            if "progress" in message_params_loc["display_status"]:
                self._klippy.printing_progress = message_params_loc["display_status"]["progress"]
                self._notifier.schedule_notification(progress=int(message_params_loc["display_status"]["progress"] * 100))

        if "toolhead" in message_params_loc and "position" in message_params_loc["toolhead"]:
            pass
        if "gcode_move" in message_params_loc and "gcode_position" in message_params_loc["gcode_move"]:
            position_z = message_params_loc["gcode_move"]["gcode_position"][2]
            self._klippy.printing_height = position_z
            self._notifier.schedule_notification(position_z=round(position_z, 2))
            self._timelapse.take_lapse_photo(position_z)

        if "virtual_sdcard" in message_params_loc and "progress" in message_params_loc["virtual_sdcard"]:
            self._klippy.vsd_progress = message_params_loc["virtual_sdcard"]["progress"]

        if "print_stats" in message_params_loc:
            await self.parse_print_stats(message_params)

        self.parse_sensors(message_params_loc)

    def parse_sensors(self, message_parts_loc: Dict[str, Any]) -> None:
        for sens in [key for key in message_parts_loc if key.startswith("temperature_sensor")]:
            self._klippy.update_sensor(sens.replace("temperature_sensor ", ""), message_parts_loc[sens])

        for fan in [key for key in message_parts_loc if key.startswith(("heater_fan", "controller_fan", "temperature_fan", "fan_generic")) or key == "fan"]:
            self._klippy.update_sensor(
                fan.replace("heater_fan ", "").replace("controller_fan ", "").replace("temperature_fan ", "").replace("fan_generic ", ""),
                message_parts_loc[fan],
            )

        for heater in [key for key in message_parts_loc if key.startswith(("extruder", "heater_bed", "heater_generic"))]:
            self._klippy.update_sensor(
                heater.replace("extruder ", "").replace("heater_bed ", "").replace("heater_generic ", ""),
                message_parts_loc[heater],
            )

    async def parse_print_stats(self, message_params: List[Dict[str, Any]]) -> None:
        state = ""
        print_stats_loc = message_params[0]["print_stats"]
        # Fixme:  maybe do not parse without state? history data may not be available
        # Message with filename will be sent before printing is started
        if "filename" in print_stats_loc:
            await self._klippy.set_printing_filename(print_stats_loc["filename"])
        if "filament_used" in print_stats_loc:
            self._klippy.filament_used = print_stats_loc["filament_used"]
        if "state" in print_stats_loc:
            state = print_stats_loc["state"]
        # Fixme: reset notify percent & height on finish/cancel/start
        if "print_duration" in print_stats_loc:
            self._klippy.printing_duration = print_stats_loc["print_duration"]
        if state == "printing":
            self._klippy.paused = False
            if not self._klippy.printing:
                self._klippy.printing = True
                await self._notifier.reset_notifications()
                self._notifier.add_notifier_timer()
                if not self._klippy.printing_filename:
                    await self._klippy.get_status()
                if not self._timelapse.manual_mode:
                    self._timelapse.clean()
                    self._timelapse.is_running = True
                self._notifier.send_print_start_info()

            if not self._timelapse.manual_mode:
                self._timelapse.paused = False
        elif state == "paused":
            self._klippy.paused = True
            if not self._timelapse.manual_mode:
                self._timelapse.paused = True
        # Todo: cleanup timelapse dir on cancel print!
        elif state == "complete":
            self._klippy.printing = False
            self._notifier.remove_notifier_timer()
            if not self._timelapse.manual_mode:
                self._timelapse.is_running = False
                self._timelapse.send_timelapse()
            # Fixme: add finish printing method in notifier
            self._notifier.send_print_finish()
        elif state == "error":
            self._notifier.update_status_on_abort(state=PrintState.ERROR)
            self._klippy.printing = False
            self._timelapse.is_running = False
            self._notifier.remove_notifier_timer()
            error_mess = f"Printer state change error: {print_stats_loc['state']}\n"
            if print_stats_loc.get("message"):
                self._notifier.send_error(error_mess, logs_upload=True, preformat_text=print_stats_loc["message"])
            else:
                self._notifier.send_error(error_mess, logs_upload=True)
        elif state == "standby":
            self._klippy.printing = False
            self._notifier.remove_notifier_timer()
            # Fixme: check manual mode
            self._timelapse.is_running = False
            # if not self._timelapse.manual_mode:
            # self._timelapse.send_timelapse()
            self._notifier.send_printer_status_notification(f"Printer state change: {print_stats_loc['state']} \n")
        elif state == "cancelled":
            self._notifier.update_status_on_abort(state=PrintState.CANCELLED)
            self._klippy.paused = False
            self._klippy.printing = False
            self._timelapse.is_running = False
            self._notifier.remove_notifier_timer()
            self._timelapse.clean()
            self._notifier.send_printer_status_notification("Print cancelled")
        elif state:
            logger.error("Unknown state: %s", state)

    def power_device_state(self, device: Dict[str, Any]) -> None:
        device_name = device["device"]
        device_state = bool(device["status"] == "on")
        self._klippy.update_power_device(device_name, device)
        if self._klippy.psu_device and self._klippy.psu_device.name == device_name:
            self._klippy.psu_device.device_state = device_state
        if self._klippy.light_device and self._klippy.light_device.name == device_name:
            self._klippy.light_device.device_state = device_state

    async def websocket_to_message(self, ws_message: bytes) -> None:
        json_message = orjson.loads(ws_message)

        if "error" in json_message and "id" not in json_message:
            logger.warning("Error received from websocket: %s", json_message["error"])
            return

        if "id" in json_message:
            method = self._pending_requests.pop(json_message["id"], "unknown")
            if "result" in json_message:
                message_result = json_message["result"]

                if "status" in message_result:
                    await self.status_response(message_result["status"])
                    return

                if "state" in message_result:
                    klippy_state = message_result["state"]
                    self._klippy.state = klippy_state
                    if klippy_state == "ready":
                        if self._ws.state is State.OPEN:
                            await self._klippy.set_connected(True)
                            if self._klippy.state_message:
                                self._notifier.send_error(f"Klippy changed state to {self._klippy.state}")
                                self._klippy.state_message = ""
                            await self.subscribe()
                            if self._scheduler.get_job("ws_reschedule"):
                                self._scheduler.remove_job("ws_reschedule")
                    elif klippy_state in ["error", "shutdown", "startup"]:
                        await self._klippy.set_connected(False)
                        self._scheduler.add_job(self.reschedule, "interval", seconds=2, id="ws_reschedule", replace_existing=True, coalesce=True, misfire_grace_time=10)
                        state_message = message_result["state_message"]
                        if self._klippy.state_message != state_message and klippy_state != "startup":
                            self._klippy.state_message = state_message
                            self._notifier.send_error(f"Klippy changed state to {self._klippy.state}", logs_upload=True, preformat_text=self._klippy.state_message)
                    else:
                        logger.error("Unknown klippy state: %s", klippy_state)
                        await self._klippy.set_connected(False)
                        self._scheduler.add_job(self.reschedule, "interval", seconds=2, id="ws_reschedule", replace_existing=True, coalesce=True, misfire_grace_time=10)
                    return

                if "devices" in message_result:
                    for device in message_result["devices"]:
                        self.power_device_state(device)
                    return

            if "error" in json_message:
                error = json_message["error"]
                if error.get("code") == JSONRPC_METHOD_NOT_FOUND and method in _OPTIONAL_METHODS:
                    logger.info("Optional method %s is not available", method)
                else:
                    logger.warning("Error response for %s: %s", method, error)

        else:
            message_method = json_message["method"]
            if message_method in ["notify_klippy_shutdown", "notify_klippy_disconnected"]:
                logger.warning("klippy disconnect detected with message: %s", json_message["method"])
                await self.stop_all()
                await self._klippy.set_connected(False)
                self._scheduler.add_job(self.reschedule, "interval", seconds=2, id="ws_reschedule", replace_existing=True, coalesce=True, misfire_grace_time=10)

            if "params" not in json_message:
                return

            message_params = json_message["params"]

            if message_method == "notify_gcode_response":
                await self.notify_gcode_response(message_params)

            if message_method == "notify_power_changed":
                for device in message_params:
                    self.power_device_state(device)

            if message_method == "notify_status_update":
                await self.notify_status_update(message_params)

    async def manage_printing(self, command: str) -> None:
        await self._send_jsonrpc(f"printer.print.{command}")

    async def emergency_stop_printer(self) -> None:
        await self._send_jsonrpc("printer.emergency_stop")

    async def firmware_restart_printer(self) -> None:
        await self._send_jsonrpc("printer.firmware_restart")

    async def shutdown_pi_host(self) -> None:
        await self._send_jsonrpc("machine.shutdown")

    async def reboot_pi_host(self) -> None:
        await self._send_jsonrpc("machine.reboot")

    async def restart_system_service(self, service_name: str) -> None:
        await self._send_jsonrpc("machine.services.restart", {"service": service_name})

    async def execute_ws_gcode_script(self, gcode: str) -> None:
        await self._send_jsonrpc("printer.gcode.script", {"script": gcode})

    async def parselog(self) -> None:
        with open("../telegram.log", encoding="utf-8") as file:
            lines = file.readlines()

        wslines = list(filter(lambda it: " - b'{" in it, lines))
        messages = [el.split(" - b'")[-1].replace("'\n", "").encode() for el in wslines]

        for mes in messages:
            await self.websocket_to_message(mes)
            time.sleep(0.01)

    async def run_forever_async(self) -> None:
        # Todo: use headers instead of inline token
        async for websocket in connect(
            uri=f"{self._protocol}://{self._host}:{self._port}/websocket{await self._klippy.get_one_shot_token()}",
            process_exception=self.on_error,
            open_timeout=5.0,
            ping_interval=10.0,  # as moonraker
            ping_timeout=30.0,  # as moonraker
            close_timeout=5.0,
            max_queue=1024,
            logger=logger,
            ssl=self._ssl_context,
        ):
            try:
                self._ws = websocket
                self._scheduler.add_job(self.reschedule, "interval", seconds=2, id="ws_reschedule", replace_existing=True, coalesce=True, misfire_grace_time=10)
                # async for message in self._ws:
                #     await self.websocket_to_message(message)

                while True:
                    res = await self._ws.recv(decode=False)
                    await self.websocket_to_message(res)

            except Exception as ex:
                # Todo: add some TG notification?
                logger.error(ex)
                await self._klippy.set_connected(False)
                if self._scheduler.get_job("ws_reschedule"):
                    self._scheduler.remove_job("ws_reschedule")
