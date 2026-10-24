"""Tests for Midtown Utilities redesign: help menu, permlevels, staff-only mode, and command protections."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from core.bot import Veil
from core.help import BackButton, CategoryButton, Help, HelpView
from core.utils import Colors, UserError


def _help():
    h = Help()
    h.context = SimpleNamespace(
        guild=SimpleNamespace(),
        clean_prefix=",",
        author=SimpleNamespace(id=42),
        bot=SimpleNamespace(user=None, is_owner=AsyncMock(return_value=False)),
    )
    return h


def test_help_home_view_has_no_secret_or_dev_buttons():
    view = HelpView(_help(), {"Utility": [], "Music": [], "Moderation": []})
    labels = [b.label for b in view.children if isinstance(b, CategoryButton)]
    assert "Secret" not in labels
    assert "Developer" not in labels
    assert all(not isinstance(b, BackButton) for b in view.children)


def test_help_category_view_has_back_button():
    view = HelpView(_help(), {"Utility": []}, start="Utility")
    assert any(isinstance(b, BackButton) and b.label == "Back" for b in view.children)


def test_help_back_button_returns_to_home():
    async def go():
        view = HelpView(_help(), {"Utility": []}, start="Utility")
        back_btn = next(b for b in view.children if isinstance(b, BackButton))
        it = SimpleNamespace(
            response=SimpleNamespace(edit_message=AsyncMock()),
            user=SimpleNamespace(id=42),
        )
        await back_btn.callback(it)
        return view, it

    view, it = asyncio.run(go())
    assert view.current is None
    assert it.response.edit_message.called


def test_category_embed_formatting():
    h = _help()
    cmd1 = SimpleNamespace(name="userinfo", signature="[user]", short_doc="Show profile info", aliases=["ui", "info"])
    cmd2 = SimpleNamespace(name="serverinfo", signature="", short_doc="Server stats", aliases=["si"])
    embed = h.category_embed("Utility", [cmd1, cmd2], page=0)

    assert embed.title == "Utility"
    assert embed.color.value == Colors.mint
    assert "`,userinfo [user]` - show profile info" in embed.description
    assert "`,serverinfo` - server stats" in embed.description
    assert "**Aliases:**" in embed.description
    assert "`userinfo` -> `ui` `info`" in embed.description
    assert "`serverinfo` -> `si`" in embed.description
    assert "prefix: ," in embed.description


def test_perm_alias_on_userperm():
    bot = Veil()
    asyncio.run(bot.load_extension("cogs.permissions"))
    cmd = bot.get_command("perm")
    assert cmd is not None
    assert cmd.name == "userperm" or "perm" in cmd.aliases


def test_staff_only_blocks_strangers():
    bot = Veil()
    bot.owner_ids = frozenset([1])
    bot.auth_required = False
    bot.db = SimpleNamespace(
        get_config=AsyncMock(side_effect=lambda gid, mod: {"staff_only": True} if mod == "settings" else {}),
    )
    author = SimpleNamespace(
        id=999,
        roles=[],
        guild_permissions=discord.Permissions.none(),
    )
    guild = SimpleNamespace(id=1, owner_id=100)
    channel = SimpleNamespace(id=10)
    cmd = SimpleNamespace(hidden=False, parents=[], qualified_name="ping", root_parent=None, name="ping")
    ctx = SimpleNamespace(
        guild=guild,
        channel=channel,
        author=author,
        command=cmd,
        cog=None,
    )

    with pytest.raises(UserError, match="staff-only"):
        asyncio.run(bot._global_check(ctx))


def test_staff_only_allows_server_owner():
    bot = Veil()
    bot.owner_ids = frozenset([1])
    bot.auth_required = False
    bot.db = SimpleNamespace(
        get_config=AsyncMock(side_effect=lambda gid, mod: {"staff_only": True} if mod == "settings" else {}),
    )
    author = SimpleNamespace(
        id=100,  # owner
        roles=[],
        guild_permissions=discord.Permissions.none(),
    )
    guild = SimpleNamespace(id=1, owner_id=100)
    channel = SimpleNamespace(id=10)
    cmd = SimpleNamespace(hidden=False, parents=[], qualified_name="ping", root_parent=None, name="ping")
    ctx = SimpleNamespace(
        guild=guild,
        channel=channel,
        author=author,
        command=cmd,
        cog=None,
    )

    allowed = asyncio.run(bot._global_check(ctx))
    assert allowed is True


def test_bot_channels_restriction():
    bot = Veil()
    bot.owner_ids = frozenset([1])
    bot.auth_required = False
    bot.db = SimpleNamespace(
        get_config=AsyncMock(
            side_effect=lambda gid, mod: {"bot_channels": [555]} if mod == "settings" else {}
        ),
    )
    author = SimpleNamespace(
        id=999,
        roles=[],
        guild_permissions=discord.Permissions.none(),
    )
    guild = SimpleNamespace(id=1, owner_id=100)
    channel = SimpleNamespace(id=10)  # not in bot_channels (555)
    cmd = SimpleNamespace(hidden=False, parents=[], qualified_name="ping", root_parent=None, name="ping")
    ctx = SimpleNamespace(
        guild=guild,
        channel=channel,
        author=author,
        command=cmd,
        cog=None,
    )

    with pytest.raises(UserError, match="restricted to <#555>"):
        asyncio.run(bot._global_check(ctx))
