## Installation, configuration and updating

**Please understand, that entering commands into the console takes a certain amount of knowledge and is your own responsibility.**

If you are installing the bot for the first time, there are multiple things you have to do:

### Step 1: Create a telegram bot.
This is done by talking to [BotFather](https://telegram.me/botfather) on telegram. Name the bot, give it a username. You will get a token to control the bot.

### Step 2: Install the bot.
**We recommend installing the bot with [KIAUH](https://github.com/th33xitus/KIAUH), and updating it via moonraker or via KIAUH.**  
**Moonraker [history] component must be configured.**

You may of course still install the bot manually:
Simply clone the distro:
```
cd ~
git clone https://github.com/nlef/moonraker-telegram-bot.git
cd moonraker-telegram-bot
```

After cloning is done, run the install script:

```
./scripts/install.sh
```

The script will ask for config file location. We recommend to place it in the same catalog, where the klipper config files are located for ease of access and backup.
The bot will create a base minimal config named `telegram.conf`.



### Step 3: Connect the telegram bot with your bot installation 
Copy the token from **step 1** and paste it in the `telegram.conf` in the `[bot]` section.  
Restart the bot with `sudo systemctl restart moonraker-telegram-bot`.
Open the chat window with your bot in telegram, and write anything to it. The bot will respond, that you are not authorized, and provide you with your chat id.  
Copy this id to the `telegram.conf` in the `[bot]` section.  
Restart the bot with `sudo systemctl restart moonraker-telegram-bot`.  

You should get a response from the bot in chat, and base functionality should be accessible.
If you are not getting any meaningful responses from the bot, or no response at all, you propably have made mistakes while installing it and have a corrupted installation, or the bot is not able to reach telegram servers.
Try checking the logs in (default would be `~/klipper_logs/`) or do a clean reinstall. 

**After step 3 the bot should be running properly and respond to commands, as well as present you with buttons. You can restart the bot directly with /bot_restart after this for any config changes.**


### Step 4: Add the bot to the moonraker update manager
Mainsail and Fluidd both support checking for updates as well as updating installed klipper components. If you regularly update klipper and moonraker, you should keep the bot updated as well. 

Paste this to the moonraker config and restart moonraker.
```
[update_manager client moonraker-telegram-bot]
type: git_repo
path: ~/moonraker-telegram-bot
origin: https://github.com/nlef/moonraker-telegram-bot.git
env: ~/moonraker-telegram-bot-env/bin/python
requirements: scripts/requirements.txt
install_script: scripts/install.sh
```

If you need more information on the process, you can check it out in detail on [moonraker update manager page](https://moonraker.readthedocs.io/en/latest/configuration/#update_manager).

### Step 5 (optional): Include the macro to store lapse variables
If you intend on using the timelapse module to make timelapse videos, you should add a macro to store finished lapse parameters. 

Simply paste this little macro to any place in your klipper configuration:
```
[gcode_macro _bot_data]
variable_lapse_video_size: 0
variable_lapse_filename: 'None'
variable_lapse_path: 'None'
gcode:
    M118 Setting bot lapse variables
```
You can then if you need later on access video parameters after its built and done with klipper macros. This might be useful for different automatisations.

### Step 5: Additional features 
To enable the more advanced functions, you should check out the [config_sample](config_sample.md) document. It contains a description of the new fresh functions available in the latest version of the bot, and how to use them.

Another good place to get information from are the [interacting with klipper](interacting_with_klipper.md) and [macro_sample](macro_sample.md) files. They describe different ways and ideas how to use the bot together with klipper to get the maximum usability out of both.

If you have suggestions on usage scenarios, don't hesitate to drop us an issue with your usage example, we would love to see it and describe it in the documentation.