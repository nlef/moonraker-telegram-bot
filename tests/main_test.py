from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import main
from main import prepare_command
import pytest


def test_bot_commands_preparation() -> None:
    valid_command = prepare_command("SuperCommand")
    long_command = prepare_command("InvalidCommandToooooooooooooooooLong")
    invalid_symblos_command = prepare_command("InvalidSymblosCommand&^)))")
    assert valid_command
    assert long_command is None
    assert invalid_symblos_command is None


def test_resolve_camera_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    front = SimpleNamespace(name="front")
    bed = SimpleNamespace(name="bed")
    monkeypatch.setattr(main, "cameras", {"front": front, "bed": bed}, raising=False)

    assert main._resolve_camera(["bed"]) is bed
    assert main._resolve_camera(["missing"]) is None


@pytest.mark.asyncio
async def test_video_command_shows_camera_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "cameras", {"front": SimpleNamespace(name="front"), "bed": SimpleNamespace(name="bed")}, raising=False)
    monkeypatch.setattr(main, "notifier", SimpleNamespace(silent_commands=False), raising=False)

    message = MagicMock()
    message.reply_text = AsyncMock()
    update = SimpleNamespace(effective_message=message)
    context = SimpleNamespace(args=[])

    await main.get_video(update, context)

    message.reply_text.assert_awaited_once()
    markup = message.reply_text.await_args.kwargs["reply_markup"].to_dict()
    buttons = [button["callback_data"] for row in markup["inline_keyboard"] for button in row]
    assert buttons == ["video:front", "video:bed"]


@pytest.mark.asyncio
async def test_video_command_without_cameras_reports_missing_camera(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "cameras", {}, raising=False)

    message = MagicMock()
    message.reply_text = AsyncMock()
    update = SimpleNamespace(effective_message=message)
    context = SimpleNamespace(args=[])

    await main.get_video(update, context)

    message.reply_text.assert_awaited_once_with("No camera is configured.", do_quote=True)
