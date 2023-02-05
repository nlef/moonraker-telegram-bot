from functools import wraps
import logging
import random
import time

from apscheduler.schedulers.background import BackgroundScheduler  # type: ignore
import ujson
import websocket  # type: ignore

from configuration import ConfigWrapper
from klippy import Klippy
from notifications import Notifier
from power_device import PowerDevice
from timelapse import Timelapse

logger = logging.getLogger(__name__)


def websocket_alive(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if self.websocket is None:
            logger.warning("Websocket call `%s` on non initialized ws", func.__name__)
            return None
        else:
            return func(self, *args, **kwargs)

    return wrapper


class WebSocketHelper:
    def __init__(
        self,
        config: ConfigWrapper,
        klippy: Klippy,
        notifier: Notifier,
        timelapse: Timelapse,
        scheduler: BackgroundScheduler,
        light_power_device: PowerDevice,
        psu_power_device: PowerDevice,
        logging_handler: logging.Handler,
    ):
        self._host: str = config.bot_config.host
        self._klippy: Klippy = klippy
        self._notifier: Notifier = notifier
        self._timelapse: Timelapse = timelapse
        self._scheduler: BackgroundScheduler = scheduler
        self._light_power_device: PowerDevice = light_power_device
        self._psu_power_device: PowerDevice = psu_power_device
        self._log_parser: bool = config.bot_config.log_parser

        if config.bot_config.debug:
            logger.setLevel(logging.DEBUG)
        if logging_handler:
            logger.addHandler(logging_handler)

        self.websocket = websocket.WebSocketApp(
            f"ws://{self._host}/websocket{self._klippy.one_shot_token}",
            on_message=self.websocket_to_message,
            on_open=self.on_open,
            on_error=self.on_error,
            on_close=self.on_close,
        )

    @staticmethod
    def on_close(_, close_status_code, close_msg):
        logger.info("WebSocket closed")
        if close_status_code or close_msg:
            logger.error("WebSocket close status code: %s", str(close_status_code))
            logger.error("WebSocket close message: %s", str(close_msg))

    @staticmethod
    def on_error(_, error):
        logger.error(error)

    @property
    def _my_id(self) -> int:
        return random.randint(0, 300000)

    def subscribe(self, websock):
        subscribe_objects = {
            "print_stats": None,
            "display_status": None,
            "toolhead": ["position"],
            "gcode_move": ["position", "gcode_position"],
            "virtual_sdcard": ["progress"],
        }

        sensors = self._klippy.prepare_sens_dict_subscribe()
        if sensors:
            subscribe_objects.update(sensors)

        websock.send(
            ujson.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "printer.objects.subscribe",
                    "params": {"objects": subscribe_objects},
                    "id": self._my_id,
                }
            )
        )

    def on_open(self, websock):
        websock.send(ujson.dumps({"jsonrpc": "2.0", "method": "printer.info", "id": self._my_id}))
        websock.send(ujson.dumps({"jsonrpc": "2.0", "method": "machine.device_power.devices", "id": self._my_id}))

    def reshedule(self):
        if not self._klippy.connected and self.websocket.keep_running:
            self.on_open(self.websocket)

    def stop_all(self):
        self._klippy.stop_all()
        self._notifier.stop_all()
        self._timelapse.stop_all()

    def status_response(self, status_resp):
        if "print_stats" in status_resp:
            print_stats = status_resp["print_stats"]
            if print_stats["state"] in ["printing", "paused"]:
                self._klippy.printing = True
                self._klippy.printing_filename = print_stats["filename"]
                self._klippy.printing_duration = print_stats["print_duration"]
                self._klippy.filament_used = print_stats["filament_used"]
                # Todo: maybe get print start time and set start interval for job?
                self._notifier.add_notifier_timer()
                if not self._timelapse.manual_mode:
                    self._timelapse.is_running = True
                    # TOdo: manual timelapse start check?

            # Fixme: some logic error with states for klippy.paused and printing
            if print_stats["state"] == "printing":
                self._klippy.paused = False
                if not self._timelapse.manual_mode:
                    self._timelapse.paused = False
            if print_stats["state"] == "paused":
                self._klippy.paused = True
                if not self._timelapse.manual_mode:
                    self._timelapse.paused = True
        if "display_status" in status_resp:
            self._notifier.m117_status = status_resp["display_status"]["message"]
            self._klippy.printing_progress = status_resp["display_status"]["progress"]
        if "virtual_sdcard" in status_resp:
            self._klippy.vsd_progress = status_resp["virtual_sdcard"]["progress"]

        self.parse_sensors(status_resp)

    def notify_gcode_reponse(self, message_params):
        if self._timelapse.manual_mode:
            if "timelapse start" in message_params:
                if not self._klippy.printing_filename:
                    self._klippy.get_status()
                self._timelapse.clean()
                self._timelapse.is_running = True

            if "timelapse stop" in message_params:
                self._timelapse.is_running = False
            if "timelapse pause" in message_params:
                self._timelapse.paused = True
            if "timelapse resume" in message_params:
                self._timelapse.paused = False
            if "timelapse create" in message_params:
                self._timelapse.send_timelapse()
        if "timelapse photo_and_gcode" in message_params:
            self._timelapse.take_lapse_photo(manually=True, gcode=True)
        if "timelapse photo" in message_params:
            self._timelapse.take_lapse_photo(manually=True)

        message_params_loc = message_params[0]
        if message_params_loc.startswith("tgnotify "):
            self._notifier.send_notification(message_params_loc[9:])
        if message_params_loc.startswith("tgnotify_photo "):
            self._notifier.send_notification_with_photo(message_params_loc[15:])
        if message_params_loc.startswith("tgalarm "):
            self._notifier.send_error(message_params_loc[8:])
        if message_params_loc.startswith("tgalarm_photo "):
            self._notifier.send_error_with_photo(message_params_loc[14:])
        if message_params_loc.startswith("tgnotify_status "):
            self._notifier.tgnotify_status = message_params_loc[16:]

        if message_params_loc.startswith("set_timelapse_params "):
            self._timelapse.parse_timelapse_params(message_params_loc)
        if message_params_loc.startswith("set_notify_params "):
            self._notifier.parse_notification_params(message_params_loc)
        if message_params_loc.startswith("tgcustom_keyboard "):
            self._notifier.send_custom_inline_keyboard(message_params_loc)

        if message_params_loc.startswith("tg_send_image"):
            self._notifier.send_image(message_params_loc)
        if message_params_loc.startswith("tg_send_video"):
            self._notifier.send_video(message_params_loc)
        if message_params_loc.startswith("tg_send_document"):
            self._notifier.send_document(message_params_loc)

    def notify_status_update(self, message_params):
        message_params_loc = message_params[0]
        if "display_status" in message_params_loc:
            if "message" in message_params_loc["display_status"]:
                self._notifier.m117_status = message_params_loc["display_status"]["message"]
            if "progress" in message_params_loc["display_status"]:
                self._klippy.printing_progress = message_params_loc["display_status"]["progress"]
                self._notifier.schedule_notification(progress=int(message_params_loc["display_status"]["progress"] * 100))

        if "toolhead" in message_params_loc and "position" in message_params_loc["toolhead"]:
            # position_z = json_message["params"][0]['toolhead']['position'][2]
            pass
        if "gcode_move" in message_params_loc and "position" in message_params_loc["gcode_move"]:
            position_z = message_params_loc["gcode_move"]["gcode_position"][2]
            self._klippy.printing_height = position_z
            self._notifier.schedule_notification(position_z=int(position_z))
            self._timelapse.take_lapse_photo(position_z)

        if "virtual_sdcard" in message_params_loc and "progress" in message_params_loc["virtual_sdcard"]:
            self._klippy.vsd_progress = message_params_loc["virtual_sdcard"]["progress"]

        if "print_stats" in message_params_loc:
            self.parse_print_stats(message_params)

        self.parse_sensors(message_params_loc)

    def parse_sensors(self, message_parts_loc):
        for sens in [key for key in message_parts_loc if key.startswith("temperature_sensor")]:
            self._klippy.update_sensor(sens.replace("temperature_sensor ", ""), message_parts_loc[sens])

        for fan in [
            key for key in message_parts_loc if key.startswith("heater_fan") or key == "fan" or key.startswith("controller_fan") or key.startswith("temperature_fan") or key.startswith("fan_generic")
        ]:
            self._klippy.update_sensor(
                fan.replace("heater_fan ", "").replace("controller_fan ", "").replace("temperature_fan ", "").replace("fan_generic ", ""),
                message_parts_loc[fan],
            )

        for heater in [key for key in message_parts_loc if key.startswith("extruder") or key.startswith("heater_bed") or key.startswith("heater_generic")]:
            self._klippy.update_sensor(
                heater.replace("extruder ", "").replace("heater_bed ", "").replace("heater_generic ", ""),
                message_parts_loc[heater],
            )

    def parse_print_stats(self, message_params):
        state = ""
        print_stats_loc = message_params[0]["print_stats"]
        # Fixme:  maybe do not parse without state? history data may not be avaliable
        # Message with filename will be sent before printing is started
        if "filename" in print_stats_loc:
            self._klippy.printing_filename = print_stats_loc["filename"]
        if "filament_used" in print_stats_loc:
            self._klippy.filament_used = print_stats_loc["filament_used"]
        if "state" in print_stats_loc:
            state = print_stats_loc["state"]
        # Fixme: reset notify percent & height on finish/cancel/start
        if "print_duration" in print_stats_loc:
            self._klippy.printing_duration = print_stats_loc["print_duration"]
        if state == "printing":
            self._klippy.paused = False
            if not self._klippy.printing:
                self._klippy.printing = True
                self._notifier.reset_notifications()
                self._notifier.add_notifier_timer()
                if not self._klippy.printing_filename:
                    self._klippy.get_status()
                if not self._timelapse.manual_mode:
                    self._timelapse.clean()
                    self._timelapse.is_running = True
                self._notifier.send_print_start_info()

            if not self._timelapse.manual_mode:
                self._timelapse.paused = False
        elif state == "paused":
            self._klippy.paused = True
            if not self._timelapse.manual_mode:
                self._timelapse.paused = True
        # Todo: cleanup timelapse dir on cancel print!
        elif state == "complete":
            self._klippy.printing = False
            self._notifier.remove_notifier_timer()
            if not self._timelapse.manual_mode:
                self._timelapse.is_running = False
                self._timelapse.send_timelapse()
            # Fixme: add finish printing method in notifier
            self._notifier.send_print_finish()
        elif state == "error":
            self._klippy.printing = False
            self._timelapse.is_running = False
            self._notifier.remove_notifier_timer()
            error_mess = f"Printer state change error: {print_stats_loc['state']}\n"
            if "message" in print_stats_loc and print_stats_loc["message"]:
                error_mess += f"{print_stats_loc['message']}\n"
            self._notifier.send_error(error_mess)
        elif state == "standby":
            self._klippy.printing = False
            self._notifier.remove_notifier_timer()
            # Fixme: check manual mode
            self._timelapse.is_running = False
            if not self._timelapse.manual_mode:
                self._timelapse.send_timelapse()
            self._notifier.send_printer_status_notification(f"Printer state change: {print_stats_loc['state']} \n")
        elif state:
            logger.error("Unknown state: %s", state)

    def power_device_state(self, device):
        device_name = device["device"]
        device_state = bool(device["status"] == "on")
        self._klippy.update_power_device(device_name, device)
        if self._psu_power_device and self._psu_power_device.name == device_name:
            self._psu_power_device.device_state = device_state
        if self._light_power_device and self._light_power_device.name == device_name:
            self._light_power_device.device_state = device_state

    def websocket_to_message(self, ws_loc, ws_message):
        logger.debug(ws_message)
        json_message = ujson.loads(ws_message)

        if "error" in json_message:
            logger.warning("Error received from websocket: %s", json_message["error"])
            return

        if "id" in json_message:
            if "result" in json_message:
                message_result = json_message["result"]

                if "status" in message_result:
                    self.status_response(message_result["status"])
                    return

                if "state" in message_result:
                    klippy_state = message_result["state"]
                    self._klippy.state = klippy_state
                    if klippy_state == "ready":
                        if ws_loc.keep_running:
                            self._klippy.connected = True
                            if self._klippy.state_message:
                                self._notifier.send_error(f"Klippy changed state to {self._klippy.state}")
                                self._klippy.state_message = ""
                            self.subscribe(ws_loc)
                            if self._scheduler.get_job("ws_reschedule"):
                                self._scheduler.remove_job("ws_reschedule")
                    elif klippy_state in ["error", "shutdown", "startup"]:
                        self._klippy.connected = False
                        self._scheduler.add_job(
                            self.reshedule,
                            "interval",
                            seconds=2,
                            id="ws_reschedule",
                            replace_existing=True,
                        )
                        state_message = message_result["state_message"]
                        if self._klippy.state_message != state_message and klippy_state != "startup":
                            self._klippy.state_message = state_message
                            self._notifier.send_error(f"Klippy changed state to {self._klippy.state}\n{self._klippy.state_message}")
                    else:
                        logger.error("UnKnown klippy state: %s", klippy_state)
                        self._klippy.connected = False
                        self._scheduler.add_job(
                            self.reshedule,
                            "interval",
                            seconds=2,
                            id="ws_reschedule",
                            replace_existing=True,
                        )
                    return

                if "devices" in message_result:
                    for device in message_result["devices"]:
                        self.power_device_state(device)
                    return

            if "error" in json_message:
                self._notifier.send_error(f"{json_message['error']['message']}")

        else:
            message_method = json_message["method"]
            if message_method in ["notify_klippy_shutdown", "notify_klippy_disconnected"]:
                logger.warning("klippy disconnect detected with message: %s", json_message["method"])
                self.stop_all()
                self._klippy.connected = False
                self._scheduler.add_job(
                    self.reshedule,
                    "interval",
                    seconds=2,
                    id="ws_reschedule",
                    replace_existing=True,
                )

            if "params" not in json_message:
                return

            message_params = json_message["params"]

            if message_method == "notify_gcode_response":
                self.notify_gcode_reponse(message_params)

            if message_method == "notify_power_changed":
                for device in message_params:
                    self.power_device_state(device)

            if message_method == "notify_status_update":
                self.notify_status_update(message_params)

    @websocket_alive
    def manage_printing(self, command: str) -> None:
        self.websocket.send(ujson.dumps({"jsonrpc": "2.0", "method": f"printer.print.{command}", "id": self._my_id}))

    @websocket_alive
    def emergency_stop_printer(self) -> None:
        self.websocket.send(ujson.dumps({"jsonrpc": "2.0", "method": "printer.emergency_stop", "id": self._my_id}))

    @websocket_alive
    def firmware_restart_printer(self) -> None:
        self.websocket.send(ujson.dumps({"jsonrpc": "2.0", "method": "printer.firmware_restart", "id": self._my_id}))

    @websocket_alive
    def shutdown_pi_host(self) -> None:
        self.websocket.send(ujson.dumps({"jsonrpc": "2.0", "method": "machine.shutdown", "id": self._my_id}))

    @websocket_alive
    def reboot_pi_host(self) -> None:
        self.websocket.send(ujson.dumps({"jsonrpc": "2.0", "method": "machine.reboot", "id": self._my_id}))

    @websocket_alive
    def restart_system_service(self, service_name: str) -> None:
        self.websocket.send(ujson.dumps({"jsonrpc": "2.0", "method": "machine.services.restart", "params": {"service": service_name}, "id": self._my_id}))

    @websocket_alive
    def execute_ws_gcode_script(self, gcode: str) -> None:
        self.websocket.send(ujson.dumps({"jsonrpc": "2.0", "method": "printer.gcode.script", "params": {"script": gcode}, "id": self._my_id}))

    def parselog(self):
        with open("../telegram.log", encoding="utf-8") as file:
            lines = file.readlines()

        wslines = list(filter(lambda it: " - {" in it, lines))
        messages = list(map(lambda el: el.split(" - ")[-1].replace("\n", ""), wslines))

        for mes in messages:
            self.websocket_to_message(self.websocket, mes)
            time.sleep(0.01)
        print("lalal")

    def run_forever(self):
        # debug reasons only
        if self._log_parser:
            self.parselog()

        self._scheduler.add_job(self.reshedule, "interval", seconds=2, id="ws_reschedule", replace_existing=True)

        self.websocket.run_forever(skip_utf8_validation=True)
