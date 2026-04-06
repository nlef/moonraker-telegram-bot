"""WebSocket client for Moonraker event subscriptions and reconnection handling."""

from __future__ import annotations

import asyncio
from http import HTTPStatus
import logging
import os
import ssl
import traceback
from typing import TYPE_CHECKING, Any, Callable, ClassVar

import aiofiles
import anyio
import orjson

os.environ.setdefault("WEBSOCKETS_MAX_LOG_SIZE", "1048576")
os.environ.setdefault("WEBSOCKETS_BACKOFF_MAX_DELAY", "15.0")

from websockets.asyncio.client import ClientConnection, connect
from websockets.client import backoff
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK, InvalidStatus
from websockets.protocol import State

from klippy import Klippy, PrintState

if TYPE_CHECKING:
    from pathlib import Path

    from apscheduler.schedulers.base import BaseScheduler  # type: ignore[import-untyped]

    from configuration import ConfigWrapper
    from notifications import Notifier
    from timelapse import Timelapse

JSONRPC_METHOD_NOT_FOUND = -32601

# Methods that may not be available depending on Moonraker configuration
_OPTIONAL_METHODS = frozenset({"machine.device_power.devices"})

# HTTP status codes that indicate a transient server error worth retrying
_RETRYABLE_HTTP_CODES = frozenset(
    {
        HTTPStatus.INTERNAL_SERVER_ERROR,
        HTTPStatus.BAD_GATEWAY,
        HTTPStatus.SERVICE_UNAVAILABLE,
        HTTPStatus.GATEWAY_TIMEOUT,
    }
)

logger = logging.getLogger(__name__)


