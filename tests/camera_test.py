import logging
from pathlib import Path
from unittest.mock import MagicMock

from camera import Camera, NumpyCamera


class _TestCamera(NumpyCamera):
    """Minimal concrete camera for testing."""

    def _open_capture(self) -> None:
        pass

    def _read_frame(self) -> tuple[bool, None]:
        return False, None

    def _release_capture(self) -> None:
        pass

    def _get_capture_fps(self) -> float:
        return 0.0


def make_camera(test_dir: Path) -> Camera:
    config = MagicMock()
    config.camera.enabled = True
    config.camera.host = "localhost:8080"
    config.camera.threads = 2
    config.camera.flip_vertically = True
    config.camera.flip_horizontally = True

    config.camera.fourcc = "h264"
    config.camera.video_duration = 5
    config.camera.stream_fps = 15

    config.timelapse.base_dir = test_dir
    config.timelapse.ready_dir = None
    config.timelapse.cleanup = True

    config.camera.light_timeout = 0

    config.camera.picture_quality = "high"
    config.timelapse.save_lapse_photos_as_images = False
    config.camera.rotate = ""
    config.bot_config.debug = False

    klippy = MagicMock()
    klippy.light_device = None
    klippy.printing = True
    klippy.printing_duration = 100.0

    return _TestCamera(config, klippy, logging.NullHandler())


LAPSES_NAMES = ["lapse1", "lapse2", "lapse3", "lapse4"]


def _create_test_lapses(test_dir: Path) -> None:
    for lap in LAPSES_NAMES:
        lap_path = test_dir / lap
        lap_path.mkdir(parents=True, exist_ok=True)
        (lap_path / "lapse.lock").touch()


def test_detect_unfinished_lapses(tmp_path: Path) -> None:
    _create_test_lapses(tmp_path)
    cam = make_camera(tmp_path)
    lapses_list = cam.detect_unfinished_lapses()
    lapses_list.sort()
    assert lapses_list == LAPSES_NAMES


def test_cleanup_unfinished_lapses(tmp_path: Path) -> None:
    _create_test_lapses(tmp_path)
    cam = make_camera(tmp_path)
    cam.cleanup_unfinished_lapses()
    assert not any(tmp_path.iterdir())
