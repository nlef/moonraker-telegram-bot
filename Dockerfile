FROM python:3.9-bullseye  as build

RUN apt update \
 && apt install -y python3-virtualenv

WORKDIR /opt

COPY . moonraker-telegram-bot/

RUN virtualenv -p /usr/bin/python3 --system-site-packages venv \
 && venv/bin/pip install --no-cache-dir -r moonraker-telegram-bot/scripts/requirements.txt

FROM python:3.9-slim-bullseye as run

RUN apt update \
 && apt install -y \
      python3-virtualenv \
      python3-cryptography \
      python3-gevent \
      python3-opencv \
      x264 \
      libx264-dev \
      libwebp-dev \
      && apt clean \
      && rm -rf /var/lib/apt/lists/*

WORKDIR /opt

RUN groupadd moonraker-telegram-bot --gid 1000 \
 && useradd moonraker-telegram-bot --uid 1000 --gid moonraker-telegram-bot

COPY --chown=moonraker-telegram-bot:moonraker-telegram-bot --from=build /opt/moonraker-telegram-bot ./moonraker-telegram-bot
COPY --chown=moonraker-telegram-bot:moonraker-telegram-bot --from=build /opt/venv ./venv

RUN mkdir -p printer_data/logs printer_data/config timelapse timelapse_finished \
 && chown -R moonraker-telegram-bot:moonraker-telegram-bot /opt/*

USER moonraker-telegram-bot

VOLUME [ "/opt/printer_data/logs", "/opt/printer_data/config", "/opt/timelapse","/opt/timelapse_finished"]
ENTRYPOINT ["/opt/venv/bin/python3", "moonraker-telegram-bot/bot/main.py"]
CMD ["-c", "/opt/printer_data/config/telegram.conf", "-l", "/opt/printer_data/logs"]
