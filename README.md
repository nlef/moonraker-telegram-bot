# moonraker-telegram-bot

## Features
- Photo&Gif creation without using filesystem (with rotations & resizing for gifs)
- Systemd service
- photo notifications on Z-heigth & percentage

## telegram bot commands list
This list of commands is usefull during bot creation/editing with BotFather
```
    /start - start communication via inline keyboard 
    /status - send klipper status
    /pause - pause printing
    /resume - resume printing
    /cancel - cancel printing
    /photo - capture & send me a photo
    /gif - let's make some gif from printer cam
    /video - record and send 10 seconds timelaps in mp4
    /poweroff - turn off moonraker power device from config
    /chat - get curent chatId
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
./install.sh
```

Then edit your config (application.conf) using fluidd web interface or some other way

### Configuration
Some tips to set up your telegram bot.
- server should point to your moonraker host (like "192.168.1.50") You would better set it to your raspberry/orange host IP for default setup with Kiauh.
- bot_token - token for your bot. To create a new bot in telegram, talk to <a href="https://telegram.me/BotFather">BotFather</a>
- chat_id - id for your chat with bot. To get this id, after creating a new bot write something to this bot, then navigate to https://api.telegram.org/bot<bot_token>/getUpdates
  you will see json with information about your message, sent to the bot. Find chat_id there.
```
server = "localhost"
  # Using localhost is enough in most cases (also for default setup with Kiauh)
  # some sbc could have problems with connection to board ip addres due to power safe mode enabled on wifi
chat_id = ""
  # id for your chat with bot. To get this id, after creating a new bot write something to this bot,
  # then navigate to https://api.telegram.org/bot<bot_token>/getUpdates
  # you will see json with information about your message, sent to the bot. Find chat_id there.
  # else this param could be taken using bot command /chat
bot_token = "*123***:***123123*"
  # token for your bot. To create a new bot in telegram, talk to <a href="https://telegram.me/BotFather">BotFather</a>
poweroff_device = "printer"
  # name of power device, configured in moonraker.

notify {
    # setting parameters to 0  will disable the corresponding notifications 
    percent = 5
    heigth = 5
}

timelapse {
    enabled = true
      # default is false
    heigth = 0.2
      # it will take photos if height change is equal provided in param
      # one may add gcode "RESPOND PREFIX=timelapse MSG=photo" in layer change g-code slicer
      # to enable photos on layer change regardless of layer height
    basedir = '/tmp/timelapse'
      # base drectory, were timelapse photos and videos are stored ( now it is automaticly cleaning) 
}

camera {
    host = 'http://{host}:8080/?action=stream'
        # camera streaming url. default is mjpeg url format on host address.
    flipVertically = false
    flipHorisontally = false
    gifDuration = 5
    reduceGif = 2
}
```


### Helpfull console commands
- check logs: ```sudo journalctl -r -u moonraker-telegram-bot```
- restart service (e.g. to read changes in config): ```sudo systemctl restart moonraker-telegram-bot```

### Moonraker update section
```
[update_manager client moonraker-telegram-bot]
type: git_repo
path: ~/moonraker-telegram-bot
origin: https://github.com/nlef/moonraker-telegram-bot.git
env: ~/moonraker-telegram-bot-env/bin/python
requirements: requirements.txt
install_script: install.sh
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