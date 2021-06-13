# THIS EXAMPLE IS OUTDATED; WHILE THE LOGIC IS VALID, SUCH COMPLEX MACROS ARE NOT NEEDED ANYMORE.




This document provides information on implementing klipper G-Code Macros for usage with the moonraker-telegram-bot

# Your new bot and G-Codes




## General limitations

The most important limitation when using the bot is the fact, that the light device used for video and photo capturing is configured in moonraker. Right now (06-2021) it is not possible to get the status of a moonraker power device via a gcode macro/command, so if you intend to do anything with the light device while printing is in progress, the bot might change the status without you (klipper macros) being aware of it.

There is some trickery involved, and hopefully there will be more elegant ways in the future to interact with moonraker power devices.


## Making klipper aware of a moonraker power device status

This is a somewhat generalized example, on how to make a macro, which will toggle the lights every time that it is run. 
Moonraker power devices have a consistent state between restarts, configured by `initial_state: off`

Assuming that we only restart moonraker together with klipper, and that we can not detect restarts not done simultaniously, which is set to the same status, as the `initial_state` described before. In that case, when the printer restarts, both the device in the moonraker, as well as the variable are reliably reset to the same state.

```
[gcode_macro CHAMBER_LED_TOGGLE]
variable_chamber_led_status: 0
gcode:
	{% if printer["gcode_macro CHAMBER_LED_TOGGLE"].chamber_led_status|int == 0 %}
		SET_GCODE_VARIABLE MACRO=CHAMBER_LED_TOGGLE VARIABLE=chamber_led_status VALUE=1
		CHAMBER_LED_ON
	{% else %}
		SET_GCODE_VARIABLE MACRO=CHAMBER_LED_TOGGLE VARIABLE=chamber_led_status VALUE=0
		CHAMBER_LED_OFF
	{% endif %}
```

In the above example we check if the lights status is on or off, and change it, writing the state to the variable. That means, that we retain the functionality of being able to check "are the lights on?" and making decisions based on thet. 

This whole contraption might seem useless at first, but the usage scenario will be clearer later.


## Taking timelapse pictures with gcodes

The built-in timelapse based on layer height as of 06-2021 is limited in its ability to toggle the light on layer change, so in the case of using a dark, unlit chamber it is unfit to be used for timelapsing. An alternative is the 'RESPOND PREFIX=timelapse MSG=photo' command triggered in the layer change process.

The simplest way to use this command is to simply insert it in your "on layer change" G-Code in your preferred slicer. Again, if you need lights to be toggled, this will not be enough.
The simplest way to enable the light, take the picture, and disable the light would be defining 3 separate macros:

```
[gcode_macro CHAMBER_LED_ON]
gcode:
	{action_call_remote_method("set_device_power",
                             device="chamber_led",
                             state="on")}
```

```
[gcode_macro CHAMBER_LED_OFF]
gcode:
	{action_call_remote_method("set_device_power",
                             device="chamber_led",
                             state="off")}
```

```
[gcode_macro TIMELAPSE_PIC]
gcode:
	RESPOND PREFIX=timelapse MSG=photo
```


And pasting them into the layer change G-Code in the corresponding order:

```
CHAMBER_LED_ON
TIMELAPSE_PIC
CHAMBER_LED_OFF
```


This approach however will also almost certainly fail. Your camera needs time to adjust to the light change, which for the most cameras will be about 2 seconds. That means, that we have to insert a delay, before we take a picture. A`G4 P2000` code might seem intuitive, but will certainly be hazardous to the model being printed - the printer will simply freeze in place, and melt a hole where it will be idling, so that is out of the question.

One might of course move the head to the side, to make a cleaner picture, but most of the time, the delay assotiated with such a movement, as well as time loss will be a dealbreaker for a normal print. How do we take the picture then, without moving the head away, and idling for 2 precious seconds every layer? Delayed gcode, of course!


We adjust our three macros accordingly:



```
[gcode_macro CHAMBER_LED_ON]
gcode:
	{action_call_remote_method("set_device_power",
                             device="chamber_led",
                             state="on")}
	UPDATE_DELAYED_GCODE ID=TIMELAPSE_PIC DURATION=2
```

```
[gcode_macro CHAMBER_LED_OFF]
gcode:
	{action_call_remote_method("set_device_power",
                             device="chamber_led",
                             state="off")}
```

```
[delayed_gcode TIMELAPSE_PIC]
gcode:
	RESPOND PREFIX=timelapse MSG=photo
	CHAMBER_LED_OFF
```


And only run the `CHAMBER_LED_ON` in our slicer layer change G-Code.

If you have never worked with delayed_gcode macros before, what happens is, that the CHAMBER_LED_ON turns on the light, and puts "TIMELAPSE_PIC" in queue to be run in 2 seconds. Meanwhile, the printer proceeds with the print, and when 2 seconds pass, it takes a picture, and disables the light afterwards.




This might seem to be the final and ultimate solution to your timelapse-light-pictures problem, but it is not! Consider a scenario, where you have an object with very quick layer times, lets say a small vertical cylinder. What will happen is, that your CHAMBER_LED_ON will spazz out, and get called to often, and that your relay for enabling the lights will sound like a signal light in a broken down lada from 1960. 

And now we come back to the original "chamber_led_status" variable we had discussed in the beginning, finally!

Let's say we know, that we will be printing a small model, with very short layer times. The lights will propably have to cycle so fast, that it does not make any sense, to disable them at all. Let's keep it primitive, and say that we will simply turn on the lights by hand, before the print, however you find it suitable, the only important thing being, that 'chamber_led_status=1'.

We add another helper macro, which will check, if the lights need to be toggled, before we toggle the picture taking, which we call from our slicers layer change G-Code.

```
[gcode_macro LAYER_CHANGE_TIMELAPSE]
gcode:
	{% if printer["gcode_macro CHAMBER_LED_TOGGLE"].chamber_led_status|int == 0 %}
		CHAMBER_LED_ON
	{% endif %}
	UPDATE_DELAYED_GCODE ID=TIMELAPSE_PIC DURATION=2
```

We simply check, if we have enabled the light by hand before we started the print, and if we did, we just take the picture. If we did not, we turn them on, but do not change the status of the variable, since we will turn the off straight after the picture, and for decision making based on the lights status the state should remain as "off".

```
[delayed_gcode TIMELAPSE_PIC]
gcode:
	RESPOND PREFIX=timelapse MSG=photo
	{% if printer["gcode_macro CHAMBER_LED_TOGGLE"].chamber_led_status|int == 0 %}
		UPDATE_DELAYED_GCODE ID=CHAMBER_LED_OFF_DELAYED DURATION=1
	{% endif %}
```

This macro does the same - after taking the picture, it checks, if it should turn off the lights. If the lights were set by the user before the print, we leave them on, if they were not set- we turn them off, since we only needed them for the picture. For good measure, since taking a picture might take some miniscule amount of time, we delay the turning off of the light by the smallest possible delay, 1 second.


```
[delayed_gcode CHAMBER_LED_OFF_DELAYED]
gcode:
	SET_GCODE_VARIABLE MACRO=CHAMBER_LED_TOGGLE VARIABLE=chamber_led_status VALUE=0
	{action_call_remote_method("set_device_power",
                             device="chamber_led",
                             state="off")}
```



Of course, these macros can and should be improved and adjusted to your usecase. For example, it hardly makes sense to delay the picture, if the lights do not need to be toggled.
Or, the variable for toggling lights might be set based on the print_time from your slicer. As always with macros, only your creativity is the limit, and don't forget kids - __sharing is caring!__