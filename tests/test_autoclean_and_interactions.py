"""Channels stay clean (command messages go once they've run, replies after the delay, menus when they close),
clicks are always answered, and the log/snipe/ghost-ping checks don't mistake the clean-up for a user deleting."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

import core.autoclean as ac
from core.utils import Context, UserError


class FakeDB:
    def __init__(self, settings=None):
        self.store = {"settings": settings or {}}

    async def get_config(self, gid, module, default=None):
        return self.store.setdefault(module, json.loads(json.dumps(default)) if default else {})

    async def set_config(self, gid, module, data):
        self.store[module] = data


def message(mid):
    return SimpleNamespace(id=mid, delete=AsyncMock())


def ctx_for(name="ping", settings=None, parent=None):
    command = SimpleNamespace(qualified_name=name, root_parent=parent)
    return SimpleNamespace(guild=SimpleNamespace(id=10), command=command, message=message(1),
                           bot=SimpleNamespace(db=FakeDB(settings)))


@pytest.fixture
def no_wait(monkeypatch):
    waited = []

    async def fake_sleep(seconds):
        waited.append(seconds)
    monkeypatch.setattr(ac.asyncio, "sleep", fake_sleep)
    return waited


def finish(ctx, ran=True):
    async def go():
        await ac.after_command(ctx, ran=ran)
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if pending:
            await asyncio.gather(*pending)  # the delayed deletions
    asyncio.run(go())


def test_the_command_message_goes_and_the_reply_follows_after_the_delay(no_wait):
    ctx = ctx_for()
    reply = message(2)
    ac.track(ctx, reply, None)
    finish(ctx)
    ctx.message.delete.assert_awaited_once()
    reply.delete.assert_awaited_once()
    assert no_wait == [15] and ac.was_cleaned(1)


def test_the_delay_follows_the_server_setting(no_wait):
    ctx = ctx_for(settings={"autoclean": {"on": True, "delay": 30}})
    ac.track(ctx, message(2), None)
    finish(ctx)
    assert no_wait == [30]


def test_turned_off_nothing_is_deleted(no_wait):
    ctx = ctx_for(settings={"autoclean": {"on": False}})
    reply = message(2)
    ac.track(ctx, reply, None)
    finish(ctx)
    ctx.message.delete.assert_not_awaited()
    reply.delete.assert_not_awaited()


def test_a_menu_goes_when_it_closes_not_after_the_delay(no_wait):
    ctx = ctx_for("help")
    menu = message(2)
    view = SimpleNamespace(timeout=300, is_finished=lambda: False, wait=AsyncMock())
    ac.track(ctx, menu, view)
    finish(ctx)
    view.wait.assert_awaited_once()
    menu.delete.assert_awaited_once()
    assert no_wait == []


def test_lasting_buttons_and_content_commands_stay(no_wait):
    ctx = ctx_for("giveaway start")
    panel = message(2)
    ac.track(ctx, panel, SimpleNamespace(timeout=None))  # giveaway / button roles: buttons that never expire
    finish(ctx)
    panel.delete.assert_not_awaited()
    ctx.message.delete.assert_awaited_once()
    poll = ctx_for("poll")
    posted = message(3)
    ac.track(poll, posted, None)
    finish(poll)
    posted.delete.assert_not_awaited()


def test_a_failed_check_that_showed_nothing_leaves_the_message(no_wait):
    ctx = ctx_for()
    finish(ctx, ran=False)
    ctx.message.delete.assert_not_awaited()


def test_an_error_reply_is_cleaned_up_too(no_wait):
    ctx = ctx_for()
    error_reply = message(2)
    ac.track(ctx, error_reply, None)
    finish(ctx, ran=False)
    ctx.message.delete.assert_awaited_once()
    error_reply.delete.assert_awaited_once()


def test_cleaning_runs_once_per_command(no_wait):
    ctx = ctx_for()
    finish(ctx)
    finish(ctx)
    assert ctx.message.delete.await_count == 1


def test_context_send_remembers_its_replies():
    sent = message(5)
    ctx = Context.__new__(Context)
    ctx.message = SimpleNamespace(to_reference=lambda **kw: None)
    ctx.__dict__["channel"] = SimpleNamespace(send=AsyncMock(return_value=sent))
    view = object()
    asyncio.run(Context.send(ctx, "hi", view=view))
    assert ctx._autoclean_replies == [(sent, view)]


# ------------------------------------------------------------------ the clean-up isn't a user deleting a message

def test_logs_snipe_and_ghost_pings_ignore_cleaned_commands():
    from cogs.automod import Automod
    from cogs.logging import Logging
    from cogs.utility import Utility
    ac._mark(777)
    m = SimpleNamespace(id=777, guild=SimpleNamespace(id=10), author=SimpleNamespace(id=5, bot=False),
                        channel=SimpleNamespace(id=3), content=",ban @someone", attachments=[], stickers=[])
    logging_cog = Logging.__new__(Logging)
    logging_cog.route = AsyncMock()
    asyncio.run(Logging.on_message_delete(logging_cog, m))
    logging_cog.route.assert_not_awaited()
    utility = Utility.__new__(Utility)
    utility.deleted = MagicMock()
    asyncio.run(Utility.on_message_delete(utility, m))
    utility.deleted.__getitem__.assert_not_called()
    automod = Automod.__new__(Automod)
    automod.cfg = AsyncMock()
    asyncio.run(Automod.on_message_delete(automod, m))
    automod.cfg.assert_not_awaited()


# ------------------------------------------------------------------ clicks always get an answer

def interaction(custom_id="x", message_id=50, author_id=4):
    response = SimpleNamespace(edit_message=AsyncMock(), send_message=AsyncMock(), defer=AsyncMock(),
                               is_done=MagicMock(return_value=False))
    return SimpleNamespace(type=discord.InteractionType.component, response=response,
                           followup=SimpleNamespace(send=AsyncMock()),
                           data={"custom_id": custom_id, "component_type": 2},
                           message=SimpleNamespace(id=message_id, author=SimpleNamespace(id=author_id)))


def bot_with(views=None, dynamic=None):
    from core.bot import Veil
    bot = Veil()
    store = bot._connection._view_store
    store._views = views or {}
    store._dynamic_items = dynamic or {}
    bot._connection.user = SimpleNamespace(id=4)
    return bot


def test_an_expired_menu_says_so_and_loses_its_buttons():
    bot = bot_with()
    it = interaction()
    asyncio.run(bot.on_interaction(it))
    it.response.edit_message.assert_awaited_once_with(view=None)
    assert "expired" in it.followup.send.await_args.args[0] and it.followup.send.await_args.kwargs["ephemeral"]


def test_a_live_menu_is_left_to_its_own_handler(monkeypatch):
    bot = bot_with(views={50: {(2, "x"): object()}})
    it = interaction()
    it.response.is_done.return_value = True  # the handler answered in time
    monkeypatch.setattr("core.bot.asyncio.sleep", AsyncMock())
    asyncio.run(bot.on_interaction(it))
    it.response.edit_message.assert_not_awaited()
    it.response.defer.assert_not_awaited()


def test_a_slow_handler_is_acknowledged_before_discord_gives_up(monkeypatch):
    bot = bot_with(views={None: {(2, "x"): object()}})  # a persistent view
    it = interaction()
    monkeypatch.setattr("core.bot.asyncio.sleep", AsyncMock())
    asyncio.run(bot.on_interaction(it))
    it.response.defer.assert_awaited_once()


def test_dynamic_buttons_count_as_live(monkeypatch):
    import re
    bot = bot_with(dynamic={re.compile(r"banlog:\d+"): object})
    it = interaction(custom_id="banlog:123")
    it.response.is_done.return_value = True
    monkeypatch.setattr("core.bot.asyncio.sleep", AsyncMock())
    asyncio.run(bot.on_interaction(it))
    it.response.edit_message.assert_not_awaited()


# ------------------------------------------------------------------ the setting

def test_the_autoclean_command_switches_and_sets_the_delay():
    from cogs.settings import Settings
    db = FakeDB()
    cog = Settings.__new__(Settings)
    cog.bot = SimpleNamespace(db=db)
    ctx = SimpleNamespace(guild=SimpleNamespace(id=10), approve=AsyncMock(), neutral=AsyncMock(), clean_prefix=",")
    asyncio.run(Settings.autoclean.callback(cog, ctx, "30s"))
    assert db.store["settings"]["autoclean"] == {"on": True, "delay": 30}
    asyncio.run(Settings.autoclean.callback(cog, ctx, "off"))
    assert db.store["settings"]["autoclean"]["on"] is False
    with pytest.raises(UserError):
        asyncio.run(Settings.autoclean.callback(cog, ctx, "forever"))


def test_restart_commands_are_cleaned_up_once_the_bot_is_back(tmp_path, monkeypatch):
    from core.bot import Veil
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".restart_state.json").write_text(json.dumps(
        {"channel_id": 30, "message_id": 31, "command_message_id": 32, "start_time": 0}))
    bot = Veil()
    bot.db = FakeDB()
    status = SimpleNamespace(embeds=[], edit=AsyncMock(), delete=AsyncMock())
    command = SimpleNamespace(delete=AsyncMock())
    channel = SimpleNamespace(guild=SimpleNamespace(id=10), fetch_message=AsyncMock(return_value=status),
                              send=AsyncMock(), get_partial_message=lambda mid: command if mid == 32 else None)
    bot.get_channel = lambda cid: channel if cid == 30 else None
    asyncio.run(bot.check_restart_notification())
    command.delete.assert_awaited_once()
    status.delete.assert_awaited_once_with(delay=15)
    assert ac.was_cleaned(32)
