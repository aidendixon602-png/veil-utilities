"""IDs the bot sets up itself: developers, its own client ID and the private error/auth channels."""
from __future__ import annotations

import asyncio
import itertools
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from core import devchannels
from core import perms as perms_mod
from core.bot import Veil
from core.config import ConfigError, Settings
from core.dashboard import DashboardServer


# ------------------------------------------------------------------ developers come from the Discord application

@pytest.fixture
def logged_in_bot(monkeypatch):
    monkeypatch.setattr(perms_mod, "DISCOVERED_DEVELOPERS", set())
    monkeypatch.setattr(Veil, "user", property(lambda self: SimpleNamespace(id=5)))
    bot = Veil()
    bot.owner_ids = frozenset([7])  # an ID from OWNER_IDS
    return bot


def test_the_application_owner_becomes_a_developer(logged_in_bot):
    bot = logged_in_bot
    bot.application_info = AsyncMock(return_value=SimpleNamespace(owner=SimpleNamespace(id=42), team=None))
    asyncio.run(bot.discover_developers())
    assert bot.owner_ids == {7, 42}
    assert perms_mod.is_bot_developer(42) and perms_mod.is_bot_developer(42, bot)  # recognised even without the bot
    assert not perms_mod.is_bot_developer(99)


def test_every_team_member_becomes_a_developer(logged_in_bot):
    bot = logged_in_bot
    team = SimpleNamespace(members=[SimpleNamespace(id=1), SimpleNamespace(id=2)])
    bot.application_info = AsyncMock(return_value=SimpleNamespace(owner=SimpleNamespace(id=1), team=team))
    asyncio.run(bot.discover_developers())
    assert bot.owner_ids == {7, 1, 2}


def test_a_failed_lookup_never_stops_startup(logged_in_bot):
    bot = logged_in_bot
    bot.application_info = AsyncMock(side_effect=RuntimeError("discord is down"))
    asyncio.run(bot.discover_developers())
    assert bot.owner_ids == {7}


def test_nothing_is_fetched_when_the_bot_is_not_logged_in(monkeypatch):
    monkeypatch.setattr(perms_mod, "DISCOVERED_DEVELOPERS", set())
    bot = Veil()  # tooling (docs, tests) builds the bot without connecting
    bot.application_info = AsyncMock()
    asyncio.run(bot.discover_developers())
    bot.application_info.assert_not_awaited()


def test_owner_ids_is_no_longer_required():
    Settings.from_env({"DISCORD_TOKEN": "abc"}).require_runtime()


# ------------------------------------------------------------------ the dashboard uses the bot's own client ID

def test_dashboard_login_needs_only_the_secret_and_the_address():
    on = {"DASHBOARD_ENABLED": "true"}
    Settings.from_env({**on, "DISCORD_CLIENT_SECRET": "s", "DASHBOARD_URL": "https://veil.example.com"})
    with pytest.raises(ConfigError, match="DISCORD_CLIENT_SECRET and DASHBOARD_URL"):
        Settings.from_env({**on, "DISCORD_CLIENT_SECRET": "s"})
    assert Settings.from_env(on).dashboard_login_configured is False


def test_dashboard_takes_the_client_id_from_the_bot():
    s = Settings.from_env({"DASHBOARD_ENABLED": "true", "DISCORD_CLIENT_SECRET": "s",
                           "DASHBOARD_URL": "https://veil.example.com"})
    server = DashboardServer.from_settings(SimpleNamespace(application_id=123), s)
    assert server.login_enabled and server.client_id == "123"


def test_a_client_id_in_env_still_wins():
    s = Settings.from_env({"DASHBOARD_ENABLED": "true", "DISCORD_CLIENT_ID": "999", "DISCORD_CLIENT_SECRET": "s",
                           "DASHBOARD_URL": "https://veil.example.com"})
    assert DashboardServer.from_settings(SimpleNamespace(application_id=123), s).client_id == "999"


# ------------------------------------------------------------------ private developer channels

DEV, STRANGER = 1, 2


