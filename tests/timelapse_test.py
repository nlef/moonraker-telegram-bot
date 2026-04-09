from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from timelapse import Timelapse

if TYPE_CHECKING:
    from pathlib import Path

LAPSES_NAMES = ["lapse1", "lapse2", "lapse3", "lapse4"]


def make_timelapse(base_dir: Path, *, cleanup: bool = True) -> Timelapse:
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
    config.timelapse.base_dir = base_dir
    config.timelapse.ready_dir = None
    config.timelapse.cleanup = cleanup
    config.bot_config.debug = False
    config.bot_config.max_upload_file_size = 50
    config.secrets.chat_id = 123
    config.telegram_ui.silent_progress = False
    config.camera.fourcc = "h264"

    camera = MagicMock()
    camera.enabled = True

    klippy = MagicMock()
    klippy.light_device = None

    scheduler = MagicMock()
    scheduler.get_job.return_value = None

    return Timelapse(config, klippy, camera, scheduler, MagicMock(), MagicMock())


def _create_test_lapses(test_dir: Path) -> None:
    for lap in LAPSES_NAMES:
        lap_path = test_dir / lap
        lap_path.mkdir(parents=True, exist_ok=True)
        (lap_path / "lapse.lock").touch()


def test_detect_unfinished_lapses(tmp_path: Path) -> None:
    _create_test_lapses(tmp_path)
    tl = make_timelapse(tmp_path)
    lapses_list = tl.detect_unfinished_lapses()
    lapses_list.sort()
    assert lapses_list == LAPSES_NAMES


def test_cleanup_unfinished_lapses(tmp_path: Path) -> None:
    _create_test_lapses(tmp_path)
    tl = make_timelapse(tmp_path)
    tl.cleanup_unfinished_lapses()
    assert not any(tmp_path.iterdir())


def test_detect_unfinished_lapses_in_nested_folder(tmp_path: Path) -> None:
    lapse_name = "queued/parts/cube_2025-02-24_12-30"
    lapse_path = tmp_path / lapse_name
    lapse_path.mkdir(parents=True, exist_ok=True)
    (lapse_path / "lapse.lock").touch()
    tl = make_timelapse(tmp_path)
    assert tl.detect_unfinished_lapses() == [lapse_name]


def test_cleanup_unfinished_lapses_in_nested_folder(tmp_path: Path) -> None:
    lapse_path = tmp_path / "queued" / "parts" / "cube_2025-02-24_12-30"
    lapse_path.mkdir(parents=True, exist_ok=True)
    (lapse_path / "lapse.lock").touch()
    tl = make_timelapse(tmp_path)
    tl.cleanup_unfinished_lapses()
    assert not any(tmp_path.rglob("*.lock"))


def test_stray_root_lock_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "lapse.lock").touch()
    (tmp_path / "real_lapse").mkdir()
    (tmp_path / "real_lapse" / "lapse.lock").touch()
    tl = make_timelapse(tmp_path)
    assert tl.detect_unfinished_lapses() == ["real_lapse"]
    tl.cleanup_unfinished_lapses()
    assert tmp_path.exists()
    assert (tmp_path / "lapse.lock").exists()


@pytest.mark.asyncio
async def test_save_and_restore_state(tmp_path: Path) -> None:
    tl = make_timelapse(tmp_path)
    saved_state: dict[str, object] = {}

    async def mock_save(key: str, value: object) -> None:
        saved_state[key] = value

    async def mock_get(key: str) -> object:
        return saved_state.get(key)

    tl._klippy.save_param_to_db = AsyncMock(side_effect=mock_save)
    tl._klippy.get_param_from_db = AsyncMock(side_effect=mock_get)

    tl._running = True
    tl._paused = True
    tl._last_height = 12.5
    await tl._save_state()

    tl._running = False
    tl._paused = False
    tl._last_height = 0.0
    await tl.restore_state()

    assert tl._running is True
    assert tl._paused is True
    assert tl._last_height == 12.5


@pytest.mark.asyncio
async def test_stop_all_clears_state(tmp_path: Path) -> None:
    tl = make_timelapse(tmp_path)
    tl._running = True
    tl.stop_all()

    assert tl._running is False
    assert tl._paused is False
    assert tl._last_height == 0.0


@pytest.mark.asyncio
async def test_restore_with_no_saved_state(tmp_path: Path) -> None:
    tl = make_timelapse(tmp_path)
    tl._klippy.get_param_from_db = AsyncMock(return_value=None)
    await tl.restore_state()

    assert tl._running is False
    assert tl._paused is False
    assert tl._last_height == 0.0


@pytest.mark.asyncio
async def test_save_clears_on_not_running(tmp_path: Path) -> None:
    tl = make_timelapse(tmp_path)
    delete_mock = AsyncMock()
    save_mock = AsyncMock()
    tl._klippy.delete_param_from_db = delete_mock
    tl._klippy.save_param_to_db = save_mock

    tl._running = False
    await tl._save_state()

    delete_mock.assert_called_once_with("timelapse_state")
    save_mock.assert_not_called()
