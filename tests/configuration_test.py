import configparser
import pathlib
from pathlib import Path

import pytest

from configuration import ConfigWrapper

CONFIG_PATH = "tests/resources/telegram.conf"
CONFIG_MINIMAL_PATH = "tests/resources/telegram_minimal.conf"
CONFIG_TEMPLATE_PATH = "scripts/base_install_template"
CONFIG_WITH_SECRETS_PATH = "tests/resources/telegram_secrets.conf"
CONFIG_WITH_AUTH_PATH = "tests/resources/telegram_with_auth.conf"


def test_template_config_has_no_errors() -> None:
    config_path = pathlib.Path(CONFIG_TEMPLATE_PATH).absolute()
    assert ConfigWrapper(config_path).configuration_errors == ""


def test_minimal_config_has_no_errors() -> None:
    config_path = pathlib.Path(CONFIG_MINIMAL_PATH).absolute()
    assert ConfigWrapper(config_path).configuration_errors == ""


@pytest.fixture
def config_secrets_helper() -> ConfigWrapper:
    config_path = pathlib.Path(CONFIG_WITH_SECRETS_PATH).absolute()
    return ConfigWrapper(config_path)


def test_config_with_secrets_has_no_errors(config_secrets_helper: ConfigWrapper) -> None:
    assert config_secrets_helper.configuration_errors == ""


def test_config_with_secrets_is_valid(config_secrets_helper: ConfigWrapper) -> None:
    assert config_secrets_helper.secrets.chat_id == 1661233333
    assert config_secrets_helper.secrets.token == "23423423334:sdfgsdfg-doroasd"


@pytest.fixture
def config_helper() -> ConfigWrapper:
    config_path = pathlib.Path(CONFIG_PATH).absolute()
    return ConfigWrapper(config_path)


def test_config_has_no_errors(config_helper: ConfigWrapper) -> None:
    assert config_helper.configuration_errors == ""


def test_config_bot_is_valid(config_helper: ConfigWrapper) -> None:
    assert config_helper.secrets.chat_id == 16612341234
    assert config_helper.secrets.token == "23423423334:sdfgsdfg-dfgdfgsdfg"


@pytest.fixture
def config_with_auth(tmp_path: Path) -> ConfigWrapper:
    config_path = pathlib.Path(CONFIG_WITH_AUTH_PATH).absolute()
    wrapper = ConfigWrapper(config_path)
    wrapper.bot_config.log_file = tmp_path / "bot.log"
    return wrapper


def _read_dumped_config(wrapper: ConfigWrapper) -> configparser.ConfigParser:
    """Dump config to log and parse the written INI back."""
    wrapper.dump_config_to_log()
    with wrapper.bot_config.log_file.open(encoding="utf-8") as f:
        lines = [line for line in f if not line.startswith("*") and not line.startswith("Current")]
    dumped = configparser.ConfigParser()
    dumped.read_string("".join(lines))
    return dumped


def test_dump_redacts_bot_token(config_with_auth: ConfigWrapper) -> None:
    dumped = _read_dumped_config(config_with_auth)
    assert dumped.get("bot", "bot_token") == "<redacted>"


def test_dump_redacts_chat_id(config_with_auth: ConfigWrapper) -> None:
    dumped = _read_dumped_config(config_with_auth)
    assert dumped.get("bot", "chat_id") == "<redacted>"


def test_dump_redacts_password(config_with_auth: ConfigWrapper) -> None:
    dumped = _read_dumped_config(config_with_auth)
    assert dumped.get("bot", "password") == "<redacted>"


def test_dump_redacts_api_token(config_with_auth: ConfigWrapper) -> None:
    dumped = _read_dumped_config(config_with_auth)
    assert dumped.get("bot", "api_token") == "<redacted>"


def test_dump_does_not_mutate_live_config(config_with_auth: ConfigWrapper) -> None:
    original_token = config_with_auth._config.get("bot", "bot_token")
    config_with_auth.dump_config_to_log()
    assert config_with_auth._config.get("bot", "bot_token") == original_token


def test_dump_does_not_add_redacted_for_unset_options(config_helper: ConfigWrapper, tmp_path: Path) -> None:
    """Options not present in config should not appear as <redacted>."""
    config_helper.bot_config.log_file = tmp_path / "bot.log"
    dumped = _read_dumped_config(config_helper)
    assert not dumped.has_option("bot", "password")
    assert not dumped.has_option("bot", "api_token")


def test_single_camera_section(config_helper: ConfigWrapper) -> None:
    assert "default" in config_helper.cameras
    assert config_helper.cameras["default"].host
    assert config_helper.default_camera is not None
    assert config_helper.default_camera.name == "default"


def test_multi_camera_sections(tmp_path: Path) -> None:
    conf_file = tmp_path / "multi_cam.conf"
    conf_file.write_text("""\
[bot]
server: localhost
bot_token: 123:abc
chat_id: 123

[camera]
host: http://cam1/stream

[camera bed]
host: http://cam2/stream
""")
    config = ConfigWrapper(conf_file)
    assert len(config.cameras) == 2
    assert "default" in config.cameras
    assert "bed" in config.cameras
    assert config.cameras["default"].host == "http://cam1/stream"
    assert config.cameras["bed"].host == "http://cam2/stream"


def test_camera_default_named_section_reports_error(tmp_path: Path) -> None:
    conf_file = tmp_path / "named_default.conf"
    conf_file.write_text("""\
[bot]
server: localhost
bot_token: 123:abc
chat_id: 123

[camera default]
host: http://cam1/stream
""")
    config = ConfigWrapper(conf_file)
    assert "Use [camera] instead of [camera default]" in config.parsing_errors


def test_camera_default_named_section_is_ignored(tmp_path: Path) -> None:
    """`[camera default]` reports an error and is not parsed — it must not shadow `[camera]`."""
    conf_file = tmp_path / "named_default.conf"
    conf_file.write_text("""\
[bot]
server: localhost
bot_token: 123:abc
chat_id: 123

[camera]
host: http://real-default/stream

[camera default]
host: http://override/stream
""")
    config = ConfigWrapper(conf_file)
    assert "Use [camera] instead of [camera default]" in config.parsing_errors
    assert "default" in config.cameras
    assert config.cameras["default"].host == "http://real-default/stream"
    assert len(config.cameras) == 1  # `[camera default]` did not register as a second entry


def test_camera_default_named_section_alone_is_ignored(tmp_path: Path) -> None:
    """Without `[camera]`, a lone `[camera default]` still can't register itself as the default."""
    conf_file = tmp_path / "lone_default.conf"
    conf_file.write_text("""\
[bot]
server: localhost
bot_token: 123:abc
chat_id: 123

[camera default]
host: http://override/stream
""")
    config = ConfigWrapper(conf_file)
    assert "Use [camera] instead of [camera default]" in config.parsing_errors
    assert "default" not in config.cameras
    assert config.default_camera is None


def test_no_camera_section(tmp_path: Path) -> None:
    conf_file = tmp_path / "no_cam.conf"
    conf_file.write_text("""\
[bot]
server: localhost
bot_token: 123:abc
chat_id: 123
""")
    config = ConfigWrapper(conf_file)
    assert len(config.cameras) == 0
    assert config.default_camera is None
