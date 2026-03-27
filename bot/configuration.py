"""INI-based configuration parsing and validation for all bot settings."""

from __future__ import annotations

import configparser
import copy
from pathlib import Path
import re
from typing import Any, Callable, ClassVar, Final


class ConfigHelper:
    """Base class for INI section parsers with typed getters and validation."""

    _section: str
    _KNOWN_ITEMS: ClassVar[list[str]]

    def __init__(self, config: configparser.ConfigParser) -> None:
        self._config = config
        self._parsing_errors: list[str] = []

    @property
    def unknown_fields(self) -> str:
        return self._check_config()

    @property
    def parsing_errors(self) -> str:
        if self._parsing_errors:
            return f"Config errors in section [{self._section}]:\n  " + "\n  ".join(self._parsing_errors) + "\n"
        return ""

    def _check_config(self) -> str:
        if not self._config.has_section(self._section):
            return ""
        unknown = [f"  {fil[0]}: {fil[1]}\n" for fil in self._config.items(self._section) if fil[0] not in self._KNOWN_ITEMS]
        if unknown:
            return f"Unknown/bad items in section [{self._section}]:\n{''.join(unknown)}\n"
        return ""

    def _check_numerical_value(
        self,
        option: str,
        value: float,
        above: float | None = None,
        below: float | None = None,
        min_value: float | None = None,
        max_value: float | None = None,
    ) -> None:
        if not self._config.has_option(self._section, option):
            return
        if above is not None and value <= above:
            self._parsing_errors.append(f"Option '{option}: {value}': value is not above {above}")
        if below is not None and value >= below:
            self._parsing_errors.append(f"Option '{option}: {value}': value is not below {below}")
        if min_value is not None and value < min_value:
            self._parsing_errors.append(f"Option '{option}: {value}': value is below minimum value {min_value}")
        if max_value is not None and value > max_value:
            self._parsing_errors.append(f"Option '{option}: {value}': value is above maximum value {max_value}")

    def _check_string_values(self, option: str, value: str, allowed_values: list[str] | None = None) -> None:
        if not self._config.has_option(self._section, option):
            return
        if allowed_values is not None and value not in allowed_values:
            self._parsing_errors.append(f"Option '{option}: {value}': value '{value}' is not allowed")

    def _check_list_values(self, option: str, values: list[Any], allowed_values: list[Any] | None = None) -> None:
        if not self._config.has_option(self._section, option):
            return
        unallowed_params = [val for val in values if val not in allowed_values] if allowed_values is not None else []
        if unallowed_params:
            self._parsing_errors.append(f"Option '{option}: {values}': values [" + ",".join(unallowed_params) + "] are not allowed")

    def _get_option_value(self, func: Callable[..., Any], option: str, default: Any | None = None) -> Any:
        try:
            val = func(self._section, option, fallback=default) if default is not None else func(self._section, option)
        except Exception as ex:
            if default is not None:
                self._parsing_errors.append(f"Error parsing option ({option}) \n {ex}")
                val = default
            else:
                raise
        return val

    def _get_int(
        self,
        option: str,
        default: int | None = None,
        above: float | None = None,
        below: float | None = None,
        min_value: float | None = None,
        max_value: float | None = None,
    ) -> int:
        val: int = self._get_option_value(self._config.getint, option, default)
        self._check_numerical_value(option, val, above, below, min_value, max_value)
        return val

    def _get_float(
        self,
        option: str,
        default: float | None = None,
        above: float | None = None,
        below: float | None = None,
        min_value: float | None = None,
        max_value: float | None = None,
    ) -> float:
        val: float = self._get_option_value(self._config.getfloat, option, default)
        self._check_numerical_value(option, val, above, below, min_value, max_value)
        return val

    def _get_str(self, option: str, default: str | None = None, allowed_values: list[Any] | None = None) -> str:
        val: str = self._get_option_value(self._config.get, option, default)
        self._check_string_values(option, val, allowed_values)
        return val

    def _get_boolean(self, option: str, default: bool | None = None) -> bool:
        val: bool = self._get_option_value(self._config.getboolean, option, default)
        return val

    def _get_list(self, option: str, default: list[Any] | None = None, el_type: Any = str, allowed_values: list[Any] | None = None) -> list[Any]:
        if self._config.has_option(self._section, option):
            try:
                val = [el_type(el.strip()) for el in self._get_str(option).split(",")]
            except Exception as ex:
                if default is not None:
                    self._parsing_errors.append(f"Error parsing option ({option}) \n {ex}")
                    val = default
                else:
                    val = []
                    # TODO: raise some parsing exception
        elif default is not None:
            val = default
        else:
            # TODO: raise some parsing exception
            val = []

        self._check_list_values(option, val, allowed_values)
        return val


