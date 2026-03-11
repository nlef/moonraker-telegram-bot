# Sample bot configuration

This document is a reference for configuration options available in the moonraker-telegram-bot.
The descriptions in this document are formatted so that it is possible to cut-and-paste them into a printer config file. See the installation document for information on setting up the bot and setting
up an initial config file.

## [bot]

Configuration of the main bot parameters

```text
[bot]
server: localhost
#   This is the address, where the moonraker of the desired printer is located at.
#   In most cases it will be 'localhost'. Alternatively, an ip:port, as in 192.168.0.19:7125 can be entered,
#   if you are running multiple moonraker instances on the machine, or if the bot is located not on the printer itself.
#   This value is not validated automatically.
#   If you are running ssl, it should be yourcooldomain.xyz:ssl_poort, if certificate validation is enabled
bot_token: xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
#   This is the bot token. Please keep it safe and do not post it online.
#   Only the chat with the correct chat_id can send/receive commands to/from the bot.
#   Alternatively can be stored in a file. See [secrets] section.
#   This value is not validated automatically.
chat_id: xxxxxxxxx
#   This is the ID of the chat, where the bot is supposed to be able to send updates to.
#   Alternatively can be stored in a file. See [secrets] section.
#   This value should be an integer.
#ssl: False
#   This enables ssl for the moonraker connection.
#   Default is to omit this.
#   This value should be boolean.
#ssl_verify: True
#   If ssl is enabled, you can enable or disable certificate verification.
#   Default is true.
#   This value should be boolean.
#api_url: https://api.telegram.org/bot
#   If you need it, you can specify a custom url for the telegram API.
#   This value is not validated automatically.
#socks_proxy: 192.168.0.22:1080
#   If needed, you can configure the bot to use a socks5 proxy.
#   This value is not validated automatically.
#user: root
#   If you have moonraker authorization enabled, you can enter the user and password here.
#   My advice is to not expose your printer to the internet, but I am not your mother.
#   Alternatively can be stored in a file. See [secrets] section.
#   This value is not validated automatically.
#password: qwerty
#   The password is stored in plain text.
#   Alternatively can be stored in a file. See [secrets] section.
#   This value is not validated automatically.
#api_token: 86c4841ec730415da0a66064ceeaa99c
#   As an alternative to the password you can use a token.
#   Alternatively can be stored in a file. See [secrets] section.
#   This value is not validated automatically.
#light_device: leds
#   This is the power device in moonraker, to which the lights of the printer/chamber are connected to.
#   If you do not have lights/have no need to cycle them, skip this parameter.
#   Default is to omit this.
#   This can be a klipper output_pin or a macro as well. Refer to moonrakers "klipper_device" documentation.
#   This parameter will be derpeciated in coming updates.
#power_device: power
#   This is the power device in moonraker, to which the power of the printer slave boards are connected to.
#   A typical usage scenario is to shutdown power to the MCUs, but not to disable the host on which klipper is running.
#   If you do not have such a setup, skip this.
#   Default is to omit this.
#   This parameter will be derpeciated in coming updates.
#debug: false
#   This enables extensive logging. Only use it for debugging/troubleshooting.
#   Default is to omit this/false.
#   This value should be boolean.
#upload_path: myfolder/mysubfolder
#   This determines, to which folder the bot will upload the gcodes sent to it.
#   Default is to use the base gcode folder.
#   Any path entered is relative from the klippers gcode folder.
#   This value is not validated automatically.
```

## [camera]

This section is responsible for the different webcam/webstream parameters.

