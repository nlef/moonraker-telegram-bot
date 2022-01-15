This document is a reference for available interactions between klipper and moonraker-telegram-bot. 

**The interaction is done via "M118"/RESPOND extended commands.**
**You will need to enable the corresponding [section](https://github.com/KevinOConnor/klipper/blob/master/docs/Config_Reference.md#respond) in your printer.cfg file.** 

The commands in this document are formatted so that it is possible to cut-and-paste them into the console or into your macros.

## m117 gcode
```
Printed 20mm
Layer 99/120
Estimated time left: 0:11:35
Finish at 2021-12-10 15:28
```
`Layer 99/120` is message fom m117 gcode. 


# RESPOND commands for bot interaction

## Running macros from the chat window
You have the possibility to run klipper macros directly from the chat interface in addition to the macros button. Simply type your macro name with a "/" in front of it. Please note, that the macro must be saved in klipper config in upper-case lettering. Calling the macro in the bot can be lower or uppercase. Example usage would be typing `/MY_FAVOURITE_MACRO` into the chat.

This might not directly seem useful, but this opens some interesting possbilities:
You can have a "respond-type" message with pre-typed `/MY_FAVOURITE_MACRO`, allowing you to simply click on it in the chat to respond to a specific action. 

This might not seem useful on first sight, but opens up some interesting possibilities on automating workflows like filament reloading. See [macro examples](macro_sample.md#highlighting) for more info.

## Running gcode from the chat window
You have the possibility to run any gcode directly from the chat interface.
Simply type `/gcode %your gcode here%` into the chat. Spaces are supported.
Example usage would be typing `/gcode G28 X Y` into the chat.


## Manual timelapse modes
If you have set manual_mode in [[timelapse]](config_sample.md#timelapse), you can use command to manage timelapse capturing by the bot.
Following commands are available:
- `RESPOND PREFIX=timelapse MSG=photo` Used to capture a single timelapse frame. Can be used with automated mode as well, but might lead to undesired results.
- `RESPOND PREFIX=timelapse MSG=start` Marks the beginning of the timelapse capture. Useful, if you want to skip some time before you start the recodring.
- `RESPOND PREFIX=timelapse MSG=stop` Marks the end of the timelapse capture. You can only run "create" after this command. Useful if you want to skip something at the end of the print, like bed extension, or purge operations. 
- `RESPOND PREFIX=timelapse MSG=pause` Pauses the automatic capturing. Useful, if you have to run service operations, like switching filament, or if you do not want automated lapse features to run for a reason.
- `RESPOND PREFIX=timelapse MSG=resume` Resumes the automated capturing, if it was paused.
- `RESPOND PREFIX=timelapse MSG=create` This starts the rendering of captured pictures to a video file. After the video is done, it is sent to the chat. You might want to run this, while you are not printing, since video-rendering is resource intensive.

## Custom notifications 
You can use RESPOND-type commands to send custom messages to the bot. 
- `tgnotify` Sends a message with an alert configured by 'silent_status'. 
Intended usage is to send custom status updates to the bot, as "heating done". An example command, to be sent from gcode or from a macro would be `RESPOND PREFIX=tgnotify MSG=my_message` or `RESPOND PREFIX=tgnotify MSG="my message with spaces"` if you need spaces.
- `tgnotify_photo`  Captures a picture, sends a message with an alert configured by 'silent_status'.
Works exactly the same as the simple notify command, but also takes a photo from the camera. It respects all the settings from the ```[camera]``` config section.
- `tgalarm` Sends a message with an alert. You get a "red" notification with sound or vibration.
An example command, to be sent from gcode or from a macro would be `RESPOND PREFIX=tgalarm MSG=my_message` or `RESPOND PREFIX=tgalarm MSG="my message with spaces"` if you need spaces.
- `tgalarm_photo` Captures a picture, sends a message with an alert. You get a "red" notification with sound or vibration.
Works exactly the same as the simple alarm command, but also takes a photo from the camera. It respects all the settings from the ```[camera]``` config section.
- `tgnotify_manual_status` status message, appened to notification message

## Runtime lapse and notification setting
If you want to run specific notifications and lapse settings based on criteria from the slicer, you can issue the following command to the bot:

Parameters for the timelapse give you the option to control settings similarly to the [[timelapse]](config_sample.md#timelapse) config section:

`RESPOND PREFIX=set_timelapse_params MSG="enabled=[1|0] manual_mode=[1|0] height=0.22 time=18 target_fps=20 min_lapse_duration=5 max_lapse_duration=15 last_frame_duration=10"`

Parameters for the notifications give you the option to control settings similarly to the [[progress_notification]](config_sample.md#progress_notification) config section:

`RESPOND PREFIX=set_notify_params MSG="percent=5 height=0.24 time=65"`

This run-time setting behaves similarly to klipper - the requested parameters remain consistent until the next restart of the bot.

## Macro for storing finished timelapse variables
```
# lapse_video_size, lapse_path, lapse_filename
[gcode_macro bot_data]
variable_lapse_video_size: 0
variable_lapse_filename: 'None'
variable_lapse_path: 'None'
gcode:
    M118 Setting bot lapse variables
```