class SecretsConfig(ConfigHelper):
    """Config for the [secrets] section."""

    _section = "secrets"
    _KNOWN_ITEMS: ClassVar[list[str]] = [
        "bot_token",
        "chat_id",
        "user",
        "password",
        "api_token",
        "proxy_login",
        "proxy_password",
    ]

    def __init__(self, config: configparser.ConfigParser) -> None:
        secrets_path = Path(config.get("secrets", "secrets_path", fallback="")).expanduser()
        secrets_path_default_name = (secrets_path / "secrets.conf").expanduser()
        conf = configparser.ConfigParser(allow_no_value=True, inline_comment_prefixes=(";", "#"))
        if secrets_path and secrets_path.is_file():
            conf.read(secrets_path)
            super().__init__(conf)
        elif secrets_path_default_name and secrets_path_default_name.is_file():
            conf.read(secrets_path_default_name)
            super().__init__(conf)
        else:
            self._section = "bot"
            super().__init__(config)

        if not self._config.has_option(self._section, "bot_token"):
            self._parsing_errors.append("Option 'bot_token': value is not provided")

        self.token: str = self._get_str("bot_token", default="")
        self.chat_id: int = self._get_int("chat_id", default=0)
        self.user: str = self._get_str("user", default="")
        self.passwd: str = self._get_str("password", default="")
        self.api_token: str = self._get_str("api_token", default="")
        self.proxy_login: str = self._get_str("proxy_login", default="")
        self.proxy_password: str = self._get_str("proxy_password", default="")

        if (self.proxy_login or self.proxy_password) and not (self.proxy_login and self.proxy_password):
            self._parsing_errors.append("Both proxy_login and proxy_password must be set, ignoring proxy credentials")
            self.proxy_login = ""
            self.proxy_password = ""


class BotConfig(ConfigHelper):
    """Config for the [bot] section."""

    _section = "bot"
    _KNOWN_ITEMS: ClassVar[list[str]] = [
        "bot_token",
        "chat_id",
        "user",
        "password",
        "api_token",
        "server",
        "port",
        "ssl",
        "ssl_verify",
        "api_url",
        "http_proxy",
        "socks_proxy",
        "debug",
        "log_parser",
        "power_device",
        "light_device",
        "log_path",
        "upload_path",
        "services",
    ]

    def __init__(self, config: configparser.ConfigParser) -> None:
        super().__init__(config)

        # TODO: validate server addr have ho port or protocol!
        self.host: str = self._get_str("server", default="localhost")
        self.ssl: bool = self._get_boolean("ssl", default=False)
        self.ssl_verify: bool = self._get_boolean("ssl_verify", default=True)
        self.port: int = self._get_int("port", default=80)
        self.api_url: str = self._get_str("api_url", default="https://api.telegram.org/bot")
        self.max_upload_file_size: int = 50 if self.api_url == "https://api.telegram.org/bot" else 2000
        self.socks_proxy: str = self._get_str("socks_proxy", default="")
        self.http_proxy: str = self._get_str("http_proxy", default="")
        self.light_device_name: str = self._get_str("light_device", default="")
        self.poweroff_device_name: str = self._get_str("power_device", default="")
        self.debug: bool = self._get_boolean("debug", default=False)
        self.log_file: Path = Path(self._get_str("log_path", default="/tmp/telegram.log"))
        self.upload_path: str = self._get_str("upload_path", default="")
        self.services: list[str] = self._get_list("services", default=["klipper", "moonraker"])
        self.log_parser: bool = self._get_boolean("log_parser", default=False)

        host_parts = self.host.split(":")
        if len(host_parts) == 2 and host_parts[1].isdigit():  # noqa: PLR2004
            self.host = host_parts[0]
            self.port = int(host_parts[1])
        elif len(host_parts) >= 2:  # noqa: PLR2004
            self._parsing_errors.append("Protocol must be specified in other configuration parameters")

        if self.http_proxy and self.socks_proxy:
            self._parsing_errors.append("Http and Socks proxies are mutually exclusive. Only one type can be specified.")
            self.socks_proxy = ""
            self.http_proxy = ""

    @property
    def formatted_upload_path(self) -> str:
        if not self.upload_path:
            return ""
        if not self.upload_path.endswith("/"):
            return self.upload_path + "/"
        return self.upload_path

    def resolve_log_path(self, cli_log_path: Path | None) -> None:
        """Apply CLI override to log file path if provided."""
        if cli_log_path is not None:
            self.log_file = cli_log_path
        if not self.log_file.suffix:
            self.log_file = self.log_file / "telegram.log"
        self.log_file.parent.mkdir(parents=True, exist_ok=True)


