#!/bin/bash
# This script installs Moonraker telegram bot
set -eu

SYSTEMDDIR="/etc/systemd/system"
MOONRAKER_BOT_SERVICE="moonraker-telegram-bot.service"
MOONRAKER_BOT_ENV="${HOME}/moonraker-telegram-bot-env"
MOONRAKER_BOT_DIR="${HOME}/moonraker-telegram-bot"
MOONRAKER_BOT_LOG="${HOME}/klipper_logs"
MOONRAKER_BOT_CONF="${HOME}/klipper_config"
KLIPPER_CONF_DIR="${HOME}/klipper_config"
CURRENT_USER=${USER}

# Helper functions
report_status() {
  echo -e "\n\n###### $1"
}

# Main functions
init_config_path() {
  if [ -z ${klipper_cfg_loc+x} ]; then
    report_status "Telegram bot configuration file location selection"
    echo -e "\n\n\n"
	  echo "Enter the path for the configuration files location. Subfolders for multiple instances wil be created under this path."
	  echo "Its recommended to store it together with the klipper configuration for easier backup and usage."
    read -p "Enter desired path: " -e -i "${KLIPPER_CONF_DIR}" klip_conf_dir
    KLIPPER_CONF_DIR=${klip_conf_dir}
  else
    KLIPPER_CONF_DIR=${klipper_cfg_loc}
  fi
  report_status "Bot configuration file will be located in ${KLIPPER_CONF_DIR}"
}

create_initial_config() {
  if [[ $INSTANCE_COUNT -eq 1 ]]; then
    MOONRAKER_BOT_CONF=${KLIPPER_CONF_DIR}
    # check in config exists!
    if [[ ! -f "${MOONRAKER_BOT_CONF}"/telegram.conf ]]; then
      report_status "Telegram bot log file location selection"
      echo -e "\n\n\n"
      echo "Enter the path for the log file location."
      echo "Its recommended to store it together with the klipper log files for easier backup and usage."
      read -p "Enter desired path: " -e -i "${MOONRAKER_BOT_LOG}" bot_log_path
      MOONRAKER_BOT_LOG=${bot_log_path}
      report_status "Bot logs will be located in ${MOONRAKER_BOT_LOG}"

      report_status "Creating base config file"
      cp -n "${MOONRAKER_BOT_DIR}"/scripts/base_install_template "${MOONRAKER_BOT_CONF}"/telegram.conf

      sed -i "s+some_log_path+${MOONRAKER_BOT_LOG}+g" "${MOONRAKER_BOT_CONF}"/telegram.conf
    fi

    create_service
    ok_msg "Single Moonraker instance created!"

  else
    read -p "Use automatic paths? (Y/n): " -e -i y manual_paths
    i=1
    while [[ $i -le $INSTANCE_COUNT ]]; do
      ### rewrite default variables for multi instance cases
      if [ "${manual_paths}" == "n" ]; then
        report_status "Telegram bot instance name selection for instance ${i}"
        read -p "Enter bot instance name: " -e -i "printer_${i}" instance_name
        MOONRAKER_BOT_SERVICE="moonraker-telegram-bot-${instance_name}.service"
        MOONRAKER_BOT_CONF="${KLIPPER_CONF_DIR}/${instance_name}"
        MOONRAKER_BOT_LOG_loc="${MOONRAKER_BOT_LOG}/telegram-logs-${instance_name}"
      else
        MOONRAKER_BOT_SERVICE="moonraker-telegram-bot-$i.service"
        MOONRAKER_BOT_CONF="${KLIPPER_CONF_DIR}/printer_$i"
        MOONRAKER_BOT_LOG_loc="${MOONRAKER_BOT_LOG}/telegram-logs-$i"
      fi

      report_status "Creating base config file"
      mkdir -p "${MOONRAKER_BOT_CONF}"
      cp -n "${MOONRAKER_BOT_DIR}"/scripts/base_install_template "${MOONRAKER_BOT_CONF}"/telegram.conf
      mkdir -p "${MOONRAKER_BOT_LOG_loc}"
      sed -i "s+some_log_path+${MOONRAKER_BOT_LOG_loc}+g" "${MOONRAKER_BOT_CONF}"/telegram.conf
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
    echo "$pkg"
  done
  report_status "Installing packages..."
  sudo apt-get install --yes ${PKGLIST}
}

create_virtualenv() {
  report_status "Installing python virtual environment..."

  ### If venv exists and user prompts a rebuild, then do so
  if [ -d "$MOONRAKER_BOT_ENV" ]; then
    echo "Moonraker telegram bot python virtualenv already exists."
    read -p "Rebuild python virtualenv? (Y/n): " -e -i "y" REBUILD_VENV
    if [ "${REBUILD_VENV}" == "y" ]; then
      echo "Removing old virtualenv"
      rm -rf "$MOONRAKER_BOT_ENV"
    fi
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
ExecStart=${MOONRAKER_BOT_ENV}/bin/python ${MOONRAKER_BOT_DIR}/bot/main.py -c ${MOONRAKER_BOT_CONF}/telegram.conf
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

  #stop_sevice
  sudo systemctl stop moonraker-telegram-bot*
  install_packages
  create_virtualenv

  init_config_path
  create_initial_config

}

setup_dialog(){
    ### checking system for python3.7+
#    system_check_moonraker_telegram_bot
#    ### exit moonraker setup if python versioncheck fails
#    if [ $py_chk_ok = "false" ]; then
#      ERROR_MSG="Versioncheck failed! Python 3.7 or newer required!\n"
#      ERROR_MSG="${ERROR_MSG} Please upgrade Python."
#      print_msg && clear_msg && return
#    fi

    ### count amount of mooonraker services
    SERVICE_FILES=$(find "$SYSTEMDDIR" -regextype posix-extended -regex "$SYSTEMDDIR/moonraker(-[^0])+[0-9]*.service")
    if [ -f /etc/init.d/moonraker ] || [ -f /etc/systemd/system/moonraker.service ]; then
      MOONRAKER_COUNT=1
    elif [ -n "$SERVICE_FILES" ]; then
      MOONRAKER_COUNT=$(echo "$SERVICE_FILES" | wc -l)
    fi

    echo -e "/=======================================================\\"
    if [[ $MOONRAKER_COUNT -eq 1 ]]; then
      printf " 1 Mooonraker instance was found!"
    elif [[ $MOONRAKER_COUNT -gt 1 ]]; then
      printf "${MOONRAKER_COUNT} Mooonraker instances were found!"
    else
      echo -e "| INFO: No existing Mooonraker installation found!        |"
      init_config_path
    fi
    echo -e "| Usually you need one Moonraker telegram bot instance per Mooonraker   |"
    echo -e "| instance. Though you can install as many as you wish. |"
    echo -e "\=======================================================/"
    echo
    count=""
    while [[ ! ($count =~ ^[1-9]+((0)+)?$) ]]; do
      read -p "###### Number of Moonraker telegram bot instances to set up: " count
      if [[ ! ($count =~ ^[1-9]+((0)+)?$) ]]; then
        echo -e "Invalid Input!\n"
      else
        echo
        read -p "###### Install $count instance(s)? (Y/n): " yn
        case "$yn" in
          Y|y|Yes|yes|"")
            echo -e "###### > Yes"
            echo -e "Installing Moonraker telegram bot ...\n"
            install_instances "$count"
            break;;
          N|n|No|no)
            echo -e "###### > No"
            echo -e "Exiting Moonraker telegram bot setup ...\n"
            break;;
          *)
            print_unkown_cmd
            print_msg && clear_msg;;
        esac
      fi
    done
}


setup_dialog