class WebSocketHelper:
    """Subscribes to Moonraker printer events and dispatches them to the scheduler."""

    _TIMELAPSE_ASYNC_COMMANDS: ClassVar[dict[str, Callable[..., Any]]] = {
        "timelapse start": lambda self: self._timelapse_start(),
    }

    _TIMELAPSE_COMMANDS: ClassVar[dict[str, Callable[..., Any]]] = {
        "timelapse stop": lambda self: setattr(self._timelapse, "is_running", False),
        "timelapse pause": lambda self: setattr(self._timelapse, "paused", True),
        "timelapse resume": lambda self: setattr(self._timelapse, "paused", False),
        "timelapse create": lambda self: self._timelapse.send_timelapse(),
    }

    _TGNOTIFY_PREFIXES: ClassVar[dict[str, Callable[..., Any]]] = {
        "tgnotify ": lambda notifier, mess: notifier.send_notification(mess),
        "tgnotify_photo ": lambda notifier, mess: notifier.send_notification_with_photo(mess),
        "tgalarm ": lambda notifier, mess: notifier.send_error(mess),
        "tgalarm_photo ": lambda notifier, mess: notifier.send_error_with_photo(mess),
        "tgnotify_status ": lambda notifier, mess: setattr(notifier, "tgnotify_status", mess),
    }

    _TGNOTIFY_ASYNC_PREFIXES: ClassVar[dict[str, Callable[..., Any]]] = {
        "set_timelapse_params ": lambda timelapse, mess: timelapse.parse_timelapse_params(mess),
        "set_notify_params ": lambda notifier, mess: notifier.parse_notification_params(mess),
        "tgcustom_keyboard ": lambda notifier, mess: notifier.send_custom_inline_keyboard(mess),
    }

    _TG_MEDIA_PREFIXES: ClassVar[dict[str, Callable[..., Any]]] = {
        "tg_send_image": lambda notifier, mess: notifier.send_image(mess),
        "tg_send_video": lambda notifier, mess: notifier.send_video(mess),
        "tg_send_document": lambda notifier, mess: notifier.send_document(mess),
    }

    _SENSOR_TYPE_MAPPING: ClassVar[dict[str, str]] = {
        "temperature_sensor ": "temperature_sensor",
        "temperature_fan ": "fan",
        "controller_fan ": "fan",
        "fan_generic ": "fan",
        "heater_generic ": "heater",
        "heater_fan ": "fan",
        "heater_bed ": "heater",
        "extruder ": "heater",
        "fan": "fan",
        "heater_bed": "heater",
        "heater_generic": "heater",
        "extruder": "heater",
    }

    _NOTIFICATION_HANDLERS: ClassVar[dict[str, Callable[..., Any]]] = {
        "notify_gcode_response": lambda self, params: self.notify_gcode_response(params),
        "notify_power_changed": lambda self, params: [self.power_device_state(d) for d in params],
        "notify_status_update": lambda self, params: self.notify_status_update(params),
    }

    _WS_KLIPPY_STATES: ClassVar[dict[str, str]] = {
        "ready": "ready",
        "error": "error",
        "shutdown": "shutdown",
        "startup": "startup",
    }

    _WS_KLIPPY_RECONNECT_STATES: ClassVar[frozenset[str]] = frozenset({"error", "shutdown", "startup"})

    _WS_RESCHEDULE_JOB_ID: ClassVar[str] = "ws_reschedule"

    def __init__(
        self,
        config: ConfigWrapper,
        klippy: Klippy,
        notifier: Notifier,
        timelapse: Timelapse,
        scheduler: BaseScheduler,
        logging_handler: logging.Handler,
    ) -> None:
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
        self._log_file: Path = config.bot_config.log_file

        self._ws: ClientConnection
        self._pending_requests: dict[int, str] = {}
        self._request_id_counter: int = 0

        if config.bot_config.debug:
            logger.setLevel(logging.DEBUG)

        if logging_handler:
            logger.addHandler(logging_handler)

    @staticmethod
    def _is_retryable(error: Exception) -> bool:
        """Check if a connection error is transient and worth retrying.

        Network errors and transient server errors (e.g. when moonraker
        restarts) are retryable.  Everything else is fatal.
        """
        if isinstance(error, (OSError, TimeoutError)):
            return True
        return isinstance(error, InvalidStatus) and error.response.status_code in _RETRYABLE_HTTP_CODES

    @property
    def _next_request_id(self) -> int:
        self._request_id_counter += 1
        return self._request_id_counter

    async def _send_jsonrpc(self, method: str, params: dict[str, Any] | None = None) -> None:
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

    async def _update_print_stats_from_message(self, print_stats: dict[str, Any]) -> None:
        if "filename" in print_stats:
            await self._klippy.set_printing_filename(print_stats["filename"])
        if "filament_used" in print_stats:
            self._klippy.filament_used = print_stats["filament_used"]
        if "print_duration" in print_stats:
            self._klippy.printing_duration = print_stats["print_duration"]

    def _update_display_status(self, status_data: dict[str, Any], schedule_notify: bool = False) -> None:
        if "message" in status_data:
            self._notifier.m117_status = status_data["message"]
        if "progress" in status_data:
            self._klippy.printing_progress = status_data["progress"]
            if schedule_notify:
                self._notifier.schedule_notification(progress=int(status_data["progress"] * 100))

    def _update_vsd_progress(self, vsd_data: dict[str, Any]) -> None:
        if "progress" in vsd_data:
            self._klippy.vsd_progress = vsd_data["progress"]

    async def _timelapse_start(self) -> None:
        if not self._klippy.printing_filename:
            await self._klippy.get_status()
        self._timelapse.clean()
        self._timelapse.is_running = True

    async def notify_gcode_response(self, message_params: list[str]) -> None:
        if self._timelapse.manual_mode:
            for cmd, handler in self._TIMELAPSE_ASYNC_COMMANDS.items():
                if cmd in message_params:
                    await handler(self)
                    break

            for cmd, handler in self._TIMELAPSE_COMMANDS.items():
                if cmd in message_params:
                    handler(self)
                    break

        if "timelapse photo_and_gcode" in message_params:
            self._timelapse.take_lapse_photo(manually=True, with_after_gcode=True)
        elif "timelapse photo" in message_params:
            self._timelapse.take_lapse_photo(manually=True)

        message = message_params[0]

        for prefix, handler in self._TGNOTIFY_PREFIXES.items():
            if message.startswith(prefix):
                payload = message[len(prefix) :]
                handler(self._notifier, payload)
                return

        for prefix, handler in self._TGNOTIFY_ASYNC_PREFIXES.items():
            if message.startswith(prefix):
                payload = message[len(prefix) :]
                if prefix == "set_timelapse_params ":
                    await handler(self._timelapse, payload)
                else:
                    await handler(self._notifier, payload)
                return

        for prefix, handler in self._TG_MEDIA_PREFIXES.items():
            if message.startswith(prefix):
                handler(self._notifier, message)
                return

    def parse_sensors(self, message_parts_loc: dict[str, Any]) -> None:
        for key, value in message_parts_loc.items():
            sensor_type = None
            sensor_name = None

            for prefix, sens_type in self._SENSOR_TYPE_MAPPING.items():
                if key == prefix:
                    sensor_type = sens_type
                    sensor_name = key
                    break
                if prefix.endswith(" ") and key.startswith(prefix):
                    sensor_type = sens_type
                    sensor_name = key[len(prefix) :]
                    break
                if not prefix.endswith(" ") and key.startswith(prefix) and key != prefix:
                    sensor_type = sens_type
                    sensor_name = key
                    break

            if sensor_type and sensor_name is not None:
                self._klippy.update_sensor(sensor_name, value)

    async def notify_status_update(self, message_params: list[dict[str, Any]]) -> None:
        await self._handle_status_update(message_params[0], schedule_notify=True)

    async def status_response(self, status_resp: dict[str, Any]) -> None:
        await self._handle_status_update(status_resp, schedule_notify=True)

    async def _handle_status_update(self, status_data: dict[str, Any], schedule_notify: bool = False) -> None:
        if "gcode_move" in status_data and "gcode_position" in status_data["gcode_move"]:
            position_z = status_data["gcode_move"]["gcode_position"][2]
            self._klippy.printing_height = position_z
            self._notifier.schedule_notification(position_z=round(position_z, 2))
            self._timelapse.take_lapse_photo(position_z)

        if "print_stats" in status_data:
            await self.parse_print_stats(status_data)

        if "display_status" in status_data:
            self._update_display_status(status_data["display_status"], schedule_notify=schedule_notify)

        if "virtual_sdcard" in status_data:
            self._update_vsd_progress(status_data["virtual_sdcard"])

        self.parse_sensors(status_data)

    async def parse_print_stats(self, message_params_loc: dict[str, Any]) -> None:
        print_stats = message_params_loc["print_stats"]

        await self._update_print_stats_from_message(print_stats)

        if "state" not in print_stats:
            return
        state = print_stats["state"]

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
        elif state == "complete":
            self._klippy.printing = False
            self._notifier.remove_notifier_timer()
            if not self._timelapse.manual_mode:
                self._timelapse.is_running = False
                self._timelapse.send_timelapse()
            self._notifier.send_print_finish()
        elif state == "error":
            self._notifier.update_status_on_abort(state=PrintState.ERROR)
            self._klippy.printing = False
            self._timelapse.is_running = False
            self._notifier.remove_notifier_timer()
            self._notifier.send_error(
                f"Printer state change error: {state}\n",
                logs_upload=True,
                preformat_text=print_stats.get("message"),
            )
        elif state == "standby":
            self._klippy.printing = False
            self._notifier.remove_notifier_timer()
            self._timelapse.is_running = False
            self._notifier.send_printer_status_notification(f"Printer state change: {state} \n")
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

    async def power_device_state(self, device: dict[str, Any]) -> None:
        device_name = device["device"]
        device_state = bool(device["status"] == "on")
        self._klippy.update_power_device(device_name, device)
        if self._klippy.psu_device and self._klippy.psu_device.name == device_name:
            self._klippy.psu_device.device_state = device_state
        if self._klippy.light_device and self._klippy.light_device.name == device_name:
            self._klippy.light_device.device_state = device_state

    def _schedule_reconnect(self, reason: str = "") -> None:
        if self._scheduler.get_job(self._WS_RESCHEDULE_JOB_ID):
            self._scheduler.remove_job(self._WS_RESCHEDULE_JOB_ID)
        self._scheduler.add_job(
            self.reschedule,
            "interval",
            seconds=2,
            id=self._WS_RESCHEDULE_JOB_ID,
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=10,
        )
        if reason:
            logger.info("Scheduling reconnect: %s", reason)

    def _cancel_reconnect(self) -> None:
        if self._scheduler.get_job(self._WS_RESCHEDULE_JOB_ID):
            self._scheduler.remove_job(self._WS_RESCHEDULE_JOB_ID)

    async def _handle_klippy_ready_state(self) -> None:
        if self._ws.state is State.OPEN:
            await self._klippy.on_connected()
            await self._timelapse.restore_state()
            if self._klippy.state_message:
                self._notifier.send_error(f"Klippy changed state to {self._klippy.state}")
                self._klippy.state_message = ""
            await self.subscribe()
            self._cancel_reconnect()

    async def _handle_klippy_disconnected_state(self, klippy_state: str, state_message: str | None = None) -> None:
        await self._klippy.on_disconnected()
        self._schedule_reconnect(f"klippy state: {klippy_state}")
        if state_message and self._klippy.state_message != state_message and klippy_state != "startup":
            self._klippy.state_message = state_message
            self._notifier.send_error(
                f"Klippy changed state to {self._klippy.state}",
                logs_upload=True,
                preformat_text=self._klippy.state_message,
            )

    async def _handle_unknown_klippy_state(self, klippy_state: str) -> None:
        logger.error("Unknown klippy state: %s", klippy_state)
        await self._klippy.on_disconnected()
        self._schedule_reconnect(f"unknown klippy state: {klippy_state}")

    async def _handle_klippy_state_change(self, klippy_state: str, message_result: dict[str, Any]) -> None:
        self._klippy.state = klippy_state

        if klippy_state == self._WS_KLIPPY_STATES["ready"]:
            await self._handle_klippy_ready_state()
        elif klippy_state in self._WS_KLIPPY_RECONNECT_STATES:
            await self._handle_klippy_disconnected_state(klippy_state, message_result.get("state_message"))
        else:
            await self._handle_unknown_klippy_state(klippy_state)

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
                    await self._handle_klippy_state_change(message_result["state"], message_result)
                    return

                if "devices" in message_result:
                    for device in message_result["devices"]:
                        await self.power_device_state(device)
                    return

            if "error" in json_message:
                error = json_message["error"]
                if error.get("code") == JSONRPC_METHOD_NOT_FOUND and method in _OPTIONAL_METHODS:
                    logger.info("Optional method %s is not available", method)
                else:
                    logger.warning("Error response for %s: %s", method, error)

        else:
            message_method = json_message["method"]
            if message_method in ("notify_klippy_shutdown", "notify_klippy_disconnected"):
                logger.warning("klippy disconnect detected with message: %s", message_method)
                await self.stop_all()
                await self._klippy.on_disconnected()
                self._schedule_reconnect(f"moonraker notification: {message_method}")

            if "params" not in json_message:
                return

            message_params = json_message["params"]

            if handler := self._NOTIFICATION_HANDLERS.get(message_method):
                await handler(self, message_params)

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
        async with aiofiles.open(self._log_file, encoding="utf-8") as file:
            lines = await file.readlines()

        wslines = list(filter(lambda it: " - b'{" in it, lines))
        messages = [el.split(" - b'")[-1].replace("'\n", "").encode() for el in wslines]

        for mes in messages:
            await self.websocket_to_message(mes)
            await anyio.sleep(0.01)

    async def _cleanup_connection(self) -> None:
        await self._klippy.on_disconnected()
        self._cancel_reconnect()

    async def run_forever_async(self) -> None:
        if self._log_parser:
            await self.parselog()

        delays = backoff()
        was_connected = False

        while True:
            try:
                async with connect(
                    uri=f"{self._protocol}://{self._host}:{self._port}/websocket",
                    additional_headers=self._klippy.auth_headers,
                    open_timeout=5.0,
                    ping_interval=10.0,  # as moonraker
                    ping_timeout=30.0,  # as moonraker
                    close_timeout=5.0,
                    max_queue=1024,
                    logger=logger,
                    ssl=self._ssl_context,
                ) as websocket:
                    delays = backoff()
                    if was_connected:
                        logger.info("Moonraker reconnected")
                        self._notifier.send_printer_status_notification("Moonraker reconnected")
                    was_connected = True
                    self._ws = websocket
                    self._scheduler.add_job(self.reschedule, "interval", seconds=2, id=self._WS_RESCHEDULE_JOB_ID, replace_existing=True, coalesce=True, misfire_grace_time=10)

                    while True:
                        res = await self._ws.recv(decode=False)
                        await self.websocket_to_message(res)

            except ConnectionClosedOK:
                logger.warning("Moonraker disconnected, reconnecting")
                await self._cleanup_connection()

            except ConnectionClosedError as exc:
                logger.warning("WebSocket connection closed with error: %s", exc)
                await self._cleanup_connection()

            except Exception as exc:
                if not self._is_retryable(exc):
                    logger.exception("Fatal WebSocket error")
                    self._notifier.send_error(f"Fatal WebSocket error: {exc}")
                    await self._cleanup_connection()
                    raise

                delay = next(delays)
                logger.info("Connect failed; reconnecting in %.1f seconds: %s", delay, traceback.format_exception_only(type(exc), exc)[0].strip())
                await self._cleanup_connection()
                await asyncio.sleep(delay)

            await self._klippy.ensure_auth()
