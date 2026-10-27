"""What ,help shows each permission level is exactly what that level can run, and every level up sees and can run
strictly more. Loads the real bot and runs every command's real checks for a member at each level."""
import asyncio
import logging
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest
from discord.ext import commands

from core import help as H
from core import perms as P
from core.utils import MissingRank, UserError, level_gate

LEVELS = ["helper", "moderator", "admin", "headadmin", "overseer", "supremeleader"]
MEMBER = 500


def _audit():
    async def go():
        saved = {k: os.environ.get(k) for k in ("DB_PATH", "OWNER_IDS", "DISCORD_TOKEN")}
        os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "levels.db")
        os.environ["OWNER_IDS"] = "1"
        os.environ.setdefault("DISCORD_TOKEN", "x")
        logging.disable(logging.CRITICAL)
        import core.bot
        from core.config import Settings
        saved_settings, core.bot.settings = core.bot.settings, Settings.from_env()
        bot = core.bot.Veil()
        try:
            await bot._async_setup_hook()
            await bot.setup_hook()
            role = SimpleNamespace(id=900, name="r", is_default=lambda: False, mention="<@&900>", position=1)
            guild = SimpleNamespace(id=42, owner_id=7, me=None, roles=[], get_member=lambda i: None, name="g")
            chan = SimpleNamespace(id=3, permissions_for=lambda m: discord.Permissions.none(), name="c")
            author = SimpleNamespace(id=MEMBER, roles=[role], guild_permissions=discord.Permissions.none(), bot=False,
                                     top_role=role, mention="<@500>", name="m", display_name="m")
            result = {}
            for key in LEVELS:
                await bot.db.set_config(42, "permlevels", {"users": {str(MEMBER): key}, "roles": {}})
                rank = P.PRESETS[key][1]
                cats = set(H.category_labels({c: [1] for c, _, _ in H.CATEGORIES}, False, rank))
                shown, runs, mismatches = set(), set(), []
                for cmd in bot.walk_commands():
                    if cmd.hidden or any(p.hidden for p in cmd.parents) or cmd.cog_name in H.HIDDEN_COGS:
                        continue
                    ctx = SimpleNamespace(bot=bot, guild=guild, author=author, channel=chan, command=cmd, cog=cmd.cog,
                                          message=SimpleNamespace(id=1), clean_prefix=",", prefix=",")
                    try:
                        ok = all([await check(ctx) for check in cmd.checks])
                    except (UserError, commands.CommandError):
                        ok = False
                    except Exception:
                        continue  # a check that needs real Discord objects, unrelated to levels
                    cat = H.category_for_command(cmd, bot)
                    staff = H.CATEGORY_MIN_RANKS.get(cat, 0) > 0
                    visible = (not staff or cat in cats) and rank >= H.min_rank_for_command(cmd, bot)
                    if visible:
                        shown.add(cmd.qualified_name)
                    if ok:
                        runs.add(cmd.qualified_name)
                    if staff and cmd.checks and ok != visible:
                        mismatches.append((cmd.qualified_name, "runs but hidden" if ok else "shown but blocked"))
                result[key] = (shown, runs, mismatches)
            return result
        finally:
            await bot.close()
            core.bot.settings = saved_settings
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            logging.disable(logging.NOTSET)
    return asyncio.run(go())


@pytest.fixture(scope="module")
def audit():
    return _audit()


def test_help_shows_each_level_exactly_what_it_can_run(audit):
    for key in LEVELS:
        assert audit[key][2] == [], (key, audit[key][2])


def test_every_level_up_sees_and_can_run_strictly_more(audit):
    for lower, higher in zip(LEVELS, LEVELS[1:]):
        assert audit[lower][0] < audit[higher][0], (lower, higher)  # shown in help
        assert audit[lower][1] <= audit[higher][1], (lower, higher)  # can run (never less)


def test_each_level_has_every_permission_its_rank_needs():
    for key, (name, rank, perms) in P.PRESETS.items():
        needed = {p for p, r in H.PERM_RANKS.items() if r <= rank}
        assert needed <= set(perms) or "administrator" in perms, (key, needed - set(perms))


def _gate_ctx(level, category_cog, fake=None):
    store = {"permlevels": {"users": {"5": level}} if level else {}, "fakeperms": fake or {}}
    bot = SimpleNamespace(db=SimpleNamespace(get_config=AsyncMock(side_effect=lambda g, m: store[m])),
                          owner_ids=set(), owner_id=None, get_command=lambda n: None)
    cmd = SimpleNamespace(name="x", qualified_name="x", root_parent=None, cog_name=category_cog)
    return SimpleNamespace(bot=bot, guild=SimpleNamespace(id=1, owner_id=99), command=cmd,
                           author=SimpleNamespace(id=5, roles=[SimpleNamespace(id=8)]))


def test_the_level_gate():
    with pytest.raises(MissingRank, match="Overseer"):
        asyncio.run(level_gate(_gate_ctx("headadmin", "Permissions"), ["manage_guild"]))
    asyncio.run(level_gate(_gate_ctx("headadmin", "Automod"), ["manage_guild"]))  # high enough
    asyncio.run(level_gate(_gate_ctx("helper", "Moderation"), []))  # Moderator category: Helper is enough
    asyncio.run(level_gate(_gate_ctx(None, "Automod"), ["manage_guild"]))  # no level: Discord perms rules apply
    asyncio.run(level_gate(_gate_ctx("headadmin", "Automod", {"8": ["manage_guild"]}), ["manage_guild"]))  # fakeperms
    asyncio.run(level_gate(_gate_ctx("helper", "Utility"), []))  # public categories have no gate
    owner = _gate_ctx("helper", "Automod")
    owner.guild.owner_id = 5
    asyncio.run(level_gate(owner, ["manage_guild"]))


def test_owner_only_commands_are_shown_to_the_owner_only():
    assert H.rank_name(P.OWNER_RANK) == "Owner" and H.rank_name(P.DEV_RANK) == "Developer"
    assert H.rank_name(6) == "Overseer" and H.rank_name(4) == "Head Admin"
