""",testnuke / ,testcleanup: only OWNER_IDS developers, only in the designated test server, confirmed by the developer
who started it; the antinuke catches the fake nuke, alerts and undoes it, and punishes nobody."""
import asyncio
import itertools
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

import core.testnuke as tn
from cogs.antinuke import Antinuke
from core.utils import UserError

DEV, ADMIN, OTHER_DEV, BOT_ID = 1, 2, 3, 4
TEST_GUILD = 500
ids = itertools.count(9000)


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    # nothing in .env for the test server: it's picked in Discord
    monkeypatch.setattr(tn, "settings", SimpleNamespace(owner_ids=frozenset({DEV}), test_guild_id=None))


# ------------------------------------------------------------------ who and where

def run_check(user_id):
    @tn.env_developer_only()
    async def cmd(ctx):
        pass
    (predicate,) = cmd.__commands_checks__
    ctx = SimpleNamespace(author=SimpleNamespace(id=user_id, guild_permissions=discord.Permissions.all()))
    return asyncio.run(predicate(ctx))


def test_only_developer_ids_from_the_environment_pass():
    assert run_check(DEV) is True
    for uid in (ADMIN, OTHER_DEV):  # an Administrator, or a developer only found from the Discord app
        with pytest.raises(UserError, match="OWNER_IDS"):
            run_check(uid)


def picker_bot(saved=None):
    store = {"global": {"test_guild_id": saved} if saved else {}}

    async def get_config(gid, module, default=None):
        return store.setdefault(module, {})

    async def set_config(gid, module, data):
        store[module] = data
    return SimpleNamespace(db=SimpleNamespace(get_config=get_config, set_config=set_config),
                           get_guild=lambda gid: SimpleNamespace(id=gid, name="Sandbox")), store


def test_with_no_test_server_yet_the_current_one_is_offered():
    bot, _ = picker_bot()
    guild, new = asyncio.run(tn.test_guild(bot, SimpleNamespace(guild=SimpleNamespace(id=TEST_GUILD))))
    assert guild.id == TEST_GUILD and new is True


def test_once_picked_tests_only_run_there():
    bot, _ = picker_bot(saved=TEST_GUILD)
    guild, new = asyncio.run(tn.test_guild(bot, SimpleNamespace(guild=SimpleNamespace(id=TEST_GUILD))))
    assert new is False
    with pytest.raises(UserError, match=r"only run in the test server \(\*\*Sandbox\*\*\)"):
        asyncio.run(tn.test_guild(bot, SimpleNamespace(guild=SimpleNamespace(id=777))))


def test_an_id_in_env_still_wins(monkeypatch):
    monkeypatch.setattr(tn, "settings", SimpleNamespace(owner_ids=frozenset({DEV}), test_guild_id=TEST_GUILD))
    bot, _ = picker_bot(saved=777)
    assert asyncio.run(tn.saved_test_guild_id(bot)) == TEST_GUILD


def test_the_testserver_command_sets_shows_and_clears(monkeypatch):
    from cogs.owner import Owner
    monkeypatch.setattr("core.config.settings", SimpleNamespace(test_guild_id=None, owner_ids=frozenset({DEV})))
    bot, store = picker_bot()
    owner = Owner.__new__(Owner)
    owner.bot = bot
    ctx = SimpleNamespace(guild=SimpleNamespace(id=TEST_GUILD, name="Sandbox"), approve=AsyncMock(),
                          neutral=AsyncMock(), clean_prefix=",")
    asyncio.run(Owner.cmd_testserver.callback(owner, ctx, "set"))
    assert store["global"]["test_guild_id"] == TEST_GUILD
    asyncio.run(Owner.cmd_testserver.callback(owner, ctx, None))
    assert "**Sandbox**" in ctx.neutral.await_args.args[0]
    asyncio.run(Owner.cmd_testserver.callback(owner, ctx, "clear"))
    assert "test_guild_id" not in store["global"]


