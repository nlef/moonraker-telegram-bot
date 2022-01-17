# moonraker-telegram-bot

![image](https://user-images.githubusercontent.com/51682059/140623765-3b839b4b-40c2-4f87-8969-6cb609f2c5f1.png)


The general idea of this project is to provide you with a way to control and monitor your printer without having to setup a vpn, opening your home network, or doing any sort of other network-related vodoo.
In addition you get the benefits of push-style notifications always in your pocket, and a bandwidth-friendly way to check up on your print progress, when not near the printer.

As always with solutions like these, we kindly remind you not to print unattended, and always to take all necessary precautions against fire hazards.





## Features
- Printing progress notifications at custom intervals with pictures from a webstream/webcam
- Light control for pictures and videos, confiugurable delay for camera adjustment
- Configurable timelapsing (https://youtu.be/gzbzW7Vv2cs)
- Configurable keyboard for easy control without command typing in the bot
- Macro/gcode execution via the bot chat
- Moonraker power device control for PSU/MCU
- Sampling of photos/videos on request at any time
- Pause, Cancel, Resume
- Emergency stop


## Sample commands available:

This is a basic overview of different commands available "out of the box" after installation.
To get an indepth overview over available functionality you can check out the [config_sample](docs/config_sample.md).  
Commands can be entered directly in chat, suggested by telegram hightlightning or placed as buttons.

```
	/status			- get the status (printing, paused, error) of the printer
	/pause			- pause the current print
	/resume			- resume the current print
	/cancel			- cancel the current print
	/files			- List available G-code files for printing
	/macros			- list all available non-hidden macros
	/gcode %gcode%		- run any gcode command, spaces are supported
	/photo 			- capture a picture from the webstream/webcam
	/video 			- capture a video from the webstream/webcam
	/power			- turn off a specified moonraker power device
	/light			- toggle a specified moonraker power device
	/emergency		- run an emergency stop
	/bot_restart		- Restart the bot to apply config changes
	/shutdown		- Shut down the host system
	/%macro_name%		- Run any macro available on your system.
```

## Installation, configuration and updating

Please refer to the [installation instructions](docs/installation.md).


## Issues and bug reports

We will be happy to assist you with any issues that you have, as long as you can form a coherent sentence and are polite in your requests.  
Please write an issue, and we will try our best to reproduce and fix it.  
Feature requests and ideas are also more than welcome.

When writing issues/contacting for support please attach the 'telegram.log' as well as the output of `sudo journalctl -r -u moonraker-telegram-bot`




### Happy Printing!





---

**Klipper** by [KevinOConnor](https://github.com/KevinOConnor) :

https://github.com/KevinOConnor/klipper

---
**Moonraker** by [Arksine](https://github.com/Arksine) :

https://github.com/Arksine/moonraker

---

**Fluidd Webinterface** by [cadriel](https://github.com/cadriel) :

https://github.com/cadriel/fluidd

---

**KIAUH - Klipper Installation And Update Helper** by [th33xitus](https://github.com/th33xitus) :

https://github.com/th33xitus/KIAUH

---