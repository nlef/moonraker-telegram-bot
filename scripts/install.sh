#!/bin/bash
# This script installs Moonraker telegram bot
set -eu

SYSTEMDDIR="/etc/systemd/system"
MOONRAKER_BOT_ENV="${HOME}/moonraker-telegram-bot-env"
MOONRAKER_BOT_DIR="${HOME}/moonraker-telegram-bot"
MOONRAKER_BOT_LOG="${HOME}/klipper_logs"
KLIPPER_CONF_DIR="${HOME}/klipper_config"
CURRENT_USER=${USER}

# Helper functions
report_status() {
  echo -e "\n\n###### $1"
}

# Main functions
init_config_path() {
  if [ -z ${klipper_cfg_loc+x} ]; then
    report_status "Selecting config path"
    echo -e "\n\n\n"
    read -p "Enter your klipper configs path: " -e -i "${KLIPPER_CONF_DIR}" klip_conf_dir
    KLIPPER_CONF_DIR=${klip_conf_dir}
  else
    KLIPPER_CONF_DIR=${klipper_cfg_loc}
  fi
  report_status "Using configs from ${KLIPPER_CONF_DIR}"
}

create_initial_config() {
  # check in config exists!
  if [[ ! -f "${KLIPPER_CONF_DIR}"/telegram.conf ]]; then
    report_status "Selecting log path"
    echo -e "\n\n\n"
    read -p "Enter your bot log file: " -e -i "${MOONRAKER_BOT_LOG}" bot_log_path
    MOONRAKER_BOT_LOG=${bot_log_path}
    report_status "Writing bot logs to ${MOONRAKER_BOT_LOG}"

    report_status "Creating base config file"
    cp -n "${MOONRAKER_BOT_DIR}"/scripts/base_install_template "${KLIPPER_CONF_DIR}"/telegram.conf

    sed -i "s+some_log_path+${MOONRAKER_BOT_LOG}+g" "${KLIPPER_CONF_DIR}"/telegram.conf
  fi
}

stop_sevice() {
  serviceName="moonraker-telegram-bot"
  if sudo systemctl --all --type service --no-legend | grep "$serviceName" | grep -q running; then
    ## stop existing instance
    report_status "Stopping moonraker-telegram-bot instance ..."
    sudo systemctl stop moonraker-telegram-bot
  else
    report_status "$serviceName service does not exist or not running."
  fi
}

install_packages() {
  PKGLIST="python3-virtualenv python3-dev python3-cryptography python3-gevent python3-opencv x264 libx264-dev libwebp-dev"

  report_status "Running apt-get update..."
  sudo apt-get update --allow-releaseinfo-change

  report_status "Installing packages..."
  sudo apt-get install --yes ${PKGLIST}
}

create_virtualenv() {
  report_status "Installing python virtual environment..."

  mkdir -p "${HOME}"/space
  virtualenv -p /usr/bin/python3 --system-site-packages "${MOONRAKER_BOT_ENV}"
  export TMPDIR=${HOME}/space
  "${MOONRAKER_BOT_ENV}"/bin/pip install -r "${MOONRAKER_BOT_DIR}"/scripts/requirements.txt
}

create_service() {
  ### create systemd service file
  sudo /bin/sh -c "cat > ${SYSTEMDDIR}/moonraker-telegram-bot.service" <<EOF
#Systemd service file for Moonraker Telegram Bot
[Unit]
Description=Starts Moonraker Telegram Bot on startup
After=network-online.target moonraker.service

[Install]
WantedBy=multi-user.target

[Service]
Type=simple
User=${CURRENT_USER}
ExecStart=${MOONRAKER_BOT_ENV}/bin/python ${MOONRAKER_BOT_DIR}/bot/main.py -c ${KLIPPER_CONF_DIR}/telegram.conf
Restart=always
RestartSec=5
EOF

  ### enable instance
  sudo systemctl enable moonraker-telegram-bot.service
  report_status "Single moonraker-telegram-bot instance created!"

  ### launching instance
  report_status "Launching moonraker-telegram-bot instance ..."
  sudo systemctl start moonraker-telegram-bot
}

init_config_path
create_initial_config
stop_sevice
install_packages
create_virtualenv
create_service
