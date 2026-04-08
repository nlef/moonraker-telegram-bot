import logging
from unittest.mock import AsyncMock, MagicMock

import orjson
import pytest
from websockets.protocol import State

from klippy import Klippy
from notifications import Notifier
from timelapse import Timelapse
from websocket_helper import WebSocketHelper


@pytest.fixture
def mock_config() -> MagicMock:
    config = MagicMock()
    config.bot_config.host = "localhost"
    config.bot_config.port = 7125
    config.bot_config.ssl = False
    config.bot_config.ssl_verify = True
    config.bot_config.debug = False
    config.bot_config.log_parser = False
    config.bot_config.log_file = MagicMock()
    return config


@pytest.fixture
def mock_klippy() -> MagicMock:
    klippy = MagicMock(spec=Klippy)
    klippy.printing = False
    klippy.paused = False
    klippy.printing_filename = ""
    klippy.connected = False
    klippy.auth_headers = {}
    klippy.update_sensor = MagicMock()
    klippy.update_power_device = MagicMock()
    klippy.prepare_sens_dict_subscribe = MagicMock(return_value={})
    klippy.set_printing_filename = AsyncMock()
    klippy.get_status = AsyncMock()
    klippy.stop_all = MagicMock()
    klippy.on_disconnected = AsyncMock()
    klippy.on_connected = AsyncMock()
    klippy.ensure_auth = AsyncMock()
    klippy.state = ""
    klippy.state_message = ""
    return klippy


@pytest.fixture
def mock_notifier() -> MagicMock:
    notifier = MagicMock(spec=Notifier)
    notifier.send_notification = MagicMock()
    notifier.send_error = MagicMock()
    notifier.send_printer_status_notification = MagicMock()
    notifier.add_notifier_timer = MagicMock()
    notifier.remove_notifier_timer = MagicMock()
    notifier.reset_notifications = AsyncMock()
    notifier.send_print_finish = MagicMock()
    notifier.send_print_start_info = MagicMock()
    notifier.update_status_on_abort = MagicMock()
    notifier.m117_status = ""
    notifier.schedule_notification = MagicMock()
    notifier.stop_all = AsyncMock()
    return notifier


@pytest.fixture
def mock_timelapse() -> MagicMock:
    timelapse = MagicMock(spec=Timelapse)
    timelapse.manual_mode = False
    timelapse.is_running = False
    timelapse.paused = False
    timelapse.clean = MagicMock()
    timelapse.send_timelapse = MagicMock()
    timelapse.take_lapse_photo = MagicMock()
    timelapse.parse_timelapse_params = AsyncMock()
    timelapse.restore_state = AsyncMock()
    timelapse.stop_all = MagicMock()
    return timelapse


@pytest.fixture
def mock_scheduler() -> MagicMock:
    scheduler = MagicMock()
    scheduler.get_job = MagicMock(return_value=None)
    scheduler.add_job = MagicMock()
    scheduler.remove_job = MagicMock()
    return scheduler


@pytest.fixture
def ws_helper(
    mock_config: MagicMock,
    mock_klippy: MagicMock,
    mock_notifier: MagicMock,
    mock_timelapse: MagicMock,
    mock_scheduler: MagicMock,
) -> WebSocketHelper:
    return WebSocketHelper(
        mock_config,
        mock_klippy,
        mock_notifier,
        mock_timelapse,
        mock_scheduler,
        logging.NullHandler(),
    )