class CameraConfig(ConfigHelper):
    """Config for the [camera] section."""

    _section = "camera"
    _KNOWN_ITEMS: ClassVar[list[str]] = [
        "host",
        "host_snapshot",
        "threads",
        "flip_vertically",
        "flip_horizontally",
        "rotate",
        "fourcc",
        "video_duration",
        "video_buffer_size",
        "fps",
        "light_control_timeout",
        "picture_quality",
        "type",
    ]

    def __init__(self, config: configparser.ConfigParser) -> None:
        super().__init__(config)
        self.enabled: bool = config.has_section(self._section)
        self.cam_type: str = self._get_str("type", default="mjpeg", allowed_values=["opencv", "ffmpeg", "mjpeg", "raw_stream"])
        self.host: str = self._get_str("host", default="")
        self.host_snapshot: str = self._get_str("host_snapshot", default="")
        self.stream_fps: int = self._get_int("fps", default=0, above=0)
        self.flip_vertically: bool = self._get_boolean("flip_vertically", default=False)
        self.flip_horizontally: bool = self._get_boolean("flip_horizontally", default=False)
        self.rotate: str = self._get_str("rotate", default="", allowed_values=["", "90_cw", "90_ccw", "180"])
        self.fourcc: str = self._get_str("fourcc", default="h264", allowed_values=["h264", "mpeg4"])

        # TODO: [fixme] fix default calcs! add check max value cpu count
        # self.threads: int = self._getint( "threads", fallback=int(len(os.sched_getaffinity(0)) / 2))
        self.threads: int = self._get_int("threads", default=2, min_value=0)

        self.video_duration: int = self._get_int("video_duration", default=5, above=0)
        self.video_buffer_size: int = self._get_int("video_buffer_size", default=2, above=0)
        self.light_timeout: int = self._get_int("light_control_timeout", default=0, min_value=0)
        self.picture_quality: str = self._get_str("picture_quality", default="high", allowed_values=["low", "high"])


class NotifierConfig(ConfigHelper):
    """Config for the [progress_notification] section."""

    _section = "progress_notification"
    _KNOWN_ITEMS: ClassVar[list[str]] = ["percent", "height", "time", "groups", "group_only"]

    def __init__(self, config: configparser.ConfigParser) -> None:
        super().__init__(config)
        self.enabled: bool = config.has_section(self._section)
        self.percent: int = self._get_int("percent", default=0, min_value=0)
        self.height: float = self._get_float("height", default=0, min_value=0.0)
        self.interval: int = self._get_int("time", default=0, min_value=0)
        self.notify_groups: list[tuple[int, int | None]] = self._get_groups_list()
        self.group_only: bool = self._get_boolean("group_only", default=False)

    def _get_groups_list(self) -> list[tuple[int, int | None]]:
        els = [self._get_group_with_thread_id(el) for el in self._get_list("groups", default=[], el_type=str)]
        return [ell for ell in els if ell is not None]

    def _get_group_with_thread_id(self, group_id: str) -> tuple[int, int | None] | None:
        try:
            parts = group_id.split(":")
            if len(parts) == 2:  # noqa: PLR2004
                return int(parts[0]), int(parts[1])
            if len(parts) == 1:
                return int(parts[0]), None
            self._parsing_errors.append(f"Malformed group_id `{group_id}`")
            return None  # noqa: TRY300
        except Exception as ex:
            self._parsing_errors.append(f"Error parsing group_id `{group_id}` \n {ex}")
            return None


