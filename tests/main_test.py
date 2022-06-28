from bot.main import prepare_command


def test_bot_commands_preparation():
    valid_command = prepare_command("SuperCommand")
    long_command = prepare_command("InvalidCommandToooooooooooooooooLong")
    invalid_symblos_command = prepare_command("InvalidSymblosCommand&^)))")
    assert valid_command and long_command is None and invalid_symblos_command is None
