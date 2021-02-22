SYSTEMDDIR="/etc/systemd/system"
MOONRAKER_BOT_ENV="${HOME}/moonraker-telegram-bot/venv"
MOONRAKER_BOT_DIR="${HOME}/moonraker-telegram-bot"
CURRENT_USER=${USER}

### stop existing instance
echo "Stopping moonraker-telegram-bot instance ..."
sudo systemctl stop moonraker-telegram-bot

# check versions from repos https://packages.debian.org/search?arch=armhf&searchon=sourcenames&keywords=pillow
sudo apt install -y python3-cryptography python3-pil python3-opencv python3-gevent

mkdir -p ${HOME}/space
virtualenv -p /usr/bin/python3 --system-site-packages ${MOONRAKER_BOT_ENV}
export TMPDIR=${HOME}/space
${MOONRAKER_BOT_ENV}/bin/pip install -r ${MOONRAKER_BOT_DIR}/requirements.txt

# create symlink on configfile
ln -s ${MOONRAKER_BOT_DIR}/application.conf ~/klipper_config/application.conf

### create systemd service file
sudo /bin/sh -c "cat > ${SYSTEMDDIR}/moonraker-telegram-bot.service" << EOF
#Systemd service file for Moonraker Telegram Bot
[Unit]
Description=Starts Moonraker Telegram Bot on startup
After=network.target

[Install]
WantedBy=multi-user.target

[Service]
Type=simple
User=${CURRENT_USER}
RemainAfterExit=yes
ExecStart=${MOONRAKER_BOT_ENV}/bin/python ${MOONRAKER_BOT_DIR}/main.py -c ${MOONRAKER_BOT_DIR}/application.conf
Restart=always
RestartSec=10
EOF

### enable instance
sudo systemctl enable moonraker-telegram-bot.service
echo "Single moonraker-telegram-bot instance created!"

### launching instance
echo "Launching moonraker-telegram-bot instance ..."
sudo systemctl start moonraker-telegram-bot