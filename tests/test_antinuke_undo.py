"""When the antinuke catches someone it puts the server back: unbans their victims, rebuilds deleted channels and
roles (with members and overwrites), deletes what they created, and keeps undoing anything still in flight."""
import asyncio
import itertools
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

import core.nukerestore as restore
from cogs.antinuke import Antinuke
from core.nukerestore import collapse

A = discord.AuditLogAction
BOT_ID, OWNER, ATTACKER, VICTIM = 4, 1, 5, 6
ids = itertools.count(1000)


class FakeDB:
    def __init__(self, antinuke):
        self.store = {"antinuke": antinuke}

    async def get_config(self, gid, module, default=None):
        return self.store.setdefault(module, {} if default is None else dict(default))

    async def set_config(self, gid, module, data):
        self.store[module] = data


class Obj(SimpleNamespace):
    """Hashable, because permission overwrites are keyed by role/member objects."""
    __hash__ = object.__hash__

    def __eq__(self, other):
        return self is other


class Ow:
    def __init__(self, target_id, kind, allow, deny):
        self.id, self.type, self.allow, self.deny = target_id, kind, allow, deny


def make_role(guild, rid, name, position, **perms):
    role = Obj(id=rid, name=name, position=position, managed=False, hoist=True, mentionable=False,
                           permissions=discord.Permissions(**perms), colour=discord.Colour(0x123456), guild=guild,
                           members=[], edit=AsyncMock(), delete=AsyncMock())
    return role


class Guild:
    """Just enough of a guild: channels, roles and members it can create, find and delete."""

    def __init__(self):
        self.id, self.name, self.owner_id = 10, "Test", OWNER
        self.channels_by_id, self.roles_by_id, self.members_by_id = {}, {}, {}
        self.me = SimpleNamespace(top_role=SimpleNamespace(position=50))
        self.default_role = Obj(id=self.id, name="@everyone")
        self.sent = []
        self.owner = SimpleNamespace(id=OWNER, send=AsyncMock(side_effect=self._owner_send))
        self.ban, self.unban, self.edit = AsyncMock(), AsyncMock(), AsyncMock()
        self.text_channels = []
        self.made_channels, self.made_roles = [], []

    async def _owner_send(self, **kw):
        msg = MagicMock()
        msg.edit = AsyncMock()
        self.sent.append((kw, msg))
        return msg

    @property
    def channels(self):
        return list(self.channels_by_id.values())

    def get_channel(self, cid):
        return self.channels_by_id.get(cid)

    def get_role(self, rid):
        return self.roles_by_id.get(rid) or (self.default_role if rid == self.id else None)

    def get_member(self, uid):
        return self.members_by_id.get(uid)

    def add_channel(self, name, kind=discord.ChannelType.text, category_id=None, position=0, overwrites=(), **extra):
        channel = SimpleNamespace(id=next(ids), name=name, type=kind, category_id=category_id, position=position,
                                  _overwrites=list(overwrites), topic=extra.get("topic"), nsfw=False,
                                  slowmode_delay=extra.get("slowmode", 0), delete=AsyncMock(),
                                  set_permissions=AsyncMock())
        self.channels_by_id[channel.id] = channel
        return channel

    async def _make(self, kind, name, **kw):
        channel = self.add_channel(name, kind)
        channel.kw = kw
        self.made_channels.append(channel)
        return channel

    async def create_text_channel(self, name, **kw):
        return await self._make(discord.ChannelType.text, name, **kw)

    async def create_category(self, name, **kw):
        return await self._make(discord.ChannelType.category, name, **kw)

    async def create_voice_channel(self, name, **kw):
        return await self._make(discord.ChannelType.voice, name, **kw)

    async def create_role(self, **kw):
        role = make_role(self, next(ids), kw["name"], 1)
        role.kw = kw
        self.roles_by_id[role.id] = role
        self.made_roles.append(role)
        return role


