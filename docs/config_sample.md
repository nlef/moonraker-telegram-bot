This document is a reference for options available in the moonraker-telegram-bot

The descriptions in this document are formatted so that it is possible to cut-and-paste them into a printer config file. See the installation document for information on setting up the bot and setting up an initial config file.


## [bot]

Configuration of the main bot parameters

```
[bot]
server: localhost
#	This is the adress, where the moonraker of the desired printer is located at. 
#	In most cases it will be 'localhost'. Alternatively, an ip:port, as in 192.168.0.19:7125 can be entered, 
#	if you are running multiple moonraker instances on the machine, or if the bot is located not on the printer itself.
chat_id: xxxxxxxxx
#	This is the ID of the chat, where the bot is supposed to be able to send updates to. 
#	To get the ID, after creating a new bot write something to this bot, then navigate to 
#	https://api.telegram.org/bot<bot_token>/getUpdates you will see json with information about your message, sent to the bot. 
#	Find chat_id there.
bot_token: xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
#	This is the bot token, the most important part of every bot. 
#	You get it when you create a new bot. To create a new bot, you have to talk to @BotFather in telegram. 
#	The only thing you need is the token, the rest is taken care of by the chat_id.
#	Only the chat with the correct chat_id can send/receive commans to the bot.
#light_device: leds
#	This is the power device in moonraker, to which the lights of the printer/chamber are connected to.
#	If you do not have lights/have no need to cycle them, you can skip this.
#power_device: power
#	This is the power device in moonraker, to which the power of the printer/chamber is connected to.
#	This may be useful, if you do not shutdown the device klipper runs on, but shut down all the slave boards.
#debug: true
#	This enables extensive logging. Only use it for debugging/troubleshooting.
#	Default is to omit this/false.
#log_path: /tmp
#eta_source: slicer
#   Values avaliable: slicer, file
#   Default value is slicer.
```


## [camera]

This section is responsible for the different webcam/webstream parameters.

```
[camera]
host: http://localhost:8080/?action=stream
#	This is the adress, where the desired webcam/webstream is located at. Enter this the same way you enter it in 
#	your printers web interface/your player. If you can stream it, the bot supports it, native h264 streams 
#	for example a vlc stream from a runcam webcam is absolutely possible. Do not feel contstrained by mjpeg streams.
#flipVertically: false
#	You can flip the camera image vertically, if needed. Disabled by default. Set to true if needed.
#flipHorizontally: false
#	You can flip the camera image horizontally, if needed. Disabled by default. Set to true if needed.
#fourcc: x264
#	You can change the opencv VideoWriter fourcc codec. The default value is 'x264'.
# 	An alternative is m4v for playback on specific apple devices, or if the machine which is going to do
#	the encoding is very weak.
#threads: 2 
#	You may limit the threads used for image processing. Default value is calculalated, (os.cpu_count() / 2)
#gifDuration: 20
#	This is the length in seconds of the gif, which is sent when requested with /gif command. 
#	Default length of a gif is 5 seconds.
#reduceGif: 2
#	If you insist on using gifs, which constitutes as a war crime in 2021, you can use a divider for the resolution.
#	Width and height get divided by this number, and the result is rounded down. 
#	Default value is 2.
#videoDuration: 125
#	This is the length in seconds of the video, which is sent when requested with /video command. 
#	Default length of a video is 5 seconds
#light_control_timeout: 2
#	When the bot toggles lights to take a picture, or record a video, most cameras need a couple of seconds to adjust to 
#	the transition between full darkness and full brightness. This option tells the bot to wait n seconds, before
#	taking the picture, recording a video, doing timelapse photos. The default is not to use a delay.
```


## [progress_notification]

This section is responsible for the notification on printing progress updates. This entire section is optional.

```
#[progress_notification]
#percent: 5
#	This is an interval in percent, when a notification with a picture is sent to the chat.
#	When set to 5, notifications are sent at 5%, 10%, 15%, etc.
#	When set to 3, notifications are sent at 3%, 6&, 9%, etc.
#	The default is not to send notifications based on print percentage.
#height: 5
#	This is an interval in mm, when a notification with a picture is sent to the chat.
#	When set to 5, notifications are sent at 5mm, 10mm, 15mm, etc, print height.
#	When set to 3, notifications are sent at 3mm, 6mm, 9mm, etc, print height
#	The default is not to send notifications based on print height.
#min_delay_between_notifications: 60
#	When printing small models the bot can cause unwanted notification/message spam. In future releases
#	the notification type (silent/normal) will be available. For now you can either mute the bot, or use this parameter
#	to limit how often notifications are sent. The value sets, how many seconds have to pass, before the next 
#	notification is sent. Default is not to use any limits.
#groups: group_id_1, group_id_2
#	When running multiple printers/a farm, you may want to aggregate all notifications from all printers in a group.
#	You can enter group IDs here, to which notifications will be sent. No control from a group is possible.
#	Only notifications are sent.
```


## [timelapse]

This section is responsible for timelapse creation as well as file location for timelapse processing. This entire section is optional.
Please consider, that in the current release both picture capturing methods are unstable, and it is recommended to use 
`RESPOND PREFIX=timelapse MSG=photo` instead to take pictures via gcode/macro/slicer. This warning will be removed, when the feature is considered stable.

```
[timelapse]
#basedir: /tmp/timelapse
#	This sets the folder, where to save timelapse pictures and the resulting video. 
#	Default is '/tmp/timelapse', but you can set it to any catalog, which the bot 
#	has rights to write to. Might be useful for saving the sd cards life by writing to external storage.
#cleanup: true
#	Should the bot clean the catalog with pictures and video after the successful sending to the telegram chat.
#	Default is true. You might want to set it to false, if you intend on using the pictures later.
#height: 0.2
#	The bot can take timelapse pictures based on the z axis height. The default is not to take pictures based on height.
#	Your layer height should be a multiple/equal to this number.
#time: 5
#	The bot can take timelapse pictures based on time intervals in seconds. The default is not to take pictures based on time intervals.
#target_fps: 15  
#	This is the target fps of the created video. The larger this number, the "faster" the timelapse will be.
#	15 fps equals 15 images per second lapsing. The default is 15 fps.
```


## [telegram_ui]

This section is responsible for different ui settings of the bot in telegram. More configuration options will be available in the future. This entire section is optional.

```
[telegram_ui]
#hidden_methods: /gif, /video
#	This allows you to hide unused buttons from your bots keyboard. For example, if you do not intend to commit war crimes,
#	you can disable the /gif button. 
disabled_macros: PAUSE,RESUME
#silent_progress: true
#silent_commands: true
#silent_status: true
```