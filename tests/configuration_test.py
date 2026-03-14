import configparser
import pathlib

import pytest

from bot.configuration import ConfigWrapper  # type: ignore

CONFIG_PATH = "tests/resources/telegram.conf"
CONFIG_MINIMAL_PATH = "tests/resources/telegram_minimal.conf"
CONFIG_TEMPLATE_PATH = "scripts/base_install_template"
CONFIG_WITH_SECRETS_PATH = "tests/resources/telegram_secrets.conf"
CONFIG_WITH_AUTH_PATH = "tests/resources/telegram_with_auth.conf"


def test_template_config_has_no_errors():
    config_path = pathlib.Path(CONFIG_TEMPLATE_PATH).absolute().as_posix()
    assert ConfigWrapper(config_path).configuration_errors == ""


def test_minimal_config_has_no_errors():
    config_path = pathlib.Path(CONFIG_MINIMAL_PATH).absolute().as_posix()
    assert ConfigWrapper(config_path).configuration_errors == ""


@pytest.fixture
def config_secrets_helper():
    config_path = pathlib.Path(CONFIG_WITH_SECRETS_PATH).absolute().as_posix()
    return ConfigWrapper(config_path)


def test_config_with_secrets_has_no_errors(config_secrets_helper):
    assert config_secrets_helper.configuration_errors == ""


def test_config_with_secrets_is_valid(config_secrets_helper):
    assert config_secrets_helper.secrets.chat_id == 1661233333 and config_secrets_helper.secrets.token == "23423423334:sdfgsdfg-doroasd"


@pytest.fixture
def config_helper():
    config_path = pathlib.Path(CONFIG_PATH).absolute().as_posix()
    return ConfigWrapper(config_path)


def test_config_has_no_errors(config_helper):
    assert config_helper.configuration_errors == ""


def test_config_bot_is_valid(config_helper):
    assert config_helper.secrets.chat_id == 16612341234 and config_helper.secrets.token == "23423423334:sdfgsdfg-dfgdfgsdfg"


@pytest.fixture
def config_with_auth(tmp_path):
    config_path = pathlib.Path(CONFIG_WITH_AUTH_PATH).absolute().as_posix()
    wrapper = ConfigWrapper(config_path)
    wrapper.bot_config.log_file = str(tmp_path / "bot.log")
    return wrapper


def _read_dumped_config(wrapper) -> configparser.ConfigParser:
    """Dump config to log and parse the written INI back."""
    wrapper.dump_config_to_log()
    with open(wrapper.bot_config.log_file, encoding="utf-8") as f:
        lines = [line for line in f if not line.startswith("*") and not line.startswith("Current")]
    dumped = configparser.ConfigParser()
    dumped.read_string("".join(lines))
    return dumped


def test_dump_redacts_bot_token(config_with_auth):
    dumped = _read_dumped_config(config_with_auth)
    assert dumped.get("bot", "bot_token") == "<redacted>"


def test_dump_redacts_chat_id(config_with_auth):
    dumped = _read_dumped_config(config_with_auth)
    assert dumped.get("bot", "chat_id") == "<redacted>"


def test_dump_redacts_password(config_with_auth):
    dumped = _read_dumped_config(config_with_auth)
    assert dumped.get("bot", "password") == "<redacted>"


def test_dump_redacts_api_token(config_with_auth):
    dumped = _read_dumped_config(config_with_auth)
    assert dumped.get("bot", "api_token") == "<redacted>"


def test_dump_does_not_mutate_live_config(config_with_auth):
    original_token = config_with_auth._config.get("bot", "bot_token")
    config_with_auth.dump_config_to_log()
    assert config_with_auth._config.get("bot", "bot_token") == original_token


def test_dump_does_not_add_redacted_for_unset_options(config_helper, tmp_path):
    """Options not present in config should not appear as <redacted>."""
    config_helper.bot_config.log_file = str(tmp_path / "bot.log")
    dumped = _read_dumped_config(config_helper)
    assert not dumped.has_option("bot", "password")
    assert not dumped.has_option("bot", "api_token")
