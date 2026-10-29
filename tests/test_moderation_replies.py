"""Moderation replies: short lower-case lines with a mention and the case number, and honest empty states."""
from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from cogs.moderation import Moderation
from core.bot import Veil
from core.utils import UserError, compact_failure
from test_roleblacklist_enforcement import _mock_context, _mock_guild, _mock_member


@pytest.fixture
def bot():
    b = Veil()
    b.db.get_config = AsyncMock(return_value={})
    b.db.set_config = AsyncMock()
    b.db.fetchone = AsyncMock(return_value={"c": 3})
    b.db.fetchall = AsyncMock(return_value=[])
    b.db.execute = AsyncMock()
    return b


@pytest.fixture(autouse=True)
def no_side_effects(monkeypatch):
    """Skip hierarchy, antinuke, DMs and the case table: only the reply is under test."""
    monkeypatch.setattr("cogs.moderation.check_hierarchy_async", AsyncMock())
    monkeypatch.setattr("cogs.moderation.antinuke_guard", AsyncMock())
    monkeypatch.setattr("cogs.moderation.dm_target", AsyncMock())
    monkeypatch.setattr("cogs.moderation.create_case", AsyncMock(return_value=7))


def setup(bot):
    guild = _mock_guild(1)
    guild.ban, guild.unban = AsyncMock(), AsyncMock()
    author = _mock_member(guild.owner_id)
    ctx = _mock_context(bot, guild, author)
    ctx.channel = MagicMock()
    ctx.channel.mention = "<#9>"
    target = _mock_member(50)
    target.kick, target.ban, target.timeout = AsyncMock(), AsyncMock(), AsyncMock()
    return Moderation(bot), ctx, target


def said(ctx, method="approve"):
    return getattr(ctx, method).await_args.args[0]


def test_ban_kick_unban_softban_lines(bot):
    async def go():
        cog, ctx, target = setup(bot)
        await Moderation.ban.callback(cog, ctx, target, reason="spam")
        assert said(ctx, "warn") == f"**{target.name}** has been banned · case #7"
        await Moderation.kick.callback(cog, ctx, target, reason="spam")
        assert said(ctx) == "kicked <@50> · case #7"
        await Moderation.softban.callback(cog, ctx, target, reason="spam")
        assert said(ctx) == "softbanned <@50> · case #7"
        bot.db.fetchone = AsyncMock(return_value=None)  # not hardbanned
        unbanned = MagicMock(mention="<@60>", id=60)
        unbanned.name = "freed"
        await Moderation.unban.callback(cog, ctx, unbanned, reason=None)
        assert said(ctx, "warn") == "**freed** has been unbanned · case #7"
    asyncio.run(go())


def test_timeout_line_has_the_duration(bot):
    async def go():
        cog, ctx, target = setup(bot)
        await Moderation.timeout.callback(cog, ctx, target, timedelta(minutes=10), reason="loud")
        assert said(ctx) == "timed out <@50> for **10 minutes** · case #7"
    asyncio.run(go())


def test_warn_line_counts_the_warning(bot):
    async def go():
        cog, ctx, target = setup(bot)
        await Moderation.warn.callback(cog, ctx, target, reason="rude")
        assert said(ctx) == "warned <@50> (warning **3**) · case #7"
    asyncio.run(go())


def test_auto_punishment_is_an_amber_note(bot):
    async def go():
        cog, ctx, target = setup(bot)
        bot.db.get_config = AsyncMock(return_value={"3": "kick"})
        await Moderation.warn.callback(cog, ctx, target, reason="rude")
        target.kick.assert_awaited_once()
        assert "reached **3** warnings" in said(ctx, "warn")
    asyncio.run(go())


def test_lock_unlock_slowmode_lines(bot):
    async def go():
        cog, ctx, _ = setup(bot)
        channel = MagicMock(spec=discord.TextChannel)
        channel.mention = "<#9>"
        channel.overwrites_for.return_value = discord.PermissionOverwrite()
        channel.set_permissions, channel.edit = AsyncMock(), AsyncMock()
        await Moderation.lock.callback(cog, ctx, channel)
        assert said(ctx) == "locked <#9>"
        await Moderation.unlock.callback(cog, ctx, channel)
        assert said(ctx) == "unlocked <#9>"
        await Moderation.slowmode.callback(cog, ctx, "10s", channel)
        assert said(ctx) == "set slowmode in <#9> to **10 seconds**"
        await Moderation.slowmode.callback(cog, ctx, "0", channel)
        assert said(ctx) == "turned off slowmode in <#9>"
    asyncio.run(go())


def test_purge_count_is_singular_or_plural(bot):
    async def go():
        cog, ctx, _ = setup(bot)
        ctx.message.delete = AsyncMock()
        for count, expected in ((1, "purged **1** message"), (12, "purged **12** messages")):
            ctx.channel.purge = AsyncMock(return_value=[object()] * count)
            await cog._purge(ctx, 50)
            assert said(ctx) == expected
    asyncio.run(go())


# ------------------------------------------------------------------ empty states say so

def test_empty_lists_are_amber_notes_not_blank_pages(bot):
    async def go():
        cog, ctx, target = setup(bot)
        await Moderation.warnings.callback(cog, ctx, target)
        assert said(ctx, "warn") == "<@50> has no warnings"
        await Moderation.cases.callback(cog, ctx, None)
        assert said(ctx, "warn") == "this server has no cases yet"
        await Moderation.cases.callback(cog, ctx, target)
        assert said(ctx, "warn") == "<@50> has no cases"
        await Moderation.jail_list.callback(cog, ctx)
        assert said(ctx, "warn") == "nobody is jailed"
        ctx.paginate = AsyncMock()
        assert not ctx.paginate.await_count
    asyncio.run(go())


def test_populated_lists_show_the_total_in_the_title(bot):
    async def go():
        cog, ctx, target = setup(bot)
        ctx.paginate = AsyncMock()
        bot.db.fetchall = AsyncMock(return_value=[
            {"id": 1, "mod_id": 2, "created_at": 0, "reason": "x"},
            {"id": 2, "mod_id": 2, "created_at": 0, "reason": "y"}])
        await Moderation.warnings.callback(cog, ctx, target)
        embeds = ctx.paginate.await_args.args[0]
        assert embeds[0].title.endswith("(2)")
    asyncio.run(go())


# ------------------------------------------------------------------ failures read as one red line

def test_failures_are_lower_case_red_lines():
    ctx = MagicMock(compact_action=None, compact_role=None)
    cases = [
        (UserError("nobody is timed out"), "failed: nobody is timed out"),
        (UserError("<@5> isn't banned"), "failed: <@5> isn't banned"),
        (UserError("you can't ban a bot developer"), "failed: you can't ban a bot developer"),
    ]
    for error, expected in cases:
        assert compact_failure(ctx, error) == expected


def test_nobody_to_ban_raises_a_user_error(bot):
    async def go():
        cog, ctx, _ = setup(bot)
        ctx.guild.members = []
        with pytest.raises(UserError, match="nobody joined in that window"):
            await Moderation.massban.callback(cog, ctx, 30)
    asyncio.run(go())
