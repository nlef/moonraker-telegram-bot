import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from klippy import Klippy, PowerDevice, PrintState

test_sensors = {
    "heater": {"temperature": 155.345325234, "target": 255.343434, "power": 0.60},
    "temp": {"temperature": 155.345325234},
    "fan": {"temperature": 155.345325234, "target": 255.343434, "speed": 0.75, "rpm": 2550.255},
}


def test_sensor_message() -> None:
    heater_message = Klippy._sensor_message("heater", test_sensors["heater"])
    temp_sensor_message = Klippy._sensor_message("temp", test_sensors["temp"])
    fan_message = Klippy._sensor_message("fan", test_sensors["fan"])
    assert heater_message == "♨️ Heater: 155 °C ➡️ 255 °C 🔥"
    assert fan_message == "🌪️ Fan: 155 °C ➡️ 255 °C 75% 2550 RPM"
    assert temp_sensor_message == "🌡️ Temp: 155 °C"


@pytest.fixture
def mock_klippy() -> Klippy:
    config = MagicMock()
    config.bot_config.ssl = False
    config.bot_config.host = "localhost"
    config.bot_config.port = 7125
    config.bot_config.ssl_verify = True
    config.bot_config.debug = False
    config.telegram_ui.hidden_macros = []
    config.telegram_ui.show_private_macros = False
    config.telegram_ui.eta_source = "slicer"
    config.status_message_content.content = []
    config.status_message_content.sensors = []
    config.status_message_content.heaters = []
    config.status_message_content.fans = []
    config.status_message_content.moonraker_devices = []
    config.secrets.user = ""
    config.secrets.passwd = ""
    config.secrets.api_token = ""

    return Klippy(config, logging.NullHandler())


@pytest.mark.asyncio
async def test_jwt_refresh_updates_headers_on_retry(mock_klippy: Klippy) -> None:
    mock_klippy._jwt_token = "expired_token"
    mock_klippy._refresh_token = "valid_refresh"

    retry_headers = {}

    async def fake_refresh() -> None:
        mock_klippy._jwt_token = "new_token"

    call_count = 0

    async def fake_request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(status_code=401, request=httpx.Request(method, url))
        retry_headers.update(kwargs.get("headers", {}))
        return httpx.Response(status_code=200, text='{"result":"ok"}', request=httpx.Request(method, url))

    mock_klippy._client = MagicMock()
    mock_klippy._client.request = AsyncMock(side_effect=fake_request)
    mock_klippy._refresh_moonraker_token = AsyncMock(side_effect=fake_refresh)

    await mock_klippy.make_request("GET", "/api/test")
    assert retry_headers.get("Authorization") == "Bearer new_token"


@pytest.mark.asyncio
async def test_set_printing_filename_handles_bad_response(mock_klippy: Klippy) -> None:
    error_response = httpx.Response(
        status_code=404,
        text='{"error": {"message": "File not found"}}',
        request=httpx.Request("GET", "http://localhost:7125/server/files/metadata"),
    )
    mock_klippy.make_request = AsyncMock(return_value=error_response)

    await mock_klippy.set_printing_filename("nonexistent_file.gcode")
    assert mock_klippy.printing_filename == "nonexistent_file.gcode"


def test_progress_shows_current_and_object_height(mock_klippy: Klippy) -> None:
    mock_klippy._message_parts = ["progress", "height"]
    mock_klippy._printing_filename = "test.gcode"
    mock_klippy.printing_height = 5.2
    mock_klippy.file_object_height = 19.6
    msg = mock_klippy._get_printing_file_info()
    assert "height: 5.2 / 19.6mm" in msg


def test_start_shows_only_object_height(mock_klippy: Klippy) -> None:
    mock_klippy._message_parts = ["progress", "height"]
    mock_klippy._printing_filename = "test.gcode"
    mock_klippy.printing_height = 15.3
    mock_klippy.file_object_height = 19.6
    msg = mock_klippy._get_printing_file_info(state=PrintState.START)
    assert "print height: 19.6mm" in msg
    assert "15.3" not in msg


def test_finish_shows_only_object_height(mock_klippy: Klippy) -> None:
    mock_klippy._message_parts = ["progress", "height"]
    mock_klippy._printing_filename = "test.gcode"
    mock_klippy.printing_height = 2.0
    mock_klippy.file_object_height = 19.6
    msg = mock_klippy._get_printing_file_info(state=PrintState.FINISH)
    assert "print height: 19.6mm" in msg
    assert "2.0" not in msg


def test_height_fallback_without_object_height(mock_klippy: Klippy) -> None:
    mock_klippy._message_parts = ["progress", "height"]
    mock_klippy._printing_filename = "test.gcode"
    mock_klippy.printing_height = 5.2
    mock_klippy.file_object_height = 0.0
    msg = mock_klippy._get_printing_file_info()
    assert "height: 5.2mm" in msg


def test_no_height_when_both_zero(mock_klippy: Klippy) -> None:
    mock_klippy._message_parts = ["progress", "height"]
    mock_klippy._printing_filename = "test.gcode"
    mock_klippy.printing_height = 0.0
    mock_klippy.file_object_height = 0.0
    msg = mock_klippy._get_printing_file_info()
    assert "height" not in msg


