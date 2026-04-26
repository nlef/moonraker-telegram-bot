# AGENTS.md — moonraker-telegram-bot

## Project Overview

Python Telegram bot for Klipper/Moonraker 3D printer control. Async architecture using
`python-telegram-bot`, `httpx`, `websockets`, and `APScheduler`. Source code lives in `bot/`,
tests in `tests/`. Targets Python 3.9–3.12.

## Build & Environment Setup

```bash
# Create and activate virtualenv
python -m venv ~/moonraker-telegram-bot-env
source ~/moonraker-telegram-bot-env/bin/activate

# Install all dependencies (runtime + dev)
pip install -r scripts/requirements.dev.txt

# Install pre-commit hooks
pre-commit install
```

No build step — pure Python. The bot entrypoint is `bot/main.py`.

## Lint & Format Commands

Pre-commit runs all checks. CI runs these on Python 3.9–3.12:

```bash
# Run all linters + formatters at once (same as CI)
pre-commit run --all-files

# Individual tools
ruff check bot/ tests/             # Lint (E, F, W, I rules)
ruff format bot/ tests/            # Auto-format
mypy bot/                          # Type checking
```

### Linter Configuration (pyproject.toml)

- **Line length**: 200 characters (ruff formatter and mypy use this)
- **ruff linting**: Enabled rules: E (pycodestyle), F (Pyflakes), W (warnings), I (isort)
  - Disabled: E501 (line length, handled by formatter), E722 (bare except — existing pattern)
- **ruff import sorting**: Uses isort-compatible settings with `force-sort-within-sections = true`
  and `combine-as-imports = true`. First-party modules: `camera`, `configuration`, `klippy`,
  `notifications`, `timelapse`, `websocket_helper`, `telegram_helper`.
- **mypy**: Used via pre-commit; untyped third-party libs use `# type: ignore` inline comments.

## Testing

Tests use **pytest** (v7.3.1). Test files follow the `*_test.py` naming pattern.

```bash
# Run all tests
pytest -v

# Run a single test file
pytest -v tests/configuration_test.py

# Run a single test function
pytest -v tests/configuration_test.py::test_config_has_no_errors

# Run tests matching a keyword
pytest -v -k "secrets"
```

### Test Conventions

- Test files live in `tests/` and are named `<module>_test.py` (e.g., `configuration_test.py`).
- Test functions are named `test_<description>` using snake_case.
- Imports reference the `bot` package: `from bot.configuration import ConfigWrapper  # type: ignore`
  (the `# type: ignore` is needed because `bot/` is not a proper installable package).
- Fixtures use `@pytest.fixture` and are passed by name as function parameters.
- Test resources (config files etc.) go in `tests/resources/`.
- Tests are simple and assertion-based — no mocking frameworks currently in use.

## Code Style Guidelines

### Formatting

- **Max line length**: 200 characters.
- **Formatter**: ruff with default settings except line length.
- **Trailing whitespace**: Removed (enforced by pre-commit).
- **End of file**: Single newline (enforced by pre-commit).

### Imports

Imports are organized by ruff's isort-compatible import sorting:

1. Standard library imports (one per line, alphabetical)
2. Blank line
3. Third-party imports (alphabetical)
4. Blank line
5. Local/first-party imports from `bot/` modules

First-party modules: `camera`, `configuration`, `klippy`, `notifications`, `timelapse`,
`websocket_helper`.

```python
# Example import ordering
import asyncio
import logging
from typing import List, Optional

from apscheduler.schedulers.base import BaseScheduler  # type: ignore
import httpx
import orjson

from configuration import ConfigWrapper
from klippy import Klippy
```

- Use `combine_as_imports = true` — multiple names from one module on one line.
- Use `# type: ignore` for untyped third-party packages (apscheduler, ffmpegcv, cv2, etc.).

### Type Annotations

