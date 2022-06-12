import configparser

import pytest

from bot.configuration import ConfigWrapper  # type: ignore

CONFIG_PATH = "tests/resources/telegram.conf"
CONFIG_MINIMAL_PATH = "tests/resources/telegram_minimal.conf"
CONFIG_TEMPLATE_PATH = "scripts/base_install_template"


def test_template_config_has_no_errors():
    conf = configparser.ConfigParser(allow_no_value=True, inline_comment_prefixes=(";", "#"))
    conf.read(CONFIG_TEMPLATE_PATH)
    assert ConfigWrapper(conf).configuration_errors == ""


def test_minimal_config_has_no_errors():
    conf = configparser.ConfigParser(allow_no_value=True, inline_comment_prefixes=(";", "#"))
    conf.read(CONFIG_MINIMAL_PATH)
    assert ConfigWrapper(conf).configuration_errors == ""


@pytest.fixture
def config_helper():
    conf = configparser.ConfigParser(allow_no_value=True, inline_comment_prefixes=(";", "#"))
    conf.read(CONFIG_PATH)
    return ConfigWrapper(conf)


def test_config_has_no_errors(config_helper):
    assert config_helper.configuration_errors == ""
