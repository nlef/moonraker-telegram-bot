# moonraker-telegram-bot

The general idea of this project is to provide you with a way to control and monitor your printer without having to setup a vpn, opening your home network, or doing any sort of other network-related vodoo.
In addition you get the benefits of push-style notifications always in your pocket, and a bandwidth-friendly way to check up on your print progress, when not near the printer.

As always with solutions like these, we kindly remind you not to print unattended, and always to take all necessary precautions against fire hazards.



## Features
- Printing progress notifications at custom intervals with pictures from a webstream/webcam
- Light control for pictures and videos, confiugurable delay for camera adjustment
- Configurable timelapsing 
- Configurable keyboard for easy control without command typing in the bot
- Power device control for PSU/MCU control via moonraker
- Sampling of photos/videos/gifs on request at any time
- Pause, Cancel, Resume with a double confirmation 
- Emergency stop with a double confirmation


## Currently available commands:

These are the commands, which are currently available in the bot. Most of them are configurable according to the manual.
All commands are available on the bot keyboard, unused commands can be hidden via config.

```
	/status		- get the status (printing, paused, error) of the printer
	/pause		- pause the current print
	/resume		- resume the current print
	/cancel		- cancel the current print
	/files		- get the last 5 .gcode files, and the option to print them
	/photo 		- capture a picture from the webstream/webcam
	/video 		- capture a video from the webstream/webcam
	/gif 		- capture a gif from the webstream/webcam
	/poweroff	- turn off a specified moonraker power device
	/light		- toggle a specified moonraker power device
	/emergency	- run an emergency stop
```

## Installation, configuration and updating

When installing the bot for the first time, simply clone this distro. 

```
cd ~
git clone https://github.com/nlef/moonraker-telegram-bot.git
cd moonraker-telegram-bot
```

When the process is done, run the install script:

```
./install.sh
```

You will get asked, where to place the configuration file to. It is recommended to place it in the same catalog, where klipper configs are located, for ease of access and backup.
You can check on all the parameters and what they do in the [config_sample](docs/config_sample.md). As with klipper, start with the minimum, and expand the functionality based on your needs.

Before you can start using the bot you will have to create and configure a telegram bot.
The process is straightforward and is explained in the 'config_sample' in more detail. 


To update the bot, we recommend simply using the moonraker update manager. This is explained in detail on [moonraker update manager page](https://moonraker.readthedocs.io/en/latest/configuration/#update_manager/).
Here is the section needed:

```
[update_manager client moonraker-telegram-bot]
type: git_repo
path: ~/moonraker-telegram-bot
origin: https://github.com/nlef/moonraker-telegram-bot.git
env: ~/moonraker-telegram-bot-env/bin/python
requirements: requirements.txt
install_script: install.sh
```

Alternatively you can update by hand at your own risk, by doing a pull and running the install.sh again.
Please understand, that entering commands into the console takes a certain amount of knowledge and is your own responsibility.


When tweaking the bot, remember that you have to restart the service every time you change the config:
`sudo systemctl restart moonraker-telegram-bot`

Moonraker [history] component must be configured

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