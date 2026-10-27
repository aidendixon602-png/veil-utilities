"""`,presence clear` (and `,setstatus clear`) remove a custom status text instead of saving the word "clear"."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from cogs.owner import RESET_WORDS, Owner


def setup(saved):
    store = {"global": saved}
    db = SimpleNamespace(get_config=AsyncMock(side_effect=lambda gid, mod: store[mod]),
                         set_config=AsyncMock(side_effect=lambda gid, mod, data: store.__setitem__(mod, data)))
    bot = SimpleNamespace(db=db, presence_status=discord.Status.dnd, update_dynamic_status=AsyncMock(),
                          change_presence=AsyncMock())
    cog = Owner.__new__(Owner)
    cog.bot = bot
    return cog, SimpleNamespace(approve=AsyncMock()), store


@pytest.mark.parametrize("word", ["clear", "CLEAR", "reset", "default"])
def test_presence_clear_removes_the_text_and_keeps_the_status(word):
    cog, ctx, store = setup({"custom_status_text": "clear", "presence_status": "dnd"})
    asyncio.run(Owner.setpresence.callback(cog, ctx, word))
    assert "custom_status_text" not in store["global"] and store["global"]["presence_status"] == "dnd"
    cog.bot.update_dynamic_status.assert_awaited_once()
    cog.bot.change_presence.assert_not_awaited()  # never shows the word as a status
    assert "cleared the status text" in ctx.approve.await_args.args[0]


def test_setstatus_uses_the_same_words():
    cog, ctx, store = setup({"custom_status_text": "old"})
    asyncio.run(Owner.setstatus.callback(cog, ctx, arg="none"))
    assert "custom_status_text" not in store["global"] and "none" in RESET_WORDS


def test_other_words_still_set_text():
    cog, ctx, store = setup({})
    asyncio.run(Owner.setpresence.callback(cog, ctx, "Protecting", text="Midtown"))
    assert store["global"]["custom_status_text"] == "Protecting Midtown"