- Use `typing` module types: `List`, `Dict`, `Optional`, `Union`, `Tuple`, `Any`.
- Instance variables are annotated in `__init__`: `self._host: str = config.bot_config.host`.
- Function signatures have parameter and return type annotations where practical.
- Properties use `-> ReturnType` annotations.
- The codebase does NOT require 100% type coverage — pragmatic typing is fine.

### Naming Conventions

- **Classes**: PascalCase (`ConfigWrapper`, `WebSocketHelper`, `PowerDevice`).
- **Functions/methods**: snake_case (`get_status`, `parse_print_stats`).
- **Private members**: Leading underscore (`_host`, `_connected`, `_get_eta`).
- **Constants**: UPPER_SNAKE_CASE (`_DATA_MACRO`, `_SENSOR_PARAMS`, `_KNOWN_ITEMS`).
- **Module-level logger**: Always `logger = logging.getLogger(__name__)`.
- **Global variables** (in main.py): camelCase for legacy wrappers (`configWrap`, `cameraWrap`).

### Class Structure

- Config classes inherit from `ConfigHelper` base class with section-based config parsing.
- Classes use `@property` for getters and explicit setter methods for async state changes.
- Thread safety via `threading.Lock()` and `asyncio.Lock()` where needed.
- HTTP clients: `httpx.AsyncClient` for async, `httpx.Client` for sync operations.
- JSON: Use `orjson` everywhere (not stdlib `json`). Main.py patches `sys.modules["json"] = orjson`.

### Error Handling

- Catch specific exceptions where possible (`httpx.HTTPError`, `BadRequest`, `FileNotFoundError`).
- Broad `except Exception` is used at boundaries (top-level handlers, connection loops) — ruff's
  `broad-except` rule is disabled.
- Log errors with `logger.error(...)` including `exc_info=True` for tracebacks when useful.
- HTTP responses: Check `response.is_success` or call `response.raise_for_status()`.
- Websocket reconnection is handled automatically by `websockets.connect()` with `async for`.

### Async Patterns

- Bot is fully async using `asyncio`. CPU-bound work (camera operations) runs in a
  `ThreadPoolExecutor` via `loop.run_in_executor(executors_pool, func)`.
- Telegram handlers: `async def handler(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None`.
- Unused context parameter conventionally named `_` or `__`.
- Optional `uvloop` imported with `contextlib.suppress(ImportError)`.

### Logging

- Use `logging` module exclusively. One logger per module: `logger = logging.getLogger(__name__)`.
- Format: `%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s`.
- Sensitive data (bot tokens) is filtered by `SensitiveFormatter` in main.py.
- Use `logger.debug` for websocket messages, `logger.info` for normal operations,
  `logger.warning` for recoverable issues, `logger.error` for failures.

## Docker

Two Dockerfiles exist: `Dockerfile` (standard) and `Dockerfile-mjpeg` (mjpeg variant).
Docker Compose files: `docker-compose.yml` (prod) and `docker-compose-dev.yml` (dev).

## CI Pipeline

GitHub Actions (`.github/workflows/ci.yaml`) runs on push to `master`/`development` and PRs:

1. Pre-commit checks on all files (ruff check, ruff format, mypy)
2. pytest
3. Docker build (on success, pushes images for non-PR events)

Matrix: Python 3.9, 3.10, 3.11, 3.12 on Ubuntu 22.04.

## Generating a Changelog

This procedure is for an AI coding agent to follow when asked to generate a changelog. It
produces two artifacts: a detailed changelog (`changes_generated.md`) and a short release-notes
summary suitable for GitHub Releases or announcements.

### Step 1 — Determine the diff range

The changelog can be generated for any valid git range. Common cases:

```bash
# Branch diff (e.g., pre-release draft)
git log development..master --oneline

# Tag-to-tag (e.g., for a release)
git log v2.0.0..v2.1.0 --oneline

# Arbitrary range
git log <from>..<to> --oneline
```

If the user does not specify a range, ask which range to use. Do not assume.

### Step 2 — Gather the raw material

For the chosen range, read and analyse:

