"""The bot never deletes command messages or its replies; clicks are always answered; and when the bot does delete
something on purpose, the log/snipe/ghost-ping checks don't mistake it for a user deleting a message."""
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


def test_nothing_is_deleted_after_a_command():
    """Command messages and the bot's replies stay: there's no clean-up after a command any more."""
    from core.bot import Veil
    assert not hasattr(ac, "after_command") and not hasattr(ac, "track")
    assert "on_command_completion" not in vars(Veil)


def test_the_autoclean_command_is_gone():
    from cogs.settings import Settings
    assert not hasattr(Settings, "autoclean")


def test_after_a_restart_the_status_message_is_edited_and_nothing_is_deleted(tmp_path, monkeypatch):
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
    status.edit.assert_awaited_once()
    command.delete.assert_not_awaited()
    status.delete.assert_not_awaited()
    channel.send.assert_not_awaited()  # the edited status says it already