class TestNotifyGcodeResponse:
    @pytest.mark.asyncio
    async def test_timelapse_start_command(self, ws_helper: WebSocketHelper) -> None:
        ws_helper._timelapse.manual_mode = True
        ws_helper._klippy.printing_filename = "test.gcode"
        ws_helper._timelapse_start = AsyncMock()

        await ws_helper.notify_gcode_response(["timelapse start"])

        ws_helper._timelapse_start.assert_called_once()

    @pytest.mark.asyncio
    async def test_timelapse_stop_command(self, ws_helper: WebSocketHelper) -> None:
        ws_helper._timelapse.manual_mode = True

        await ws_helper.notify_gcode_response(["timelapse stop"])

        assert ws_helper._timelapse.is_running is False

    @pytest.mark.asyncio
    async def test_timelapse_pause_command(self, ws_helper: WebSocketHelper) -> None:
        ws_helper._timelapse.manual_mode = True

        await ws_helper.notify_gcode_response(["timelapse pause"])

        assert ws_helper._timelapse.paused is True

    @pytest.mark.asyncio
    async def test_timelapse_resume_command(self, ws_helper: WebSocketHelper) -> None:
        ws_helper._timelapse.manual_mode = True

        await ws_helper.notify_gcode_response(["timelapse resume"])

        assert ws_helper._timelapse.paused is False

    @pytest.mark.asyncio
    async def test_timelapse_create_command(self, ws_helper: WebSocketHelper) -> None:
        ws_helper._timelapse.manual_mode = True

        await ws_helper.notify_gcode_response(["timelapse create"])

        ws_helper._timelapse.send_timelapse.assert_called_once()

    @pytest.mark.asyncio
    async def test_tgnotify_message(self, ws_helper: WebSocketHelper) -> None:
        await ws_helper.notify_gcode_response(["tgnotify test message"])

        ws_helper._notifier.send_notification.assert_called_once_with("test message")

    @pytest.mark.asyncio
    async def test_tgnotify_photo_message(self, ws_helper: WebSocketHelper) -> None:
        await ws_helper.notify_gcode_response(["tgnotify_photo photo message"])

        ws_helper._notifier.send_notification_with_photo.assert_called_once_with("photo message")

    @pytest.mark.asyncio
    async def test_tgalarm_message(self, ws_helper: WebSocketHelper) -> None:
        await ws_helper.notify_gcode_response(["tgalarm error message"])

        ws_helper._notifier.send_error.assert_called_once_with("error message")

    @pytest.mark.asyncio
    async def test_tgalarm_photo_message(self, ws_helper: WebSocketHelper) -> None:
        await ws_helper.notify_gcode_response(["tgalarm_photo error with photo"])

        ws_helper._notifier.send_error_with_photo.assert_called_once_with("error with photo")

    @pytest.mark.asyncio
    async def test_tgnotify_status(self, ws_helper: WebSocketHelper) -> None:
        await ws_helper.notify_gcode_response(["tgnotify_status some_status"])

        assert ws_helper._notifier.tgnotify_status == "some_status"

    @pytest.mark.asyncio
    async def test_timelapse_photo(self, ws_helper: WebSocketHelper) -> None:
        await ws_helper.notify_gcode_response(["timelapse photo"])

        ws_helper._timelapse.take_lapse_photo.assert_called_once_with(manually=True)

    @pytest.mark.asyncio
    async def test_timelapse_photo_with_gcode(self, ws_helper: WebSocketHelper) -> None:
        await ws_helper.notify_gcode_response(["timelapse photo_and_gcode"])

        ws_helper._timelapse.take_lapse_photo.assert_called_once_with(manually=True, with_after_gcode=True)

    @pytest.mark.asyncio
    async def test_tg_send_image(self, ws_helper: WebSocketHelper) -> None:
        await ws_helper.notify_gcode_response(["tg_send_image image_url"])

        ws_helper._notifier.send_image.assert_called_once_with("tg_send_image image_url")

    @pytest.mark.asyncio
    async def test_tg_send_video(self, ws_helper: WebSocketHelper) -> None:
        await ws_helper.notify_gcode_response(["tg_send_video video_url"])

        ws_helper._notifier.send_video.assert_called_once_with("tg_send_video video_url")

    @pytest.mark.asyncio
    async def test_tg_send_document(self, ws_helper: WebSocketHelper) -> None:
        await ws_helper.notify_gcode_response(["tg_send_document doc_url"])

        ws_helper._notifier.send_document.assert_called_once_with("tg_send_document doc_url")

    @pytest.mark.asyncio
    async def test_set_timelapse_params(self, ws_helper: WebSocketHelper) -> None:
        await ws_helper.notify_gcode_response(["set_timelapse_params param=value"])

        ws_helper._timelapse.parse_timelapse_params.assert_called_once_with("param=value")

    @pytest.mark.asyncio
    async def test_set_notify_params(self, ws_helper: WebSocketHelper) -> None:
        ws_helper._notifier.parse_notification_params = AsyncMock()

        await ws_helper.notify_gcode_response(["set_notify_params notify=value"])

        ws_helper._notifier.parse_notification_params.assert_called_once_with("notify=value")


