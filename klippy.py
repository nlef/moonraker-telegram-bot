# Todo: class for printer states!

class Klippy():
    def __init__(self):
        self.connected: bool = False
        self.printing: bool = False
        self.printing_duration: float = 0.0
        self.printing_progress: float = 0.0
        self.printing_filename: str = ''
