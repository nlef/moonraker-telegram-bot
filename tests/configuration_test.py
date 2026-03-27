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
