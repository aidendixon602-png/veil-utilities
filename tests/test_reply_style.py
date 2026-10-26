"""The shared reply style: every plain answer is one bare coloured line, sent as a reply."""
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
    ctx = Context(message=message, bot=bot, view=StringView(""), prefix=",", command=command)
    ctx.send = AsyncMock()  # stop at the Discord API boundary
    return ctx


STYLES = [
    ("approve", Colors.success),
    ("done", Colors.success),
    ("warn", Colors.warn),
    ("deny", Colors.error),
    ("fail", Colors.error),
    ("neutral", Colors.neutral),
]


@pytest.mark.parametrize("method,color", STYLES)
def test_each_reply_is_a_bare_coloured_line_sent_as_a_reply(bot, method, color):
    async def go():
        ctx = make_ctx(bot)
        await getattr(ctx, method)("did the thing")
        kwargs = ctx.send.await_args.kwargs
        embed = kwargs["embed"]
        assert embed.description == "did the thing"
        assert embed.colour.value == color
        assert embed.author.name is None and embed.footer.text is None
        assert embed.title is None and embed.timestamp is None
        assert kwargs["reference"] == "REF" and kwargs["mention_author"] is False
    asyncio.run(go())


def test_colours_are_distinct_so_a_glance_tells_success_from_failure():
    assert len({Colors.success, Colors.warn, Colors.error, Colors.neutral}) == 4


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
        assert ctx.send.await_args.args == ("ok",)
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
            assert colour == Colors.error, type(error).__name__
    asyncio.run(go())


def test_cooldown_nudge_stays_amber_and_disappears(bot):
    async def go():
        ctx = make_ctx(bot, Moderation.stripstaff)
        ctx.author = MagicMock(id=1)
        error = commands.CommandOnCooldown(commands.Cooldown(1, 5), 3.2, commands.BucketType.user)
        await bot.on_command_error(ctx, error)
        kwargs = ctx.send.await_args.kwargs
        assert kwargs["embed"].colour.value == Colors.warn and kwargs["delete_after"] == 3.5
    asyncio.run(go())


def test_failure_lines_read_the_same_for_every_command(bot):
    async def go():
        ctx = make_ctx(bot, Moderation.stripstaff)
        await bot.on_command_error(ctx, UserError("Member `bob` not found."))
        embed = ctx.send.await_args.kwargs["embed"]
        assert embed.description == "failed: member `bob` not found"
        assert embed.colour.value == Colors.error
    asyncio.run(go())
