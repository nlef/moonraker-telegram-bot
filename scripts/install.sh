#!/bin/bash
# This script installs Moonraker telegram bot
set -eu

SYSTEMDDIR="/etc/systemd/system"
MOONRAKER_BOT_SERVICE="moonraker-telegram-bot.service"
MOONRAKER_BOT_ENV="${HOME}/moonraker-telegram-bot-env"
MOONRAKER_BOT_DIR="${HOME}/moonraker-telegram-bot"
MOONRAKER_BOT_LOG="${HOME}/printer_data/logs/telegram.log"
MOONRAKER_BOT_CONF="${HOME}/printer_data/config"
KLIPPER_CONF_DIR="${HOME}/printer_data/config"
KLIPPER_LOGS_DIR="${HOME}/printer_data/logs"
CURRENT_USER=${USER}


### set color variables
green=$(echo -en "\e[92m")
yellow=$(echo -en "\e[93m")
red=$(echo -en "\e[91m")
cyan=$(echo -en "\e[96m")
default=$(echo -en "\e[39m")

# Helper functions
report_status() {
  echo -e "\n\n###### $1"
}
warn_msg(){
  echo -e "${red}<!!!!> $1${default}"
}
status_msg(){
  echo; echo -e "${yellow}###### $1${default}"
}
ok_msg(){
  echo -e "${green}>>>>>> $1${default}"
}

# Main functions
init_config_path() {
  report_status "Telegram bot configuration file location selection"
  echo -e "\n"
  echo "Enter the path for the configuration files location. Subfolders for multiple instances wil be created under this path."
  echo "Its recommended to store it together with the klipper configuration for easier backup and usage."
  read -p "Enter desired path: " -e -i "${KLIPPER_CONF_DIR}" klip_conf_dir
  KLIPPER_CONF_DIR=${klip_conf_dir}

  if ! [ -z ${LPATH+x} ]; then
    KLIPPER_LOGS_DIR=${LPATH}
  fi

  report_status "Bot configuration file will be located in ${KLIPPER_CONF_DIR}"
}

create_initial_config() {
  if [[ $INSTANCE_COUNT -eq 1 ]]; then
    MOONRAKER_BOT_CONF=${KLIPPER_CONF_DIR}
    # check in config exists!
    if [[ ! -f "${MOONRAKER_BOT_CONF}"/telegram.conf ]]; then
      report_status "Creating base config file"
      cp -n "${MOONRAKER_BOT_DIR}"/scripts/base_install_template "${MOONRAKER_BOT_CONF}"/telegram.conf

    fi

    create_service
    ok_msg "Single Telegram bot instance created!"

  else
    manual_paths=""
    while [[ ! ($manual_paths =~ ^(?i)(y|n|no|yes)(?-i)$) ]]; do
      read -p "Use automatic paths? (Y/n): " -e -i y manual_paths
      case "${manual_paths}" in
        Y|y|Yes|yes)
          echo -e "###### > Yes"
          manual_paths="y"
          break;;
        N|n|No|no)
          echo -e "###### > No"
          manual_paths="n"
          break;;
        *)
          warn_msg "Invalid command!";;
      esac
    done
    i=1
    while [[ $i -le $INSTANCE_COUNT ]]; do
      ### rewrite default variables for multi instance cases
      if [ "${manual_paths}" == "n" ]; then
        report_status "Telegram bot instance name selection for instance ${i}"
        read -p "Enter bot instance name: " -e -i "printer_${i}" instance_name
        MOONRAKER_BOT_SERVICE="moonraker-telegram-bot-${instance_name}.service"
        MOONRAKER_BOT_CONF="${KLIPPER_CONF_DIR}/${instance_name}"
        MOONRAKER_BOT_LOG="${KLIPPER_LOGS_DIR}/telegram-${instance_name}.log"
      else
        MOONRAKER_BOT_SERVICE="moonraker-telegram-bot-$i.service"
        MOONRAKER_BOT_CONF="${KLIPPER_CONF_DIR}/printer_$i"
        MOONRAKER_BOT_LOG="${KLIPPER_LOGS_DIR}/telegram-$i.log"
      fi

      report_status "Creating base config file"
      mkdir -p "${MOONRAKER_BOT_CONF}"
      cp -n "${MOONRAKER_BOT_DIR}"/scripts/base_install_template "${MOONRAKER_BOT_CONF}"/telegram.conf
      mkdir -p "${KLIPPER_LOGS_DIR}"
      create_service
      ### raise values by 1
      i=$((i+1))
    done
    unset i
  fi
}

#Todo: stop multiple?
stop_sevice() {
  serviceName="moonraker-telegram-bot"
  if sudo systemctl --all --type service --no-legend | grep "$serviceName" | grep -q running; then
    ## stop existing instance
    report_status "Stopping moonraker-telegram-bot instance ..."
    sudo systemctl stop moonraker-telegram-bot
  else
    report_status "$serviceName service does not exist or is not running."
  fi
}

install_packages() {
  PKGLIST="python3-virtualenv python3-dev python3-cryptography python3-gevent python3-opencv x264 libx264-dev libwebp-dev"

  report_status "Running apt-get update..."
  sudo apt-get update --allow-releaseinfo-change
  for pkg in $PKGLIST; do
    echo "${cyan}$pkg${default}"
  done
  report_status "Installing packages..."
  sudo apt-get install --yes ${PKGLIST}
}