def press(view, label, user_id):
    it = SimpleNamespace(user=SimpleNamespace(id=user_id),
                         response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock()))

    async def go():
        if await view.interaction_check(it):
            button = next(b for b in view.children if b.label == label)
            await button.callback(it)
    asyncio.run(go())
    return it


def test_only_the_developer_who_started_it_can_confirm():
    view = tn.ConfirmTest(DEV)
    it = press(view, "Confirm Test", OTHER_DEV)
    it.response.send_message.assert_awaited_once()
    assert it.response.send_message.await_args.kwargs["ephemeral"] is True and view.confirmed is None
    press(view, "Confirm Test", DEV)
    assert view.confirmed is True and all(b.disabled for b in view.children)


def test_cancel_cancels():
    view = tn.ConfirmTest(DEV)
    press(view, "Cancel", DEV)
    assert view.confirmed is False


def test_the_warning_says_what_will_happen():
    cfg = {"enabled": True, "modules": {"rolecreate": {"on": True, "threshold": 5, "window": 120},
                                        "channelcreate": {"on": True, "threshold": 2, "window": 120}}}
    e = tn.warning_embed(SimpleNamespace(name="Test"), cfg, 6, 3, ",")
    assert "**6** role(s) and **3** channel(s)" in e.description
    assert "Role Create rule: **5 in 120s**" in e.description and "nobody is punished" in e.description
    off = tn.warning_embed(SimpleNamespace(name="Test"), {"enabled": False}, 1, 1, ",")
    assert "antinuke is **off**" in off.description


# ------------------------------------------------------------------ the antinuke during a test

class FakeDB:
    def __init__(self, **store):
        self.store = store

    async def get_config(self, gid, module, default=None):
        return self.store.setdefault(module, {} if default is None else json.loads(json.dumps(default)))

    async def set_config(self, gid, module, data):
        self.store[module] = data


class Guild:
    """Creating a role or channel makes the audit log entry the antinuke listens to, like Discord does."""

    def __init__(self, cog):
        self.id, self.name, self.owner_id = TEST_GUILD, "Test", 99
        self.cog, self.roles_by_id, self.channels_by_id = cog, {}, {}
        self.owner = SimpleNamespace(send=AsyncMock())
        self.me = SimpleNamespace(top_role=SimpleNamespace(position=50),
                                  guild_permissions=discord.Permissions(manage_roles=True, manage_channels=True))
        self.ban = AsyncMock()
        self.log = SimpleNamespace(id=60, send=AsyncMock(return_value=MagicMock()))

    @property
    def roles(self):
        return list(self.roles_by_id.values())

    @property
    def channels(self):
        return list(self.channels_by_id.values())

    def get_role(self, rid):
        return self.roles_by_id.get(rid)

    def get_channel(self, cid):
        return self.log if cid == 60 else self.channels_by_id.get(cid)

    def get_member(self, uid):
        return None

    def _thing(self, store, name):
        thing = SimpleNamespace(id=next(ids), name=name, managed=False)

        async def delete(reason=None):
            store.pop(thing.id, None)
        thing.delete = AsyncMock(side_effect=delete)
        store[thing.id] = thing
        return thing

    async def _audit(self, action, thing):
        entry = SimpleNamespace(action=action, guild=self, user_id=BOT_ID, user=SimpleNamespace(id=BOT_ID, bot=True),
                                target=thing, before=SimpleNamespace(), after=SimpleNamespace(name=thing.name))
        asyncio.get_running_loop().create_task(self.cog.on_audit_log_entry_create(entry))

    async def create_role(self, name, reason=None):
        role = self._thing(self.roles_by_id, name)
        await self._audit(discord.AuditLogAction.role_create, role)
        return role

    async def create_text_channel(self, name, reason=None):
        channel = self._thing(self.channels_by_id, name)
        await self._audit(discord.AuditLogAction.channel_create, channel)
        return channel


