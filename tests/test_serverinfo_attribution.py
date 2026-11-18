"""Midtown attribution belongs in command help, not arbitrary server details."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
from discord.ext import commands

from cogs.utility import Utility
from core.help import Help


def test_serverinfo_aliases_share_attributed_help():
    bot = commands.Bot(command_prefix=",", intents=discord.Intents.none())
    bot.add_command(Utility.serverinfo.copy())
    help_cmd = Help()
    help_cmd.context = SimpleNamespace(clean_prefix=",", bot=bot)

    for name in ("serverinfo", "si", "guildinfo"):
        command = bot.get_command(name)
        assert command is bot.get_command("serverinfo")
        description = help_cmd.command_embed(command).description
        assert "Property of Midtown Genre Owner: simisear" in description
        assert "843909457616044092" in description


def test_serverinfo_shows_real_server_description_and_owner():
    guild = SimpleNamespace(
        name="Test server", description="A real server description", icon=None, banner=None,
        owner_id=1234, created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        member_count=1, members=[], text_channels=[], voice_channels=[], categories=[], roles=[],
        emojis=[], emoji_limit=50, premium_subscription_count=0, premium_tier=0,
        verification_level="none", vanity_url_code=None, id=5678,
    )
    ctx = SimpleNamespace(guild=guild, send=AsyncMock())
    asyncio.run(Utility.serverinfo.callback(Utility(SimpleNamespace()), ctx))

    embed = ctx.send.await_args.kwargs["embed"]
    assert embed.description == "A real server description"
    assert next(field.value for field in embed.fields if field.name == "Owner") == "<@1234>"
    assert "simisear" not in str(embed.to_dict())