class Obj:
    """Like SimpleNamespace but hashable, because permission overwrites are keyed by member/role objects."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeDB:
    def __init__(self):
        self.store: dict = {}

    async def get_config(self, gid, module, default=None):
        return self.store.setdefault((gid, module), {})

    async def set_config(self, gid, module, data):
        self.store[(gid, module)] = data


def make_guild(gid, *, owner=STRANGER, members=(), admin=True):
    guild = Obj(id=gid, name=f"guild{gid}", owner_id=owner, default_role=Obj(), categories=[],
                me=Obj(guild_permissions=discord.Permissions(administrator=admin)),
                members={m: Obj(id=m) for m in members})
    guild.get_member = lambda uid: guild.members.get(uid)
    return guild


def make_bot(*guilds):
    ids = itertools.count(1000)
    channels: dict[int, SimpleNamespace] = {}
    created = []
    made_categories = []

    def attach(guild):
        async def create_category(name, **kw):
            cat = SimpleNamespace(id=next(ids), name=name, guild=guild, kwargs=kw)
            guild.categories.append(cat)
            made_categories.append(cat)
            return cat

        async def create_text_channel(name, **kw):
            ch = SimpleNamespace(id=next(ids), name=name, guild=guild, kwargs=kw, category=kw.get("category"))
            ch.category_id = ch.category.id if ch.category else None

            async def edit(**changes):
                ch.category = changes.get("category", ch.category)
                ch.category_id = ch.category.id if ch.category else None
            ch.edit = AsyncMock(side_effect=edit)
            channels[ch.id] = ch
            created.append(ch)
            return ch
        guild.create_category = AsyncMock(side_effect=create_category)
        guild.create_text_channel = AsyncMock(side_effect=create_text_channel)

    for g in guilds:
        attach(g)
    bot = SimpleNamespace(guilds=list(guilds), owner_ids=frozenset([DEV]), is_authorized=lambda gid: True,
                          get_guild=lambda gid: next((g for g in guilds if g.id == gid), None),
                          get_channel=channels.get, db=FakeDB(), dev_channels={})
    bot.made_categories = made_categories
    return bot, channels, created


@pytest.fixture(autouse=True)
def no_env_channels(monkeypatch):
    monkeypatch.setattr(devchannels, "settings", SimpleNamespace(error_log_channel=None, auth_log_channel=None))


def test_the_home_server_is_one_a_developer_owns_or_is_in():
    nobody = make_guild(1)
    dev_inside = make_guild(2, members=[DEV])
    dev_owned = make_guild(3, owner=DEV)
    cannot_manage = make_guild(4, owner=DEV, admin=False)
    bot, _, _ = make_bot(nobody, dev_inside, dev_owned, cannot_manage)
    assert devchannels.pick_home_guild(bot) is dev_owned
    bot.guilds = [nobody, dev_inside]
    assert devchannels.pick_home_guild(bot) is dev_inside
    bot.guilds = [nobody, cannot_manage]
    assert devchannels.pick_home_guild(bot) is None


def test_it_makes_a_private_channel_for_errors_and_for_auth_and_remembers_them():
    home = make_guild(3, owner=DEV, members=[DEV])
    bot, channels, created = make_bot(home)
    stored = asyncio.run(devchannels.ensure(bot))
    assert [c.name for c in created] == ["veil-errors", "veil-auth"]
    assert stored == {"guild": 3, "errors": created[0].id, "auth": created[1].id}
    assert bot.db.store[(0, "global")]["dev_channels"] == stored
    overwrites = created[0].kwargs["overwrites"]
    assert overwrites[home.default_role].view_channel is False      # hidden from everyone
    assert overwrites[home.me].send_messages is True                 # the bot can post
    assert overwrites[home.members[DEV]].view_channel is True        # developers can read
    assert devchannels.channel_for(bot, "errors") is created[0]


def test_starting_again_does_not_make_more_channels():
    home = make_guild(3, owner=DEV)
    bot, _, created = make_bot(home)
    asyncio.run(devchannels.ensure(bot))
    asyncio.run(devchannels.ensure(bot))
    assert len(created) == 2


def test_a_deleted_channel_is_made_again():
    home = make_guild(3, owner=DEV)
    bot, channels, created = make_bot(home)
    asyncio.run(devchannels.ensure(bot))
    del channels[bot.dev_channels["errors"]]
    asyncio.run(devchannels.ensure(bot))
    assert [c.name for c in created] == ["veil-errors", "veil-auth", "veil-errors"]
    assert devchannels.channel_for(bot, "errors") is created[2]
    assert devchannels.channel_for(bot, "auth") is created[1]


def test_a_channel_id_in_env_wins_and_is_not_recreated(monkeypatch):
    monkeypatch.setattr(devchannels, "settings", SimpleNamespace(error_log_channel=555, auth_log_channel=None))
    home = make_guild(3, owner=DEV)
    bot, channels, created = make_bot(home)
    channels[555] = SimpleNamespace(id=555, name="my-errors")
    asyncio.run(devchannels.ensure(bot))
    assert [c.name for c in created] == ["veil-auth"]
    assert devchannels.channel_for(bot, "errors") is channels[555]


def test_without_a_suitable_server_it_waits_quietly():
    bot, _, created = make_bot(make_guild(1), make_guild(2, owner=DEV, admin=False))
    assert asyncio.run(devchannels.ensure(bot)) == {}
    assert not created and devchannels.channel_for(bot, "errors") is None


def test_a_refused_channel_is_skipped_not_fatal():
    home = make_guild(3, owner=DEV)
    bot, _, created = make_bot(home)
    forbidden = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "missing permissions")
    original = home.create_text_channel.side_effect
    calls = []

    async def refuse_the_first(name, **kw):
        calls.append(name)
        if len(calls) == 1:
            raise forbidden
        return await original(name, **kw)

    home.create_text_channel = AsyncMock(side_effect=refuse_the_first)
    stored = asyncio.run(devchannels.ensure(bot))
    assert "errors" not in stored and "auth" in stored


def test_the_authorization_alert_goes_to_the_auto_made_channel():
    home = make_guild(3, owner=DEV)
    bot, _, created = make_bot(home)
    asyncio.run(devchannels.ensure(bot))
    assert devchannels.channel_for(bot, "auth") is created[1]


def test_both_channels_are_made_under_the_veil_logs_category():
    home = make_guild(3, owner=DEV)
    bot, _, created = make_bot(home)
    asyncio.run(devchannels.ensure(bot))
    assert [c.name for c in bot.made_categories] == ["VEIL LOGS"]
    category = bot.made_categories[0]
    assert all(c.category is category for c in created)
    # the category is as private as the log channels ,logs setup puts in it
    assert category.kwargs["overwrites"][home.default_role].view_channel is False
    assert category.kwargs["overwrites"][home.me].view_channel is True


def test_an_existing_veil_logs_category_is_reused_whatever_its_case():
    home = make_guild(3, owner=DEV)
    existing = SimpleNamespace(id=7, name="Veil Logs", guild=home)
    home.categories.append(existing)
    bot, _, created = make_bot(home)
    asyncio.run(devchannels.ensure(bot))
    assert not bot.made_categories
    assert all(c.category is existing for c in created)


def test_the_category_is_not_made_twice_on_the_next_start():
    home = make_guild(3, owner=DEV)
    bot, _, created = make_bot(home)
    asyncio.run(devchannels.ensure(bot))
    asyncio.run(devchannels.ensure(bot))
    assert len(bot.made_categories) == 1 and len(created) == 2


def test_channels_made_earlier_outside_the_category_are_moved_in():
    home = make_guild(3, owner=DEV)
    bot, channels, created = make_bot(home)
    # what an older version left behind: both channels at the top level of the server
    for name, key in (("veil-errors", "errors"), ("veil-auth", "auth")):
        ch = asyncio.run(home.create_text_channel(name))
        bot.dev_channels[key] = ch.id
    bot.dev_channels["guild"] = 3
    asyncio.run(devchannels.ensure(bot))
    category = bot.made_categories[0]
    assert len(created) == 2                       # nothing was made again
    assert all(c.category is category for c in created)
    assert all(c.edit.await_count == 1 for c in created)


def test_a_refused_category_still_gets_the_channels():
    home = make_guild(3, owner=DEV)
    bot, _, created = make_bot(home)
    home.create_category = AsyncMock(side_effect=discord.Forbidden(SimpleNamespace(status=403, reason="x"), "no"))
    stored = asyncio.run(devchannels.ensure(bot))
    assert {"errors", "auth"} <= set(stored) and all(c.category is None for c in created)


def test_the_logs_cog_uses_the_same_category_name():
    from cogs import logging as logs_cog
    assert logs_cog.CATEGORY_NAME == devchannels.LOGS_CATEGORY == "VEIL LOGS"
