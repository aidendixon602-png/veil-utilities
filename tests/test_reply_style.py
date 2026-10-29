"""The shared reply style (greed / bleed): one line, "✅ @you: text", sent as a reply."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord.ext import commands
from discord.ext.commands.view import StringView

from cogs.moderation import Moderation
from core.bot import Veil
from core.utils import Colors, Context, UserError


@pytest.fixture
def bot():
    return Veil()


def make_ctx(bot, command=None):
    message = MagicMock()
    message.to_reference.return_value = "REF"
    message.author.mention = "<@1>"
    ctx = Context(message=message, bot=bot, view=StringView(""), prefix=",", command=command)
    ctx.send = AsyncMock()  # stop at the Discord API boundary
    return ctx


STYLES = [
    ("approve", Colors.approve, "✅ <@1>: did the thing"),
    ("done", Colors.approve, "✅ <@1>: did the thing"),
    ("warn", Colors.caution, "⚠️ <@1>: did the thing"),
    ("deny", Colors.deny, "❌ <@1>: did the thing"),
    ("fail", Colors.deny, "❌ <@1>: did the thing"),
    ("neutral", Colors.neutral, "did the thing"),
]


@pytest.mark.parametrize("method,color,line", STYLES)
def test_each_reply_is_one_line_with_an_emoji_and_your_mention(bot, method, color, line):
    async def go():
        ctx = make_ctx(bot)
        await getattr(ctx, method)("did the thing")
        kwargs = ctx.send.await_args.kwargs
        embed = kwargs["embed"]
        assert embed.description == line
        assert embed.colour.value == color
        assert embed.author.name is None and embed.footer.text is None
        assert embed.title is None and embed.timestamp is None
        assert kwargs["reference"] == "REF" and kwargs["mention_author"] is False
    asyncio.run(go())


def test_colours_are_distinct_so_a_glance_tells_success_from_failure():
    assert len({Colors.approve, Colors.caution, Colors.deny, Colors.neutral}) == 4


def test_a_reply_with_its_own_emoji_keeps_it():
    ctx = make_ctx(Veil())
    assert ctx._line("❌", "🔒 You need the **Admin** level") == "🔒 <@1>: You need the **Admin** level"
    assert ctx._line("✅", "**bold** start") == "✅ <@1>: **bold** start"


def test_extra_arguments_reach_send(bot):
    async def go():
        ctx = make_ctx(bot)
        view = discord.ui.View()
        await ctx.warn("sure?", view=view, delete_after=5)
        kwargs = ctx.send.await_args.kwargs
        assert kwargs["view"] is view and kwargs["delete_after"] == 5
    asyncio.run(go())


def test_falls_back_to_plain_text_when_embeds_are_refused(bot):
    async def go():
        ctx = make_ctx(bot)
        response = MagicMock(status=403, reason="Forbidden")
        ctx.send.side_effect = [discord.Forbidden(response, "no embeds"), "sent"]
        assert await ctx.approve("ok") == "sent"
        assert ctx.send.await_args.args == ("✅ <@1>: ok",)
    asyncio.run(go())


def test_the_old_footer_style_is_gone():
    assert not hasattr(Context, "_footer") and not hasattr(Context, "_styled")


# ------------------------------------------------------------------ errors use the same red line everywhere

def test_unhandled_input_errors_are_red_not_amber(bot):
    async def go():
        for error in (UserError("That role is blacklisted"), commands.BadArgument("bad number"),
                      commands.NoPrivateMessage(), commands.DisabledCommand("x")):
            ctx = make_ctx(bot, Moderation.stripstaff)
            await bot.on_command_error(ctx, error)
            colour = ctx.send.await_args.kwargs["embed"].colour.value
            assert colour == Colors.deny, type(error).__name__
    asyncio.run(go())


def test_cooldown_nudge_stays_amber_and_disappears(bot):
    async def go():
        ctx = make_ctx(bot, Moderation.stripstaff)
        ctx.author = MagicMock(id=1)
        error = commands.CommandOnCooldown(commands.Cooldown(1, 5), 3.2, commands.BucketType.user)
        await bot.on_command_error(ctx, error)
        kwargs = ctx.send.await_args.kwargs
        assert kwargs["embed"].colour.value == Colors.caution and kwargs["delete_after"] == 3.5
    asyncio.run(go())


def test_failure_lines_read_the_same_for_every_command(bot):
    async def go():
        ctx = make_ctx(bot, Moderation.stripstaff)
        await bot.on_command_error(ctx, UserError("Member `bob` not found."))
        embed = ctx.send.await_args.kwargs["embed"]
        assert embed.description == "❌ <@1>: member `bob` not found"  # the ❌ says it failed
        assert embed.colour.value == Colors.deny
    asyncio.run(go())


def test_last_resort_errors_use_the_same_failed_line(bot):
    async def go():
        cases = [
            (commands.NoPrivateMessage(), "❌ <@1>: this command can only be used in servers"),
            (commands.DisabledCommand("x"), "❌ <@1>: that command is disabled right now"),
            (commands.CheckFailure("You can't do that."), "❌ <@1>: you can't do that"),
            (commands.CheckFailure(), "❌ <@1>: you can't use this command"),
            (commands.UserInputError("Bad flag."), "❌ <@1>: bad flag"),
            (asyncio.TimeoutError(), "❌ <@1>: that took too long, try again in a moment"),
        ]
        for error, expected in cases:
            ctx = make_ctx(bot, Moderation.stripstaff)
            await bot.on_command_error(ctx, error)
            embed = ctx.send.await_args.kwargs["embed"]
            assert embed.description == expected, type(error).__name__
            assert embed.colour.value == Colors.deny
    asyncio.run(go())
