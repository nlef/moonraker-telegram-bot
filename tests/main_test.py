from bot.main import prepare_command  # type: ignore[import-not-found]


def test_bot_commands_preparation():
    valid_command = prepare_command("SuperCommand")
    long_command = prepare_command("InvalidCommandToooooooooooooooooLong")
    invalid_symblos_command = prepare_command("InvalidSymblosCommand&^)))")
    assert valid_command
    assert long_command is None
    assert invalid_symblos_command is None
