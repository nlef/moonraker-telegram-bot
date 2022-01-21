import configparser
import os
import re


class BotConfig:
    def __init__(self, config: configparser.ConfigParser):
        self.host = config.get('bot', 'server', fallback='localhost')
        self.socks_proxy = config.get('bot', 'socks_proxy', fallback='')
        self.token = config.get('bot', 'bot_token')
        self.chat_id = config.getint('bot', 'chat_id')
        self.debug = config.getboolean('bot', 'debug', fallback=False)
        self.log_parser = config.getboolean('bot', 'log_parser', fallback=False)
        self.log_path = config.get('bot', 'log_path', fallback='/tmp')
        self.poweroff_device_name = config.get('bot', 'power_device', fallback='')
        self.light_device_name = config.get('bot', 'light_device', fallback="")
        self.user = config.get('bot', 'user', fallback='')
        self.passwd = config.get('bot', 'password', fallback='')
        self.unknown_fields = self._check_config(config) if config.has_section('bot') else ''

    def _check_config(selfб, config: configparser.ConfigParser):
        known_items = ['server', 'socks_proxy', 'bot_token', 'chat_id', 'debug', 'log_parser', 'log_path', 'power_device', 'light_device', 'user', 'password']
        unknwn = list(map(lambda fil: f"    {fil[0]}: {fil[1]}\n", filter(lambda el: el[0] not in known_items, config.items('bot'))))
        if unknwn:
            return f"[bot] unknown/bad items\n{''.join(unknwn)}"
        else:
            return ''


class CameraConfig:
    def __init__(self, config: configparser.ConfigParser):
        self.enabled: bool = 'camera' in config
        self.host = config.get('camera', 'host', fallback=f"")  # Todo: remove default host?
        self.threads: int = config.getint('camera', 'threads', fallback=int(os.cpu_count() / 2))
        self.flipVertically: bool = config.getboolean('camera', 'flipVertically', fallback=False)
        self.flipHorizontally: bool = config.getboolean('camera', 'flipHorizontally', fallback=False)
        self.rotate: str = config.get('camera', 'rotate', fallback='')
        self.fourcc: str = config.get('camera', 'fourcc', fallback='x264')
        self.videoDuration: int = config.getint('camera', 'videoDuration', fallback=5)
        self.stream_fps: int = config.getint('camera', 'fps', fallback=0)
        self.light_timeout: int = config.getint('camera', 'light_control_timeout', fallback=0)
        self.picture_quality = config.get('camera', 'picture_quality', fallback='high')
        self.unknown_fields = self._check_config(config) if config.has_section('camera') else ''

    def _check_config(selfб, config: configparser.ConfigParser):
        known_items = ['host', 'threads', 'flipVertically', 'flipHorizontally', 'rotate', 'fourcc', 'videoDuration', 'fps', 'light_control_timeout', 'picture_quality']
        unknwn = list(map(lambda fil: f"    {fil[0]}: {fil[1]}\n", filter(lambda el: el[0] not in known_items, config.items('camera'))))
        if unknwn:
            return f"[camera] unknown/bad items\n{''.join(unknwn)}"
        else:
            return ''


class NotifierConfig:
    def __init__(self, config: configparser.ConfigParser):
        self.percent: int = config.getint('progress_notification', 'percent', fallback=0)
        self.height: int = config.getint('progress_notification', 'height', fallback=0)
        self.interval: int = config.getint('progress_notification', 'time', fallback=0)
        self.notify_groups: list = [el.strip() for el in config.get('progress_notification', 'groups').split(',')] if 'progress_notification' in config and 'groups' in config['progress_notification'] else list()
        self.group_only: bool = config.getboolean('progress_notification', 'group_only', fallback=False)
        self.unknown_fields = self._check_config(config) if config.has_section('progress_notification') else ''

    def _check_config(selfб, config: configparser.ConfigParser):
        known_items = ['percent', 'height', 'time', 'groups', 'group_only']
        unknwn = list(map(lambda fil: f"    {fil[0]}: {fil[1]}\n", filter(lambda el: el[0] not in known_items, config.items('progress_notification'))))
        if unknwn:
            return f"[progress_notification] unknown/bad items\n{''.join(unknwn)}"
        else:
            return ''