class TestParseSensors:
    def test_temperature_sensor_parsing(self, ws_helper: WebSocketHelper) -> None:
        message = {"temperature_sensor extruder": {"temperature": 200.0}}

        ws_helper.parse_sensors(message)

        ws_helper._klippy.update_sensor.assert_called_once_with("extruder", {"temperature": 200.0})

    def test_heater_fan_parsing(self, ws_helper: WebSocketHelper) -> None:
        message = {"heater_fan fan0": {"speed": 0.5}}

        ws_helper.parse_sensors(message)

        ws_helper._klippy.update_sensor.assert_called_once_with("fan0", {"speed": 0.5})

    def test_controller_fan_parsing(self, ws_helper: WebSocketHelper) -> None:
        message = {"controller_fan controller": {"speed": 0.3}}

        ws_helper.parse_sensors(message)

        ws_helper._klippy.update_sensor.assert_called_once_with("controller", {"speed": 0.3})

    def test_temperature_fan_parsing(self, ws_helper: WebSocketHelper) -> None:
        message = {"temperature_fan rad": {"speed": 0.8}}

        ws_helper.parse_sensors(message)

        ws_helper._klippy.update_sensor.assert_called_once_with("rad", {"speed": 0.8})

    def test_fan_generic_parsing(self, ws_helper: WebSocketHelper) -> None:
        message = {"fan_generic part_cooling": {"speed": 0.6}}

        ws_helper.parse_sensors(message)

        ws_helper._klippy.update_sensor.assert_called_once_with("part_cooling", {"speed": 0.6})

    def test_fan_exact_match(self, ws_helper: WebSocketHelper) -> None:
        message = {"fan": {"speed": 0.5}}

        ws_helper.parse_sensors(message)

        ws_helper._klippy.update_sensor.assert_called_once_with("fan", {"speed": 0.5})

    def test_extruder_parsing(self, ws_helper: WebSocketHelper) -> None:
        message = {"extruder": {"temperature": 200.0, "target": 0.0, "power": 0.0}}

        ws_helper.parse_sensors(message)

        ws_helper._klippy.update_sensor.assert_called_once_with("extruder", message["extruder"])

    def test_extruder1_parsing(self, ws_helper: WebSocketHelper) -> None:
        message = {"extruder1": {"temperature": 200.0, "target": 0.0, "power": 0.0}}

        ws_helper.parse_sensors(message)

        ws_helper._klippy.update_sensor.assert_called_once_with("extruder1", message["extruder1"])

    def test_heater_bed_parsing(self, ws_helper: WebSocketHelper) -> None:
        message = {"heater_bed": {"temperature": 60.0}}

        ws_helper.parse_sensors(message)

        ws_helper._klippy.update_sensor.assert_called_once_with("heater_bed", message["heater_bed"])

    def test_heater_generic_parsing(self, ws_helper: WebSocketHelper) -> None:
        message = {"heater_generic chamber": {"temperature": 45.0}}

        ws_helper.parse_sensors(message)

        ws_helper._klippy.update_sensor.assert_called_once_with("chamber", {"temperature": 45.0})


class TestParsePrintStats:
    @pytest.mark.asyncio
    async def test_printing_state(self, ws_helper: WebSocketHelper) -> None:
        message_params = {"print_stats": {"state": "printing", "filename": "test.gcode"}}

        await ws_helper.parse_print_stats(message_params, is_initial_sync=False)

        assert ws_helper._klippy.printing is True
        assert ws_helper._klippy.paused is False

    @pytest.mark.asyncio
    async def test_paused_state(self, ws_helper: WebSocketHelper) -> None:
        ws_helper._klippy.printing = True
        message_params = {"print_stats": {"state": "paused"}}

        await ws_helper.parse_print_stats(message_params, is_initial_sync=False)

        assert ws_helper._klippy.paused is True
        assert ws_helper._klippy.printing is True

    @pytest.mark.asyncio
    async def test_complete_state(self, ws_helper: WebSocketHelper) -> None:
        message_params = {"print_stats": {"state": "complete"}}

        await ws_helper.parse_print_stats(message_params, is_initial_sync=False)

        ws_helper._notifier.send_print_finish.assert_called_once()
        ws_helper._notifier.remove_notifier_timer.assert_called_once()

    @pytest.mark.asyncio
    async def test_error_state(self, ws_helper: WebSocketHelper) -> None:
        message_params = {"print_stats": {"state": "error"}}

        await ws_helper.parse_print_stats(message_params, is_initial_sync=False)

        ws_helper._notifier.send_error.assert_called_once()
        ws_helper._notifier.update_status_on_abort.assert_called_once()

    @pytest.mark.asyncio
    async def test_standby_state(self, ws_helper: WebSocketHelper) -> None:
        message_params = {"print_stats": {"state": "standby"}}

        await ws_helper.parse_print_stats(message_params, is_initial_sync=False)

        ws_helper._notifier.send_printer_status_notification.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancelled_state(self, ws_helper: WebSocketHelper) -> None:
        message_params = {"print_stats": {"state": "cancelled"}}

        await ws_helper.parse_print_stats(message_params, is_initial_sync=False)

        ws_helper._timelapse.clean.assert_called_once()
        ws_helper._notifier.send_printer_status_notification.assert_called_once()
        ws_helper._notifier.update_status_on_abort.assert_called_once()

    @pytest.mark.asyncio
    async def test_unknown_state_logs_error(self, ws_helper: WebSocketHelper, caplog: pytest.LogCaptureFixture) -> None:
        message_params = {"print_stats": {"state": "unknown_state"}}

        await ws_helper.parse_print_stats(message_params, is_initial_sync=False)

        assert "Unknown state: unknown_state" in caplog.text

    @pytest.mark.asyncio
    async def test_empty_state_returns_early(self, ws_helper: WebSocketHelper) -> None:
        message_params = {"print_stats": {}}

        await ws_helper.parse_print_stats(message_params, is_initial_sync=False)

        assert ws_helper._klippy.printing is False