def member(guild, uid, name, roles=(), top=10):
    m = SimpleNamespace(id=uid, name=name, bot=False, roles=list(roles), top_role=SimpleNamespace(position=top),
                        send=AsyncMock(), remove_roles=AsyncMock(), add_roles=AsyncMock(), ban=AsyncMock(),
                        kick=AsyncMock(), display_avatar=SimpleNamespace(url="https://cdn/a.png"))
    guild.members_by_id[uid] = m
    return m


def setup(modules=None):
    guild = Guild()
    everyone = make_role(guild, guild.id, "@everyone", 0)
    admin = make_role(guild, 77, "Admin", 9, administrator=True)
    attacker = member(guild, ATTACKER, "rogue", [everyone, admin])
    cfg = {"enabled": True, "punishment": "ban", "window": 60, "whitelist": [], "admins": [], "log": None,
           "modules": modules or {"ban": {"on": True, "threshold": 3, "window": 60, "punishment": "ban"}}}
    bot = SimpleNamespace(user=SimpleNamespace(id=BOT_ID), owner_ids=set(), owner_id=None, get_user=lambda _id: None,
                          fetch_user=AsyncMock(), db=FakeDB(cfg), fetch_webhook=AsyncMock(), delete_invite=AsyncMock(),
                          get_cog=lambda _name: None)
    cog = Antinuke.__new__(Antinuke)
    Antinuke.__init__(cog, bot)
    return cog, guild, attacker


def entry(guild, action, target, user_id=ATTACKER, before=None, after=None, bot=False):
    return SimpleNamespace(action=action, guild=guild, user_id=user_id, user=SimpleNamespace(id=user_id, bot=bot),
                           target=target, before=before or SimpleNamespace(), after=after or SimpleNamespace())


def feed(cog, *entries):
    async def go():
        with patch("cogs.authorization.developers", AsyncMock(return_value=set())):
            for e in entries:
                await cog.on_audit_log_entry_create(e)
    asyncio.run(go())


def field(embed, name):
    return next((f.value for f in embed.fields if f.name == name), None)


@pytest.fixture(autouse=True)
def quick_snapshot_wait(monkeypatch):
    real = restore.Snapshots.wait_for

    async def fast(self, kind, object_id, timeout=3.0):
        return await real(self, kind, object_id, timeout=0)
    monkeypatch.setattr(restore.Snapshots, "wait_for", fast)


# ------------------------------------------------------------------ bans

def test_a_mass_ban_unbans_everyone_they_banned():
    cog, guild, attacker = setup()
    victims = [SimpleNamespace(id=100 + i, name=f"victim{i}") for i in range(3)]
    feed(cog, *(entry(guild, A.ban, v) for v in victims))
    attacker.ban.assert_awaited_once()
    assert [c.args[0].id for c in guild.unban.await_args_list] == [100, 101, 102]
    card = guild.sent[0][0]["embed"]
    assert field(card, "Result") == "Banned"
    assert field(card, "Undone") == "• unbanned victim0, victim1, victim2"


def test_below_the_limit_nothing_is_undone():
    cog, guild, attacker = setup()
    feed(cog, entry(guild, A.ban, SimpleNamespace(id=100, name="v")))
    guild.unban.assert_not_awaited()
    attacker.ban.assert_not_awaited()


# ------------------------------------------------------------------ deleted channels and roles

def test_deleted_channels_are_rebuilt_with_category_overwrites_and_order():
    cog, guild, attacker = setup({"channeldelete": {"on": True, "threshold": 2, "window": 60, "punishment": "ban"}})
    staff = make_role(guild, 55, "Staff", 5)
    guild.roles_by_id[55] = staff
    category = guild.add_channel("Info", discord.ChannelType.category, position=0)
    rules = guild.add_channel("rules", category_id=category.id, position=1, topic="be nice", slowmode=5,
                              overwrites=[Ow(guild.id, 0, 0, 2048), Ow(55, 0, 2048, 0)])
    for channel in (category, rules):
        asyncio.run(cog.on_guild_channel_delete(channel))
        del guild.channels_by_id[channel.id]
    feed(cog, entry(guild, A.channel_delete, SimpleNamespace(id=rules.id)),
         entry(guild, A.channel_delete, SimpleNamespace(id=category.id)))

    made_category, made_rules = guild.made_channels
    assert made_category.name == "Info" and made_category.type == discord.ChannelType.category
    assert made_rules.name == "rules" and made_rules.kw["category"] is made_category  # back inside its category
    assert made_rules.kw["topic"] == "be nice" and made_rules.kw["slowmode_delay"] == 5
    overwrites = made_rules.kw["overwrites"]
    assert overwrites[guild.default_role].send_messages is False and overwrites[staff].send_messages is True
    assert "restored #Info, #rules" in field(guild.sent[0][0]["embed"], "Undone")


