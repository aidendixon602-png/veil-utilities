"""Antinuke must also count destructive actions done *through bot commands* (bot permission levels / fake perms).

Discord's audit log credits those actions to the bot, which the audit-log listener ignores — so before this
guard, a member with a bot-granted level could ban or delete freely without antinuke ever noticing.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cogs.antinuke import Antinuke
from core.db import Database
from core.utils import UserError

OWNER, DEV, MOD, BOT = 1, 2, 3, 4


class FakeBot:
    def __init__(self, db):
        self.db = db
        self.user = SimpleNamespace(id=BOT)
        self.owner_ids = {DEV}
        self.owner_id = None
        self.dms = []

    async def is_owner(self, user):
        return user.id in self.owner_ids

    def get_user(self, _):
        return None

    async def fetch_user(self, uid):
        user = SimpleNamespace(id=uid, name=f"user{uid}", bot=False, send=AsyncMock(),
                               display_avatar=SimpleNamespace(url="https://cdn.example/a.png"))
        self.dms.append(user)
        return user


def make_ctx(author_id: int, guild):
    return SimpleNamespace(guild=guild, author=SimpleNamespace(id=author_id),
                           command=SimpleNamespace(qualified_name="ban"))


OPEN_DBS = []


def run(coro_fn):
    """asyncio.run, but always close the aiosqlite connections (their threads otherwise hang pytest)."""
    async def wrapper():
        try:
            await coro_fn()
        finally:
            while OPEN_DBS:
                await OPEN_DBS.pop().close()
    asyncio.run(wrapper())


async def setup(antinuke_cfg: dict):
    db = Database(":memory:")
    await db.connect()
    OPEN_DBS.append(db)
    bot = FakeBot(db)
    cog = Antinuke.__new__(Antinuke)
    Antinuke.__init__(cog, bot)
    cfg = {"enabled": True, "punishment": "ban", "window": 60, "whitelist": [], "admins": [], "log": None,
           "modules": {"ban": {"on": True, "threshold": 3}, "permissions": {"on": True, "threshold": 1}}}
    cfg.update(antinuke_cfg)
    await db.set_config(10, "antinuke", cfg)
    await db.set_config(10, "permlevels", {"users": {str(MOD): "admin"}, "overrides": {}})
    guild = SimpleNamespace(id=10, owner_id=OWNER, name="test", owner=None, get_member=lambda _id: None,
                            get_channel=lambda _id: None, ban=AsyncMock())
    return db, cog, guild


def test_bot_mediated_bans_hit_the_threshold_and_get_punished():
    async def _run():
        db, cog, guild = await setup({})
        ctx = make_ctx(MOD, guild)
        await cog.guard(ctx, "ban")          # 1st ban allowed
        await cog.guard(ctx, "ban")          # 2nd ban allowed
        with pytest.raises(UserError, match="Antinuke blocked"):
            await cog.guard(ctx, "ban")      # 3rd would reach the threshold → blocked
        guild.ban.assert_awaited_once()      # attacker punished (not in member cache → ban by ID)
        assert guild.ban.await_args.args[0].id == MOD
        levels = await db.get_config(10, "permlevels")
        assert str(MOD) not in levels["users"]   # bot-granted level revoked
        dev = next(u for u in cog.bot.dms if u.id == DEV)
        dev.send.assert_awaited_once()             # developers get the global alert
        alert = dev.send.await_args.kwargs["embed"]
        assert "using `ban`" in alert.description  # done through the bot's own command
    run(_run)


@pytest.mark.parametrize("trusted", [OWNER, DEV, "whitelist", "admin"])
def test_trusted_users_are_not_limited(trusted):
    async def _run():
        extra = {"whitelist": [MOD]} if trusted == "whitelist" else {"admins": [MOD]} if trusted == "admin" else {}
        _, cog, guild = await setup(extra)
        uid = MOD if isinstance(trusted, str) else trusted
        for _ in range(10):
            await cog.guard(make_ctx(uid, guild), "ban")
        guild.ban.assert_not_awaited()
    run(_run)


def test_disabled_antinuke_or_module_does_nothing():
    async def _run():
        for cfg in ({"enabled": False}, {"modules": {"ban": {"on": False, "threshold": 3}}}):
            _, cog, guild = await setup(cfg)
            for _ in range(10):
                await cog.guard(make_ctx(MOD, guild), "ban")
            guild.ban.assert_not_awaited()
    run(_run)


def test_oversized_bulk_request_is_refused_without_punishment():
    async def _run():
        _, cog, guild = await setup({})
        with pytest.raises(UserError, match="at once"):
            await cog.guard(make_ctx(MOD, guild), "ban", amount=25)   # e.g. a massban
        guild.ban.assert_not_awaited()
        await cog.guard(make_ctx(MOD, guild), "ban")                   # normal use still works afterwards
    run(_run)


def test_granting_staff_permissions_is_instant():
    async def _run():
        _, cog, guild = await setup({})
        with pytest.raises(UserError, match="Antinuke blocked"):
            await cog.guard(make_ctx(MOD, guild), "permissions")
        # permission grants are answered by stripping, never a ban (even when the member isn't cached)
        guild.ban.assert_not_awaited()
        dev = next(u for u in cog.bot.dms if u.id == DEV)
        alert = dev.send.await_args.kwargs["embed"]
        assert "**Punishment:** Nothing to strip" in alert.description
    run(_run)


def test_direct_and_bot_actions_share_one_counter():
    async def _run():
        _, cog, guild = await setup({})
        import time
        cog.hits[(10, MOD, "ban")].extend([time.monotonic()] * 2)    # 2 bans done directly in Discord
        with pytest.raises(UserError):
            await cog.guard(make_ctx(MOD, guild), "ban")             # + 1 through the bot = 3
    run(_run)
