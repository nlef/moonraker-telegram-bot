# Please open PR only in `development` branch

# Setup environment
## Activate virtualenv
This is the default location.
```shell
source ~/moonraker-telegram-bot-env/bin/activate
```
## Install dependencies
```shell
pip install -r scripts/requirements.dev.txt
```
## Install git hook
We use [prek](https://prek.j178.dev/) for running linters and formatters. [pre-commit](https://pre-commit.com/) can also be used as a drop-in alternative.
```shell
prek install
```

You can also run linters and tests manually:
```shell
prek run --all-files --show-diff-on-failure --color=always
pytest -v
```

## Run bot locally

Create a dev config file `telegram_dev.conf`, for example in the bot folder:
```shell
cd ~/moonraker-telegram-bot
~/moonraker-telegram-bot-env/bin/python3 ~/moonraker-telegram-bot/bot/main.py -c ~/moonraker-telegram-bot/telegram_dev.conf
```

## Test changes using Docker

Docker Buildx or Docker Desktop is required. A new container with the bot will be created and started on arm64.
```shell
docker compose -f .\docker-compose-dev.yml up --build -d
```
You must create a bot config file under `./docker_data/config` or copy the previously created `telegram_dev.conf` to the container config folder and rename it to `telegram.conf`.

The dev container contains preinstalled `memray` and `memory-profiler` for memory profiling.

## Test building Docker images

Buildx is required.
```shell
docker buildx build --platform linux/arm64 -t test -f Dockerfile-mjpeg .
docker buildx build --platform linux/arm64 -t test -f Dockerfile .
```
