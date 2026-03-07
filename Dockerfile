FROM python:3.13.11-slim-trixie

RUN apt update  \
 && export DEBIAN_FRONTEND=noninteractive \
 && apt install -y \
      ffmpeg x264 libx264-dev libuv1 \
      libwebp-dev libtiff5-dev libjpeg*-turbo libjpeg*-turbo-dev libopenjp2-7-dev  \
      zlib1g-dev libfreetype6-dev liblcms2-dev \
 && apt clean \
 && rm -rf /var/lib/apt/lists/*


WORKDIR /opt

RUN groupadd moonraker-telegram-bot --gid 1000 \
 && useradd moonraker-telegram-bot --uid 1000 --gid moonraker-telegram-bot \
 && mkdir -p printer_data/logs printer_data/config timelapse timelapse_finished \
 && chown -R moonraker-telegram-bot:moonraker-telegram-bot /opt/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY --chown=moonraker-telegram-bot:moonraker-telegram-bot . ./moonraker-telegram-bot
RUN cd moonraker-telegram-bot && uv sync --no-dev --group docker-opencv

USER moonraker-telegram-bot
VOLUME [ "/opt/printer_data/logs", "/opt/printer_data/config", "/opt/timelapse","/opt/timelapse_finished"]
ENTRYPOINT ["moonraker-telegram-bot/.venv/bin/python3", "moonraker-telegram-bot/bot/main.py"]
CMD ["-c", "/opt/printer_data/config/telegram.conf", "-l", "/opt/printer_data/logs"]
