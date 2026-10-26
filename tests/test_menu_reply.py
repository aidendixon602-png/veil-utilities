"""All bot output replies to the command message (the help menu itself is covered in test_help_menu)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from core.utils import Context


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
