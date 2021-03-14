SYSTEMDDIR="/etc/systemd/system"
MOONRAKER_BOT_ENV="${HOME}/moonraker-telegram-bot-env"
MOONRAKER_BOT_DIR="${HOME}/moonraker-telegram-bot"
KLIPPER_CONF_DIR="${HOME}/klipper_config"
CURRENT_USER=${USER}

## stop existing instance
echo "Stopping moonraker-telegram-bot instance ..."
sudo systemctl stop moonraker-telegram-bot

## check versions from repos https://packages.debian.org/search?arch=armhf&searchon=sourcenames&keywords=pillow
PKGLIST="python3-cryptography"
PKGLIST="${PKGLIST} python3-pil python3-opencv python3-gevent"
sudo apt-get update
sudo apt install -y ${PKGLIST}

mkdir -p ${HOME}/space
virtualenv -p /usr/bin/python3 --system-site-packages ${MOONRAKER_BOT_ENV}
export TMPDIR=${HOME}/space
${MOONRAKER_BOT_ENV}/bin/pip install -r ${MOONRAKER_BOT_DIR}/requirements.txt

echo -e "\n\n\n"
read -p "Enter your klipper configs path: " -e -i "${KLIPPER_CONF_DIR}" klip_conf_dir
KLIPPER_CONF_DIR=${klip_conf_dir}
echo -e "\nUsing configs from ${KLIPPER_CONF_DIR}\n"

# check in config exists!
# copy configfile if not exists
cp -n ${MOONRAKER_BOT_DIR}/application.conf ${KLIPPER_CONF_DIR}/application.conf

### create systemd service file
sudo /bin/sh -c "cat > ${SYSTEMDDIR}/moonraker-telegram-bot.service" <<EOF
#Systemd service file for Moonraker Telegram Bot
[Unit]
Description=Starts Moonraker Telegram Bot on startup
After=network.target

[Install]
WantedBy=multi-user.target

[Service]
Type=simple
User=${CURRENT_USER}
ExecStart=${MOONRAKER_BOT_ENV}/bin/python ${MOONRAKER_BOT_DIR}/main.py -c ${KLIPPER_CONF_DIR}/application.conf
Restart=always
RestartSec=5
EOF

### enable instance
sudo systemctl enable moonraker-telegram-bot.service
echo "Single moonraker-telegram-bot instance created!"

### launching instance
echo "Launching moonraker-telegram-bot instance ..."
sudo systemctl start moonraker-telegram-bot