def test_deleted_roles_come_back_with_their_members_and_channel_overwrites():
    cog, guild, attacker = setup({"roledelete": {"on": True, "threshold": 1, "window": 60, "punishment": "strip"}})
    vip = make_role(guild, 66, "VIP", 4)
    alice = member(guild, 200, "alice", [vip])
    vip.members = [alice]
    lounge = guild.add_channel("lounge", overwrites=[Ow(66, 0, 1024, 0)])
    asyncio.run(cog.on_guild_role_delete(vip))
    feed(cog, entry(guild, A.role_delete, SimpleNamespace(id=66)))

    (new_role,) = guild.made_roles
    assert new_role.kw["name"] == "VIP" and new_role.kw["hoist"] is True
    assert new_role.kw["colour"].value == 0x123456
    alice.add_roles.assert_awaited_once_with(new_role, reason="Antinuke: Deleting roles")
    lounge.set_permissions.assert_awaited_once()
    assert lounge.set_permissions.await_args.args[0] is new_role
    assert "restored @VIP (given back to 1 member)" in field(guild.sent[0][0]["embed"], "Undone")


def test_a_channel_that_pointed_at_a_deleted_role_points_at_the_rebuilt_one():
    cog, guild, attacker = setup({"roledelete": {"on": True, "threshold": 2, "window": 60, "punishment": "strip"}})
    mods = make_role(guild, 66, "Mods", 4)
    chat = guild.add_channel("mod-chat", overwrites=[Ow(66, 0, 1024, 0)])
    asyncio.run(cog.on_guild_role_delete(mods))
    asyncio.run(cog.on_guild_channel_delete(chat))
    del guild.channels_by_id[chat.id]
    feed(cog, entry(guild, A.channel_delete, SimpleNamespace(id=chat.id)),  # remembered, its own rule is off
         entry(guild, A.role_delete, SimpleNamespace(id=66)),
         entry(guild, A.role_delete, SimpleNamespace(id=67)))
    (new_role,) = guild.made_roles
    (made_chat,) = guild.made_channels
    assert new_role in made_chat.kw["overwrites"]


def test_no_copy_of_a_deleted_channel_is_reported_not_hidden():
    cog, guild, attacker = setup({"channeldelete": {"on": True, "threshold": 1, "window": 60, "punishment": "ban"}})
    feed(cog, entry(guild, A.channel_delete, SimpleNamespace(id=999)))
    assert "couldn't rebuild a deleted channel" in field(guild.sent[0][0]["embed"], "Problems")


# ------------------------------------------------------------------ things they created

def test_channels_roles_webhooks_and_invites_they_made_are_deleted():
    cog, guild, attacker = setup({"ban": {"on": True, "threshold": 1, "window": 60, "punishment": "ban"}})
    spam = guild.add_channel("spam")
    raid_role = make_role(guild, 88, "raid", 2)
    guild.roles_by_id[88] = raid_role
    hook = SimpleNamespace(id=5, name="Hook", delete=AsyncMock())
    cog.bot.fetch_webhook = AsyncMock(return_value=hook)
    feed(cog, entry(guild, A.channel_create, SimpleNamespace(id=spam.id)),
         entry(guild, A.role_create, SimpleNamespace(id=88)),
         entry(guild, A.webhook_create, SimpleNamespace(id=5)),
         entry(guild, A.invite_create, SimpleNamespace(code="abc123")),
         entry(guild, A.ban, SimpleNamespace(id=100, name="victim")))  # the ban trips the rule
    spam.delete.assert_awaited_once()
    raid_role.delete.assert_awaited_once()
    hook.delete.assert_awaited_once()
    cog.bot.delete_invite.assert_awaited_once_with("abc123", reason="Antinuke: Mass banning members")
    undone = field(guild.sent[0][0]["embed"], "Undone")
    assert "deleted #spam, @raid, the webhook Hook" in undone and "unbanned victim" in undone


