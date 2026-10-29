"""Replies from the developer, integration and music-free cogs: short lines, honest empty states."""
from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from cogs.devtools import DevTools
from cogs.integrations import Integrations
from cogs.owner import Owner
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


def run(coro):
    asyncio.run(coro)


def test_empty_developer_lists_say_so(bot):
    async def go():
        owner, ctx = Owner(bot), make_ctx()
        bot.killswitch = set()
        await Owner.killswitch_list.callback(owner, ctx)
        assert said(ctx, "warn") == "nothing is kill-switched"
        bot.recent_errors.clear()
        await Owner.error.callback(owner, ctx)
        assert said(ctx, "neutral") == "no errors recorded since the last restart"

        tools = DevTools(bot)
        await DevTools.anp_list.callback(tools, ctx)
        assert said(ctx, "warn") == "there are no antinuke presets"
    run(go())


def test_blacklist_and_leave_lines(bot):
    async def go():
        owner, ctx = Owner(bot), make_ctx()
        bot.blacklist = set()
        bot.get_guild = MagicMock(return_value=None)
        await Owner.blacklist.callback(owner, ctx, 999)
        assert said(ctx) == "blacklisted `999`"
        await Owner.blacklist.callback(owner, ctx, 999)
        assert said(ctx) == "unblacklisted `999`"

        guild = SimpleNamespace(name="My *Server*", leave=AsyncMock())
        bot.get_guild = MagicMock(return_value=guild)
        await Owner.leaveguild.callback(owner, ctx, 5)
        assert said(ctx) == "left **My \\*Server\\***"
    run(go())


def test_reload_line_and_core_note(bot, monkeypatch):
    async def go():
        owner, ctx = Owner(bot), make_ctx()
        bot.reload_extension = AsyncMock()
        monkeypatch.setattr("cogs.owner.core_changed_on_disk", lambda b: True)
        await Owner.reload.callback(owner, ctx, "moderation")
        text = said(ctx)
        assert text.startswith("reloaded **1** module\n-# ")
        assert "`!restart`" in text
    run(go())


def test_devtools_preset_lines(bot):
    async def go():
        tools, ctx = DevTools(bot), make_ctx()
        await DevTools.anp_save.callback(tools, ctx, "Strict")
        assert said(ctx) == "saved antinuke preset **strict**"
        await DevTools.anp_list.callback(tools, ctx)
        assert said(ctx, "neutral") == "antinuke presets: `strict`"
        await DevTools.anp_delete.callback(tools, ctx, "strict")
        assert said(ctx) == "deleted preset **strict**"
    run(go())


def test_convert_same_currency_and_lastfm_unlink(bot):
    async def go():
        cog, ctx = Integrations(bot), make_ctx()
        await Integrations.convert.callback(cog, ctx, 5.0, "usd", "USD")
        assert said(ctx, "neutral") == "**5.00 USD** is **5.00 USD**"
        ctx.author = SimpleNamespace(id=3)
        await Integrations.lf_unlink.callback(cog, ctx)
        assert said(ctx) == "unlinked Last.fm"
    run(go())
