from unittest.mock import AsyncMock, MagicMock

import pytest

from timelapse import Timelapse


@pytest.fixture
def mock_timelapse() -> Timelapse:
    config = MagicMock()
    config.timelapse.enabled = True
    config.timelapse.mode_manual = True
    config.timelapse.height = 0.0
    config.timelapse.interval = 0
    config.timelapse.target_fps = 15
    config.timelapse.limit_fps = False
    config.timelapse.min_lapse_duration = 0
    config.timelapse.max_lapse_duration = 0
    config.timelapse.last_frame_duration = 5
    config.timelapse.after_lapse_gcode = ""
    config.timelapse.send_finished_lapse = True
    config.timelapse.after_photo_gcode = ""
    config.bot_config.debug = False
    config.bot_config.max_upload_file_size = 50
    config.secrets.chat_id = 123
    config.telegram_ui.silent_progress = False

    klippy = MagicMock()
    klippy.save_param_to_db = AsyncMock()
    klippy.get_param_from_db = AsyncMock(return_value=None)
    klippy.delete_param_from_db = AsyncMock()

    camera = MagicMock()
    camera.enabled = True

    scheduler = MagicMock()
    scheduler.get_job.return_value = None

    bot = MagicMock()

    return Timelapse(config, klippy, camera, scheduler, bot, MagicMock())


@pytest.mark.asyncio
async def test_save_and_restore_state(mock_timelapse: Timelapse) -> None:
    saved_state: dict[str, object] = {}

    async def mock_save(key: str, value: object) -> None:
        saved_state[key] = value

    async def mock_get(key: str) -> object:
        return saved_state.get(key)

    mock_timelapse._klippy.save_param_to_db = AsyncMock(side_effect=mock_save)
    mock_timelapse._klippy.get_param_from_db = AsyncMock(side_effect=mock_get)

    mock_timelapse._running = True
    mock_timelapse._paused = True
    mock_timelapse._last_height = 12.5
    await mock_timelapse._save_state()

    mock_timelapse._running = False
    mock_timelapse._paused = False
    mock_timelapse._last_height = 0.0
    await mock_timelapse.restore_state()

    assert mock_timelapse._running is True
    assert mock_timelapse._paused is True
    assert mock_timelapse._last_height == 12.5


@pytest.mark.asyncio
async def test_stop_all_clears_state(mock_timelapse: Timelapse) -> None:
    mock_timelapse._running = True
    mock_timelapse.stop_all()

    assert mock_timelapse._running is False
    assert mock_timelapse._paused is False
    assert mock_timelapse._last_height == 0.0


@pytest.mark.asyncio
async def test_restore_with_no_saved_state(mock_timelapse: Timelapse) -> None:
    mock_timelapse._klippy.get_param_from_db = AsyncMock(return_value=None)

    mock_timelapse._running = False
    mock_timelapse._paused = False
    mock_timelapse._last_height = 0.0
    await mock_timelapse.restore_state()

    assert mock_timelapse._running is False
    assert mock_timelapse._paused is False
    assert mock_timelapse._last_height == 0.0


@pytest.mark.asyncio
async def test_save_clears_on_not_running(mock_timelapse: Timelapse) -> None:
    delete_mock = AsyncMock()
    save_mock = AsyncMock()
    mock_timelapse._klippy.delete_param_from_db = delete_mock
    mock_timelapse._klippy.save_param_to_db = save_mock

    mock_timelapse._running = False
    await mock_timelapse._save_state()

    delete_mock.assert_called_once_with("timelapse_state")
    save_mock.assert_not_called()
