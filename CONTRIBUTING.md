# Setup environment
## Active virtualenv
This is the default location.
```shell
source ~/moonraker-telegram-bot-env/bin/activate
```
## Install dependencies
```shell
pip install -r scripts/requirements.dev.txt
```
## Install pre-commit hook
```shell
pre-commit install
```

You can also run pre-commit manually on all files:
```shell
pre-commit run --all-files
```
