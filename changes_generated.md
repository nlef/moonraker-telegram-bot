# Release Notes — development → master

## New Features

- **New camera type: `raw_stream`** — Takes snapshots via `host_snapshot` and records video by stream-copying directly from the source using ffmpeg (`-c:v copy`) with no re-encoding. Ideal for RTSP or native H264 streams. Flip/rotate only applies to photos; for video transforms, use `type=ffmpeg`. Requires system `ffmpeg`.

- **Inline "Update Status" button** — Status messages now include an inline "Update" button that refreshes the status in-place (edits the existing message with fresh data/photo) instead of sending a new message. Controlled by new config option `status_update_button` (default: `true`).

- **Option to disable reply keyboard** — The persistent reply keyboard at the bottom of the chat can now be disabled. New config option `send_reply_keyboard` (default: `true`). When set to `false`, the keyboard is actively removed.

- **Final status update on print abort** — When a print is cancelled or errors out, the bot now sends a final status update notification before resetting state, so you see the last state of the print.

- **Python 3.13 support** — Added to CI matrix and Docker images.

## Changes to Existing Features

- **Notification system refactored** — All message sending/editing logic consolidated into a new `TelegramMessageRepr` helper class (`bot/telegram_helper.py`), unifying how messages are sent, replied, and edited in-place.

- **HTML formatting restored in notifications** — Notifications now default to `ParseMode.HTML` instead of `MARKDOWN_V2`, fixing formatting issues.

- **Async file I/O** — Log file reading and uploads now use `aiofiles` instead of blocking `open()`. Log upload to the analyzer uses `httpx.AsyncClient` instead of synchronous `httpx.post()`.

- **Error messages improved** — Error notifications from klipper/websocket now use `<pre>` formatted blocks for error details, improving readability.

- **Status message unpinning** — `reset_notifications()` now properly unpins the status message before clearing it.

- **Docker base images upgraded** — From `python:3.12.8-slim-bookworm` to `python:3.13.11-slim-trixie` (Debian Trixie).

- **Linting toolchain replaced** — `black` + `isort` + `pylint` replaced with `ruff`; `pre-commit` replaced with `prek` as the git hook runner.

- **Code style modernization** — Many `map`/`lambda`/`filter` patterns replaced with list/dict comprehensions throughout the codebase.

## Bug Fixes

- **JWT token refresh using stale headers** — Fixed a bug where retried requests after a 401/token-refresh would use stale headers. The `headers` parameter was removed from `make_request()` / `make_request_sync()`; they now always use `self._headers`, ensuring refreshed tokens are used on retry.

- **`set_printing_filename` crash on failed metadata response** — No longer crashes when the metadata response from Moonraker fails (e.g. 404). Sets fallback values for estimated time, filament totals, etc.

- **Power toggle confirmation message text** — Fixed operator precedence bug: `"Power " + "Off" if ...` → `"Power " + ("Off" if ... else "On") + " printer?"`.

- **PSU and light devices null check in message creation** — Fixed crash in power device message creation when `_light_device` or `_psu_device` is not configured (`None`).

- **Timelapse folder creation on bad fileinfo response** — Fixed crash when Moonraker returns a bad response for file info during timelapse.

- **Blocking `time.sleep` in async context** — `time.sleep(1)` in the async `check_connection()` loop replaced with `await asyncio.sleep(1)`.

- **"Update status" inline keyboard removed on print finish/cancel** — The inline keyboard is now cleaned up when printing ends.

## Breaking Changes

- **Docker base image: Debian Trixie + Python 3.13** — Docker images moved from `python:3.12.8-slim-bookworm` to `python:3.13.11-slim-trixie`. Users building custom images or depending on specific Debian Bookworm packages should be aware of this change.

- **`python-telegram-bot` upgraded from v21.10 to v22.5/22.6** — Major library upgrade. Among other things, the `quote=True` parameter changed to `do_quote=True`. Users with custom forks or patches based on the old API should take note.