1. **Every commit** in the range (`git log <from>..<to>` — full messages, not just `--oneline`).
2. **The full diff** (`git diff <from>...<to>`) — skim for config changes, dependency bumps,
   renamed/deleted files, signature changes, and new modules.
3. **Requirements files** — diff any `requirements*.txt` and `pyproject.toml` changes to
   identify dependency additions, removals, and version bumps.
4. **Config-related source changes** — look at `bot/configuration.py` (and any config helper
   classes) for new or removed options, default values, and types.
5. **CI / Docker changes** — diff `.github/workflows/`, `Dockerfile*`, and `docker-compose*`.
6. **Wiki directory** (`../moonraker-telegram-bot.wiki/`) — list existing pages so the "Wiki
   Updates Needed" section can reference real page names.

### Step 3 — Write `changes_generated.md`

Overwrite `changes_generated.md` in the repository root. Use the title format:

```
# Release Notes — <range description>
```

For example: `# Release Notes — v2.1.0 → v2.2.0` or `# Release Notes — development → master`.

#### Required sections (in order)

Only include a section if there is content for it. Omit empty sections entirely.

1. **Features** — New user-facing capabilities. Each item is a bold short title followed by an
   em-dash and a 1–3 sentence description. Mention relevant config options and defaults inline.

2. **Bug Fixes** — Each item is a bold short title describing the symptom, followed by an
   em-dash and a description of the root cause and fix.

3. **Breaking Changes** — Anything that could break existing setups: removed/renamed config
   options, changed method signatures, major dependency upgrades, Docker base image changes.
   Be explicit about what users or contributors need to do.

4. **Config Changes** — A table of new, changed, or removed configuration options:

   | Section | Option | Type | Default | Description |
   |---|---|---|---|---|

   End with a note on whether user config migration is required.

5. **Developer / Contributor Changes** — Tooling, CI, test, dependency, and internal
   refactoring changes that don't affect end users. Fold dependency version bumps into this
   section as a sub-table when there are multiple:

   | Package | Old Version | New Version | Notes |
   |---|---|---|---|

6. **Wiki Updates Needed** — A table listing which wiki pages (by their actual filename in
   `../moonraker-telegram-bot.wiki/`) need updating and what to change:

   | Wiki Page | What to Update |
   |---|---|

#### Writing guidelines

- Write from the perspective of someone reading release notes — clear, concise, no commit
  hashes, no PR numbers unless they add context.
- Group related commits into a single bullet point rather than listing every commit separately.
- For each bug fix, describe the **user-visible symptom** first, then the technical cause.
- Use backticks for code identifiers, config option names, and package names.
- Tables must use GitHub-flavored markdown.
- Do not invent or speculate — every item must be traceable to actual changes in the diff.

### Step 4 — Write a release-notes summary

After writing the full changelog, produce a shorter summary block at the end of
`changes_generated.md`, separated by a horizontal rule (`---`). Format:

```markdown
---

## Release Summary (for GitHub Release / announcements)

<One paragraph (3–5 sentences) summarising the release highlights.>

### Highlights
- <Bullet list of 3–7 most important items across all sections.>

### Upgrading
- <Any required user actions: config migration, dependency changes, Docker rebuild, etc.>
- If none: "No user action required — all new options have safe defaults."
```

This summary is intended to be copy-pasted into a GitHub Release or announcement post.

### Step 5 — Self-review checklist

Before presenting the result, verify:

- [ ] Every section that has content is present; empty sections are omitted.
- [ ] No commit in the range was overlooked (cross-check `git log --oneline` count).
- [ ] Config changes table matches actual code changes (option names, types, defaults).
- [ ] Dependency table versions match the actual `requirements*.txt` / `pyproject.toml` diffs.
- [ ] Wiki page names in the "Wiki Updates Needed" table exist in `../moonraker-telegram-bot.wiki/`.
- [ ] The release summary accurately reflects the full changelog.
- [ ] No markdown formatting errors (preview-check tables and nested lists).
