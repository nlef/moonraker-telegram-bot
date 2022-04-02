#!/bin/bash
# This script installs Moonraker telegram bot
set -eu

SYSTEMDDIR="/etc/systemd/system"
MOONRAKER_BOT_ENV="${HOME}/moonraker-telegram-bot-env"

remove_all(){
  echo -e "Stopping services"
  sudo systemctl stop moonraker-telegram-bot*
  echo -e "Removing service files"
  sudo rm -f "${SYSTEMDDIR}/moonraker-telegram-bot*"

  ### remove MoonrakerTelegramBot VENV dir
  if [ -d $MOONRAKER_BOT_ENV ]; then
    echo -e "Removing MoonrakerTelegramBot VENV directory ..."
    rm -rf "${MOONRAKER_BOT_ENV}" && echo -e "Directory removed!"
  fi

}

remove_all
