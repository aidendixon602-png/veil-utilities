"""Replies from utility, permissions, automod and AI: short lines, honest empty states, toggles that say which way."""
from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from cogs.ai import AI
from cogs.automod import Automod
from cogs.permissions import Permissions
from cogs.utility import Utility
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
    ctx.channel = SimpleNamespace(id=3)
    ctx.author = SimpleNamespace(id=4, mention="<@4>")
    ctx.clean_prefix = "!"
    for name in ("approve", "warn", "neutral", "paginate", "send"):
        setattr(ctx, name, AsyncMock())
    return ctx


def said(ctx, method="approve"):
    return getattr(ctx, method).await_args.args[0]


def run(coro):
    asyncio.run(coro)


def member(uid=7):
    return SimpleNamespace(id=uid, mention=f"<@{uid}>")


# ------------------------------------------------------------------ utility

def test_snipe_toggle_and_clear(bot):
    async def go():
        cog, ctx = Utility(bot), make_ctx()
        await Utility.togglesnipe.callback(cog, ctx)
        assert said(ctx) == "turned snipe **off** for this server"
        await Utility.togglesnipe.callback(cog, ctx)
        assert said(ctx) == "turned snipe **on** for this server"
        await Utility.clearsnipe.callback(cog, ctx)
        assert said(ctx) == "cleared the snipe history for this channel"
    run(go())


def test_afk_and_reminder_lines(bot):
    async def go():
        cog, ctx = Utility(bot), make_ctx()
        await Utility.afk.callback(cog, ctx, reason="lunch")
        assert said(ctx) == "you're now AFK: **lunch**"
        await Utility.remind_list.callback(cog, ctx)
        assert said(ctx, "warn") == "you have no reminders"
        bot.db.execute = AsyncMock(return_value=SimpleNamespace(rowcount=1))
        await Utility.remind_delete.callback(cog, ctx, 12)
        assert said(ctx) == "deleted reminder `12`"
    run(go())


def test_notes_lines(bot):
    async def go():
        cog, ctx = Utility(bot), make_ctx()
        await Utility.notes.callback(cog, ctx, member=member())
        assert said(ctx, "warn") == "<@7> has no notes"
        await Utility.notes_add.callback(cog, ctx, member(), note="watch this one")
        assert said(ctx) == "added a note for <@7>"
        await Utility.notes_clear.callback(cog, ctx, member())
        assert said(ctx) == "cleared the notes for <@7>"
    run(go())


def test_empty_message_board_says_so(bot):
    async def go():
        cog, ctx = Utility(bot), make_ctx()
        await Utility.messageboard.callback(cog, ctx, 0)
        assert said(ctx, "warn") == "no messages have been counted yet"
    run(go())


# ------------------------------------------------------------------ permissions

def test_checking_a_role_without_a_level_is_a_neutral_note_not_a_warning(bot):
    async def go():
        cog, ctx = Permissions(bot), make_ctx()
        role = MagicMock(spec=discord.Role)
        role.id, role.name, role.mention = 4242, "Nobody", "<@&4242>"
        await Permissions.up_check.callback(cog, ctx, role)
        ctx.warn.assert_not_awaited()
        assert said(ctx, "neutral") == "<@&4242> has no permission level"
    run(go())


def test_level_rename_and_rank_lines(bot):
    async def go():
        cog, ctx = Permissions(bot), make_ctx()
        await Permissions.pl_rename.callback(cog, ctx, "mod", name="Janitor")
        assert said(ctx) == "renamed `moderator` to **Janitor**"  # the old name still finds it
        await Permissions.pl_rank.callback(cog, ctx, "mod", 50)
        assert said(ctx) == "set `moderator` to rank **50**"
        await Permissions.pl_grant.callback(cog, ctx, "mod", "ban_members")
        assert said(ctx).startswith("`moderator` now grants `ban_members`") or said(ctx).startswith("`moderator` no longer grants")
        assert not said(ctx).endswith(".")
    run(go())


# ------------------------------------------------------------------ automod

def test_ghostping_and_filter_toggles(bot):
    async def go():
        cog, ctx = Automod(bot), make_ctx()
        await Automod.agp_on.callback(cog, ctx)
        assert said(ctx) == "turned anti-ghostping **on**"
        await Automod.agp_off.callback(cog, ctx)
        assert said(ctx) == "turned anti-ghostping **off**"
        await Automod.f_invites.callback(cog, ctx, "on")
        assert "filter **on**" in said(ctx)
        await Automod.f_invites.callback(cog, ctx, "off")
        assert "filter **off**" in said(ctx)
    run(go())


def test_filter_lists_say_which_way_they_went(bot):
    async def go():
        cog, ctx = Automod(bot), make_ctx()
        await Automod.f_allowlink.callback(cog, ctx, "youtube.com")
        assert said(ctx) == "`youtube.com` is now allowed through the link filter"
        await Automod.f_allowlink.callback(cog, ctx, "youtube.com")
        assert said(ctx) == "`youtube.com` is no longer allowed through the link filter"
        await Automod.f_word.callback(cog, ctx, word="Spoiler")
        assert said(ctx) == "blocked `spoiler`"
        await Automod.f_word.callback(cog, ctx, word="spoiler")
        assert said(ctx) == "unblocked `spoiler`"
        await Automod.f_regex.callback(cog, ctx, pattern="a+b")
        assert said(ctx) == "blocked pattern `a+b`"
        await Automod.f_regex.callback(cog, ctx, pattern="a+b")
        assert said(ctx) == "unblocked pattern `a+b`"
        target = MagicMock(spec=discord.TextChannel)  # exemption works for roles and channels alike
        target.id, target.mention = 9, "<#9>"
        await Automod.f_exempt.callback(cog, ctx, target)
        assert said(ctx) == "<#9> is now exempt from the filters"
        await Automod.f_exempt.callback(cog, ctx, target)
        assert said(ctx) == "<#9> is no longer exempt from the filters"
    run(go())


def test_empty_filter_list_says_so(bot):
    async def go():
        cog, ctx = Automod(bot), make_ctx()
        await Automod.f_list.callback(cog, ctx)
        assert said(ctx, "warn") == "no words or patterns are blocked"
    run(go())


# ------------------------------------------------------------------ ai

def test_ai_reset_line(bot):
    async def go():
        cog = AI.__new__(AI)
        cog.bot, cog.history = bot, {}
        ctx = make_ctx()
        await AI.ask_reset.callback(cog, ctx)
        assert said(ctx) == "cleared your conversation"
    run(go())
