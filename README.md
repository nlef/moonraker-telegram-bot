# moonraker-telegram-bot

## Features
- Photo&Gif creation without using filesystem (with rotations & resizing for gifs)
- Systemd service
- photo notifications on Z-heigth & percentage

## telegram bot commands list
This list of commands is usefull during bot creation/editing with BotFather
```
    status - send klipper status
    photo - capture & send me a photo
    gif - let's make some gif from printer cam
```

## Bot installation

For updates or if you use it for the first time
```
cd ~
git clone https://github.com/nlef/moonraker-telegram-bot.git
cd moonraker-telegram-bot
```
then start install script
```
bash ./install.sh
```

Then edit your config (application.conf) using fluidd web interface or some other way

### Moonraker update section

```
[update_manager client moonraker-telegram-bot]
type: git_repo
path: /home/klipper/moonraker-telegram-bot
origin: https://github.com/nlef/moonraker-telegram-bot.git
env: /home/klipper/moonraker-telegram-bot/venv/bin/python
requirements: /home/klipper/moonraker-telegram-bot/requirements.txt
install_script: /home/klipper/moonraker-telegram-bot/install.sh
```

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