def test_anything_still_in_flight_after_they_are_caught_is_undone_too():
    cog, guild, attacker = setup()
    victims = [SimpleNamespace(id=100 + i, name=f"v{i}") for i in range(3)]
    feed(cog, *(entry(guild, A.ban, v) for v in victims))
    late = guild.add_channel("late")
    feed(cog, entry(guild, A.channel_create, SimpleNamespace(id=late.id)))
    late.delete.assert_awaited_once()
    _, msg = guild.sent[0]
    assert "#late" in field(msg.edit.await_args.kwargs["embed"], "Undone")
    assert len(guild.sent) == 1  # one alert, updated


# ------------------------------------------------------------------ false alarms

def test_one_invite_from_a_regular_member_is_not_an_attack():
    cog, guild, attacker = setup({"roleinvite": {"on": True, "threshold": 10, "window": 60, "punishment": "strip"}})
    member(guild, 300, "regular")
    feed(cog, entry(guild, A.invite_create, SimpleNamespace(code="x"), user_id=300))
    assert not guild.sent and not cog.bot.delete_invite.await_count


def test_old_saved_instant_invite_rules_are_moved_to_the_new_default():
    old = {"modules": {"roleinvite": {"on": True, "threshold": 1, "window": 60, "punishment": "strip"}}}
    assert Antinuke.migrate(old) is True and old["modules"]["roleinvite"]["threshold"] == 10
    assert Antinuke.migrate(old) is False  # only once
    custom = {"modules": {"roleinvite": {"on": True, "threshold": 7}}}
    Antinuke.migrate(custom)
    assert custom["modules"]["roleinvite"]["threshold"] == 7  # a deliberate setting is kept


def test_other_bots_doing_their_job_are_ignored_but_their_deletes_still_count():
    cog, guild, attacker = setup({"channelcreate": {"on": True, "threshold": 1, "window": 60, "punishment": "kick"},
                                  "channeldelete": {"on": True, "threshold": 1, "window": 60, "punishment": "ban"}})
    ticket_bot = member(guild, 400, "TicketTool")
    ticket_bot.bot = True
    ticket = guild.add_channel("ticket-0001")
    feed(cog, entry(guild, A.channel_create, SimpleNamespace(id=ticket.id), user_id=400, bot=True))
    assert not guild.sent and not ticket.delete.await_count
    feed(cog, entry(guild, A.channel_delete, SimpleNamespace(id=12345), user_id=400, bot=True))
    assert guild.sent  # a bot deleting channels is still caught


def test_renaming_the_server_is_not_an_attack_but_a_vanity_change_is():
    cog, guild, attacker = setup({"serverupdate": {"on": True, "threshold": 1, "window": 60, "punishment": "strip"}})
    feed(cog, entry(guild, A.guild_update, guild, before=SimpleNamespace(name="Old"), after=SimpleNamespace(name="New")))
    assert not guild.sent
    feed(cog, entry(guild, A.guild_update, guild, before=SimpleNamespace(name="Old", vanity_url_code="cool"),
                    after=SimpleNamespace(name="Hacked", vanity_url_code="scam")))
    guild.edit.assert_awaited_once()
    kwargs = guild.edit.await_args.kwargs
    assert kwargs["name"] == "Old" and kwargs["vanity_code"] == "cool"


# ------------------------------------------------------------------ the alert text

def test_long_undo_lists_are_collapsed():
    lines = [f"unbanned u{i}" for i in range(7)] + ["restored #a", "removed the timeout from x"]
    assert collapse(lines) == ["unbanned u0, u1, u2, u3 +3 more", "restored #a", "removed the timeout from x"]
