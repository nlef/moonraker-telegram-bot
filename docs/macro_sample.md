This document is a reference on how to write useful macros to use with the bot to get the most out of available functionality. 

The macros in this document are formatted so that it is possible to cut-and-paste them into your config.

# Advanced bot-oriented macro usage

## Highlighting

Let's imagine, that you regularly change filament. 
You propably have macros to insert and extract filament.

To insert filament, you usually preheat your extruder to some temperature, which is hot enough to push filament through, load the filament, and then extrude some amoount of plastic. 

We can somewhat improve the workflow and create a shortcut for the second part.

Here are the macros we are going to use:

```
[gcode_macro FILAMENT_INSERT_PREHEAT]
gcode:
	M109 S250
	RESPOND PREFIX=tgalarm MSG="Preheated, insert filament, run "
	G4 P1000
	RESPOND PREFIX=tgnotify MSG="/FILAMENT_INSERT"
	
[gcode_macro FILAMENT_INSERT]
gcode:
	M109 S250
	M83
	G1 E100 F250
	M104 S0
```

First, we run the FILAMENT_INSERT_PREHEAT macro, does not matter which way, from your webinterface, from your macro button, type it out in console, whatever floats your boat.

What happens next is very simple - after the extruder has reached the desired temperature, the bot sends two messages to the chat, one with a notification, the other without. It looks like this in the chat:

![image](https://user-images.githubusercontent.com/51682059/140410273-33ae0cac-e805-4ff9-98f7-2fe0b4db3a66.png)

Telegram automatically highlights things it considers commands for a bot, if the message starts with "/" and does not have spaces. This means, that sending `/FILAMENT_INSERT` produces a clickable shortcut in the chat, which only requires clicking on it, to send the command. 

This means, that as soon as we have received the message and inserted the filament, we can then press the "/FILAMENT_INSERT" in the chat, to run the macro with that name, which in turn extrudes the desired amount of plastic and powers down the extruder.

![image](https://user-images.githubusercontent.com/51682059/140410315-9a85f862-99c9-496f-b624-72221625077f.png)

This method works for any macro/multiple macros you wish to run. 
