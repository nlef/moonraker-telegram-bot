SYSTEMDDIR="/etc/systemd/system"
MOONRAKER_ENV="${HOME}/moonraker-telegram-bot/venv"
MOONRAKER_DIR="${HOME}/moonraker-telegram-bot"

# check versions from repos https://packages.debian.org/search?arch=armhf&searchon=sourcenames&keywords=pillow
sudo apt install -y python3-cryptography python3-pil python3-opencv python3-gevent

mkdir -p ${HOME}/space
virtualenv -p /usr/bin/python3 --system-site-packages ${MOONRAKER_ENV}
export TMPDIR=${HOME}/space
${MOONRAKER_ENV}/bin/pip install -r ${MOONRAKER_DIR}/requirements.txt

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
User=klipper
RemainAfterExit=yes
ExecStart=${MOONRAKER_ENV}/bin/python ${MOONRAKER_DIR}/main.py -c ${MOONRAKER_DIR}/application.conf
Restart=always
RestartSec=10
EOF

### enable instance
sudo systemctl enable moonraker-telegram-bot.service
echo "Single moonraker-telegram-bot instance created!"

### launching instance
echo "Launching moonraker-telegram-bot instance ..."
sudo systemctl start moonraker-telegram-bot