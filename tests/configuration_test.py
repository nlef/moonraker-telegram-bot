import pathlib

import pytest

from bot.configuration import ConfigWrapper  # type: ignore

CONFIG_PATH = "tests/resources/telegram.conf"
CONFIG_MINIMAL_PATH = "tests/resources/telegram_minimal.conf"
CONFIG_TEMPLATE_PATH = "scripts/base_install_template"
CONFIG_WITH_SECRETS_PATH = "tests/resources/telegram_secrets.conf"


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
