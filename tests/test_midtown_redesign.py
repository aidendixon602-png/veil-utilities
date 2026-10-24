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


def test_logging_channel_auto_detection():
    from cogs.logging import Logging
    bot = Veil()
    cog = Logging(bot)
    bot.db = SimpleNamespace(
        get_config=AsyncMock(return_value={}),  # no explicit config
    )
    ch_join = SimpleNamespace(id=1, name="join-logs", __class__=discord.TextChannel)
    ch_bans = SimpleNamespace(id=2, name="bans", __class__=discord.TextChannel)
    ch_cmd = SimpleNamespace(id=3, name="command-logs", __class__=discord.TextChannel)
    ch_gen = SimpleNamespace(id=4, name="veil-utilities-logging", __class__=discord.TextChannel)
    guild = SimpleNamespace(id=10, text_channels=[ch_join, ch_bans, ch_cmd, ch_gen], get_channel=lambda cid: None)

    assert asyncio.run(cog.channel_for(guild, "joinleave")) == ch_join
    assert asyncio.run(cog.channel_for(guild, "bans")) == ch_bans
    assert asyncio.run(cog.channel_for(guild, "commands")) == ch_cmd
    assert asyncio.run(cog.channel_for(guild, "server")) == ch_gen


def test_ban_log_view_toggle():
    from cogs.logging import BanLogView
    view = BanLogView(guild_id=1, user_id=123, is_banned=True)
    assert view.toggle_btn.label == "Unban"
    assert view.toggle_btn.style == discord.ButtonStyle.success

    view.is_banned = False
    view._sync()
    assert view.toggle_btn.label == "Reban"
    assert view.toggle_btn.style == discord.ButtonStyle.danger


def test_help_home_view_shows_secret_and_dev_for_owner():
    view = HelpView(_help(), {"Utility": [], "Music": [], "Moderation": []}, is_owner=True)
    labels = [b.label for b in view.children if isinstance(b, CategoryButton)]
    assert "Secret" in labels
    assert "Developer" in labels


def test_default_log_channels_auto_creation():
    from cogs.logging import Logging
    bot = Veil()
    cog = Logging(bot)
    bot.db = SimpleNamespace(
        get_config=AsyncMock(return_value={}),
        set_config=AsyncMock(),
    )
    created_channels = []

    class MockObj:
        def __init__(self, id, **kwargs):
            self.id = id
            for k, v in kwargs.items():
                setattr(self, k, v)

        def __hash__(self):
            return self.id

        def __eq__(self, other):
            return getattr(other, "id", None) == self.id

    async def fake_create_channel(name, **kwargs):
        ch = SimpleNamespace(id=len(created_channels) + 1, name=name, __class__=discord.TextChannel)
        created_channels.append(ch)
        return ch

    async def fake_create_category(name, **kwargs):
        return SimpleNamespace(id=99, name=name)

    default_role = MockObj(1)
    me = MockObj(
        2,
        guild_permissions=SimpleNamespace(manage_channels=True),
    )
    guild = SimpleNamespace(
        id=10,
        me=me,
        default_role=default_role,
        text_channels=[],
        categories=[],
        create_category=fake_create_category,
        create_text_channel=fake_create_channel,
        get_channel=lambda cid: None,
    )

    created = asyncio.run(cog.ensure_default_channels(guild))
    assert len(created) == 4
    created_names = [c.name for c in created]
    assert "join-logs" in created_names
    assert "bans" in created_names
    assert "command-logs" in created_names
    assert "veil-utilities-logging" in created_names


def test_role_based_permlevels():
    from core import perms
    cfg = {
        "users": {"123": "admin"},
        "roles": {"456": "mod", "789": "headadmin"},
    }
    user_direct = SimpleNamespace(id=123, roles=[])
    name, source, rank = perms.member_level_info(cfg, user_direct)
    assert name == "Admin"
    assert source == "Permissions attached to user"
    assert rank == 3

    role_mock1 = SimpleNamespace(id=456, mention="<@&456>")
    role_mock2 = SimpleNamespace(id=789, mention="<@&789>")
    user_with_roles = SimpleNamespace(id=999, roles=[role_mock1, role_mock2])
    name, source, rank = perms.member_level_info(cfg, user_with_roles)
    # Highest role should win (Head Admin > Mod)
    assert name == "Head Admin"
    assert source == "<@&789>"
    assert rank == 4


def test_botperms_command_registered():
    bot = Veil()
    asyncio.run(bot.load_extension("cogs.permissions"))
    cmd = bot.get_command("botperms")
    assert cmd is not None
    assert "botpermissions" in cmd.aliases
    assert "botperm" in cmd.aliases


def test_code_defined_permissions():
    from core import perms
    perms.CODE_USERS[12345] = "supremeleader"
    perms.CODE_ROLES["Admin Role"] = "admin"
    try:
        u = SimpleNamespace(id=12345, roles=[])
        name, source, rank = perms.member_level_info({}, u)
        assert name == "Supreme Leader"
        assert source == "Built-in"
        assert rank == 7

        r = SimpleNamespace(id=9999, name="Admin Role", mention="<@&9999>")
        u2 = SimpleNamespace(id=8888, roles=[r])
        name2, source2, rank2 = perms.member_level_info({}, u2)
        assert name2 == "Admin"
        assert source2 == "Built-in"
        assert rank2 == 3
    finally:
        perms.CODE_USERS.pop(12345, None)
        perms.CODE_ROLES.pop("Admin Role", None)


def test_bot_developer_builtin_logging():
    from core import perms
    perms.BOT_DEVELOPERS.append(55555)
    try:
        u = SimpleNamespace(id=55555, roles=[])
        name, source, rank = perms.member_level_info({}, u)
        assert name == "Bot Developer"
        assert source == "Built-in"
        assert rank == perms.DEV_RANK
    finally:
        perms.BOT_DEVELOPERS.remove(55555)