class TimelapseConfig(ConfigHelper):
    """Config for the [timelapse] section."""

    _section = "timelapse"
    _KNOWN_ITEMS: ClassVar[list[str]] = [
        "basedir",
        "copy_finished_timelapse_dir",
        "cleanup",
        "manual_mode",
        "height",
        "time",
        "target_fps",
        "limit_fps",
        "min_lapse_duration",
        "max_lapse_duration",
        "last_frame_duration",
        "after_lapse_gcode",
        "send_finished_lapse",
        "after_photo_gcode",
        "save_lapse_photos_as_images",
        "raw_compressed",
    ]

    def __init__(self, config: configparser.ConfigParser) -> None:
        super().__init__(config)
        self.enabled: bool = config.has_section(self._section)
        self.base_dir: Path = Path(self._get_str("basedir", default="~/moonraker-telegram-bot-timelapse"))
        _ready_dir = self._get_str("copy_finished_timelapse_dir", default="")
        self.ready_dir: Path | None = Path(_ready_dir) if _ready_dir else None
        self.cleanup: bool = self._get_boolean("cleanup", default=True)
        self.height: float = self._get_float("height", default=0.0, min_value=0.0)
        self.interval: int = self._get_int("time", default=0, min_value=0)
        self.target_fps: int = self._get_int("target_fps", default=15, above=0)
        self.limit_fps: bool = self._get_boolean("limit_fps", default=False)
        self.min_lapse_duration: int = self._get_int("min_lapse_duration", default=0, min_value=0)  # TODO: check if max_value is max_lapse_duration
        self.max_lapse_duration: int = self._get_int("max_lapse_duration", default=0, min_value=0)  # TODO: check if min_value is more than min_lapse_duration
        self.last_frame_duration: int = self._get_int("last_frame_duration", default=5, min_value=0)
        self.after_lapse_gcode: str = self._get_str("after_lapse_gcode", default="")
        self.send_finished_lapse: bool = self._get_boolean("send_finished_lapse", default=True)
        self.mode_manual: bool = self._get_boolean("manual_mode", default=False)
        self.after_photo_gcode: str = self._get_str("after_photo_gcode", default="")
        self.save_lapse_photos_as_images: bool = self._get_boolean("save_lapse_photos_as_images", default=False)

        self._init_paths()

    def _init_paths(self) -> None:
        self.base_dir = self.base_dir.expanduser()
        if self.enabled:
            self.base_dir.mkdir(parents=True, exist_ok=True)
        if self.ready_dir:
            self.ready_dir = self.ready_dir.expanduser()


class TelegramUIConfig(ConfigHelper):
    """Config for the [telegram_ui] section."""

    _section = "telegram_ui"
    _KNOWN_ITEMS: ClassVar[list[str]] = [
        "silent_progress",
        "silent_commands",
        "silent_status",
        "pin_status_single_message",
        "send_greeting_message",
        "send_reply_keyboard",
        "buttons",
        "progress_update_message",
        "include_macros_in_command_list",
        "hidden_macros",
        "hidden_bot_commands",
        "show_private_macros",
        "eta_source",
        "status_message_m117_update",
        "require_confirmation",
        "status_update_button",
    ]
    _MESSAGE_CONTENT: Final = [
        "progress",
        "height",
        "filament_length",
        "filament_weight",
        "print_duration",
        "eta",
        "finish_time",
        "m117_status",
        "tgnotify_status",
        "last_update_time",
    ]

    def __init__(self, config: configparser.ConfigParser) -> None:
        super().__init__(config)
        self.eta_source: str = self._get_str("eta_source", default="slicer", allowed_values=["slicer", "file"])
        self.buttons_default: bool = bool(not config.has_option(self._section, "buttons"))
        self.buttons: list[list[str]] = list(  # noqa: C417
            map(
                lambda el: list(  # noqa: C417
                    map(
                        lambda iel: f"/{iel.strip()}",
                        el.replace("[", "").replace("]", "").split(","),
                    ),
                ),
                re.findall(r"\[.[^\]]*\]", self._get_str("buttons", default="[pause,cancel,resume],[status,files,macros],[fw_restart,emergency,shutdown,services]")),
            ),
        )
        self.progress_update_message: bool = self._get_boolean("progress_update_message", default=False)
        self.send_reply_keyboard: bool = self._get_boolean("send_reply_keyboard", default=True)
        self.silent_progress: bool = self._get_boolean("silent_progress", default=False)
        self.silent_commands: bool = self._get_boolean("silent_commands", default=False)
        self.silent_status: bool = self._get_boolean("silent_status", default=False)
        self.include_macros_in_command_list: bool = self._get_boolean("include_macros_in_command_list", default=True)
        self.hidden_macros: list[str] = [el.upper() for el in self._get_list("hidden_macros", default=[])]
        self.hidden_bot_commands: list[str] = self._get_list("hidden_bot_commands", default=[])
        self.show_private_macros: bool = self._get_boolean("show_private_macros", default=False)
        self.pin_status_single_message: bool = self._get_boolean("pin_status_single_message", default=True)
        self.status_message_m117_update: bool = self._get_boolean("status_message_m117_update", default=False)
        self.send_greeting_message: bool = self._get_boolean("send_greeting_message", default=True)
        self.status_update_button: bool = self._get_boolean("status_update_button", default=True)
        self.require_confirmation: list[str] = self._get_list(
            "require_confirmation",
            default=["logs", "logs_upload", "shutdown", "restart", "cancel", "fw_restart", "emergency", "reboot", "power", "bot_restart"],
        )

    def is_present_in_require_confirmation(self, command: str) -> bool:
        return command.strip() in self.require_confirmation

    def confirm_macro(self) -> bool:
        return "macro" in self.require_confirmation

    def confirm_command(self) -> bool:
        return "command" in self.require_confirmation

    def confirm_gcode(self) -> bool:
        return "gcode" in self.require_confirmation


