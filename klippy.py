# Todo: class for printer states!
import requests


class Klippy():
    def __init__(self, moonraker_host: str, disabled_macros: list):
        self._host = moonraker_host
        self._disabled_macros = disabled_macros
        self.connected: bool = False
        self.printing: bool = False
        self.printing_duration: float = 0.0
        self.printing_progress: float = 0.0
        self.printing_filename: str = ''
        self.macros = self._get_marco_list()

    def _get_marco_list(self) -> list:
        resp = requests.get(f'http://{self._host}/printer/objects/list')
        if not resp.ok:
            return list()
        macro_lines = list(filter(lambda it: 'gcode_macro' in it, resp.json()['result']['objects']))
        loaded_macros = list(map(lambda el: el.split(' ')[1], macro_lines))
        return [key for key in loaded_macros if key not in self._disabled_macros]