- **Major dependency version bumps** — `numpy` 1.26→2.x, `websockets` 14.1→15/16, `Pillow` 11.1→12.1, `orjson` 3.10→3.11, `uvloop` 0.21→0.22. May affect users who pin dependencies or run in constrained environments.

- **`Klippy.make_request()` / `make_request_sync()` signature change** — The `headers` parameter was removed. These methods now always use `self._headers`. Any code calling them with custom headers will need to be updated.

- **`Notifier._send_message()` and `_send_photo()` signature changes** — Now accept `TelegramMessageRepr` instead of separate `message: str, silent: bool` parameters. Any subclasses or custom extensions would need updating.

## Config Changes

### New options in `[telegram_ui]`

| Option | Type | Default | Description |
|---|---|---|---|
| `send_reply_keyboard` | boolean | `true` | Whether to show the persistent reply keyboard at the bottom of the chat |
| `status_update_button` | boolean | `true` | Whether to show an inline "Update" button on status messages |

### New allowed value in `[camera]`

| Option | Change |
|---|---|
| `type` | New allowed value: `raw_stream` (in addition to existing `opencv`, `ffmpeg`, `mjpeg`) |

No user config migration is required — all new options have safe defaults.

## Dependency Updates

| Package | Old Version | New Version | Notes |
|---|---|---|---|
| `python-telegram-bot` | 21.10 | 22.5 / 22.6 | Major upgrade; 22.6 for Python ≥3.10, 22.5 for 3.9 |
| `numpy` | ~1.26.4 | ~2.4.2 / ~2.2.5 | 2.4.2 for Python ≥3.11, 2.2.5 for 3.10 |
| `websockets` | 14.1 | 16.0 / 15.0.1 | 16.0 for Python ≥3.10, 15.0.1 for 3.9 |
| `Pillow` | 11.1.0 | 12.1.1 | 12.1.1 for Python ≥3.10, 10.4.0 for ≤3.9 |
| `orjson` | 3.10.15 | 3.11.7 / 3.10.14 | 3.11.7 for Python ≥3.10, 3.10.14 for 3.9 |
| `uvloop` | 0.21.0 | 0.22.1 | |
| `httpx` | 0.28.1 | 0.28.1 | Unchanged, but `httpcore` bumped 1.0.7→1.0.9 |
| `APScheduler` | 3.11.0 | 3.11.2 | |
| `ffmpegcv` | 0.3.15 | 0.3.18 | |
| `emoji` | 2.14.1 | 2.15.0 | |
| `aiofiles` | — | 25.1.0 | New dependency |
| `h2` | — | 4.3.0 | New dependency |

## Developer / Contributor Changes

- **Linting**: `black`, `isort`, `pylint` replaced with `ruff` (both formatting and linting).
- **Git hooks**: `pre-commit` replaced with `prek`. Run via `prek install` and `prek run --all-files`.
- **Config consolidation**: `pytest.ini` removed; settings merged into `pyproject.toml`.
- **New test dependency**: `pytest-asyncio` added for async test support.
- **New tests**: JWT token refresh retry test, `set_printing_filename` bad response handling test.
- **CI**: Runner upgraded from `ubuntu-22.04` to `ubuntu-24.04`. Python 3.13 added to matrix. Docker job skipped on pull requests.
- **CONTRIBUTING.md**: Expanded with Docker dev workflow, local run instructions, and image build instructions.

## Wiki Documentation Updates Needed

| Wiki Page | What to Update |
|---|---|
| **Sample config** | Add `send_reply_keyboard` and `status_update_button` to `[telegram_ui]` section. Add `raw_stream` to allowed values for `type` in `[camera]` section. |
| **Camera modes** | Add a new "Type: raw_stream" section explaining the camera type, its use case (RTSP/H264 streams), requirement for system ffmpeg, and that flip/rotate only applies to photos. |
| **Home** | Consider adding "Inline status updates" and "Reply keyboard toggle" to the Features list. |
