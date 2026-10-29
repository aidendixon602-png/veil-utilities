"""Tests for Midtown Utilities redesign: help menu, permlevels, staff-only mode, and command protections."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from core.bot import Veil
from core.help import Help, HelpView
from core.utils import UserError


def _help():
    h = Help()
    h.context = SimpleNamespace(
        guild=SimpleNamespace(),
        clean_prefix=",",
        author=SimpleNamespace(id=42),
        bot=SimpleNamespace(user=None, is_owner=AsyncMock(return_value=False)),
    )
    return h


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
    assert asyncio.run(cog.channel_for(guild, "bans")) is None  # #bans belongs to Ghost Vanguard
    assert asyncio.run(cog.channel_for(guild, "commands")) == ch_cmd
    assert asyncio.run(cog.channel_for(guild, "server")) == ch_gen


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
    # Highest role should win (Server Manager rank 4 > Moderator rank 2)
    assert name == "Server Manager"
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
        assert name == "Owner"
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
        assert name == "Developer"
        assert source == "Built-in"
        assert rank == perms.DEV_RANK
    finally:
        perms.BOT_DEVELOPERS.remove(55555)


def test_unassigned_member_gets_no_bot_perm():
    from core import perms
    # User with Discord admin permissions but NO bot perm rank assigned
    u = SimpleNamespace(id=7777, roles=[], guild_permissions=SimpleNamespace(administrator=True))
    name, source, rank = perms.member_level_info({}, u)
    assert name is None
    assert source is None
    assert rank == 0


def test_presence_is_only_the_protecting_line():
    bot = Veil()
    bot.change_presence = AsyncMock()
    bot.default_prefix = ","
    bot._connection._guilds = {1: SimpleNamespace(id=1, member_count=100), 2: SimpleNamespace(id=2, member_count=1204)}

    for _ in range(3):  # it used to rotate through several lines; now every refresh says the same thing
        bot.change_presence.reset_mock()
        asyncio.run(bot.update_dynamic_status())
        bot.change_presence.assert_called_once()
        assert bot.change_presence.call_args[1]["activity"].name == "Protecting 2 guilds | 1,304 users"


def test_command_category_and_rank_resolution():
    from core.help import category_for_command, min_rank_for_command
    from cogs.moderation import Moderation

    bot = Veil()
    mod_cog = Moderation(bot)
    asyncio.run(bot.add_cog(mod_cog))

    # Public command
    dummy_pub = SimpleNamespace(name="ping", qualified_name="ping", cog_name="Utility", callback=lambda: None)
    assert category_for_command(dummy_pub, bot) == "Utility"
    assert min_rank_for_command(dummy_pub, bot) == 0

    # Moderation commands with uperm
    ban_cmd = mod_cog.ban
    assert category_for_command(ban_cmd, bot) == "Admin"
    assert category_for_command(mod_cog.kick, bot) == "Moderator"
    assert min_rank_for_command(ban_cmd, bot) == 3  # Admin (rank 3)

    kick_cmd = mod_cog.kick
    assert min_rank_for_command(kick_cmd, bot) == 2  # Moderator (rank 2)

    purge_cmd = mod_cog.purge
    assert min_rank_for_command(purge_cmd, bot) == 1  # Helper (rank 1)


def test_moderation_uperm_predicates():
    from cogs.moderation import Moderation

    bot = Veil()
    mod_cog = Moderation(bot)

    # Test ban requires admin uperm
    # discord.py reverses a shared checks list every time a cog is created, so pick the check by what it is, not where it is
    ban_predicate = next(c for c in mod_cog.ban.checks if "requires_uperm" in getattr(c, "__qualname__", ""))

    ctx_regular = SimpleNamespace(
        guild=SimpleNamespace(id=1, owner_id=100),
        author=SimpleNamespace(id=999, roles=[], guild_permissions=SimpleNamespace(administrator=False)),
        bot=SimpleNamespace(owner_ids=frozenset(), db=SimpleNamespace(get_config=AsyncMock(return_value={})))
    )
    with pytest.raises(UserError, match="permission level or higher"):
        asyncio.run(ban_predicate(ctx_regular))

    # Helper fails ban
    cfg_helper = {"users": {"200": "helper"}}
    ctx_helper = SimpleNamespace(
        guild=SimpleNamespace(id=1, owner_id=100),
        author=SimpleNamespace(id=200, roles=[], guild_permissions=SimpleNamespace(administrator=False)),
        bot=SimpleNamespace(owner_ids=frozenset(), db=SimpleNamespace(get_config=AsyncMock(return_value=cfg_helper)))
    )
    with pytest.raises(UserError, match="permission level or higher"):
        asyncio.run(ban_predicate(ctx_helper))

    # Admin passes ban
    cfg_admin = {"users": {"300": "admin"}}
    ctx_admin = SimpleNamespace(
        guild=SimpleNamespace(id=1, owner_id=100),
        author=SimpleNamespace(id=300, roles=[], guild_permissions=SimpleNamespace(administrator=False)),
        bot=SimpleNamespace(owner_ids=frozenset(), db=SimpleNamespace(get_config=AsyncMock(return_value=cfg_admin)))
    )
    assert asyncio.run(ban_predicate(ctx_admin)) is True


def test_direct_help_lookup_permission_gating():
    from core.help import Help
    from cogs.moderation import Moderation

    bot = Veil()
    mod_cog = Moderation(bot)
    asyncio.run(bot.add_cog(mod_cog))
    bot.db = SimpleNamespace(get_config=AsyncMock(return_value={}))

    h = Help()
    sent_errors = []
    sent_embeds = []

    async def mock_error(msg):
        sent_errors.append(msg)

    async def mock_send(embed=None, **kwargs):
        if embed:
            sent_embeds.append(embed)

    h.send_error_message = mock_error

    # Context for unranked member (Joachim)
    ctx_unranked = SimpleNamespace(
        guild=SimpleNamespace(id=1, owner_id=100),
        author=SimpleNamespace(id=999, roles=[], guild_permissions=SimpleNamespace(administrator=False)),
        bot=bot,
        clean_prefix=",",
        send=mock_send,
    )
    h.context = ctx_unranked

    # 1. Unranked runs `,help moderation`
    sent_errors.clear()
    asyncio.run(h.command_callback(ctx_unranked, command="moderation"))
    assert len(sent_errors) == 1
    assert 'No command or category called "moderation" found.' in sent_errors[0]

    # 2. Unranked runs `,help ban`
    sent_errors.clear()
    asyncio.run(h.send_command_help(mod_cog.ban))
    assert len(sent_errors) == 1
    assert 'No command called "ban" found.' in sent_errors[0]

    # 3. Context for Admin member (rank 3)
    bot.db.get_config = AsyncMock(return_value={"users": {"300": "admin"}})
    ctx_admin = SimpleNamespace(
        guild=SimpleNamespace(id=1, owner_id=100),
        author=SimpleNamespace(id=300, roles=[], guild_permissions=SimpleNamespace(administrator=False)),
        bot=bot,
        clean_prefix=",",
        send=mock_send,
    )
    h.context = ctx_admin

    # Admin runs `,help ban` -> passes and sends embed
    sent_errors.clear()
    sent_embeds.clear()
    asyncio.run(h.send_command_help(mod_cog.ban))
    assert len(sent_errors) == 0
    assert len(sent_embeds) == 1
    assert sent_embeds[0].title == "Command: ban"


def test_all_moderation_actions_have_uperm_checks():
    from cogs.moderation import Moderation

    bot = Veil()
    mod_cog = Moderation(bot)

    checked_commands = [
        ("ban", "admin"),
        ("unban", "admin"),
        ("softban", "admin"),
        ("massban", "admin"),
        ("masskick", "admin"),
        ("kick", "moderator"),
        ("timeout", "moderator"),
        ("untimeout", "moderator"),
        ("warn", "moderator"),
        ("clearwarns", "moderator"),
        ("delwarn", "moderator"),
        ("purge", "helper"),
        ("botclear", "helper"),
    ]

    for cmd_name, expected_level in checked_commands:
        cmd = getattr(mod_cog, cmd_name)
        perms_list = getattr(cmd.callback, "__veil_perms__", [])
        assert any(f"uperm: {expected_level}" in p for p in perms_list), f"Command {cmd_name} missing uperm: {expected_level}"


def test_help_view_on_timeout_keeps_the_message():
    h = _help()
    view = HelpView(h, {"Utility": []}, author_rank=0)
    msg = SimpleNamespace(delete=AsyncMock(), edit=AsyncMock())
    view.message = msg

    asyncio.run(view.on_timeout())
    assert not msg.delete.called and msg.edit.await_args.kwargs == {"view": None}


def test_presence_text_pluralises_and_groups_thousands():
    from core.bot import protecting_text
    assert protecting_text(1, 1) == "Protecting 1 guild | 1 user"
    assert protecting_text(0, 0) == "Protecting 0 guilds | 0 users"
    assert protecting_text(12, 3456789) == "Protecting 12 guilds | 3,456,789 users"


def test_an_old_saved_status_is_forgotten_once_so_the_new_line_shows():
    saved = {"custom_status_text": "✨ Protecting Midtown Community • ,help", "presence_status": "dnd"}
    assert Veil.forget_old_status(saved) is True
    assert "custom_status_text" not in saved and saved["presence_status"] == "dnd"
    saved["custom_status_text"] = "set later with ,setstatus"
    assert Veil.forget_old_status(saved) is False       # only ever once
    assert saved["custom_status_text"] == "set later with ,setstatus"