def make(enabled=True):
    cfg = {"enabled": enabled, "log": 60, "whitelist": [], "admins": [], "modules": {
        "rolecreate": {"on": True, "threshold": 5, "window": 120, "punishment": "strip"},
        "channelcreate": {"on": True, "threshold": 2, "window": 120, "punishment": "kick"}}}
    bot = SimpleNamespace(user=SimpleNamespace(id=BOT_ID), owner_ids=set(), owner_id=None, db=FakeDB(antinuke=cfg),
                          get_user=lambda _id: None, fetch_user=AsyncMock(), get_cog=lambda _name: None)
    cog = Antinuke.__new__(Antinuke)
    Antinuke.__init__(cog, bot)
    return cog, Guild(cog)


def run_test(cog, guild, roles, channels, monkeypatch):
    monkeypatch.setattr(tn, "WAIT_FOR_ANTINUKE", 2.0)

    async def go():
        with patch("cogs.authorization.developers", AsyncMock(return_value={DEV})):
            return await tn.run_test(cog, guild, DEV, roles, channels)
    return asyncio.run(go())


def test_the_antinuke_catches_the_test_and_removes_everything(monkeypatch):
    cog, guild = make()
    result = run_test(cog, guild, 6, 3, monkeypatch)
    assert result["roles"] == 6 and result["channels"] == 3
    assert result["roles_left"] == 0 and result["channels_left"] == 0
    # one attacker, one incident: whichever rule trips first catches them, and everything else they made
    # (before or after) is undone as part of it
    assert len(result["incidents"]) == 1 and result["incidents"][0].module in {"rolecreate", "channelcreate"}
    guild.ban.assert_not_awaited()  # nobody punished
    guild.owner.send.assert_not_awaited()  # tests don't page the owner
    cards = [c.kwargs["embed"] for c in guild.log.send.await_args_list]
    assert cards and all(c.title.startswith("🧪 Antinuke test:") for c in cards)
    assert all("nobody was punished" in c.description for c in cards)
    assert all(not any(f.name in ("Attacker", "Their roles") for f in c.fields) for c in cards)
    assert TEST_GUILD not in cog.test_runs  # the test is over: the bot's own actions are trusted again
    report = tn.report_embed(result, ",")
    assert report.description.startswith("✅") and "removed by the antinuke: **9**" in report.description


def test_with_the_antinuke_off_nothing_is_caught_and_the_report_says_why(monkeypatch):
    cog, guild = make(enabled=False)
    result = run_test(cog, guild, 2, 1, monkeypatch)
    assert result["incidents"] == [] and result["roles_left"] == 2
    report = tn.report_embed(result, ",")
    assert report.description.startswith("❌") and "testcleanup" in report.description


def test_outside_a_test_the_bot_creating_things_is_ignored():
    cog, guild = make()

    async def go():
        for i in range(6):
            await guild.create_role(f"testnuke-role-{i}")
        await asyncio.sleep(0.05)
    asyncio.run(go())
    assert len(guild.roles) == 6 and not guild.log.send.await_count


def test_the_antinuke_deleting_test_roles_is_not_counted_as_an_attack():
    cog, guild = make()
    run = cog.start_test(guild.id, DEV)
    role = SimpleNamespace(id=5, name="testnuke-role-1")
    run.created.add(5)
    deleted = SimpleNamespace(action=discord.AuditLogAction.role_delete, target=role,
                              after=SimpleNamespace(), before=SimpleNamespace())
    assert run.owns(deleted) is False
    created = SimpleNamespace(action=discord.AuditLogAction.role_create, target=role, after=SimpleNamespace())
    assert run.owns(created) is True


def test_cleanup_removes_only_test_roles_and_channels():
    cog, guild = make()
    keep_role = guild._thing(guild.roles_by_id, "Moderators")
    keep_channel = guild._thing(guild.channels_by_id, "general")
    for name in ("testnuke-role-1", "testnuke-role-2"):
        guild._thing(guild.roles_by_id, name)
    guild._thing(guild.channels_by_id, "testnuke-channel-1")
    assert asyncio.run(tn.cleanup(cog, guild)) == (2, 1, 0)
    assert guild.roles == [keep_role] and guild.channels == [keep_channel]


# ------------------------------------------------------------------ the command