class TestStatusResponse:
    """Initial websocket status_response path — state is restored, notifications are suppressed."""

    @pytest.mark.asyncio
    async def test_standby_does_not_notify(self, ws_helper: WebSocketHelper) -> None:
        await ws_helper.status_response({"print_stats": {"state": "standby"}})

        ws_helper._notifier.send_printer_status_notification.assert_not_called()
        assert ws_helper._klippy.printing is False
        ws_helper._notifier.remove_notifier_timer.assert_called_once()

    @pytest.mark.asyncio
    async def test_printing_restores_state_and_does_not_notify(self, ws_helper: WebSocketHelper) -> None:
        await ws_helper.status_response({"print_stats": {"state": "printing", "filename": "test.gcode"}})

        assert ws_helper._klippy.printing is True
        assert ws_helper._klippy.paused is False
        assert ws_helper._timelapse.is_running is True
        ws_helper._notifier.add_notifier_timer.assert_called_once()
        ws_helper._notifier.send_print_start_info.assert_not_called()
        ws_helper._notifier.reset_notifications.assert_not_called()
        ws_helper._timelapse.clean.assert_not_called()

    @pytest.mark.asyncio
    async def test_printing_refreshes_filename_when_missing(self, ws_helper: WebSocketHelper) -> None:
        ws_helper._klippy.printing_filename = ""

        await ws_helper.status_response({"print_stats": {"state": "printing"}})

        ws_helper._klippy.get_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_paused_restores_state_and_does_not_notify(self, ws_helper: WebSocketHelper) -> None:
        # Reconnect baseline — klippy.on_connected() resets printing=False, paused=False.
        # The initial status_response must restore the full active-print state, not just the paused flag.
        await ws_helper.status_response({"print_stats": {"state": "paused"}})

        assert ws_helper._klippy.printing is True
        assert ws_helper._klippy.paused is True
        assert ws_helper._timelapse.is_running is True
        assert ws_helper._timelapse.paused is True
        ws_helper._notifier.add_notifier_timer.assert_called_once()
        ws_helper._notifier.send_printer_status_notification.assert_not_called()
        ws_helper._notifier.send_print_start_info.assert_not_called()

    @pytest.mark.asyncio
    async def test_paused_refreshes_filename_when_missing(self, ws_helper: WebSocketHelper) -> None:
        ws_helper._klippy.printing_filename = ""

        await ws_helper.status_response({"print_stats": {"state": "paused"}})

        ws_helper._klippy.get_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_complete_does_not_notify_or_upload_timelapse(self, ws_helper: WebSocketHelper) -> None:
        await ws_helper.status_response({"print_stats": {"state": "complete"}})

        assert ws_helper._klippy.printing is False
        assert ws_helper._timelapse.is_running is False
        ws_helper._notifier.remove_notifier_timer.assert_called_once()
        ws_helper._notifier.send_print_finish.assert_not_called()
        ws_helper._timelapse.send_timelapse.assert_not_called()

    @pytest.mark.asyncio
    async def test_error_does_not_notify(self, ws_helper: WebSocketHelper) -> None:
        await ws_helper.status_response({"print_stats": {"state": "error"}})

        assert ws_helper._klippy.printing is False
        assert ws_helper._timelapse.is_running is False
        ws_helper._notifier.remove_notifier_timer.assert_called_once()
        ws_helper._notifier.send_error.assert_not_called()
        ws_helper._notifier.update_status_on_abort.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancelled_does_not_notify_or_clean_lapse_dir(self, ws_helper: WebSocketHelper) -> None:
        await ws_helper.status_response({"print_stats": {"state": "cancelled"}})

        assert ws_helper._klippy.printing is False
        assert ws_helper._klippy.paused is False
        assert ws_helper._timelapse.is_running is False
        ws_helper._notifier.remove_notifier_timer.assert_called_once()
        ws_helper._notifier.send_printer_status_notification.assert_not_called()
        ws_helper._notifier.update_status_on_abort.assert_not_called()
        ws_helper._timelapse.clean.assert_not_called()