```text
[camera]
host: http://localhost:8080/?action=stream
#   This is the address, where the desired webcam/webstream is located at. Enter this the same way you enter it in
#   your printers web interface/your player. If you can stream it, the bot supports it, native h264 streams,
#   for example a vlc stream from a runcam webcam is absolutely possible. Do not feel constrained by mjpeg streams.
#   This value is not validated automatically.
#type: mjpeg
#   This parameter selects what method to use to process the videostream.
#   The default value is mjpeg.
#   Allowed values: opencv, ffmpeg or mjpeg.
#   Detailed explanation on camera types can be found in the camera modes wiki page.
#host_snapshot: http://localhost:8080/?action=snapshot
#   When you use mjpeg as type, this is needed to fetch pictures from the camera.
#   The default is generated from the host address, replacing stream with snapshot.
#   Unless you have a specific need, this parameter is unneeded
#   This value is not validated automatically.
#fps: 30
#   If you want to use the "/video" button/command, you should set the camera fps. Not set by default.
#   This value should be a positive integer >0.
#flip_vertically: false
#   You can flip the camera image vertically, if needed. Disabled by default. Set to true if needed.
#   This value should be boolean.
#flip_horizontally: false
#   You can flip the camera image horizontally, if needed. Disabled by default. Set to true if needed.
#   This value should be boolean.
#rotate: 90_cw
#   You can rotate the camera image, if needed. Disabled by default.
#   Allowed values: 90_cw, 90_ccw, 180. Default behaviour is not to rotate the image.
#fourcc: h264
#   This parameter configures, which codec is used to assemble the timelapse video.
#   mpeg4 is usually less ressource-intensive, h264 often has hardware acceleration.
#   Depending on your needs you can switch to one or the other.
#   Default value is h264.
#threads: 2
#   You may limit the threads used for image processing. Default value is calculated, (os.cpu_count() / 2)
#   This value should be and integer >0 and < os.cpu_count().
#video_duration: 5
#   This is the length in seconds of the video, which is sent when requested with /video command.
#   Default length of a video is 5 seconds
#   This value should be a positive integer >0.
#video_buffer_size: 2
#   On most single-board computers the renderer does not manage to capture and process frames fast enough for a video.
#   If you are getting shorter videos than you requested in video_duration and have free RAM, you can increase this value.
#   Be careful - 1 uncompressed captured image weights about 6mb when in fullHD.
#   OpenCV libraries have a limit of 2 GB, so please calculate your maximum available buffer from your fps and resolution.
#   This value should be a positive integer >0.
#light_control_timeout: 0
#   When the bot toggles lights to take a picture, or record a video, most cameras need a couple of seconds to adjust to
#   the transition between full darkness and full brightness. This option tells the bot to wait n seconds, before
#   taking the picture, recording a video, doing timelapse photos. The default is not to use a delay.
#   This value should be an integer >=0.
#picture_quality: high
#   This parameter controls the picture quality the bot uses for status and timelapse purposes.
#   Allowed values: low, high
#   Low uses jpeg with quality set to 80, high uses losless webp.
#   Default is "high"
```

## [secrets]

This section is responsible for the location of the file with credentials.
May be useful if you want to autoupload your config somewhere and do not want to remove the credentials manually beforehand.
This section is optional.

```text
#[secrets]
#secrets_path: /home/pi/printer_data/config
#   Set the full path to your secrets.conf here. Secrets.conf needs to be readable for the user under which the bot is running.
#   This value is not validated automatically.
```

The secrets file can contain the following lines:

```text
[secrets]
#bot_token:
#chat_id:
#user:
#password:
#api_token:
```

They are documented in the [bot] section
When the secrets section is enabled, "normal" config entries are ignored.

## [progress_notification]

