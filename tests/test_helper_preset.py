"""Tests for narrow Helper moderation actions and their safety limits."""
import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from cogs.moderation import Moderation
from cogs.permissions import Permissions
from core import perms
from core.utils import UserError, missing_perms


def make_context(level="helper", *, overrides=None, fake=None):
    cfg = {"users": {"20": level}, "overrides": overrides or {}}
    store = {"permlevels": cfg, "fakeperms": fake or {}, "warnpunish": {"1": "ban"}}
    db = SimpleNamespace(get_config=AsyncMock(side_effect=lambda gid, module, default=None: store[module]),
                         execute=AsyncMock(), fetchone=AsyncMock(return_value={"c": 1}),
                         set_config_committed=AsyncMock())
    bot = SimpleNamespace(db=db, owner_ids=set(), owner_id=None, is_owner=AsyncMock(return_value=False))
    author = SimpleNamespace(id=20, roles=[SimpleNamespace(id=55, name="staff", mention="<@&55>")])
    ctx = SimpleNamespace(bot=bot, author=author, guild=SimpleNamespace(id=1, owner_id=10),
                          command=None, approve=AsyncMock(), warn=AsyncMock())
    return ctx, store


def test_default_actions_and_explicit_overrides():
    async def run():
        ctx, store = make_context()
        assert not await missing_perms(ctx, veil_warn=True, veil_timeout=True, veil_cases=True)
        assert await missing_perms(ctx, manage_messages=True) == ["manage_messages"]
        store["permlevels"]["overrides"] = {"helper": {"perms": ["manage_messages"], "actions": ["veil_cases"]}}
        assert not await missing_perms(ctx, manage_messages=True, veil_cases=True)
        assert await missing_perms(ctx, veil_warn=True, veil_timeout=True) == ["veil_warn", "veil_timeout"]
        # Explicit fake moderation permission retains access, without granting it to everyone.
        store["fakeperms"] = {"55": ["moderate_members"]}
        assert not await missing_perms(ctx, veil_warn=True, veil_timeout=True)
        store["fakeperms"] = {}
        ctx.bot.db.get_config.side_effect = RuntimeError("offline")
        with pytest.raises(RuntimeError, match="offline"):
            await missing_perms(ctx, veil_warn=True)
    asyncio.run(run())


def test_helper_timeout_cap_and_fake_permission_cannot_bypass_it():
    async def run():
        ctx, _ = make_context(fake={"55": ["moderate_members"]})
        cog = Moderation(ctx.bot)
        member = SimpleNamespace(mention="<@30>", timeout=AsyncMock())
        functions = {"check_hierarchy_async": AsyncMock(), "antinuke_guard": AsyncMock(),
                     "dm_target": AsyncMock(), "create_case": AsyncMock(return_value=1)}
        with patch.dict(Moderation.timeout.callback.__globals__, functions):
            await Moderation.timeout.callback(cog, ctx, member, timedelta(hours=1), reason="reason")
            member.timeout.assert_awaited_once()
            member.timeout.reset_mock()
            with pytest.raises(UserError, match="at most 1 hour"):
                await Moderation.timeout.callback(cog, ctx, member, timedelta(hours=1, seconds=1))
            member.timeout.assert_not_awaited()
            ctx.bot.db.get_config.side_effect = RuntimeError("offline")
            with pytest.raises(RuntimeError, match="offline"):
                await Moderation.timeout.callback(cog, ctx, member, timedelta(minutes=1))
            member.timeout.assert_not_awaited()
    asyncio.run(run())


def test_helper_warning_does_not_trigger_auto_ban():
    async def run():
        ctx, _ = make_context()
        member = SimpleNamespace(id=30, mention="<@30>", ban=AsyncMock(), kick=AsyncMock(), timeout=AsyncMock())
        cog = Moderation(ctx.bot)
        guard = AsyncMock()
        functions = {"check_hierarchy_async": AsyncMock(), "antinuke_guard": guard,
                     "dm_target": AsyncMock(), "create_case": AsyncMock(return_value=1)}
        with patch.dict(Moderation.warn.callback.__globals__, functions):
            await Moderation.warn.callback(cog, ctx, member, reason="reason")
        member.ban.assert_not_awaited()
        member.kick.assert_not_awaited()
        member.timeout.assert_not_awaited()
        guard.assert_not_awaited()
        ctx.bot.db.execute.assert_awaited_once()
        assert "not applied" in ctx.warn.await_args.args[0]
    asyncio.run(run())


def test_action_toggle_persists_and_failed_write_does_not_mutate_cached_configuration():
    async def run():
        ctx, store = make_context()
        ctx.guild.owner_id = ctx.author.id
        cog = Permissions(ctx.bot)
        await Permissions.pl_action.callback(cog, ctx, "helper", "veil_warn")
        saved = ctx.bot.db.set_config_committed.await_args.args[2]
        assert "veil_warn" not in perms.all_levels(saved)["helper"]["actions"]
        assert "veil_warn" in perms.all_levels(store["permlevels"])["helper"]["actions"]
        ctx.bot.db.set_config_committed.side_effect = RuntimeError("offline")
        with pytest.raises(RuntimeError, match="offline"):
            await Permissions.pl_action.callback(cog, ctx, "helper", "veil_timeout")
        assert "veil_timeout" in perms.all_levels(store["permlevels"])["helper"]["actions"]
    asyncio.run(run())


def test_real_command_checks_match_helper_scope():
    from discord.ext import commands

    async def allowed(ctx, cmd):
        ctx.command = cmd
        try:
            for predicate in cmd.checks:
                # Test the bot's own Discord permissions separately from user authority.
                if "bot_has_permissions" not in predicate.__qualname__:
                    await predicate(ctx)
            return True
        except (UserError, commands.CommandError):
            return False

    async def run():
        ctx, store = make_context()
        for cmd in (Moderation.warn, Moderation.timeout, Moderation.cases, Moderation.warnings):
            assert await allowed(ctx, cmd), cmd.name
        for cmd in (Moderation.kick, Moderation.purge, Moderation.untimeout, Moderation.clearwarns):
            assert not await allowed(ctx, cmd), cmd.name
        store["permlevels"]["overrides"] = {"helper": {"actions": ["veil_cases"]}}
        assert not await allowed(ctx, Moderation.warn)
        assert not await allowed(ctx, Moderation.timeout)
        assert await allowed(ctx, Moderation.cases)
        store["permlevels"] = {"roles": {"55": "helper"}}
        assert await allowed(ctx, Moderation.warn)
        store["permlevels"] = {"users": {"20": "moderator"}}
        assert await allowed(ctx, Moderation.kick)
        assert await allowed(ctx, Moderation.purge)
    asyncio.run(run())


def test_moderator_timeout_and_warning_escalation_preserve_existing_behavior():
    async def run():
        ctx, _ = make_context("moderator")
        member = SimpleNamespace(id=30, mention="<@30>", timeout=AsyncMock(), ban=AsyncMock())
        cog = Moderation(ctx.bot)
        functions = {"check_hierarchy_async": AsyncMock(), "antinuke_guard": AsyncMock(),
                     "dm_target": AsyncMock(), "create_case": AsyncMock(return_value=1)}
        with patch.dict(Moderation.timeout.callback.__globals__, functions):
            await Moderation.timeout.callback(cog, ctx, member, timedelta(hours=2))
            member.timeout.assert_awaited_once()
            await Moderation.warn.callback(cog, ctx, member, reason="reason")
            member.ban.assert_awaited_once()
    asyncio.run(run())
