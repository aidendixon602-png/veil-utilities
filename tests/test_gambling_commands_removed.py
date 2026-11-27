from cogs.fun import Fun


def test_gambling_style_coin_and_dice_commands_are_removed():
    assert not hasattr(Fun, "coinflip")
    assert not hasattr(Fun, "roll")

    commands = [command for command in Fun.__dict__.values() if getattr(command, "name", None)]
    names = {command.name for command in commands}
    aliases = {alias for command in commands for alias in (command.aliases or [])}
    assert not ({"coinflip", "roll", "cf", "flip", "dice"} & (names | aliases))