This section is responsible for the notification on printing progress updates. This entire section is optional.
You can override these parameters with [runtime settings by gcode](interacting-with-klipper.md#runtime-lapse-and-notification-setting).

```text
#[progress_notification]
#percent: 0
#   This is an interval in percent, when a notification with a picture is sent to the chat.
#   When set to 5, notifications are sent at 5%, 10%, 15%, etc.
#   When set to 3, notifications are sent at 3%, 6%, 9%, etc.
#   The default is not to send notifications based on print percentage.
#   This value should be an integer >=0.
#height: 0.0
#   This is an interval in mm, when a notification with a picture is sent to the chat.
#   When set to 5.0, notifications are sent at 5.0mm, 10.0mm, 15.0mm, etc, print height.
#   When set to 0.25, notifications are sent at 0.25mm, 0.5mm, 0.75mm, etc, print height.
#   The default is not to send notifications based on print height.
#   This value should be an float >=0.0.
#time: 0
#   This is the recommended way to use progress notifications.
#   This is an interval in seconds, when a notification with a picture is sent to the chat.
#   When set to 600, notifications are sent at 600 seconds, 1200 seconds, 1800 seconds, etc, print time.
#   When set to 100, notifications are sent at 100 seconds, 200 seconds, 300 seconds, etc, print time.
#   The default is not to send notifications based on time.
#   This type of notifications continues, even when the print is paused. So if your printer triggers a pause, for example
#   caused by filament runout, you will still get notifications regularly, until the print is completed/canceled.
#   This value should be an integer >=0.
#groups: group_id_1, group_id_2, group_id_3:thread_id
#   When running multiple printers/a farm, you may want to aggregate all notifications from all printers in a group.
#   You can also use threads inside the group. Fastest way to get group/thread ids is to copy a link to a message inside a thread.
#   You can enter group IDs here, to which notifications will be sent. No control from a group is possible.
#   Bots send start/status print updates, errors, messages from Interacting-with-klipper#send-custom-notifications-to-the-bot to the group.
#   Telegram group IDs are negative integers. Thread IDs are short integers.
#group_only: false
#   If you are using the group feature, you might want to disable individual notifications and use group notifications only.
#   Set this to true to disable individual notifications in individual chats.
#   This value should be boolean.
```

## [timelapse]

This section is responsible for timelapse creation as well as file location for timelapse processing. This entire section is optional.
You can override these parameters with [runtime settings by gcode](interacting-with-klipper.md#runtime-lapse-and-notification-setting).

```text
[timelapse]
#basedir: ~/moonraker-telegram-bot-timelapse
#   This sets the folder, where to save timelapse pictures and the resulting video.
#   Default is '~/moonraker-telegram-bot-timelapse', but you can set it to any catalog, which the bot
#   has rights to write to. Might be useful for saving the sd cards life by writing to external storage.
#   This value is not validated automatically.
#copy_finished_timelapse_dir: /home/pi/timelapse/finished
#   This sets the folder, to which finished timelapses get copied to.
#   The default behaviour is not to copy it anywhere. This might be useful, if you want to keep only the videos,
#   but clean up the pictures, or if you want to upload the videos to some network location.
#   This value is not validated automatically.
#cleanup: true
#   Should the bot clean the catalog with pictures and video after the successful sending to the telegram chat.
#   Default is true. You might want to set it to false, if you intend on using the pictures later.
#   This value should be boolean.
#height: 0
#   The bot can take timelapse pictures based on the z axis height (e.g. 0.2). The default is not to take pictures based on height.
#   This value should be an float >=0.0.
#time: 0
#   The bot can take timelapse pictures based on time intervals in seconds.
#   The default is not to take pictures based on time intervals.
#   This value should be an integer >=0.
#target_fps: 15
#   This is the target fps of the created video. The larger this number, the "faster" the timelapse will be.
#   15 fps equals 15 images per second lapsing. The default is 15 fps.
#   This value should be an integer >0.
#limit_fps: false
#   If a video for a targeted max_lapse_duration will have larger fps than target_fps, frames will get dropped to match target_fps
#   The default is to omit this and not to drop any frames.
#   This value should be boolean.
#min_lapse_duration: 0
#   On short prints, or with limited lapse pictures available, the lapse often gets to short to meaningfully display
#   the printing progress. You can specify the desired minimum duration of the created timelapse
#   (not including last_frame_duration). This means, that the fps will get reduced, if the lapse is shorter than this time.
#   The default is to omit this.
#   This value should be an integer >0 and <max_lapse_duration
#max_lapse_duration: 0
#   Similar to min_lapse_duration, if your lapse video gets too long to watch, you can specify a desired time, which
#   should not be exceeded. This means, that the fps will get increased, if the lapse is going to be longer than this time.
#   The default is to omit this.
#   This value should be an integer >0 and >min_lapse_duration
#last_frame_duration: 5
#   This allows you to prevent the timelapse video from ending too abruptly. You can choose a duration for which
#   to loop the last picture taken.
#   Default is 5 seconds.
#   This value should be an integer >=0.
#after_lapse_gcode: /my_macro
#   You can run a macro or a gcode (e.g /gcode %your gcode here%) after the bot finishes building the timelapse.
#   This may be useful, if you intend shutting down the printer, if you want to automatically start another print,
#   or if you want to do something with the video after its ready.
#   This value is not validated automatically.
#send_finished_lapse: true
#   If you want to build the lapse, but do not want to send it, set this to false. Default is true.
#   This value should be boolean.
#save_lapse_photos_as_images: false
#   When using opencv as camera type settings, images are normally stored as raw objects.
#   If you still need the physical pictures on disk for whatever reason, you can toggle this to true.
#   This value should be boolean.
#manual_mode: false
#   If True, only commands from gcode will manage timelapse.
#   Default is false.
#   This value should be boolean.
#after_photo_gcode:
#   You can enter any macro or gcode to be run after the "photo_and_gcode" command.
#   Default is not to run any gcode after the command.
#   This value is not validated automatically.
```

## [telegram_ui]

This section is responsible for different ui settings of the bot in telegram. More configuration options will be available in the future. This entire section is optional.

```text
[telegram_ui]
#eta_source: slicer
#   You can choose, which value to use for remaining time estimation.
#   Default value is slicer.
#   Allowed values: slicer, file
#buttons: [status,pause,cancel,resume],[files,emergency,macros,shutdown]
#   You can redefine, which buttons and in which position you want displayed.
#   If this is not defined, the default order is used. Buttons are defined per row, each row separated.
#   This allows you to add your own custom macros to the bot's keyboard as well.
#   Add or replace default bot commands with "/%macro_name%. The macro listed in this section
#   should be defined in klipper config files, in UPPERCASE. Maximum macro name length is 54 chars.
#   This value is not validated automatically.
#require_confirmation:
#   This list contains commands (or macros) which will trigger an additional confirmation in chat before executing.
#   You can either list specific g-codes, macros and bot commands, or use meta-groups "gcodes", "macros", "commands" to list all types respectively.
#   Default is "logs, logs_upload, shutdown, restart, cancel, fw_restart, emergency, reboot, power, bot_restart".
#progress_update_message: false
#   Every time status gets updated, a notification message is sent, that the progress status has been updated.
#   It gets deleted after the next status update is triggered. Setting it to false will not send notifications on progress updates.
#   Default is false.
#   This value should be boolean.
#silent_progress: false
#   If progress_update_message is set to true, setting this to "true" sends the printer status change without an alert.
#   You still get a "red" notification. Sadly the bot API does not permit sending "grey" completely silent messages.
#   There is no way to work around that.
#   Default is false.
#   This value should be boolean.
#silent_commands: false
#   Sends all other messages (for example the emergency stop confirmation) without an alert.You still get a "red" notification,
#   but it does not have sound or vibration.
#   Sadly the bot API does not permit sending "grey" completely silent messages. There is no way to work around that.
#   Default is false.
#   This value should be boolean.
#silent_status: false
#   Sends the printer status change (error, idle etc.) without an alert. You still get a "red" notification,
#   but it does not have sound or vibration.
#   Sadly the bot API does not permit sending "grey" completely silent messages. There is no way to work around that.
#   Default is false.
#   This value should be boolean.
#include_macros_in_command_list: true
#   This enables in-chat autocomplete for macros.
#   Default is true.
#   Telegram can not handle autocompletion for commands longer than 32 symbols. Macro names which exceed this length will not get autocompleted.
#   This value should be boolean.
#hidden_macros: macro1, macro2
#    If you have macros displayed in the auto-complete list, you may want to hide specific ones.
#   Default is not to hide any macros.
#   This value is not validated automatically.
#hidden_bot_commands: bot_restart, cancel, video
#   You may want to hide specific commands of the bot from the auto-complete list.
#   Default is not to hide any commands.
#   This value is not validated automatically.
#show_private_macros: false
#   You can decide to show service macros prefaced with a "_" in the autocomplete list.
#   Default is to hide and not autocomplete these macros.
#   This value should be boolean.
#status_message_m117_update: false
#   If you want, you can trigger a status update on the m117 command.
#   Default is not to trigger an update.
#   This value should be boolean.
```

## [status_message_content]

This section is responsible for the contents of the status message. Here you can define, what gets displayed in the message, that you receive when using the "/status" command.
This section is optional
This section will get expanded later.

```text
#[status_message_content]
#content: progress, height, filament_length, filament_weight, print_duration, eta, finish_time, m117_status, tgnotify_status, last_update_time
#   This controls the content of the status message. You can choose to delete specific information not relevant to you.
#   Most of those parameters are self-explanatory. m117_status leaves a line to display text from the M117 gcode.
#   tgnotify_status is used to display custom information by sending the corresponding G-Code command, refer to interacting with klipper.md
#   last_updated_time displays the time, when the status message content was last updated when using status_single_message.
#   Allowed values: progress, height, filament_length, filament_weight, print_duration, eta, finish_time, m117_status, tgnotify_status, last_update_time
#sensors: mcu, ..., ...
#   You can add temperature sensors, like the "mcu" sensor to be displayed in the status message.
#   Enter the names from your klipper config, separated by commas.
#   Default is not to display any additional temperature sensors.
#   This value is not validated automatically.
#heaters: extruder, heater_bed
#   You can add heaters, like the extruder, or the bed to be displayed in the status message.
#   Enter the names from your klipper config, separated by commas.
#   Default is not to display any additional heaters.
#   This value is not validated automatically.
#fans: your_fan, your_fan2
#   You can add any fans you have in the clipper config to be displayed in the status message.
#   Enter the names from your klipper config, separated by commas.
#   Default is not to display any additional fans.
#   This value is not validated automatically.
#moonraker_devices: light, psu
#   You can add moonraker power devices to be displayed on the status message.
#   Enter the names from your moonraker config, separated by commas.
#   Default is not to display any additional devices.
#   This value is not validated automatically.
```
