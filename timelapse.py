class Timelapse:
    def __init__(self, enabled: bool, manual: bool, height: float):
        self._enabled: bool = enabled
        self._mode_manual: bool = manual
        self._running: bool = False
        self._height: float = height
        self._last_height: float = 0.0

    @property
    def enabled(self):
        return self._enabled

    # @enabled.setter
    # def enabled(self, new_val: bool):
    #     self._enabled = new_val

    @property
    def manual_mode(self):
        return self._mode_manual

    @property
    def height(self):
        return self._height

    @property
    def running(self):
        return self._running

    @running.setter
    def running(self, new_val: bool):
        self._running = new_val

    @property
    def last_height(self):
        return self._last_height

    # Todo: add lock?
    @last_height.setter
    def last_height(self, new_val: float):
        self._last_height = new_val
