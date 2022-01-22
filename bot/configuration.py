import configparser
import os
import re
from typing import List


def _check_config(config: configparser.ConfigParser, section_name: str, known_items: List[str]):
    if not config.has_section(section_name):
        return ''
    unknwn = list(map(lambda fil: f"    {fil[0]}: {fil[1]}\n", filter(lambda el: el[0] not in known_items, config.items(section_name))))
    if unknwn:
        return f"[bot] unknown/bad items\n{''.join(unknwn)}\n"
    else:
        return ''


class BotConfig:
    _SECTION = 'bot'
    _KNOWN_ITEMS = ['server', 'socks_proxy', 'bot_token', 'chat_id', 'debug', 'log_parser', 'log_path', 'power_device', 'light_device', 'user', 'password']

    def __init__(self, config: configparser.ConfigParser):
        self.host = config.get(self._SECTION, 'server', fallback='localhost')
        self.socks_proxy = config.get(self._SECTION, 'socks_proxy', fallback='')
        self.token = config.get(self._SECTION, 'bot_token')
        self.chat_id = config.getint(self._SECTION, 'chat_id')
        self.debug = config.getboolean(self._SECTION, 'debug', fallback=False)
        self.log_parser = config.getboolean(self._SECTION, 'log_parser', fallback=False)
        self.log_path = config.get(self._SECTION, 'log_path', fallback='/tmp')
        self.poweroff_device_name = config.get(self._SECTION, 'power_device', fallback='')
        self.light_device_name = config.get(self._SECTION, 'light_device', fallback="")
        self.user = config.get(self._SECTION, 'user', fallback='')
        self.passwd = config.get(self._SECTION, 'password', fallback='')

        self.unknown_fields = _check_config(config, self._SECTION, self._KNOWN_ITEMS)


class CameraConfig:
    _SECTION = 'camera'
    _KNOWN_ITEMS = ['host', 'threads', 'flip_vertically', 'flip_horizontally', 'rotate', 'fourcc', 'video_duration', 'fps', 'light_control_timeout', 'picture_quality']

    def __init__(self, config: configparser.ConfigParser):
        self.enabled: bool = config.has_section(self._SECTION)
        self.host = config.get(self._SECTION, 'host', fallback=f"")  # Todo: remove default host?
        self.threads: int = config.getint(self._SECTION, 'threads', fallback=int(os.cpu_count() / 2))
        self.flipVertically: bool = config.getboolean(self._SECTION, 'flip_vertically', fallback=False)
        self.flipHorizontally: bool = config.getboolean(self._SECTION, 'flip_horizontally', fallback=False)
        self.rotate: str = config.get(self._SECTION, 'rotate', fallback='')
        self.fourcc: str = config.get(self._SECTION, 'fourcc', fallback='x264')
        self.videoDuration: int = config.getint(self._SECTION, 'video_duration', fallback=5)
        self.stream_fps: int = config.getint(self._SECTION, 'fps', fallback=0)
        self.light_timeout: int = config.getint(self._SECTION, 'light_control_timeout', fallback=0)
        self.picture_quality = config.get(self._SECTION, 'picture_quality', fallback='high')
        self.unknown_fields = _check_config(config, self._SECTION, self._KNOWN_ITEMS)


class NotifierConfig:
    _SECTION = 'progress_notification'
    _KNOWN_ITEMS = ['percent', 'height', 'time', 'groups', 'group_only']

    def __init__(self, config: configparser.ConfigParser):
        self.percent: int = config.getint(self._SECTION, 'percent', fallback=0)
        self.height: int = config.getint(self._SECTION, 'height', fallback=0)
        self.interval: int = config.getint(self._SECTION, 'time', fallback=0)
        self.notify_groups: list = [el.strip() for el in config.get(self._SECTION, 'groups').split(',')] if config.has_option(self._SECTION, 'groups') else list()
        self.group_only: bool = config.getboolean(self._SECTION, 'group_only', fallback=False)
        self.unknown_fields = _check_config(config, self._SECTION, self._KNOWN_ITEMS)


