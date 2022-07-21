import configparser

import pytest

from bot.configuration import ConfigWrapper  # type: ignore

CONFIG_PATH = "tests/resources/telegram.conf"
CONFIG_MINIMAL_PATH = "tests/resources/telegram_minimal.conf"
CONFIG_TEMPLATE_PATH = "scripts/base_install_template"
CONFIG_WITH_SECRETS_PATH = "tests/resources/telegram_secrets.conf"


def test_template_config_has_no_errors():
    conf = configparser.ConfigParser(allow_no_value=True, inline_comment_prefixes=(";", "#"))
    conf.read(CONFIG_TEMPLATE_PATH)
    assert ConfigWrapper(conf).configuration_errors == ""


def test_minimal_config_has_no_errors():
    conf = configparser.ConfigParser(allow_no_value=True, inline_comment_prefixes=(";", "#"))
    conf.read(CONFIG_MINIMAL_PATH)
    assert ConfigWrapper(conf).configuration_errors == ""


@pytest.fixture
def config_secrets_helper():
    conf = configparser.ConfigParser(allow_no_value=True, inline_comment_prefixes=(";", "#"))
    conf.read(CONFIG_WITH_SECRETS_PATH)
    return ConfigWrapper(conf)


def test_config_with_secrets_has_no_errors(config_secrets_helper):
    assert config_secrets_helper.configuration_errors == ""


def test_config_with_secrets_is_valid(config_secrets_helper):
    assert config_secrets_helper.secrets.chat_id == 1661233333 and config_secrets_helper.secrets.token == "23423423334:sdfgsdfg-doroasd"


@pytest.fixture
def config_helper():
    conf = configparser.ConfigParser(allow_no_value=True, inline_comment_prefixes=(";", "#"))
    conf.read(CONFIG_PATH)
    return ConfigWrapper(conf)


def test_config_has_no_errors(config_helper):
    assert config_helper.configuration_errors == ""


def test_config_bot_is_valid(config_helper):
    assert config_helper.secrets.chat_id == 16612341234 and config_helper.secrets.token == "23423423334:sdfgsdfg-dfgdfgsdfg"
