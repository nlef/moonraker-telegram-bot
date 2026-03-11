# Camera modes in 2.0

With 2.0 the camera handling has received a significant rework.
This document is a intended to help choose the right type of camera handling.
Principally the development will move towards mjpeg-only, since this is what the majority is using, and supporting multiple variants is quite tiresome.
For now, three types of cameras will exist:

## Type: mjpeg

This is the default, and most light-weight camera type.
Most of our users use one or the other form of mjpeg streaming, where essentially what is getting fed from the camera is a stream of jpegs.
The only real downside is low compression/very high bitrate needed to watch such a stream.
Since the bot presumably consumes the images locally on the same machine, it is also the recommended way to run the camera (for the bot).

If you have no idea what you are using, you are most likely using mjpeg, and the bot should also be configured with mjpeg.

## Type: opencv

The old ressource-intensive camera type.
If your camera is giving you back something other than a mjpeg, this is most likely the way to go.
Here, the opencv library is used to fetch the image. The image is then stored as a numpy andarray to reduce load on the host.
If you need real physical pictures saved to the disc for whatever reason, you can use the "save_lapse_photos_as_images flag.

## Type: ffmpeg

This camera type uses ffmpeg to fetch the pictures.
Compared to opencv, this type of installation is lighter, but it incurs an additional delay of multiple seconds on the stream, compared to what is happening in real time.
The main advantage is the fact that the relatively heavy opencv library is not used, and so it and all its dependencies don't have to be installed.

## Video assembly

For all camera types the video assembly is done via ffmpeg.