create_virtualenv() {
  report_status "Installing python virtual environment..."

  ### If venv exists and user prompts a rebuild, then do so
  if [ -d "$MOONRAKER_BOT_ENV" ]; then
    status_msg "Moonraker telegram bot python virtualenv already exists."
    REBUILD_VENV=""
    while [[ ! ($REBUILD_VENV =~ ^(?i)(y|n|no|yes)(?-i)$) ]]; do
      read -p "Rebuild python virtualenv? (Y/n): " -e -i "y" REBUILD_VENV
      case "${REBUILD_VENV}" in
        Y|y|Yes|yes)
          echo -e "###### > Yes"
          echo "Removing old virtualenv"
          rm -rf "$MOONRAKER_BOT_ENV"
          break;;
        N|n|No|no)
          echo -e "###### > No"
          break;;
        *)
          warn_msg "Invalid command!";;
      esac
    done
  fi

  mkdir -p "${HOME}"/space
  virtualenv -p /usr/bin/python3 --system-site-packages "${MOONRAKER_BOT_ENV}"
  export TMPDIR=${HOME}/space
  "${MOONRAKER_BOT_ENV}"/bin/pip install --no-cache-dir -r "${MOONRAKER_BOT_DIR}"/scripts/requirements.txt
}

create_service() {
  ### create systemd service file
  sudo /bin/sh -c "cat > ${SYSTEMDDIR}/${MOONRAKER_BOT_SERVICE}" <<EOF
#Systemd service file for Moonraker Telegram Bot
[Unit]
Description=Starts Moonraker Telegram Bot on startup
After=network-online.target moonraker.service

[Install]
WantedBy=multi-user.target

[Service]
Type=simple
User=${CURRENT_USER}
ExecStart=${MOONRAKER_BOT_ENV}/bin/python ${MOONRAKER_BOT_DIR}/bot/main.py -c ${MOONRAKER_BOT_CONF}/telegram.conf -l ${MOONRAKER_BOT_LOG}
Restart=always
RestartSec=5
EOF

  ### enable instance
  sudo systemctl enable ${MOONRAKER_BOT_SERVICE}
  report_status "${MOONRAKER_BOT_SERVICE} instance created!"

  ### launching instance
  report_status "Launching moonraker-telegram-bot instance ..."
  sudo systemctl start ${MOONRAKER_BOT_SERVICE}
}


install_instances(){
  INSTANCE_COUNT=$1

  sudo systemctl stop moonraker-telegram-bot*
  status_msg "Installing dependencies"
  install_packages
  create_virtualenv

  init_config_path
  create_initial_config

}

setup_dialog(){
    ### count amount of mooonraker services
    SERVICE_FILES=$(find "$SYSTEMDDIR" -regextype posix-extended -regex "$SYSTEMDDIR/moonraker(-[^0])+[0-9]*.service")
    if [ -f /etc/init.d/moonraker ] || [ -f /etc/systemd/system/moonraker.service ]; then
      MOONRAKER_COUNT=1
    elif [ -n "$SERVICE_FILES" ]; then
      MOONRAKER_COUNT=$(echo "$SERVICE_FILES" | wc -l)
    else
      MOONRAKER_COUNT=0
    fi

    echo -e "/=======================================================\\"
    if [[ $MOONRAKER_COUNT -eq 0 ]]; then
      printf "|${yellow}%-55s${default}|\n" " No Mooonraker instance was found!"
    elif [[ $MOONRAKER_COUNT -eq 1 ]]; then
      printf "|${green}%-55s${default}|\n" " 1 Mooonraker instance was found!"
    elif [[ $MOONRAKER_COUNT -gt 1 ]]; then
      printf "|${green}%-55s${default}|\n" "${MOONRAKER_COUNT} Mooonraker instances were found!"
    else
      echo -e "| ${yellow}INFO: No existing Mooonraker installation found!${default}        |"
      init_config_path
    fi
    echo -e "| Usually you need one Moonraker telegram bot instance per Mooonraker   |"
    echo -e "| instance. Though you can install as many as you wish. |"
    echo -e "\=======================================================/"
    echo
    count=""
    while [[ ! ($count =~ ^[1-9]+((0)+)?$) ]]; do
      read -p "${cyan}###### Number of Moonraker telegram bot instances to set up:${default} " count
      if [[ ! ($count =~ ^[1-9]+((0)+)?$) ]]; then
        echo -e "Invalid Input!\n"
      else
        echo
        read -p "${cyan}###### Install $count instance(s)? (Y/n):${default} " yn
        case "$yn" in
          Y|y|Yes|yes|"")
            echo -e "###### > Yes"
            status_msg "Installing Moonraker telegram bot ...\n"
            install_instances "$count"
            break;;
          N|n|No|no)
            echo -e "###### > No"
            warn_msg "Exiting Moonraker telegram bot setup ...\n"
            break;;
          *)
            warn_msg "Invalid command!";;
        esac
      fi
    done
}


setup_dialog