def test_the_command_asks_first_then_runs_and_reports(monkeypatch):
    from cogs.owner import Owner
    cog, guild = make()
    owner = Owner.__new__(Owner)
    picker, store = picker_bot()
    owner.bot = SimpleNamespace(get_cog=lambda name: cog if name == "Antinuke" else None, db=picker.db,
                                get_guild=picker.get_guild)
    msg = SimpleNamespace(edit=AsyncMock())
    ctx = SimpleNamespace(guild=guild, author=SimpleNamespace(id=DEV), clean_prefix=",",
                          send=AsyncMock(return_value=msg))

    class AutoConfirm(tn.ConfirmTest):
        async def wait(self):
            self.confirmed = True
            return False
    monkeypatch.setattr(tn, "ConfirmTest", AutoConfirm)
    monkeypatch.setattr(tn, "run_test", AsyncMock(return_value={
        "roles": 6, "channels": 3, "failures": [], "roles_left": 0, "channels_left": 0, "incidents": [MagicMock(
            module="rolecreate", threshold=5, window=120)], "seconds": 1.2}))
    asyncio.run(Owner.cmd_testnuke.callback(owner, ctx))
    warning = ctx.send.await_args.kwargs["embed"]
    assert warning.title == "⚠️ Test nuke" and isinstance(ctx.send.await_args.kwargs["view"], tn.ConfirmTest)
    labels = [b.label for b in ctx.send.await_args.kwargs["view"].children]
    assert labels == ["Confirm Test", "Cancel"]
    tn.run_test.assert_awaited_once_with(cog, guild, DEV, 6, 3)  # one more than each rule allows
    assert msg.edit.await_args.kwargs["embed"].title == "Test nuke finished"
    assert "makes **this server** the test server" in warning.description  # none was picked yet...
    assert store["global"]["test_guild_id"] == TEST_GUILD  # ...so confirming picked this one


def test_a_cancelled_test_creates_nothing(monkeypatch):
    from cogs.owner import Owner
    cog, guild = make()
    owner = Owner.__new__(Owner)
    picker, store = picker_bot()
    owner.bot = SimpleNamespace(get_cog=lambda name: cog, db=picker.db, get_guild=picker.get_guild)
    msg = SimpleNamespace(edit=AsyncMock())
    ctx = SimpleNamespace(guild=guild, author=SimpleNamespace(id=DEV), clean_prefix=",",
                          send=AsyncMock(return_value=msg))

    class AutoCancel(tn.ConfirmTest):
        async def wait(self):
            self.confirmed = False
            return False
    monkeypatch.setattr(tn, "ConfirmTest", AutoCancel)
    monkeypatch.setattr(tn, "run_test", AsyncMock())
    asyncio.run(Owner.cmd_testnuke.callback(owner, ctx))
    tn.run_test.assert_not_awaited()
    assert msg.edit.await_args.kwargs["embed"].description == "Test cancelled."
    assert not guild.roles and not guild.channels
    assert "test_guild_id" not in store["global"]  # cancelling doesn't pick a test server


def test_the_test_alert_is_also_sent_to_the_developers_in_owner_ids(monkeypatch):
    # patch the globals the class really uses: loading the cogs elsewhere in the suite swaps in a fresh module copy
    monkeypatch.setitem(Antinuke._send_alert.__globals__, "settings",
                        SimpleNamespace(owner_ids=frozenset({DEV}), antinuke_dev_alerts=True))
    cog, guild = make()
    dev = SimpleNamespace(id=DEV, send=AsyncMock(return_value=MagicMock()))
    cog.bot.get_user = lambda uid: dev if uid == DEV else None
    result = run_test(cog, guild, 6, 0, monkeypatch)
    assert result["incidents"]
    dev.send.assert_awaited()
    card = dev.send.await_args.kwargs["embed"]
    assert card.title.startswith("🧪 Antinuke test:") and card.footer.text.endswith("test run · sent to developers")
    assert dev.send.await_args.kwargs["view"].children[0].label == "Open server"
    guild.owner.send.assert_not_awaited()  # the server owner still isn't paged for a test
