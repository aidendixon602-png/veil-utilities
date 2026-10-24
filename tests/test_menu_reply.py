"""Help menu Home button returns to the overview; all bot output replies to the command message."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from core.help import Help, HelpView
from core.utils import Context


def _help():
    h = Help()
    h.context = SimpleNamespace(guild=SimpleNamespace(), clean_prefix=",",
                                author=SimpleNamespace(id=42),
                                bot=SimpleNamespace(user=None, is_owner=AsyncMock(return_value=False)))
    return h


def test_home_button_returns_to_overview():
    async def go():
        view = HelpView(_help(), {"Utility": []}, start="Utility")
        home = next(b for b in view.children if b.label == "Home")
        it = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()),
                             user=SimpleNamespace(id=42))
        await home.callback(it)
        return view, it

    view, it = asyncio.run(go())
    assert view.current is None            # back on the home card
    assert it.response.edit_message.called  # menu updated in place


def _ctx(send_mock):
    msg = SimpleNamespace(to_reference=lambda **_: "msg-ref",
                          channel=SimpleNamespace(send=send_mock), _state=SimpleNamespace())
    return Context(message=msg, bot=None, view=None)


def test_every_response_replies_to_the_command_message():
    send = AsyncMock(return_value="ok")
    result = asyncio.run(_ctx(send).send("hello"))
    assert result == "ok"
    assert send.call_args.kwargs["reference"] == "msg-ref"  # threaded under the command message


def test_send_falls_back_when_the_message_was_deleted():
    gone = discord.NotFound.__new__(discord.NotFound)
    send = AsyncMock(side_effect=[gone, "sent"])
    result = asyncio.run(_ctx(send).send("hello"))
    assert result == "sent" and send.call_count == 2
    assert "reference" not in send.call_args_list[1].kwargs  # plain retry after the reference failed
