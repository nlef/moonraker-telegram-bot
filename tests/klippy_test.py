from bot.klippy import Klippy  # type: ignore

test_sensors = {
    "heater": {"temperature": 155.345325234, "target": 255.343434, "power": 0.60},
    "temp": {"temperature": 155.345325234},
    "fan": {"temperature": 155.345325234, "target": 255.343434, "speed": 0.75, "rpm": 2550.255},
}


def test_sensor_message():
    heater_message = Klippy._sensor_message("heater", test_sensors["heater"])
    temp_sensor_message = Klippy._sensor_message("temp", test_sensors["temp"])
    fan_message = Klippy._sensor_message("fan", test_sensors["fan"])
    assert heater_message == "♨️ Heater: 155 °C ➡️ 255 °C 🔥" and fan_message == "🌪️ Fan: 155 °C ➡️ 255 °C 75% 2550 RPM" and temp_sensor_message == "🌡️ Temp: 155 °C"
