"""Automatic setup (only what a server never set up itself), automod escalation, and the ,mod / ,admin panels
(which run the real commands with their real checks)."""
import asyncio
import json
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from core import autosetup
from core.staffpanel import ADMIN_MEMBER_ACTIONS, MOD_ACTIONS, ActionButton, StaffPanel, run_command
from core.utils import UserError


class FakeDB:
    def __init__(self, saved=None):
        self.store = {k: v for k, v in (saved or {}).items()}

    async def fetchone(self, query, gid, module):
        return {"x": 1} if module in self.store else None

    async def get_config(self, gid, module, default=None):
        return self.store.setdefault(module, json.loads(json.dumps(default)) if default else {})

    async def set_config(self, gid, module, data):
        self.store[module] = data


class Role(SimpleNamespace):
    def is_default(self):
        return self.id == 1


def guild_with(*names, manage_guild=False):
    roles = [Role(id=1, name="@everyone", managed=False)] + [
        Role(id=10 + i, name=n, managed=n.endswith("bot")) for i, n in enumerate(names)]
    return SimpleNamespace(id=5, name="S", roles=roles, owner=None,
                           me=SimpleNamespace(guild_permissions=discord.Permissions(manage_guild=manage_guild)))


def bot_with(db):
    from cogs.antinuke import Antinuke
    bot = SimpleNamespace(db=db, user=SimpleNamespace(id=4), owner_ids=set(), owner_id=None)
    an = Antinuke.__new__(Antinuke)
    Antinuke.__init__(an, bot)
    bot.get_cog = lambda name: an if name == "Antinuke" else None
    return bot


# ------------------------------------------------------------------ automatic setup

def test_a_fresh_server_gets_everything():
    db = FakeDB()
    done = asyncio.run(autosetup.run(bot_with(db), guild_with("Moderator", "• Admin •", "Supreme Leader", "MEE6 bot")))
    assert db.store["antinuke"]["enabled"] and db.store["antinuke"]["modules"]["ban"]["threshold"] == 3
    assert db.store["antiraid"]["massjoin"] == {"joins": 10, "seconds": 10}
    assert db.store["filters"]["spam"] == [6, 5] and db.store["filters"]["escalate"]["hits"] == 3
    assert db.store["warnpunish"] == {"3": "timeout:3600", "5": "kick"}
    # role names map to levels, but never above Head Admin (Supreme Leader is left for the owner to give)
    assert db.store["permlevels"]["roles"] == {"10": "moderator", "11": "admin"}
    assert len(done) == 5


def test_whatever_a_server_set_up_itself_is_left_alone():
    mine = {"enabled": False, "modules": {"ban": {"on": False}}}
    db = FakeDB({"antinuke": mine, "filters": {"invites": False}, "warnpunish": {},
                 "antiraid": {}, "permlevels": {"users": {"7": "admin"}}})
    done = asyncio.run(autosetup.run(bot_with(db), guild_with("Moderator")))
    assert done == [] and db.store["antinuke"] == mine and db.store["filters"] == {"invites": False}
    assert db.store["permlevels"] == {"users": {"7": "admin"}}


def test_it_runs_once_per_server_unless_asked_again(monkeypatch):
    db = FakeDB()
    bot = bot_with(db)
    monkeypatch.setattr(autosetup, "announce", AsyncMock())
    guild = guild_with()
    assert asyncio.run(autosetup.run_once(bot, guild))
    assert db.store["autosetup"] == {"done": True}
    del db.store["warnpunish"]
    assert asyncio.run(autosetup.run_once(bot, guild)) == []  # already done
    assert asyncio.run(autosetup.run_once(bot, guild, force=True)) == ["warnings: 3 = 1 hour timeout, 5 = kick · `,warnpunish`"]


def test_discords_own_slur_filter_is_switched_on_once():
    guild = guild_with(manage_guild=True)
    guild.fetch_automod_rules = AsyncMock(return_value=[])
    guild.create_automod_rule = AsyncMock()
    assert asyncio.run(autosetup._slur_filter(guild)) == "Discord's AutoMod slur filter"
    kw = guild.create_automod_rule.await_args.kwargs
    assert kw["trigger"].presets.slurs and kw["enabled"]
    existing = SimpleNamespace(trigger=SimpleNamespace(type=discord.AutoModRuleTriggerType.keyword_preset))
    guild.fetch_automod_rules = AsyncMock(return_value=[existing])
    assert asyncio.run(autosetup._slur_filter(guild)) is None
    assert asyncio.run(autosetup._slur_filter(guild_with())) is None  # no Manage Server: skipped quietly


# ------------------------------------------------------------------ automod escalation

