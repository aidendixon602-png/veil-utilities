"""Help pages must never leave Markdown half-open, and bad arguments are explained by what was typed."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord.ext import commands

from core import help as h
from core.utils import input_error_text, shorten_md

BUMP = "Remind the server to /bump on Disboard every 2 hours (`bump on` / `bump off`)"


@pytest.mark.parametrize("text", [
    BUMP,
    "Set things up with **bold words that go on and on** and more text after it",
    "Run `,command with a very long example argument list` to see what happens next",
    "Spoilers ||hidden text that is long enough to be cut somewhere in the middle||",
])
def test_shorten_md_never_leaves_markdown_open(text):
    for limit in range(10, len(text)):
        out = shorten_md(text, limit)
        assert len(out) <= limit
        for mark in ("`", "**", "||"):
            assert out.count(mark) % 2 == 0, (limit, out)
        assert out.count("(") == out.count(")"), (limit, out)


def test_short_text_is_left_alone():
    assert shorten_md("Set up a starboard: `starboard #stars 3 ⭐`", 70) == "Set up a starboard: `starboard #stars 3 ⭐`"


@pytest.mark.parametrize("text", [
    "```python\nprint('hi')\n```\n" + "filler " * 40,          # a fenced block: its backticks used to be miscounted
    "```\ncode\n``` " + "x" * 300,                               # a closed fence long enough to be cut inside
    "__underlined__ " + "words " * 40,                        # __ counted the `` inside `` `x` `` as bold
    "**bold** " + "words " * 40,
    "~~struck~~ " + "words " * 40,
    "||spoiler|| " + "words " * 40,
    "run `,ban @user` because " + "reason " * 30,
])
def test_a_cut_never_leaves_formatting_open(text):
    import re

    def balanced(value: str) -> bool:
        if value.count("`") % 2:
            return False
        for char in "*_~|":
            runs = [m.group() for m in re.finditer(re.escape(char) + r"+", value)]
            if sum(len(run) >= 2 for run in runs) % 2:
                return False  # an open bold / underline / strike / spoiler
        return value.count("(") == value.count(")") and value.count("[") == value.count("]")

    assert balanced(text), "the test itself needs balanced input"
    for limit in range(20, len(text)):
        out = shorten_md(text, limit)
        assert len(out) <= limit, (limit, out)
        assert balanced(out), (limit, out)


def test_the_whole_help_page_line_is_still_cut_on_a_word():
    out = shorten_md(BUMP, 60)
    assert out.endswith("…") and len(out) <= 60
    assert not out[:-1].endswith("("), out


def test_the_bump_line_on_the_server_page_is_cut_cleanly():
    cmd = SimpleNamespace(short_doc=BUMP)
    assert h.blurb(cmd) == "Remind the server to /bump on Disboard every 2 hours"


def _union_error(typed):
    param = commands.Parameter("user", commands.Parameter.POSITIONAL_OR_KEYWORD)
    errors = [commands.MemberNotFound(typed), commands.UserNotFound(typed)]
    return commands.BadUnionArgument(param, [discord.Member, discord.User], errors)


def test_a_union_argument_error_names_what_was_typed():
    assert input_error_text(_union_error("$")) == "couldn't find a member or user matching `$`"


def test_other_input_errors_read_plainly():
    assert input_error_text(commands.RoleNotFound("mods")) == "couldn't find a role matching `mods`"
    assert "missing" in input_error_text(commands.MissingRequiredArgument(
        commands.Parameter("user", commands.Parameter.POSITIONAL_OR_KEYWORD)))


def test_the_reply_to_a_bad_member_argument_is_plain(monkeypatch):
    from core.bot import Veil
    bot = Veil()
    ctx = MagicMock()
    ctx.fail = AsyncMock()
    ctx.command = SimpleNamespace(has_error_handler=lambda: False, qualified_name="antinuke whitelist")
    ctx.cog = None
    asyncio.run(bot.on_command_error(ctx, _union_error("$")))
    text = ctx.fail.call_args.args[0]
    assert text == "failed: couldn't find a member or user matching `$`"


def test_the_command_log_says_what_went_wrong_without_class_names(monkeypatch):
    from cogs.logging import Logging
    cog = Logging.__new__(Logging)
    cog._log_command = AsyncMock()
    asyncio.run(Logging.on_command_error(cog, MagicMock(), _union_error("$")))
    reason = cog._log_command.call_args.args[1]
    assert reason == "Couldn't find a member or user matching `$`"
    assert "BadUnionArgument" not in reason