class TimelapseConfig:
    _SECTION = 'timelapse'
    _KNOWN_ITEMS = ['basedir', 'copy_finished_timelapse_dir', 'cleanup', 'manual_mode', 'height', 'time', 'target_fps', 'min_lapse_duration', 'max_lapse_duration', 'last_frame_duration', 'after_lapse_gcode',
                    'send_finished_lapse']

    def __init__(self, config: configparser.ConfigParser):
        self.enabled: bool = config.has_section(self._SECTION)
        self.base_dir: str = config.get(self._SECTION, 'basedir', fallback='/tmp/timelapse')  # Fixme: relative path failed! ~/timelapse
        self.ready_dir: str = config.get(self._SECTION, 'copy_finished_timelapse_dir', fallback='')  # Fixme: relative path failed! ~/timelapse
        self.cleanup: bool = config.getboolean(self._SECTION, 'cleanup', fallback=True)
        self.mode_manual: bool = config.getboolean(self._SECTION, 'manual_mode', fallback=False)
        self.height: float = config.getfloat(self._SECTION, 'height', fallback=0.0)
        self.interval: int = config.getint(self._SECTION, 'time', fallback=0)
        self.target_fps: int = config.getint(self._SECTION, 'target_fps', fallback=15)
        self.min_lapse_duration: int = config.getint(self._SECTION, 'min_lapse_duration', fallback=0)
        self.max_lapse_duration: int = config.getint(self._SECTION, 'max_lapse_duration', fallback=0)
        self.last_frame_duration: int = config.getint(self._SECTION, 'last_frame_duration', fallback=5)

        # Todo: add to runtime params section!
        self.after_lapse_gcode: str = config.get(self._SECTION, 'after_lapse_gcode', fallback='')
        self.send_finished_lapse: bool = config.getboolean(self._SECTION, 'send_finished_lapse', fallback=True)

        self.unknown_fields = _check_config(config, self._SECTION, self._KNOWN_ITEMS)


class TelegramUIConfig:
    _SECTION = 'telegram_ui'
    _KNOWN_ITEMS = ['silent_progress', 'silent_commands', 'silent_status', 'status_single_message', 'pin_status_single_message', 'status_message_content', 'buttons', 'require_confirmation_macro',
                    'include_macros_in_command_list', 'disabled_macros', 'show_hidden_macros', 'eta_source', 'status_message_sensors', 'status_message_heaters', 'status_message_devices']
    _MESSAGE_CONTENT = ['progress', 'height', 'filament_length', 'filament_weight', 'print_duration', 'eta', 'finish_time', 'm117_status', 'tgnotify_status', 'last_update_time']

    def __init__(self, config: configparser.ConfigParser):
        self.silent_progress = config.getboolean(self._SECTION, 'silent_progress', fallback=False)
        self.silent_commands = config.getboolean(self._SECTION, 'silent_commands', fallback=False)
        self.silent_status = config.getboolean(self._SECTION, 'silent_status', fallback=False)
        self.status_single_message = config.getboolean(self._SECTION, 'status_single_message', fallback=True)
        self.pin_status_single_message = config.getboolean(self._SECTION, 'pin_status_single_message', fallback=False)  # Todo: implement
        self.status_message_content: list = [el.strip() for el in config.get(self._SECTION, 'status_message_content').split(',')] if config.has_option(self._SECTION, 'status_message_content') else self._MESSAGE_CONTENT

        buttons_string = config.get(self._SECTION, 'buttons') if config.has_option(self._SECTION, 'buttons') else '[status,pause,cancel,resume],[files,emergency,macros,shutdown]'
        self.buttons = list(map(lambda el: list(map(lambda iel: f'/{iel.strip()}', el.replace('[', '').replace(']', '').split(','))), re.findall(r'\[.[^\]]*\]', buttons_string)))
        self.require_confirmation_macro = config.getboolean(self._SECTION, 'require_confirmation_macro', fallback=True)
        self.include_macros_in_command_list = config.getboolean(self._SECTION, 'include_macros_in_command_list', fallback=True)
        self.disabled_macros = [el.strip() for el in config.get(self._SECTION, 'disabled_macros').split(',')] if config.has_option(self._SECTION, 'disabled_macros') else list()
        self.show_hidden_macros = config.getboolean(self._SECTION, 'show_hidden_macros', fallback=False)
        self.eta_source: str = config.get(self._SECTION, 'eta_source', fallback='slicer')
        self.status_message_sensors: list = [el.strip() for el in config.get(self._SECTION, 'status_message_sensors').split(',')] if config.has_option(self._SECTION, 'status_message_sensors') else []
        self.status_message_heaters: list = [el.strip() for el in config.get(self._SECTION, 'status_message_heaters').split(',')] if config.has_option(self._SECTION, 'status_message_heaters') else []
        self.status_message_devices: list = [el.strip() for el in config.get(self._SECTION, 'status_message_devices').split(',')] if config.has_option(self._SECTION, 'status_message_devices') else []
        self.unknown_fields = _check_config(config, self._SECTION, self._KNOWN_ITEMS)


class ConfigWrapper:
    def __init__(self, config: configparser.ConfigParser):
        self.bot = BotConfig(config)
        self.camera = CameraConfig(config)
        self.notifications = NotifierConfig(config)
        self.timelapse = TimelapseConfig(config)
        self.telegram_ui = TelegramUIConfig(config)
        self.unknown_fields = self.bot.unknown_fields + self.camera.unknown_fields + self.notifications.unknown_fields + self.timelapse.unknown_fields + self.telegram_ui.unknown_fields
