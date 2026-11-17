"""Temporary command menus belong to their requester, including against bot developers."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from core.help import Help, HelpView
from core.staffpanel import StaffPanel
from core.utils import OwnedView, Paginator


def test_owned_command_menus_reject_other_users_even_developers():
    async def go():
        help_command = Help()
        help_command.context = SimpleNamespace(author=SimpleNamespace(id=42), guild=None, clean_prefix=',',
                                               bot=SimpleNamespace(user=None))
        ctx = SimpleNamespace(author=SimpleNamespace(id=42))
        views = [OwnedView(42), Paginator(42, [discord.Embed(title='one')]),
                 HelpView(help_command, {}), StaffPanel(ctx, None, 'Staff')]
        for view in views:
            it = SimpleNamespace(user=SimpleNamespace(id=99), client=SimpleNamespace(is_owner=AsyncMock(return_value=True)),
                                 response=SimpleNamespace(send_message=AsyncMock()))
            assert not await view.interaction_check(it)
            assert it.response.send_message.await_args.kwargs['ephemeral'] is True
            assert 'Only <@42>' in it.response.send_message.await_args.args[0]
            it.user.id = 42
            assert await view.interaction_check(it)
            view.stop()
    asyncio.run(go())
