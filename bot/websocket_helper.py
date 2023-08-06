from functools import wraps
import logging
import random
import time
from typing import Any, Optional, Union

from apscheduler.schedulers.background import BackgroundScheduler  # type: ignore
import msgspec
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
            "toolhead": None,
            "gcode_move": None,
            "virtual_sdcard": None,
        }

        sensors = self._klippy.prepare_sens_dict_subscribe()
        if sensors:
            subscribe_objects.update(sensors)

        websock.send(
            msgspec.json.encode(
                {
                    "jsonrpc": "2.0",
                    "method": "printer.objects.subscribe",
                    "params": {"objects": subscribe_objects},
                    "id": self._my_id,
                }
            )
        )

    def on_open(self, websock):
        websock.send(msgspec.json.encode({"jsonrpc": "2.0", "method": "printer.info", "id": self._my_id}))
        websock.send(msgspec.json.encode({"jsonrpc": "2.0", "method": "machine.device_power.devices", "id": self._my_id}))

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

    def notify_gcode_reponse(self, gcode_response):
        if self._timelapse.manual_mode:
            if "timelapse start" in gcode_response:
                if not self._klippy.printing_filename:
                    self._klippy.get_status()
                self._timelapse.clean()
                self._timelapse.is_running = True

            if "timelapse stop" in gcode_response:
                self._timelapse.is_running = False
            if "timelapse pause" in gcode_response:
                self._timelapse.paused = True
            if "timelapse resume" in gcode_response:
                self._timelapse.paused = False
            if "timelapse create" in gcode_response:
                self._timelapse.send_timelapse()
        if "timelapse photo_and_gcode" in gcode_response:
            self._timelapse.take_lapse_photo(manually=True, gcode=True)
        if "timelapse photo" in gcode_response:
            self._timelapse.take_lapse_photo(manually=True)

        if gcode_response.startswith("tgnotify "):
            self._notifier.send_notification(gcode_response[9:])
        if gcode_response.startswith("tgnotify_photo "):
            self._notifier.send_notification_with_photo(gcode_response[15:])
        if gcode_response.startswith("tgalarm "):
            self._notifier.send_error(gcode_response[8:])
        if gcode_response.startswith("tgalarm_photo "):
            self._notifier.send_error_with_photo(gcode_response[14:])
        if gcode_response.startswith("tgnotify_status "):
            self._notifier.tgnotify_status = gcode_response[16:]

        if gcode_response.startswith("set_timelapse_params "):
            self._timelapse.parse_timelapse_params(gcode_response)
        if gcode_response.startswith("set_notify_params "):
            self._notifier.parse_notification_params(gcode_response)
        if gcode_response.startswith("tgcustom_keyboard "):
            self._notifier.send_custom_inline_keyboard(gcode_response)

        if gcode_response.startswith("tg_send_image"):
            self._notifier.send_image(gcode_response)
        if gcode_response.startswith("tg_send_video"):
            self._notifier.send_video(gcode_response)
        if gcode_response.startswith("tg_send_document"):
            self._notifier.send_document(gcode_response)

    def parse_status_update(self, update: dict[str, msgspec.Raw]):
        if "display_status" in update:
            disp_status = msgspec.json.decode(update.get("display_status"), type=DisplayStatus)  # type: ignore
            if disp_status.message:
                self._notifier.m117_status = disp_status.message
            if disp_status.progress:
                self._klippy.printing_progress = disp_status.progress
                self._notifier.schedule_notification(progress=int(disp_status.progress * 100))
        if "toolhead" in update:
            # maybe get Zposition?
            pass

        if "gcode_move" in update:
            gcm = msgspec.json.decode(update.get("gcode_move"), type=GcodeMove)  # type: ignore
            if gcm.position:
                self._klippy.printing_height = gcm.position[2]
                self._notifier.schedule_notification(position_z=int(gcm.position[2]))
                self._timelapse.take_lapse_photo(gcm.position[2])

        if "virtual_sdcard" in update:
            vsdc = msgspec.json.decode(update.get("virtual_sdcard"), type=VirtualSdCard)  # type: ignore
            if vsdc.progress:
                self._klippy.vsd_progress = vsdc.progress

        if "print_stats" in update:
            print_stats = msgspec.json.decode(update.get("print_stats"), type=PrintStats)  # type: ignore
            self.parse_print_stats(print_stats)

        self.parse_sensors_new(update)

    def parse_sensors_new(self, update: dict[str, msgspec.Raw]):
        for sens in [key for key in update if key.startswith("temperature_sensor")]:
            self._klippy.update_sensor(sens.replace("temperature_sensor ", ""), msgspec.json.decode(update[sens], type=dict))

        for fan in [key for key in update if key.startswith("heater_fan") or key == "fan" or key.startswith("controller_fan") or key.startswith("temperature_fan") or key.startswith("fan_generic")]:
            self._klippy.update_sensor(
                fan.replace("heater_fan ", "").replace("controller_fan ", "").replace("temperature_fan ", "").replace("fan_generic ", ""),
                msgspec.json.decode(update[fan], type=dict),
            )

        for heater in [key for key in update if key.startswith("extruder") or key.startswith("heater_bed") or key.startswith("heater_generic")]:
            self._klippy.update_sensor(
                heater.replace("extruder ", "").replace("heater_bed ", "").replace("heater_generic ", ""),
                msgspec.json.decode(update[heater], type=dict),
            )

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

    def parse_print_stats(self, print_stats):
        state = ""

        # Fixme:  maybe do not parse without state? history data may not be avaliable
        # Message with filename will be sent before printing is started
        if print_stats.filename:
            self._klippy.printing_filename = print_stats.filename
        if print_stats.filament_used:
            self._klippy.filament_used = print_stats.filament_used
        if print_stats.state:
            state = print_stats.state
        # Fixme: reset notify percent & height on finish/cancel/start
        if print_stats.print_duration:
            self._klippy.printing_duration = print_stats.print_duration
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
            error_mess = f"Printer state change error: {print_stats.state}\n"
            if print_stats.message:
                error_mess += f"{print_stats.message}\n"
            self._notifier.send_error(error_mess)
        elif state == "standby":
            self._klippy.printing = False
            self._notifier.remove_notifier_timer()
            # Fixme: check manual mode
            self._timelapse.is_running = False
            if not self._timelapse.manual_mode:
                self._timelapse.send_timelapse()
            self._notifier.send_printer_status_notification(f"Printer state change: {print_stats.state} \n")
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
        message_wrapper = msgspec.json.decode(ws_message, type=WebSocketWrapper, strict=False)

        if message_wrapper.error:
            logger.warning("Error received from websocket: %s", message_wrapper.error)
            return

        if message_wrapper.id:
            if message_wrapper.result:
                if message_wrapper.result.status:
                    self.status_response(message_wrapper.result.status)
                    return

            self._klippy.state = message_wrapper.result.state
            if message_wrapper.result.state == "ready":
                if ws_loc.keep_running:
                    self._klippy.connected = True
                    if self._klippy.state_message:
                        self._notifier.send_error(f"Klippy changed state to {self._klippy.state}")
                        self._klippy.state_message = ""
                    self.subscribe(ws_loc)
                    if self._scheduler.get_job("ws_reschedule"):
                        self._scheduler.remove_job("ws_reschedule")
            elif message_wrapper.result.state in ["error", "shutdown", "startup"]:
                self._klippy.connected = False
                self._scheduler.add_job(
                    self.reshedule,
                    "interval",
                    seconds=2,
                    id="ws_reschedule",
                    replace_existing=True,
                )
                if self._klippy.state_message != message_wrapper.result.state_message and message_wrapper.result.state != "startup":
                    self._klippy.state_message = message_wrapper.result.state_message
                    self._notifier.send_error(f"Klippy changed state to {self._klippy.state}\n{self._klippy.state_message}")
            else:
                logger.error("UnKnown klippy state: %s", message_wrapper.result.state)
                self._klippy.connected = False
                self._scheduler.add_job(
                    self.reshedule,
                    "interval",
                    seconds=2,
                    id="ws_reschedule",
                    replace_existing=True,
                )
            return

        # Fixme: parse devices
        #         if "devices" in message_result:
        #             for device in message_result["devices"]:
        #                 self.power_device_state(device)
        #             return
        #     if "error" in json_message:
        #         self._notifier.send_error(f"{json_message['error']['message']}")

        else:
            if message_wrapper.method in ["notify_klippy_shutdown", "notify_klippy_disconnected"]:
                logger.warning("klippy disconnect detected with message: %s", message_wrapper.method)
                self.stop_all()
                self._klippy.connected = False
                self._scheduler.add_job(
                    self.reshedule,
                    "interval",
                    seconds=2,
                    id="ws_reschedule",
                    replace_existing=True,
                )

            if message_wrapper.method == "notify_gcode_response":
                self.notify_gcode_reponse(message_wrapper.params[0])

            if message_wrapper.method == "notify_status_update":
                # status_update = msgspec.json.decode(message_wrapper.params[0], type=StatusUpdate)
                self.parse_status_update(message_wrapper.params[0])

            # Fixme: parse devices
            #     if message_method == "notify_power_changed":
            #         for device in message_params:
            #             self.power_device_state(device)

    @websocket_alive
    def manage_printing(self, command: str) -> None:
        self.websocket.send(msgspec.json.encode({"jsonrpc": "2.0", "method": f"printer.print.{command}", "id": self._my_id}))

    @websocket_alive
    def emergency_stop_printer(self) -> None:
        self.websocket.send(msgspec.json.encode({"jsonrpc": "2.0", "method": "printer.emergency_stop", "id": self._my_id}))

    @websocket_alive
    def firmware_restart_printer(self) -> None:
        self.websocket.send(msgspec.json.encode({"jsonrpc": "2.0", "method": "printer.firmware_restart", "id": self._my_id}))

    @websocket_alive
    def shutdown_pi_host(self) -> None:
        self.websocket.send(msgspec.json.encode({"jsonrpc": "2.0", "method": "machine.shutdown", "id": self._my_id}))

    @websocket_alive
    def reboot_pi_host(self) -> None:
        self.websocket.send(msgspec.json.encode({"jsonrpc": "2.0", "method": "machine.reboot", "id": self._my_id}))

    @websocket_alive
    def restart_system_service(self, service_name: str) -> None:
        self.websocket.send(msgspec.json.encode({"jsonrpc": "2.0", "method": "machine.services.restart", "params": {"service": service_name}, "id": self._my_id}))

    @websocket_alive
    def execute_ws_gcode_script(self, gcode: str) -> None:
        self.websocket.send(msgspec.json.encode({"jsonrpc": "2.0", "method": "printer.gcode.script", "params": {"script": gcode}, "id": self._my_id}))

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