class TestNotifyStatusUpdate:
    @pytest.mark.asyncio
    async def test_display_status_update(self, ws_helper: WebSocketHelper) -> None:
        message_params = [{"display_status": {"message": "Test", "progress": 0.5}}]

        await ws_helper.notify_status_update(message_params)

        assert ws_helper._notifier.m117_status == "Test"
        assert ws_helper._klippy.printing_progress == 0.5

    @pytest.mark.asyncio
    async def test_gcode_position_update(self, ws_helper: WebSocketHelper) -> None:
        message_params = [{"gcode_move": {"gcode_position": [0, 0, 5.5]}}]

        await ws_helper.notify_status_update(message_params)

        assert ws_helper._klippy.printing_height == 5.5

    @pytest.mark.asyncio
    async def test_virtual_sdcard_progress(self, ws_helper: WebSocketHelper) -> None:
        message_params = [{"virtual_sdcard": {"progress": 0.75}}]

        await ws_helper.notify_status_update(message_params)

        assert ws_helper._klippy.vsd_progress == 0.75


class TestPowerDeviceState:
    @pytest.mark.asyncio
    async def test_power_device_state(self, ws_helper: WebSocketHelper) -> None:
        device = {"device": "printer", "status": "on"}

        await ws_helper.power_device_state(device)

        ws_helper._klippy.update_power_device.assert_called_once_with("printer", device)

    @pytest.mark.asyncio
    async def test_psu_device_state_update(self, ws_helper: WebSocketHelper) -> None:
        psu_device = MagicMock()
        psu_device.name = "printer"
        psu_device.device_state = False
        ws_helper._klippy.psu_device = psu_device

        device = {"device": "printer", "status": "on"}
        await ws_helper.power_device_state(device)

        assert psu_device.device_state is True

    @pytest.mark.asyncio
    async def test_light_device_state_update(self, ws_helper: WebSocketHelper) -> None:
        light_device = MagicMock()
        light_device.name = "light"
        light_device.device_state = False
        ws_helper._klippy.light_device = light_device

        device = {"device": "light", "status": "on"}
        await ws_helper.power_device_state(device)

        assert light_device.device_state is True


class TestTimelapseCommands:
    @pytest.mark.asyncio
    async def test_timelapse_start_gets_filename_if_missing(self, ws_helper: WebSocketHelper) -> None:
        ws_helper._timelapse.manual_mode = True
        ws_helper._klippy.printing_filename = ""
        ws_helper._timelapse_start = AsyncMock()

        await ws_helper.notify_gcode_response(["timelapse start"])

        ws_helper._timelapse_start.assert_called_once()


def _ws_notification(method: str, params: list) -> bytes:
    return orjson.dumps({"method": method, "params": params})


def _ws_response(request_id: int, result: dict) -> bytes:
    return orjson.dumps({"jsonrpc": "2.0", "id": request_id, "result": result})


