This document is a reference for available interactions between klipper and moonraker-telegram-bot. 

The interaction is done via "M118"/RESPOND extended commands.
You will need to enable the corresponding [section](https://github.com/KevinOConnor/klipper/blob/master/docs/Config_Reference.md#respond) in your printer.cfg file. 

The commands in this document are formatted so that it is possible to cut-and-paste them into the console or into your macros.

# RESPOND commands for bot interaction
## Manual timelapse modes
If you have set manual_mode in [[timelapse]](config_sample.md), you can use command to manage timelapse capturing by the bot.
Following commands are available:
- `timelapse photo` Used to capture a single timelapse frame. Can be used with automated mode as well, but might lead to undesired results.
- `timelapse start` Marks the beginning of the timelapse capture. Useful, if you want to skip some time before you start the recodring.
- `timelapse stop` Marks the end of the timelapse capture. Useful if you want to skip something at the end of the print, like bed extension, or purge operations. 
- `timelapse pause` Pauses the capturing. Useful, if you have to run service operations, like switching filament.
- `timelapse resume` Resumes the capturing, if it was paused.
- `timelapse create` This starts the rendering of captured pictures to a video file. After the video is done, it is sent to the chat. You might want to run this, while you are not printing, since video-rendering is resource intensive.

## Manual timelapse modes
You can use RESPOND-type commands to send custom messages to the bot. You have two options:
- `tgnotify` Sends a message with an alert configured by 'silent_status'. Intended usage is to send custom status updates to the bot, as "heating done".
An example command, to be sent from gcode or from a macro would be `RESPOND PREFIX=tgnotify MSG=my_message` or `RESPOND PREFIX=tgnotify MSG="my message with spaces"` if you need spaces.
- `tgnotify_photo` 
- `tgalarm` Sends a message with an alert. You get a "red" notification with sound or vibration.
An example command, to be sent from gcode or from a macro would be `RESPOND PREFIX=tgalarm MSG=my_message` or `RESPOND PREFIX=tgalarm MSG="my message with spaces"` if you need spaces.
- `tgalarm_photo`