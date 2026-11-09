"""First-message links render cleanly without exposing inaccessible channels."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from cogs.utility import Utility
from core.utils import UserError


def setup(*, user_view=True, user_history=True, bot_view=True, bot_history=True, messages=True):
    author, me = SimpleNamespace(id=1), SimpleNamespace(id=2)
    guild = SimpleNamespace(id=123, me=me)
    found = SimpleNamespace(jump_url="https://discord.com/channels/123/456/789", author=SimpleNamespace(mention="<@5>"),
                            created_at=datetime(2025, 1, 1, tzinfo=timezone.utc))

    async def history(**kwargs):
        assert kwargs == {"limit": 1, "oldest_first": True}
        if messages:
            yield found

    channel = SimpleNamespace(guild=guild, mention="<#456>", history=MagicMock(side_effect=history))
    channel.permissions_for = lambda who: (discord.Permissions(view_channel=user_view, read_message_history=user_history)
                                           if who is author else discord.Permissions(view_channel=bot_view,
                                                                                     read_message_history=bot_history))
    ctx = SimpleNamespace(author=author, guild=guild, channel=channel, neutral=AsyncMock())
    return Utility.__new__(Utility), ctx, channel


def test_link_label_contains_no_channel_mention_and_preserves_metadata():
    cog, ctx, channel = setup()
    asyncio.run(Utility.firstmessage.callback(cog, ctx))
    text = ctx.neutral.await_args.args[0]
    assert text == "[first message](https://discord.com/channels/123/456/789) in <#456> by <@5> <t:1735689600:R>"
    channel.history.assert_called_once_with(limit=1, oldest_first=True)


@pytest.mark.parametrize("settings, message", [
    ({"user_view": False}, "You need View Channel"),
    ({"user_history": False}, "You need View Channel"),
    ({"bot_view": False}, "I need View Channel"),
    ({"bot_history": False}, "I need View Channel"),
])
def test_unavailable_history_is_never_read_or_exposed(settings, message):
    cog, ctx, channel = setup(**settings)
    with pytest.raises(UserError, match=message):
        asyncio.run(Utility.firstmessage.callback(cog, ctx, channel))
    channel.history.assert_not_called()
    ctx.neutral.assert_not_awaited()


def test_wrong_guild_or_missing_bot_identity_fails_closed():
    cog, ctx, channel = setup()
    channel.guild = SimpleNamespace(id=999)
    with pytest.raises(UserError, match="isn't in this server"):
        asyncio.run(Utility.firstmessage.callback(cog, ctx, channel))
    channel.history.assert_not_called()
    channel.guild = ctx.guild
    ctx.guild.me = None
    with pytest.raises(UserError, match="can't verify"):
        asyncio.run(Utility.firstmessage.callback(cog, ctx, channel))
    channel.history.assert_not_called()


def test_no_messages_and_discord_forbidden_do_not_report_success():
    cog, ctx, channel = setup(messages=False)
    with pytest.raises(UserError, match="No messages found"):
        asyncio.run(Utility.firstmessage.callback(cog, ctx, channel))
    ctx.neutral.assert_not_awaited()

    async def forbidden(**kwargs):
        raise discord.Forbidden(MagicMock(status=403, reason="Forbidden"), "Forbidden")
        yield  # async generator, like discord.py's history

    channel.history = MagicMock(side_effect=forbidden)
    with pytest.raises(UserError, match="can't read message history"):
        asyncio.run(Utility.firstmessage.callback(cog, ctx, channel))
    ctx.neutral.assert_not_awaited()


def test_network_ban_help_explains_required_target_and_confirmation():
    from cogs.devtools import DevTools
    from core.help import Help

    help_command = Help()
    help_command.context = SimpleNamespace(clean_prefix=",", bot=SimpleNamespace(user=None), guild=None)
    card = help_command.command_embed(DevTools.genreban)
    assert "user mention or ID" in card.description
    assert "confirm before any action" in card.description
    assert "**Usage** `,genreban <user> [reason]`" in card.description
    assert "cannot guarantee" in card.description
    assert DevTools.genreban.hidden


def test_uncached_thread_parent_fails_closed():
    cog, ctx, channel = setup()
    channel.permissions_for = MagicMock(side_effect=discord.ClientException("Parent channel not found"))
    with pytest.raises(UserError, match="can't verify channel permissions"):
        asyncio.run(Utility.firstmessage.callback(cog, ctx))
    channel.history.assert_not_called()
    ctx.neutral.assert_not_awaited()
