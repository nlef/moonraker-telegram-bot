"""WebSocket client for Moonraker event subscriptions and reconnection handling."""

from __future__ import annotations

import asyncio
from enum import Enum
from http import HTTPStatus
import logging
import os
import ssl
import traceback
from typing import TYPE_CHECKING, Any, ClassVar

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


class KlippyState(str, Enum):
    """Klippy firmware states reported by Moonraker."""

    READY = "ready"
    ERROR = "error"
    SHUTDOWN = "shutdown"
    STARTUP = "startup"


class WebSocketHelper:
    """Subscribes to Moonraker printer events and dispatches them to the scheduler."""

    _SENSOR_STRIP_PREFIXES: ClassVar[tuple[str, ...]] = (
        "temperature_sensor",
        "temperature_fan",
        "controller_fan",
        "fan_generic",
        "heater_generic",
        "heater_fan",
    )
    _SENSOR_KEEP_PREFIXES: ClassVar[tuple[str, ...]] = (
        "heater_bed",
        "extruder",
        "fan",
    )

    _KLIPPY_RECONNECT_STATES: ClassVar[frozenset[KlippyState]] = frozenset({KlippyState.ERROR, KlippyState.SHUTDOWN, KlippyState.STARTUP})

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
            if "timelapse start" in message_params:
                await self._timelapse_start()
            elif "timelapse stop" in message_params:
                self._timelapse.is_running = False
            elif "timelapse pause" in message_params:
                self._timelapse.paused = True
            elif "timelapse resume" in message_params:
                self._timelapse.paused = False
            elif "timelapse create" in message_params:
                self._timelapse.send_timelapse()

        if "timelapse photo_and_gcode" in message_params:
            self._timelapse.take_lapse_photo(manually=True, with_after_gcode=True)
        elif "timelapse photo" in message_params:
            self._timelapse.take_lapse_photo(manually=True)

        message = message_params[0]
        command, _, payload = message.partition(" ")

        if command == "tgnotify":
            self._notifier.send_notification(payload)
        elif command == "tgnotify_photo":
            self._notifier.send_notification_with_photo(payload)
        elif command == "tgnotify_status":
            self._notifier.tgnotify_status = payload
        elif command == "tgalarm":
            self._notifier.send_error(payload)
        elif command == "tgalarm_photo":
            self._notifier.send_error_with_photo(payload)
        elif command == "set_timelapse_params":
            await self._timelapse.parse_timelapse_params(payload)
        elif command == "set_notify_params":
            await self._notifier.parse_notification_params(payload)
        elif command == "tgcustom_keyboard":
            await self._notifier.send_custom_inline_keyboard(payload)
        elif command == "tg_send_image":
            self._notifier.send_image(message)
        elif command == "tg_send_video":
            self._notifier.send_video(message)
        elif command == "tg_send_document":
            self._notifier.send_document(message)

    def parse_sensors(self, message_parts_loc: dict[str, Any]) -> None:
        for key, value in message_parts_loc.items():
            for prefix in self._SENSOR_STRIP_PREFIXES:
                if key.startswith(prefix):
                    self._klippy.update_sensor(key[len(prefix) :].strip(), value)
                    break
            else:
                for prefix in self._SENSOR_KEEP_PREFIXES:
                    if key.startswith(prefix):
                        self._klippy.update_sensor(key, value)
                        break

    async def notify_status_update(self, message_params: list[dict[str, Any]]) -> None:
        await self._handle_status_update(message_params[0], schedule_notify=True)

    async def status_response(self, status_resp: dict[str, Any]) -> None:
        await self._handle_status_update(status_resp)

    async def _handle_status_update(self, status_data: dict[str, Any], schedule_notify: bool = False) -> None:
        if "gcode_move" in status_data and "gcode_position" in status_data["gcode_move"]:
            position_z = status_data["gcode_move"]["gcode_position"][2]
            self._klippy.printing_height = position_z
            if schedule_notify:
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

        if state == PrintState.PRINTING:
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
        elif state == PrintState.PAUSED:
            self._klippy.paused = True
            if not self._timelapse.manual_mode:
                self._timelapse.paused = True
        elif state == PrintState.COMPLETE:
            self._klippy.printing = False
            self._notifier.remove_notifier_timer()
            if not self._timelapse.manual_mode:
                self._timelapse.is_running = False
                self._timelapse.send_timelapse()
            self._notifier.send_print_finish()
        elif state == PrintState.ERROR:
            self._notifier.update_status_on_abort(state=PrintState.ERROR)
            self._klippy.printing = False
            self._timelapse.is_running = False
            self._notifier.remove_notifier_timer()
            self._notifier.send_error(
                f"Printer state change error: {state}\n",
                logs_upload=True,
                preformat_text=print_stats.get("message"),
            )
        elif state == PrintState.STANDBY:
            self._klippy.printing = False
            self._notifier.remove_notifier_timer()
            self._timelapse.is_running = False
            self._notifier.send_printer_status_notification(f"Printer state change: {state} \n")
        elif state == PrintState.CANCELLED:
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
        if state_message and self._klippy.state_message != state_message and klippy_state != KlippyState.STARTUP:
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

        if klippy_state == KlippyState.READY:
            await self._handle_klippy_ready_state()
        elif klippy_state in self._KLIPPY_RECONNECT_STATES:
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

            if message_method == "notify_gcode_response":
                await self.notify_gcode_response(message_params)
            elif message_method == "notify_power_changed":
                for device in message_params:
                    await self.power_device_state(device)
            elif message_method == "notify_status_update":
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