def test_repeat_filter_hits_lead_to_a_timeout():
    from cogs.automod import Automod
    cog = Automod.__new__(Automod)
    cog.hits = __import__("collections").defaultdict(deque)
    cfg = {"escalate": {"hits": 3, "within": 600, "timeout": 600}}
    m = SimpleNamespace(guild=SimpleNamespace(id=1), author=SimpleNamespace(id=2))
    assert [cog.escalates(cfg, m) for _ in range(4)] == [False, False, True, False]  # the count starts over
    assert cog.escalates({}, m) is False


# ------------------------------------------------------------------ the panels

def _ctx(allowed: set[str]):
    async def can_run(name, ctx):
        if name not in allowed:
            raise UserError("no")
        return True
    cmds = {}
    for label, name, *_ in MOD_ACTIONS + ADMIN_MEMBER_ACTIONS:
        cmd = SimpleNamespace(qualified_name=name, name=name)
        cmd.can_run = (lambda n: (lambda ctx: can_run(n, ctx)))(name)
        cmds[name] = cmd
    bot = SimpleNamespace(get_command=cmds.get, dispatch=MagicMock())
    return SimpleNamespace(bot=bot, author=SimpleNamespace(id=9), command="mod-cmd", invoke=AsyncMock())


def test_buttons_you_cant_use_are_greyed_out():
    ctx = _ctx({"warn", "timeout", "untimeout", "warnings"})  # a Helper/Moderator, say
    view = asyncio.run(StaffPanel.build(ctx, SimpleNamespace(id=50), "Moderator panel", MOD_ACTIONS))
    state = {b.label: b.disabled for b in view.children if isinstance(b, ActionButton)}
    assert state == {"Warn": False, "Timeout": False, "Untimeout": False, "Kick": True, "Jail": True,
                     "Unjail": True, "Warnings": False}


def test_a_button_runs_the_real_command_for_that_member():
    ctx = _ctx({"ban"})
    member = SimpleNamespace(id=50)
    view = asyncio.run(StaffPanel.build(ctx, member, "Admin panel", ADMIN_MEMBER_ACTIONS))
    it = SimpleNamespace(response=SimpleNamespace(is_done=lambda: False, defer=AsyncMock()),
                         followup=SimpleNamespace(send=AsyncMock()))
    asyncio.run(view.act(it, "ban", {"reason": "raid"}))
    cmd, kwargs = ctx.invoke.await_args.args[0], ctx.invoke.await_args.kwargs
    assert cmd.qualified_name == "ban" and kwargs == {"reason": "raid", "user": member}  # ban calls it `user`
    assert ctx.command == "mod-cmd"  # put back afterwards
    ctx.bot.dispatch.assert_called_once_with("command_completion", ctx)  # it shows in the command log


def test_a_blocked_action_says_why_privately():
    ctx = _ctx(set())
    view = asyncio.run(StaffPanel.build(ctx, SimpleNamespace(id=50), "Moderator panel", MOD_ACTIONS))
    it = SimpleNamespace(response=SimpleNamespace(is_done=lambda: False, defer=AsyncMock()),
                         followup=SimpleNamespace(send=AsyncMock()))
    asyncio.run(view.act(it, "kick", {}))
    ctx.invoke.assert_not_awaited()
    assert it.followup.send.await_args.kwargs["ephemeral"] is True
    with pytest.raises(UserError):
        asyncio.run(run_command(ctx, "nope"))


# ------------------------------------------------------------------ who hears about the automatic setup

def _announce_setup(dm_owner):
    log_channel, owner = SimpleNamespace(send=AsyncMock()), SimpleNamespace(send=AsyncMock())
    an = SimpleNamespace(_log_channel=AsyncMock(return_value=log_channel), cfg=AsyncMock(return_value={}))
    bot = SimpleNamespace(get_cog=lambda name: an)
    guild = SimpleNamespace(id=5, name="Midtown", icon=None, owner=owner)
    asyncio.run(autosetup.announce(bot, guild, ["raid detection"], dm_owner=dm_owner))
    return log_channel, owner


def test_servers_the_bot_was_already_in_dont_dm_the_owner():
    log_channel, owner = _announce_setup(dm_owner=False)  # a restart: one DM per server would be spam
    log_channel.send.assert_awaited_once()
    owner.send.assert_not_called()
    assert log_channel.send.await_args.kwargs["embed"].author.name == "Midtown"


def test_a_server_just_joined_tells_its_owner_once():
    log_channel, owner = _announce_setup(dm_owner=True)
    log_channel.send.assert_awaited_once()
    owner.send.assert_awaited_once()