class StatusMessageContentConfig(ConfigHelper):
    """Config for the [status_message_content] section."""

    _section = "status_message_content"
    _KNOWN_ITEMS: ClassVar[list[str]] = ["content", "sensors", "heaters", "fans", "moonraker_devices"]
    _MESSAGE_CONTENT: Final = [
        "progress",
        "height",
        "filament_length",
        "filament_weight",
        "print_duration",
        "eta",
        "finish_time",
        "m117_status",
        "tgnotify_status",
        "last_update_time",
    ]

    def __init__(self, config: configparser.ConfigParser) -> None:
        super().__init__(config)
        self.content: list[str] = self._get_list("content", default=self._MESSAGE_CONTENT, allowed_values=self._MESSAGE_CONTENT)
        self.sensors: list[str] = self._get_list("sensors", default=[])
        self.heaters: list[str] = self._get_list("heaters", default=[])
        self.fans: list[str] = self._get_list("fans", default=[])
        self.moonraker_devices: list[str] = self._get_list("moonraker_devices", default=[])


class ConfigWrapper:
    """Top-level config loader that parses telegram.conf and assembles section configs."""

    def __init__(self, path: Path) -> None:
        config = configparser.ConfigParser(allow_no_value=True, inline_comment_prefixes=(";", "#"))
        config.read(path)

        for sec in config.sections():
            if sec.startswith("include"):
                addit_conf = sec.replace("include", "").strip()
                config.read(path.parent / addit_conf)

        self._config = config
        self.secrets = SecretsConfig(config)
        self.bot_config = BotConfig(config)
        self.camera = CameraConfig(config)
        self.notifications = NotifierConfig(config)
        self.timelapse = TimelapseConfig(config)
        self.telegram_ui = TelegramUIConfig(config)
        self.status_message_content = StatusMessageContentConfig(config)
        self.unknown_fields = (
            self.bot_config.unknown_fields
            + self.camera.unknown_fields
            + self.notifications.unknown_fields
            + self.timelapse.unknown_fields
            + self.telegram_ui.unknown_fields
            + self.status_message_content.unknown_fields
        )
        self.parsing_errors = (
            self.secrets.parsing_errors
            + self.bot_config.parsing_errors
            + self.camera.parsing_errors
            + self.notifications.parsing_errors
            + self.timelapse.parsing_errors
            + self.telegram_ui.parsing_errors
            + self.status_message_content.parsing_errors
        )

    def dump_config_to_log(self) -> None:
        config_copy = copy.deepcopy(self._config)
        for option in ("bot_token", "chat_id", "password", "api_token", "proxy_password"):
            if config_copy.has_option("bot", option):
                config_copy.set("bot", option, "<redacted>")
        for sec in config_copy.sections():
            if sec.startswith("include"):
                config_copy.remove_section(sec)
        with self.bot_config.log_file.open("a", encoding="utf-8") as log_file:
            log_file.write("\n*******************************************************************\n")
            log_file.write("Current Moonraker telegram bot config\n")
            config_copy.write(log_file)
            log_file.write("\n*******************************************************************\n")
            if self.configuration_errors:
                log_file.write(self.configuration_errors)
                log_file.write("\n*******************************************************************\n")

    @property
    def configuration_errors(self) -> str:
        error_message: str = ""
        if self.unknown_fields:
            error_message += f"\n{self.unknown_fields}"
        if self.parsing_errors:
            error_message += f"\n{self.parsing_errors}"
        if error_message:
            error_message += 'Please correct the configuration according to the <a href="https://github.com/nlef/moonraker-telegram-bot/wiki">wiki</a>'
        return error_message
