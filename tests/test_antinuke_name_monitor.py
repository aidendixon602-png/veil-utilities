"""Opt-in, non-punitive monitoring of server name changes."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cogs.antinuke import Antinuke
from core.utils import UserError
from test_antinuke_undo import A, ATTACKER, OWNER, entry, feed, setup


def test_name_monitor_is_opt_in_reports_trusted_and_never_punishes():
    cog, guild, attacker = setup()
    dest = SimpleNamespace(send=AsyncMock())
    cog._log_channel = AsyncMock(return_value=dest)
    changed = entry(guild, A.guild_update, guild, user_id=OWNER,
                    before=SimpleNamespace(name="before"), after=SimpleNamespace(name="after"))
    feed(cog, changed)
    dest.send.assert_not_awaited()
    cog.bot.db.store["antinuke"]["name_change_monitor"] = True
    feed(cog, changed)
    dest.send.assert_awaited_once()
    card = dest.send.await_args.kwargs["embed"]
    assert "alert-only" in card.description and f"`{OWNER}`" in card.description
    attacker.remove_roles.assert_not_awaited()
    guild.ban.assert_not_awaited()
    guild.edit.assert_not_awaited()


def test_name_monitor_ignores_unavailable_values_and_disabled_antinuke():
    cog, guild, _ = setup()
    dest = SimpleNamespace(send=AsyncMock())
    cog._log_channel = AsyncMock(return_value=dest)
    cfg = cog.bot.db.store["antinuke"]
    cfg["name_change_monitor"] = True
    absent = entry(guild, A.guild_update, guild, before=SimpleNamespace(), after=SimpleNamespace(name="new"))
    feed(cog, absent)
    cfg["enabled"] = False
    changed = entry(guild, A.guild_update, guild, user_id=ATTACKER,
                    before=SimpleNamespace(name="a"), after=SimpleNamespace(name="b"))
    feed(cog, changed)
    dest.send.assert_not_awaited()


def test_name_monitor_configuration_is_owner_only_and_failed_writes_do_not_mutate_cache():
    async def run():
        cog, guild, _ = setup()
        ctx = SimpleNamespace(guild=guild, author=SimpleNamespace(id=ATTACKER), bot=cog.bot,
                              approve=AsyncMock(), neutral=AsyncMock())
        check = next(c for c in Antinuke.an_namemonitor.checks if getattr(c, "__veil_owner_only__", False))
        with pytest.raises(UserError):
            await check(ctx)
        ctx.author.id = OWNER
        assert await check(ctx)
        original = deepcopy(cog.bot.db.store["antinuke"])
        cog.bot.db.set_config_committed = AsyncMock(side_effect=RuntimeError("database down"))
        with pytest.raises(RuntimeError):
            await Antinuke.an_namemonitor.callback(cog, ctx, enabled=True)
        assert cog.bot.db.store["antinuke"] == original
        ctx.approve.assert_not_awaited()
    asyncio.run(run())


def test_name_monitor_configuration_persists_opt_in_and_opt_out():
    async def run():
        cog, guild, _ = setup()
        ctx = SimpleNamespace(guild=guild, author=SimpleNamespace(id=OWNER), bot=cog.bot,
                              approve=AsyncMock(), neutral=AsyncMock())

        async def save(gid, module, value):
            assert gid == guild.id and module == "antinuke"
            cog.bot.db.store[module] = value

        cog.bot.db.set_config_committed = AsyncMock(side_effect=save)
        await Antinuke.an_namemonitor.callback(cog, ctx, enabled=True)
        assert cog.bot.db.store["antinuke"]["name_change_monitor"] is True
        await Antinuke.an_namemonitor.callback(cog, ctx, enabled=None)
        assert "**on**" in ctx.neutral.await_args.args[0]
        await Antinuke.an_namemonitor.callback(cog, ctx, enabled=False)
        assert cog.bot.db.store["antinuke"]["name_change_monitor"] is False
    asyncio.run(run())


def test_name_monitor_delivery_failure_does_not_block_existing_event_processing():
    cog, guild, _ = setup()
    cog.bot.db.store["antinuke"]["name_change_monitor"] = True
    cog._log_channel = AsyncMock(side_effect=RuntimeError("log unavailable"))
    changed = entry(guild, A.guild_update, guild, user_id=OWNER,
                    before=SimpleNamespace(name="a"), after=SimpleNamespace(name="b"))
    feed(cog, changed)
    guild.ban.assert_not_awaited()