class Result(msgspec.Struct):
    software_version: str
    hostname: str
    state: str
    state_message: str
    config_file: str
    cpu_info: str
    status: Optional[str] = None


class ProcStats(msgspec.Struct):
    pass


class PrintStatsInfo(msgspec.Struct):
    total_layer: int
    current_layer: int


class PrintStats(msgspec.Struct):
    state: Optional[str] = None  # Todo: maybe use default ""
    filename: Optional[str] = None
    total_duration: Optional[float] = None
    print_duration: Optional[float] = None
    filament_used: Optional[float] = None
    message: Optional[str] = None
    info: Optional[PrintStatsInfo] = None


class ToolHead(msgspec.Struct):
    position: Optional[list[float]] = None
    estimated_print_time: Optional[float] = None
    print_time: Optional[float] = None


class GcodeMove(msgspec.Struct):
    position: Optional[list[float]] = None
    gcode_position: Optional[list[float]] = None


class DisplayStatus(msgspec.Struct):
    progress: Optional[float] = None
    message: Optional[str] = None


class VirtualSdCard(msgspec.Struct):
    file_path: Optional[str] = None
    file_position: Optional[float] = None
    progress: Optional[float] = None
    file_size: Optional[int] = None
    is_active: Optional[bool] = None


class Heater(msgspec.Struct):
    temperature: Optional[float] = None
    target: Optional[float] = None
    power: Optional[float] = None


class Fan(msgspec.Struct):
    rpm: Optional[float] = None
    temperature: Optional[float] = None
    speed: Optional[float] = None
    target: Optional[float] = None


class TempSensor(msgspec.Struct):
    pass


# class StatusUpdate(msgspec.Struct):
#     print_stats: Optional[PrintStats] = None
#     toolhead: Optional[ToolHead] = None
#     virtual_sdcard: Optional[VirtualSdCard] = None
#     gcode_move: Optional[GcodeMove] = None


class WebSocketWrapper(msgspec.Struct):
    id: Optional[int] = None
    method: Optional[str] = None
    params: Union[list[Union[dict[str, msgspec.Raw], float, str]], None] = None
    result: Optional[Result] = None
    error: Optional[Any] = None
