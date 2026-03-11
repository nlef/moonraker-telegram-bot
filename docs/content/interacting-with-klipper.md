# Interacting with Klipper

This document is a reference for available interactions between Klipper and moonraker-telegram-bot.
The commands in this document are formatted so that it is possible to cut-and-paste them into the console or into your macros.

**Some interactions are done via `M118`/`RESPOND` extended commands.**
**You will need to enable the corresponding [section](https://www.klipper3d.org/Config_Reference.html#respond) in your printer.cfg file.**

## Running Macros

You have the possibility to run Klipper macros directly from the chat interface in addition to the macros button. Simply type your macro name with a "/" in front of it. Please note, that the macro must be saved in Klipper config in upper-case lettering. Calling the macro in the bot can be lower or uppercase. Example usage would be typing `/MY_FAVOURITE_MACRO` or `/my_favourite_macro` into the chat.

This allows you to have a "respond-type" message with pre-typed `/MY_FAVOURITE_MACRO`, allowing you to simply click on it in the chat to respond to a specific action.

While not directly useful on first sight, it opens up some interesting possibilities on automating workflows like filament reloading. See [macro examples](ideas-for-macros.md#highlighting) for more info.


## Running G-Code

You have the possibility to run any gcode directly from the chat interface.
Simply type `/gcode %your gcode here%` into the chat. Spaces are supported.
Example usage would be typing `/gcode G28 X Y` into the chat.


## Manual Timelapse

If you have set `manual_mode` in [timelapse](sample-config.md#timelapse), you can use commands to manage timelapse capturing by the bot.
The following commands are available:

- `RESPOND PREFIX=timelapse MSG=photo` — Capture a single timelapse frame. Can be used with automated mode as well, but might lead to undesired results.
- `RESPOND PREFIX=timelapse MSG=photo_and_gcode` — Capture a single timelapse frame. Runs the command specified in the `after_photo_gcode` parameter in the `[timelapse]` section after the photo is taken.
- `RESPOND PREFIX=timelapse MSG=start` — Mark the beginning of the timelapse capture. Useful if you want to skip some time before you start the recording.
- `RESPOND PREFIX=timelapse MSG=stop` — Mark the end of the timelapse capture. You can only run `create` after this command. Useful if you want to skip something at the end of the print, like bed extension or purge operations.
- `RESPOND PREFIX=timelapse MSG=pause` — Pause the automatic capturing. Useful if you have to run service operations, like switching filament.
- `RESPOND PREFIX=timelapse MSG=resume` — Resume the automated capturing, if it was paused.
- `RESPOND PREFIX=timelapse MSG=create` — Start the rendering of captured pictures to a video file. After the video is done, it is sent to the chat. You might want to run this while you are not printing, since video rendering is resource-intensive.


## Sending Messages from Klipper

You can use `RESPOND`-type commands to send custom messages to the bot.
You have three types of messages:

**1. Notify-Messages**
These messages are ideal for low-importance notifications to the bot. You can configure them not to trigger notification sound on your telegram clients.

- `tgnotify` — Sends a new message with/without an alert configured by `silent_status`.
  Intended usage is to send custom status updates to the bot, such as "heating done".

    ```
    RESPOND PREFIX=tgnotify MSG=my_message
    RESPOND PREFIX=tgnotify MSG="my message with spaces"
    ```

- `tgnotify_photo` — Captures a picture, sends a message with an alert configured by `silent_status`. Works exactly the same as the simple notify command, but also takes a photo from the camera. It respects all the settings from the `[camera]` config section.

    ```
    RESPOND PREFIX=tgnotify_photo MSG=my_message
    RESPOND PREFIX=tgnotify_photo MSG="my message with spaces"
    ```

- `tgnotify_status` — Updates the status message with a line of your choice. You can use it to prevent unwanted message spam, and write any relevant information directly to the status message. This notification type is completely silent and does not generate an additional message.

    ```
    RESPOND PREFIX=tgnotify_status MSG=my_message
    RESPOND PREFIX=tgnotify_status MSG="my message with spaces"
    ```

- `M117` — The default G-code for writing to displays in Klipper. The bot automatically adds M117-type G-code to the status messages.


**2. Alarm-Messages**
These messages are used for high-priority alerts to the bot. They always trigger an alert on your telegram clients.

- `tgalarm` — Sends a message with an alert. You get a "red" notification with sound or vibration.

    ```
    RESPOND PREFIX=tgalarm MSG=my_message
    RESPOND PREFIX=tgalarm MSG="my message with spaces"
    ```

- `tgalarm_photo` — Captures a picture, sends a message with an alert. You get a "red" notification with sound or vibration.
  Works exactly the same as the simple alarm command, but also takes a photo from the camera. It respects all the settings from the `[camera]` config section.


**3. Keyboards**
You can send a message with buttons similar to how the confirmation dialogs look like.
These messages respect the configuration of `silent_commands`.
An example command looks like this:

```
RESPOND PREFIX=tgcustom_keyboard MSG="message='',buttons=[{name='', command=''}]"
```

Sadly, due to how jinja/Klipper works, you can not send this directly. You first have to assign this to a temporary variable.
We are working on something nicer, but since this is a one-time thing to do in your config, it should not bother too much.
```
{% set bot_msg = "message='Action to do:', buttons=[{name='Eject', command='_FILAMENT_EJECT_SELECTION'},{name='Change', command='_FILAMENT_CHANGE_SELECTION'},{name='Insert',command='_FILAMENT_INSERT_SELECTION'},{name='Cancel',command='delete'}]" %}
RESPOND PREFIX=tgcustom_keyboard MSG="{bot_msg}"
```
This command will generate the following message:

![](img/buttons.png)

All buttons will be added from left to right, in one row. The buttons can have any name, and can execute commands, in this case macros. Commands like "ACCEPT", "ADJUST", obviously work as well. `command='delete'` deletes the message, and helps cleaning up multiple-message-workflows.
You can also have a multiple-row message, by using a list for every row:
```
	{% set bot_msg = "message='Nuclear launch codes', buttons=[[{name='1', command='1'},{name='2', command='2'},{name='3', command='3'},{name='4', command='4'}],[{name='A', command='A'},{name='B', command='B'},{name='C', command='C'},{name='D', command='D'}],[{name='Cancel launch', command='Cancel'}]]" %}
	RESPOND PREFIX=tgcustom_keyboard MSG="{bot_msg}"
```
![](img/buttons2.png)

We suggest using those messages to automate pause, resume and service workflows.
Examples will follow in the [Ideas for macros](ideas-for-macros.md) document.


## Message Formatting

All configurable messages (status, command, keyboard etc.) support some of the HTML formatting.
Valid formatting tags are:

- `<b></b>` — bold
- `<u></u>` — underlined
- `<i></i>` — italic
- `<tg-spoiler></tg-spoiler>` — Telegram spoiler
- `<del></del>` — strikethrough
- `<pre></pre>` — code block, also forces a new line
- `<a href='http://www.example.com/'>inline URL</a>` — insert links (make sure you use single quotes)

An example message using bold formatting:

```
RESPOND PREFIX=tgnotify MSG=my_message with <b>bold text</b>
```



## Runtime Configuration

If you want to run specific notifications and lapse settings based on criteria from the slicer, you can issue the following command to the bot:

Parameters for the timelapse give you the option to control settings similarly to the [timelapse](sample-config.md#timelapse) config section:

```
RESPOND PREFIX=set_timelapse_params MSG="enabled=[1|0] manual_mode=[1|0] height=0.22 time=18 target_fps=20 min_lapse_duration=5 max_lapse_duration=15 last_frame_duration=10"
```

Parameters for the notifications give you the option to control settings similarly to the [progress_notification](sample-config.md#progress-notifications) config section:

```
RESPOND PREFIX=set_notify_params MSG="percent=5 height=0.24 time=65"
```
This run-time setting behaves similarly to Klipper — the requested parameters remain consistent until the next restart of the bot.

## Sending Files

If you want to cut and process videos yourself, and upload them later, or if you want to [automate resonance testing](ideas-for-macros.md#automating-resonance-testing), or if you have some other obscure usecase we are not aware of, you can use the following gcodes to send an image, video or file:
**Don't forget, that there is a 50mb filesize limit and 10mb limit per image for bots in telegram.**
You can use absolute or relative paths. Relative paths work from the location, where the bot is installed.
In case you used KIAUH for installation, that will be `home/pi/moonraker-telegram-bot/bot`.

**Sending an image:**

```
RESPOND PREFIX=tg_send_image MSG="path='/home/pi/hat.png', message='A man bought this hat and it fits him nicely'"
```

The `message=` parameter is optional. You can also send multiple images as an album:

```
RESPOND PREFIX=tg_send_image MSG="path=['/home/pi/hat.png', '/home/pi/his_other_hat.png'], message='Album of hats'"
```

**Sending a video:**

```
RESPOND PREFIX=tg_send_video MSG="path='/home/pi/3M-54.mp4', message='Calibration complete!'"
```

The `message=` parameter is optional. You can also send multiple videos as an album:

```
RESPOND PREFIX=tg_send_video MSG="path=['/home/pi/3M-54.mp4', '/home/pi/3M-54.1.mp4'], message='Calibration album complete!'"
```

**Sending a file:**

```
RESPOND PREFIX=tg_send_file MSG="path='/home/pi/data.zip', message='Upload finished'"
```

The `message=` parameter is optional. You can also send multiple files as an album:

```
RESPOND PREFIX=tg_send_file MSG="path=['/home/pi/data_1.zip', '/home/pi/data_2.zip'], message='Upload finished'"
```
