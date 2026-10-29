"""Raid mode: a join wave pauses invites and DMs (and locks channels when that's on), alerts the antinuke log channel
and the owner, and undoes exactly what it did once joins go quiet or someone ends it."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

import cogs.antinuke as an
from cogs.antinuke import Antinuke


class FakeDB:
    def __init__(self, **store):
        self.store = store

    async def get_config(self, gid, module, default=None):
        # a deep copy, like core.db: the default must never be shared between servers
        return self.store.setdefault(module, {} if default is None else json.loads(json.dumps(default)))

    async def set_config(self, gid, module, data):
        self.store[module] = data


def channel(cid, locked=False):
    ow = discord.PermissionOverwrite(send_messages=False if locked else None)
    ch = SimpleNamespace(id=cid, name=f"c{cid}", ow=ow)
    ch.overwrites_for = lambda target: discord.PermissionOverwrite(**{k: v for k, v in ch.ow if v is not None})

    async def set_permissions(target, overwrite=None, reason=None):
        ch.ow = overwrite
    ch.set_permissions = AsyncMock(side_effect=set_permissions)
    return ch


def setup(antiraid=None):
    log = SimpleNamespace(id=50, send=AsyncMock())
    general, rules, announcements = channel(1), channel(2), channel(3, locked=True)
    channels = {50: log, 1: general, 2: rules, 3: announcements}
    guild = SimpleNamespace(id=10, name="Test", owner=SimpleNamespace(send=AsyncMock()), default_role=object(),
                            text_channels=[general, rules, announcements], edit=AsyncMock(),
                            get_channel=channels.get, me=SimpleNamespace(top_role=SimpleNamespace(position=50)))
    db = FakeDB(antinuke={"enabled": True, "log": 50, "modules": {}}, antiraid=dict(antiraid or {}))
    bot = SimpleNamespace(db=db, get_cog=lambda _name: None, guilds=[guild], user=SimpleNamespace(id=4))
    cog = Antinuke.__new__(Antinuke)
    Antinuke.__init__(cog, bot)
    return cog, guild, log, (general, rules, announcements)


def titles(dest):
    return [c.kwargs["embed"].title for c in dest.send.await_args_list]


def run(coro):
    async def go():
        try:
            return await coro
        finally:
            for task in list(asyncio.all_tasks()):
                if task is not asyncio.current_task():
                    task.cancel()
    return asyncio.run(go())


def test_raid_mode_pauses_invites_and_dms_and_tells_the_log_channel_and_owner():
    cog, guild, log, _ = setup()
    done = run(cog.start_raid(guild, "**10** members joined within 10s"))
    kwargs = guild.edit.await_args.kwargs
    assert kwargs["invites_disabled_until"] > discord.utils.utcnow()
    assert kwargs["dms_disabled_until"] == kwargs["invites_disabled_until"]
    assert done == ["paused invites and DMs between members"]
    assert titles(log) == ["🚨 Raid mode on"] and titles(guild.owner) == ["🚨 Raid mode on"]
    assert "paused invites" in log.send.await_args.kwargs["embed"].fields[0].value
    assert cog.bot.db.store["antiraid"]["raid_state"]["invites"] is True


def test_channels_are_locked_only_when_turned_on_and_only_open_ones():
    cog, guild, log, (general, rules, announcements) = setup({"lockdown": {"channels": True}})
    run(cog.start_raid(guild, "wave"))
    assert general.ow.send_messages is False and rules.ow.send_messages is False
    announcements.set_permissions.assert_not_awaited()  # it was locked already: not ours to touch
    assert cog.bot.db.store["antiraid"]["raid_state"]["locked"] == [1, 2]

    done = run(cog.end_raid(guild))
    assert general.ow.send_messages is None and rules.ow.send_messages is None
    assert announcements.ow.send_messages is False  # still locked, as the server had it
    assert "unlocked 2 channel(s)" in done


def test_ending_raid_mode_resumes_invites_and_clears_the_state():
    cog, guild, log, _ = setup()
    run(cog.start_raid(guild, "wave"))
    done = run(cog.end_raid(guild, "ended by mod"))
    assert guild.edit.await_args.kwargs == {"invites_disabled_until": None, "dms_disabled_until": None,
                                            "reason": "Antiraid: raid over"}
    assert "raid_state" not in cog.bot.db.store["antiraid"]
    assert done == ["resumed invites and DMs"]
    assert titles(log) == ["🚨 Raid mode on", "✅ Raid mode off"]


def test_a_second_wave_extends_raid_mode_instead_of_starting_again():
    cog, guild, log, _ = setup()
    run(cog.start_raid(guild, "wave"))
    again = run(cog.start_raid(guild, "wave 2"))
    assert guild.edit.await_count == 1 and titles(log) == ["🚨 Raid mode on"]
    assert "already on" in again[0]


def test_raid_mode_ends_by_itself_once_joins_go_quiet(monkeypatch):
    monkeypatch.setattr(an, "RAID_QUIET", 0.05)
    cog, guild, log, _ = setup()

    async def go():
        await cog.start_raid(guild, "wave")
        await asyncio.wait_for(cog.raid_tasks[guild.id], timeout=2)
    asyncio.run(go())
    assert "raid_state" not in cog.bot.db.store["antiraid"]
    assert titles(log)[-1] == "✅ Raid mode off"


def test_no_permission_to_pause_invites_is_reported():
    cog, guild, log, _ = setup()
    guild.edit.side_effect = discord.Forbidden(MagicMock(status=403, reason="Forbidden"), "Missing Permissions")
    done = run(cog.start_raid(guild, "wave"))
    assert done[0].startswith("⚠️ couldn't pause invites") and "Manage Server" in done[0]
    assert cog.bot.db.store["antiraid"]["raid_state"]["invites"] is False


def test_raid_mode_is_picked_up_again_after_a_restart():
    cog, guild, log, _ = setup({"raid_state": {"since": 1, "invites": True, "locked": []}})

    async def go():
        await cog.on_ready()
        assert guild.id in cog.raid_tasks and cog.raid_until[guild.id] > 0
    run(go())


def test_mass_join_starts_raid_mode():
    cog, guild, log, _ = setup({"massjoin": {"joins": 3, "seconds": 10}, "punishment": "kick"})
    members = []
    for uid in range(100, 103):
        m = SimpleNamespace(id=uid, bot=False, guild=guild, avatar="a", kick=AsyncMock(),
                            created_at=discord.utils.utcnow())
        members.append(m)
    guild.get_member = lambda uid: next((m for m in members if m.id == uid), None)

    async def go():
        for m in members:
            await cog.on_member_join(m)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    run(go())
    assert all(m.kick.await_count == 1 for m in members)  # the whole wave
    assert "🚨 Raid mode on" in titles(log)


def test_raid_commands_turn_the_lockdown_options_on_and_off():
    cog, guild, log, _ = setup()
    ctx = SimpleNamespace(guild=guild, approve=AsyncMock(), author=SimpleNamespace(mention="<@1>"))
    run(Antinuke.ar_lockdown.callback(cog, ctx, "channels", "on"))
    assert cog.bot.db.store["antiraid"]["lockdown"] == {"invites": True, "channels": True}
    run(Antinuke.ar_lockdown.callback(cog, ctx, "invites", "off"))
    assert cog.bot.db.store["antiraid"]["lockdown"] == {"invites": False, "channels": True}
    with pytest.raises(an.UserError):
        run(Antinuke.ar_lockdown.callback(cog, ctx, "roles", "on"))