class TimelapseConfig:
    def __init__(self, config: configparser.ConfigParser):
        self.enabled: bool = 'timelapse' in config
        self.base_dir: str = config.get('timelapse', 'basedir', fallback='/tmp/timelapse')  # Fixme: relative path failed! ~/timelapse
        self.ready_dir: str = config.get('timelapse', 'copy_finished_timelapse_dir', fallback='')  # Fixme: relative path failed! ~/timelapse
        self.cleanup: bool = config.getboolean('timelapse', 'cleanup', fallback=True)
        self.mode_manual: bool = config.getboolean('timelapse', 'manual_mode', fallback=False)
        self.height: float = config.getfloat('timelapse', 'height', fallback=0.0)
        self.interval: int = config.getint('timelapse', 'time', fallback=0)
        self.target_fps: int = config.getint('timelapse', 'target_fps', fallback=15)
        self.min_lapse_duration: int = config.getint('timelapse', 'min_lapse_duration', fallback=0)
        self.max_lapse_duration: int = config.getint('timelapse', 'max_lapse_duration', fallback=0)
        self.last_frame_duration: int = config.getint('timelapse', 'last_frame_duration', fallback=5)

        # Todo: add to runtime params section!
        self.after_lapse_gcode: str = config.get('timelapse', 'after_lapse_gcode', fallback='')
        self.send_finished_lapse: bool = config.getboolean('timelapse', 'send_finished_lapse', fallback=True)
        self.unknown_fields = self._check_config(config) if config.has_section('timelapse') else ''

    def _check_config(selfб, config: configparser.ConfigParser):
        known_items = ['basedir', 'copy_finished_timelapse_dir', 'cleanup', 'manual_mode', 'height', 'time', 'target_fps', 'min_lapse_duration', 'max_lapse_duration', 'last_frame_duration', 'after_lapse_gcode',
                       'send_finished_lapse']
        unknwn = list(map(lambda fil: f"    {fil[0]}: {fil[1]}\n", filter(lambda el: el[0] not in known_items, config.items('timelapse'))))
        if unknwn:
            return f"[timelapse] unknown/bad items\n{''.join(unknwn)}"
        else:
            return ''


class TelegramUIConfig:
    def __init__(self, config: configparser.ConfigParser):
        self.silent_progress = config.getboolean('telegram_ui', 'silent_progress', fallback=False)
        self.silent_commands = config.getboolean('telegram_ui', 'silent_commands', fallback=False)
        self.silent_status = config.getboolean('telegram_ui', 'silent_status', fallback=False)
        self.status_single_message = config.getboolean('telegram_ui', 'status_single_message', fallback=True)
        self.pin_status_single_message = config.getboolean('telegram_ui', 'pin_status_single_message', fallback=False)  # Todo: implement
        # Todo: cleanup with trim  & etc
        self.status_message_content: list = [el.strip() for el in config.get('telegram_ui', 'status_message_content').split(',')] if 'telegram_ui' in config and 'status_message_content' in config['telegram_ui'] else \
            ['progress', 'height', 'filament_length', 'filament_weight', 'print_duration', 'eta', 'finish_time', 'm117_status', 'tgnotify_status', 'last_update_time']

        buttons_string = config.get('telegram_ui', 'buttons') if 'telegram_ui' in config and 'buttons' in config['telegram_ui'] else '[status,pause,cancel,resume],[files,emergency,macros,shutdown]'
        # Todo: cleanup with trim  & etc
        self.buttons = list(map(lambda el: list(map(lambda iel: f'/{iel.strip()}', el.replace('[', '').replace(']', '').split(','))), re.findall(r'\[.[^\]]*\]', buttons_string)))
        self.require_confirmation_macro = config.getboolean('telegram_ui', 'require_confirmation_macro', fallback=True)
        self.include_macros_in_command_list = config.getboolean('telegram_ui', 'include_macros_in_command_list', fallback=True)
        self.disabled_macros = [el.strip() for el in config.get('telegram_ui', 'disabled_macros').split(',')] if 'telegram_ui' in config and 'disabled_macros' in config['telegram_ui'] else list()
        self.show_hidden_macros = config.getboolean('telegram_ui', 'show_hidden_macros', fallback=False)
        self.eta_source: str = config.get('telegram_ui', 'eta_source', fallback='slicer')
        self.status_message_sensors: list = [el.strip() for el in config.get('telegram_ui', 'status_message_sensors').split(',')] if 'telegram_ui' in config and 'status_message_sensors' in config['telegram_ui'] else []
        self.status_message_heaters: list = [el.strip() for el in config.get('telegram_ui', 'status_message_heaters').split(',')] if 'telegram_ui' in config and 'status_message_heaters' in config['telegram_ui'] else []
        self.status_message_devices: list = [el.strip() for el in config.get('telegram_ui', 'status_message_devices').split(',')] if 'telegram_ui' in config and 'status_message_devices' in config['telegram_ui'] else []
        self.unknown_fields = self._check_config(config) if config.has_section('telegram_ui') else ''

    def _check_config(selfб, config: configparser.ConfigParser):
        known_items = ['silent_progress', 'silent_commands', 'silent_status', 'status_single_message', 'pin_status_single_message', 'status_message_content', 'buttons', 'require_confirmation_macro',
                       'include_macros_in_command_list', 'disabled_macros', 'show_hidden_macros', 'eta_source', 'status_message_sensors', 'status_message_heaters', 'status_message_devices']
        unknwn = list(map(lambda fil: f"    {fil[0]}: {fil[1]}\n", filter(lambda el: el[0] not in known_items, config.items('telegram_ui'))))
        if unknwn:
            return f"[telegram_ui] unknown/bad items\n{''.join(unknwn)}"
        else:
            return ''


class ConfigWrapper:
    def __init__(self, config: configparser.ConfigParser):
        self.bot = BotConfig(config)
        self.camera = CameraConfig(config)
        self.notifications = NotifierConfig(config)
        self.timelapse = TimelapseConfig(config)
        self.telegram_ui = TelegramUIConfig(config)
        self.unknown_fields = self.bot.unknown_fields + self.camera.unknown_fields + self.notifications.unknown_fields + self.timelapse.unknown_fields + self.telegram_ui.unknown_fields