def test_start_shows_only_total_filament(mock_klippy: Klippy) -> None:
    mock_klippy._message_parts = ["progress", "filament_length", "filament_weight"]
    mock_klippy._printing_filename = "test.gcode"
    mock_klippy.filament_total = 5000.0
    mock_klippy.filament_used = 1200.0
    mock_klippy.filament_weight = 15.0
    msg = mock_klippy._get_printing_file_info(state=PrintState.START)
    assert "Filament: 5.0m" in msg
    assert "1.2m" not in msg
    assert "weight: 15.0g" in msg
    assert "/" not in msg.split("Filament:")[1]


def test_finish_shows_only_used_filament(mock_klippy: Klippy) -> None:
    mock_klippy._message_parts = ["progress", "filament_length", "filament_weight"]
    mock_klippy._printing_filename = "test.gcode"
    mock_klippy.filament_total = 5000.0
    mock_klippy.filament_used = 1200.0
    mock_klippy.filament_weight = 15.0
    msg = mock_klippy._get_printing_file_info(state=PrintState.FINISH)
    assert "Filament used: 1.2m" in msg
    assert "5.0m" not in msg
    assert "/" not in msg.split("Filament")[1]


def test_finish_shows_printed_for(mock_klippy: Klippy) -> None:
    mock_klippy._message_parts = ["progress", "print_duration"]
    mock_klippy._printing_filename = "test.gcode"
    mock_klippy.printing_duration = 1800.0
    msg = mock_klippy._get_printing_file_info(state=PrintState.FINISH)
    assert "Printed for" in msg
    assert "Printing for" not in msg


def test_finish_hides_eta(mock_klippy: Klippy) -> None:
    mock_klippy._message_parts = ["progress", "eta", "finish_time"]
    mock_klippy._printing_filename = "test.gcode"
    mock_klippy.file_estimated_time = 3600.0
    mock_klippy.printing_duration = 1800.0
    msg = mock_klippy._get_printing_file_info(state=PrintState.FINISH)
    assert "Estimated time left" not in msg
    assert "Finish at" not in msg


def test_start_hides_duration(mock_klippy: Klippy) -> None:
    mock_klippy._message_parts = ["progress", "print_duration"]
    mock_klippy._printing_filename = "test.gcode"
    mock_klippy.printing_duration = 1800.0
    msg = mock_klippy._get_printing_file_info(state=PrintState.START)
    assert "Printing for" not in msg


def test_title_bold(mock_klippy: Klippy) -> None:
    mock_klippy._message_parts = ["progress"]
    mock_klippy._printing_filename = "test.gcode"
    msg = mock_klippy._get_printing_file_info()
    assert "<b>" in msg
    assert "</b>" in msg


def test_progress_no_trailing_zero(mock_klippy: Klippy) -> None:
    mock_klippy._message_parts = ["progress"]
    mock_klippy._printing_filename = "test.gcode"
    mock_klippy.printing_progress = 0.8
    msg = mock_klippy._get_printing_file_info()
    assert "Progress 80%" in msg
    assert "80.0%" not in msg


@pytest.mark.asyncio
async def test_turn_on_sync_delegates_to_async(mock_klippy: Klippy) -> None:
    mock_klippy._loop = asyncio.get_running_loop()
    ok_response = httpx.Response(status_code=200, text='{"result":"ok"}', request=httpx.Request("POST", "http://localhost:7125/machine/device_power/device"))
    mock_klippy.make_request = AsyncMock(return_value=ok_response)

    device = PowerDevice("test_light", mock_klippy)

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, device.turn_on_sync)

    assert result is True
    assert device.device_state is True
    mock_klippy.make_request.assert_called_once_with("POST", "/machine/device_power/device?device=test_light&action=on")


@pytest.mark.asyncio
async def test_execute_gcode_script_sync_delegates_to_async(mock_klippy: Klippy) -> None:
    mock_klippy._loop = asyncio.get_running_loop()
    ok_response = httpx.Response(status_code=200, text='{"result":"ok"}', request=httpx.Request("GET", "http://localhost:7125/printer/gcode/script"))
    mock_klippy.make_request = AsyncMock(return_value=ok_response)

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, mock_klippy.execute_gcode_script_sync, "G28")

    mock_klippy.make_request.assert_called_once_with("GET", "/printer/gcode/script?script=G28")


@pytest.mark.asyncio
async def test_turn_on_sync_reports_error(mock_klippy: Klippy) -> None:
    mock_klippy._loop = asyncio.get_running_loop()
    err_response = httpx.Response(
        status_code=400,
        text='{"error":{"message":"device busy"}}',
        request=httpx.Request("POST", "http://localhost:7125/machine/device_power/device"),
    )
    mock_klippy.make_request = AsyncMock(return_value=err_response)

    device = PowerDevice("test_light", mock_klippy)

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, device.turn_on_sync)

    assert result is False
    assert device.device_error == "device busy"


def test_device_message_shows_lock_when_locked_while_printing() -> None:
    value = {"status": "on", "locked_while_printing": True}
    msg = Klippy._device_message("psu", value)
    assert "🔒" in msg


def test_device_message_no_lock_when_not_locked() -> None:
    value = {"status": "on", "locked_while_printing": False}
    msg = Klippy._device_message("psu", value)
    assert "🔒" not in msg