class TestWebsocketToMessage:
    @pytest.mark.asyncio
    async def test_notify_power_changed(self, ws_helper: WebSocketHelper) -> None:
        devices = [{"device": "printer", "status": "on"}, {"device": "light", "status": "off"}]
        msg = _ws_notification("notify_power_changed", devices)

        await ws_helper.websocket_to_message(msg)

        assert ws_helper._klippy.update_power_device.call_count == 2

    @pytest.mark.asyncio
    async def test_notify_gcode_response_tgnotify(self, ws_helper: WebSocketHelper) -> None:
        msg = _ws_notification("notify_gcode_response", ["tgnotify hello world"])

        await ws_helper.websocket_to_message(msg)

        ws_helper._notifier.send_notification.assert_called_once_with("hello world")

    @pytest.mark.asyncio
    async def test_notify_gcode_response_set_timelapse_params(self, ws_helper: WebSocketHelper) -> None:
        msg = _ws_notification("notify_gcode_response", ["set_timelapse_params enabled=1 height=0.5"])

        await ws_helper.websocket_to_message(msg)

        ws_helper._timelapse.parse_timelapse_params.assert_called_once_with("enabled=1 height=0.5")

    @pytest.mark.asyncio
    async def test_notify_gcode_response_set_notify_params(self, ws_helper: WebSocketHelper) -> None:
        ws_helper._notifier.parse_notification_params = AsyncMock()
        msg = _ws_notification("notify_gcode_response", ["set_notify_params percent=5"])

        await ws_helper.websocket_to_message(msg)

        ws_helper._notifier.parse_notification_params.assert_called_once_with("percent=5")

    @pytest.mark.asyncio
    async def test_notify_status_update_printing(self, ws_helper: WebSocketHelper) -> None:
        msg = _ws_notification("notify_status_update", [{"print_stats": {"state": "printing", "filename": "test.gcode"}}])

        await ws_helper.websocket_to_message(msg)

        assert ws_helper._klippy.printing is True

    @pytest.mark.asyncio
    async def test_notify_status_update_error_stops_timelapse(self, ws_helper: WebSocketHelper) -> None:
        ws_helper._klippy.printing = True
        msg = _ws_notification("notify_status_update", [{"print_stats": {"state": "error"}}])

        await ws_helper.websocket_to_message(msg)

        assert ws_helper._klippy.printing is False
        assert ws_helper._timelapse.is_running is False
        ws_helper._notifier.send_error.assert_called_once()

    @pytest.mark.asyncio
    async def test_notify_status_update_sensors(self, ws_helper: WebSocketHelper) -> None:
        msg = _ws_notification("notify_status_update", [{"temperature_sensor chamber": {"temperature": 45.0}}])

        await ws_helper.websocket_to_message(msg)

        ws_helper._klippy.update_sensor.assert_called_once_with("chamber", {"temperature": 45.0})

    @pytest.mark.asyncio
    async def test_notify_klippy_shutdown(self, ws_helper: WebSocketHelper) -> None:
        msg = _ws_notification("notify_klippy_shutdown", [])

        await ws_helper.websocket_to_message(msg)

        ws_helper._klippy.stop_all.assert_called_once()
        ws_helper._klippy.on_disconnected.assert_called_once()

    @pytest.mark.asyncio
    async def test_klippy_ready_response(self, ws_helper: WebSocketHelper) -> None:
        ws_helper._ws = MagicMock()
        ws_helper._ws.state = State.OPEN
        ws_helper._ws.send = AsyncMock()
        ws_helper._pending_requests[1] = "printer.info"
        msg = _ws_response(1, {"state": "ready"})

        await ws_helper.websocket_to_message(msg)

        ws_helper._klippy.on_connected.assert_called_once()

    @pytest.mark.asyncio
    async def test_klippy_error_response(self, ws_helper: WebSocketHelper) -> None:
        ws_helper._pending_requests[1] = "printer.info"
        msg = _ws_response(1, {"state": "error", "state_message": "MCU error"})

        await ws_helper.websocket_to_message(msg)

        ws_helper._klippy.on_disconnected.assert_called_once()

    @pytest.mark.asyncio
    async def test_unknown_notification_ignored(self, ws_helper: WebSocketHelper) -> None:
        msg = _ws_notification("notify_unknown_method", ["something"])

        await ws_helper.websocket_to_message(msg)

    @pytest.mark.asyncio
    async def test_error_without_id(self, ws_helper: WebSocketHelper, caplog: pytest.LogCaptureFixture) -> None:
        msg = orjson.dumps({"error": {"message": "something went wrong"}})

        await ws_helper.websocket_to_message(msg)

        assert "something went wrong" in caplog.text

    @pytest.mark.asyncio
    async def test_state_only_message_preserves_print_data(self, ws_helper: WebSocketHelper) -> None:
        ws_helper._klippy.printing = True
        ws_helper._klippy.filament_used = 123.4
        ws_helper._klippy.printing_duration = 500.0
        msg = _ws_notification("notify_status_update", [{"print_stats": {"state": "paused"}}])

        await ws_helper.websocket_to_message(msg)

        assert ws_helper._klippy.filament_used == 123.4
        assert ws_helper._klippy.printing_duration == 500.0
