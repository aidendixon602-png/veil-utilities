"""Replies from the server-setup cogs (settings, config, levels, tickets, giveaways): short lines that say what changed."""
from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from cogs.config import Config
from cogs.giveaways import Giveaways
from cogs.levels import Levels
from cogs.settings import Settings
from cogs.tickets import Tickets
from core.bot import Veil


@pytest.fixture
def bot():
    b = Veil()
    store: dict = {}

    async def get_config(gid, module, default=None):
        return store.setdefault((gid, module), copy.deepcopy(default) if default is not None else {})

    async def set_config(gid, module, data):
        store[(gid, module)] = data

    b.db.get_config = AsyncMock(side_effect=get_config)
    b.db.set_config = AsyncMock(side_effect=set_config)
    b.db.fetchall = AsyncMock(return_value=[])
    b.db.fetchone = AsyncMock(return_value=None)
    b.db.execute = AsyncMock()
    return b


def make_ctx():
    ctx = MagicMock()
    ctx.guild = SimpleNamespace(id=1, owner_id=2)
    ctx.clean_prefix = "!"
    for name in ("approve", "warn", "neutral", "paginate", "send"):
        setattr(ctx, name, AsyncMock())
    return ctx


def said(ctx, method="approve"):
    return getattr(ctx, method).await_args.args[0]


def role(rid=5):
    return SimpleNamespace(id=rid, mention=f"<@&{rid}>", permissions=SimpleNamespace(administrator=False))


def channel(cid=9):
    return SimpleNamespace(id=cid, mention=f"<#{cid}>")


def member(uid=7):
    return SimpleNamespace(id=uid, mention=f"<@{uid}>")


def run(coro):
    asyncio.run(coro)


# ------------------------------------------------------------------ settings

def test_prefix_line(bot):
    async def go():
        cog, ctx = Settings(bot), make_ctx()
        await Settings.prefix_set.callback(cog, ctx, "!")
        assert said(ctx) == "set the server prefix to `!`"
    run(go())


def test_ignore_toggle_says_which_way(bot):
    async def go():
        cog, ctx = Settings(bot), make_ctx()
        text_channel = MagicMock(spec=discord.TextChannel)
        text_channel.id, text_channel.mention = 9, "<#9>"
        await Settings.ignore.callback(cog, ctx, text_channel)
        assert said(ctx) == "now ignoring <#9>"
        await Settings.ignore.callback(cog, ctx, text_channel)
        assert said(ctx) == "no longer ignoring <#9>"
    run(go())


def test_staff_only_note_is_a_subtext_line(bot):
    async def go():
        cog, ctx = Settings(bot), make_ctx()
        await Settings.staffonly.callback(cog, ctx, True)
        text = said(ctx)
        assert text.startswith("turned staff-only mode **on**\n-# ")
        await Settings.staffonly.callback(cog, ctx, False)
        assert said(ctx).startswith("turned staff-only mode **off**\n-# ")
    run(go())


def test_empty_alias_list_says_so(bot):
    async def go():
        cog, ctx = Settings(bot), make_ctx()
        await Settings.alias_list.callback(cog, ctx)
        assert said(ctx, "warn") == "no aliases are set"
    run(go())


# ------------------------------------------------------------------ config

def test_welcome_off_and_empty_autoroles(bot):
    async def go():
        cog, ctx = Config(bot), make_ctx()
        await Config.welcome_off.callback(cog, ctx)
        assert said(ctx) == "turned off welcome messages"
        await Config.autorole.callback(cog, ctx)
        assert said(ctx, "warn") == "no autoroles are set"
    run(go())


# ------------------------------------------------------------------ levels

def test_leveling_lines(bot):
    async def go():
        cog, ctx = Levels(bot), make_ctx()
        await Levels.lv_on.callback(cog, ctx)
        assert said(ctx) == "turned leveling **on**"
        await Levels.lv_off.callback(cog, ctx)
        assert said(ctx) == "turned leveling **off**"
        await Levels.lv_stack.callback(cog, ctx, True)
        assert said(ctx) == "turned stacking rewards **on**"
        await Levels.lv_voice.callback(cog, ctx, False)
        assert said(ctx) == "turned voice XP **off**"
        await Levels.lv_channel.callback(cog, ctx, None)
        assert "level-ups will go to" in said(ctx)
    run(go())


def test_leveling_ignore_toggle_and_multiplier(bot):
    async def go():
        cog, ctx = Levels(bot), make_ctx()
        await Levels.lv_ignore.callback(cog, ctx, channel())
        assert said(ctx) == "<#9> no longer gives XP"
        await Levels.lv_ignore.callback(cog, ctx, channel())
        assert said(ctx) == "<#9> gives XP again"
        await Levels.lv_mult.callback(cog, ctx, role(), 2.0)
        assert said(ctx) == "<@&5> now earns XP ×2"
        await Levels.lv_mult.callback(cog, ctx, role(), 1.0)
        assert said(ctx) == "<@&5> earns normal XP"
    run(go())


def test_empty_leaderboard_says_so(bot):
    async def go():
        cog, ctx = Levels(bot), make_ctx()
        await Levels.leaderboard.callback(cog, ctx)
        assert said(ctx, "warn") == "nobody has any XP yet"
    run(go())


# ------------------------------------------------------------------ tickets / giveaways

def test_ticket_support_toggle_says_which_way(bot):
    async def go():
        cog, ctx = Tickets(bot), make_ctx()
        await Tickets.t_support.callback(cog, ctx, role())
        assert said(ctx) == "added <@&5> to the support roles"
        await Tickets.t_support.callback(cog, ctx, role())
        assert said(ctx) == "removed <@&5> from the support roles"
        await Tickets.t_logs.callback(cog, ctx, None)
        assert said(ctx) == "transcripts will only be sent by DM"
        await Tickets.t_ping.callback(cog, ctx, True)
        assert said(ctx) == "turned support pings **on**"
    run(go())


def test_no_active_giveaways_says_so(bot):
    async def go():
        cog, ctx = Giveaways(bot), make_ctx()
        await Giveaways.gw_list.callback(cog, ctx)
        assert said(ctx, "warn") == "there are no active giveaways"
    run(go())
