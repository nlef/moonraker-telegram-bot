#!/bin/bash
# This script installs Moonraker telegram bot
set -eu

SYSTEMDDIR="/etc/systemd/system"
MOONRAKER_BOT_ENV="${HOME}/moonraker-telegram-bot-env"

remove_all(){
  echo -e "Stopping services"

  services_list=($(sudo systemctl list-units -t service --full | grep moonraker-telegram-bot | awk '{print $1}'))
  echo -e "${services_list[@]}"
  for service in "${services_list[@]}"
  do
    echo -e "${service}"
    echo -e "Removing $service ..."
    sudo systemctl stop $service
    sudo systemctl disable $service
    sudo rm -f $SYSTEMDDIR/$service
    echo -e "Done!"
  done

  rm -rf "${HOME}/klipper_logs/telegram*"


  sudo systemctl daemon-reload
  sudo systemctl reset-failed

  ### remove MoonrakerTelegramBot VENV dir
  if [ -d $MOONRAKER_BOT_ENV ]; then
    echo -e "Removing MoonrakerTelegramBot VENV directory ..."
    rm -rf "${MOONRAKER_BOT_ENV}" && echo -e "Directory removed!"
  fi

}

remove_all
