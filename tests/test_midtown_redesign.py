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

    assert "Utility" in embed.title
    assert embed.color.value == Colors.main
    assert "**`,userinfo`** `[user]` — Show profile info." in embed.description
    assert "**`,serverinfo`** — Server stats." in embed.description
    assert "**Aliases:**" in embed.description
    assert "`userinfo` ➔ `ui`, `info`" in embed.description
    assert "`serverinfo` ➔ `si`" in embed.description
    assert "Page 1 of 1 • 2 commands • Midtown Events Department" in embed.footer.text


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


def test_ban_log_view_security_and_self_ban_protection():
    from cogs.logging import BanLogView
    view = BanLogView(guild_id=1, user_id=406546241996980225, is_banned=False)

    # 1. Self-ban is blocked
    member = SimpleNamespace(id=406546241996980225, guild_permissions=discord.Permissions.all())
    guild = SimpleNamespace(id=1, owner_id=100, get_member=lambda uid: member)
    it = SimpleNamespace(
        guild=guild,
        user=member,
        client=SimpleNamespace(
            get_guild=lambda gid: guild,
            is_owner=AsyncMock(return_value=False),
            db=SimpleNamespace(get_config=AsyncMock(return_value={})),
        ),
        response=SimpleNamespace(send_message=AsyncMock()),
    )
    asyncio.run(view.toggle_btn.callback(it))
    assert it.response.send_message.called
    msg = it.response.send_message.call_args[0][0]
    assert "cannot ban or unban yourself" in msg or "Only Bot Developers" in msg

    # 2. Random member without uperm is blocked even if they have Discord admin
    random_member = SimpleNamespace(id=999, guild_permissions=discord.Permissions.all())
    it.user = random_member
    it.guild.get_member = lambda uid: random_member
    it.response.send_message.reset_mock()
    asyncio.run(view.toggle_btn.callback(it))
    assert it.response.send_message.called
    assert "Only Bot Developers" in it.response.send_message.call_args[0][0]


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
    # Highest role should win (Head Admin rank 4 > Mod rank 2)
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


def test_unassigned_member_gets_no_bot_perm():
    from core import perms
    # User with Discord admin permissions but NO bot perm rank assigned
    u = SimpleNamespace(id=7777, roles=[], guild_permissions=SimpleNamespace(administrator=True))
    name, source, rank = perms.member_level_info({}, u)
    assert name is None
    assert source is None
    assert rank == 0


def test_role_update_embed_styling():
    from cogs.logging import Logging
    cog = Logging(SimpleNamespace())

    guild = SimpleNamespace(
        id=1,
        icon=None,
        me=SimpleNamespace(guild_permissions=SimpleNamespace(view_audit_log=False)),
    )
    member = SimpleNamespace(
        id=123,
        name="ham6600y",
        display_name="Jacob",
        mention="<@123>",
        display_avatar=SimpleNamespace(url="https://cdn.discordapp.com/avatar.png"),
        guild=guild,
    )
    role_added = SimpleNamespace(id=456, name="Bloxlink Bypass", mention="<@&456>")
    role_removed = SimpleNamespace(id=789, name="Old Role", mention="<@&789>")

    # Test added only -> Colors.success
    embed_add = asyncio.run(cog._build_role_update_embed(member, [role_added], []))
    assert embed_add.color.value == Colors.success
    assert "Roles Added" in embed_add.author.name
    assert "Jacob" in embed_add.author.name
    assert any(f.name.startswith("➕ Added") for f in embed_add.fields)

    # Test removed only -> Colors.error
    embed_rem = asyncio.run(cog._build_role_update_embed(member, [], [role_removed]))
    assert embed_rem.color.value == Colors.error
    assert "Roles Removed" in embed_rem.author.name
    assert any(f.name.startswith("➖ Removed") for f in embed_rem.fields)

    # Test mixed -> Colors.blurple
    embed_mixed = asyncio.run(cog._build_role_update_embed(member, [role_added], [role_removed]))
    assert embed_mixed.color.value == Colors.blurple
    assert "Roles Updated" in embed_mixed.author.name
    assert any(f.name.startswith("➕ Added") for f in embed_mixed.fields)
    assert any(f.name.startswith("➖ Removed") for f in embed_mixed.fields)


def test_dynamic_presence_rotation():
    bot = Veil()
    bot.change_presence = AsyncMock()
    bot.default_prefix = ","
    guild_mock = SimpleNamespace(id=1, member_count=100)
    bot._guilds = {1: guild_mock}

    # First rotation call
    asyncio.run(bot.update_dynamic_status())
    bot.change_presence.assert_called_once()
    act1 = bot.change_presence.call_args[1]["activity"]
    assert "Midtown Events Department" in act1.name

    # Second rotation call
    bot.change_presence.reset_mock()
    asyncio.run(bot.update_dynamic_status())
    bot.change_presence.assert_called_once()
    act2 = bot.change_presence.call_args[1]["activity"]
    assert "veil utilities" in act2.name


def test_dynamic_category_filtering_by_rank():
    all_cats = {
        "Utility": [], "Music": [], "Moderation": [], "Roles": [], "Permissions": [],
        "Antinuke": [], "Automod": [], "Server": [], "VoiceMaster": [], "Community": []
    }

    # 1. Unranked / regular user (rank 0)
    view_unranked = HelpView(_help(), all_cats, author_rank=0, is_owner=False)
    unranked_labels = [b.label for b in view_unranked.children if isinstance(b, CategoryButton)]
    assert set(unranked_labels) == {"Utility", "Music", "VoiceMaster", "Community"}
    assert "Moderation" not in unranked_labels
    assert "Roles" not in unranked_labels
    assert "Permissions" not in unranked_labels
    assert "Antinuke" not in unranked_labels
    assert "Automod" not in unranked_labels
    assert "Server" not in unranked_labels
    assert "Developer" not in unranked_labels
    assert "Secret" not in unranked_labels

    # 2. Helper (rank 1)
    view_helper = HelpView(_help(), all_cats, author_rank=1, is_owner=False)
    helper_labels = [b.label for b in view_helper.children if isinstance(b, CategoryButton)]
    assert "Moderation" in helper_labels
    assert "Roles" not in helper_labels
    assert "Permissions" not in helper_labels
    assert "Antinuke" not in helper_labels

    # 3. Admin (rank 3)
    view_admin = HelpView(_help(), all_cats, author_rank=3, is_owner=False)
    admin_labels = [b.label for b in view_admin.children if isinstance(b, CategoryButton)]
    assert "Moderation" in admin_labels
    assert "Roles" in admin_labels
    assert "Automod" in admin_labels
    assert "Server" in admin_labels
    assert "Permissions" not in admin_labels
    assert "Antinuke" not in admin_labels
    assert "Developer" not in admin_labels

    # 4. Head Admin (rank 4)
    view_headadmin = HelpView(_help(), all_cats, author_rank=4, is_owner=False)
    headadmin_labels = [b.label for b in view_headadmin.children if isinstance(b, CategoryButton)]
    assert "Permissions" in headadmin_labels
    assert "Antinuke" in headadmin_labels
    assert "Developer" not in headadmin_labels
    assert "Secret" not in headadmin_labels

    # 5. Bot Developer / Owner (is_owner=True)
    view_owner = HelpView(_help(), all_cats, is_owner=True)
    owner_labels = [b.label for b in view_owner.children if isinstance(b, CategoryButton)]
    assert "Developer" in owner_labels
    assert "Secret" in owner_labels
    assert "Permissions" in owner_labels
    assert "Antinuke" in owner_labels


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
    assert category_for_command(ban_cmd, bot) == "Moderation"
    assert min_rank_for_command(ban_cmd, bot) == 3  # Admin (rank 3)

    kick_cmd = mod_cog.kick
    assert min_rank_for_command(kick_cmd, bot) == 2  # Mod (rank 2)

    purge_cmd = mod_cog.purge
    assert min_rank_for_command(purge_cmd, bot) == 1  # Helper (rank 1)


def test_moderation_uperm_predicates():
    from cogs.moderation import Moderation

    bot = Veil()
    mod_cog = Moderation(bot)

    # Test ban requires admin uperm
    ban_predicate = next(c for c in mod_cog.ban.checks if getattr(c, "__name__", "") == "predicate")

    ctx_regular = SimpleNamespace(
        guild=SimpleNamespace(id=1, owner_id=100),
        author=SimpleNamespace(id=999, roles=[], guild_permissions=SimpleNamespace(administrator=False)),
        bot=SimpleNamespace(owner_ids=frozenset(), db=SimpleNamespace(get_config=AsyncMock(return_value={})))
    )
    with pytest.raises(UserError, match="user permission"):
        asyncio.run(ban_predicate(ctx_regular))

    # Helper fails ban
    cfg_helper = {"users": {"200": "helper"}}
    ctx_helper = SimpleNamespace(
        guild=SimpleNamespace(id=1, owner_id=100),
        author=SimpleNamespace(id=200, roles=[], guild_permissions=SimpleNamespace(administrator=False)),
        bot=SimpleNamespace(owner_ids=frozenset(), db=SimpleNamespace(get_config=AsyncMock(return_value=cfg_helper)))
    )
    with pytest.raises(UserError, match="user permission"):
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
    assert sent_embeds[0].title == "ban"


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
        ("kick", "mod"),
        ("timeout", "mod"),
        ("untimeout", "mod"),
        ("warn", "mod"),
        ("clearwarns", "mod"),
        ("delwarn", "mod"),
        ("purge", "helper"),
        ("botclear", "helper"),
    ]

    for cmd_name, expected_level in checked_commands:
        cmd = getattr(mod_cog, cmd_name)
        perms_list = getattr(cmd.callback, "__veil_perms__", [])
        assert any(f"uperm: {expected_level}" in p for p in perms_list), f"Command {cmd_name} missing uperm: {expected_level}"


def test_home_embed_and_view_strict_uperm_filtering():
    h = _help()

    # 1. Unranked (rank 0) home embed must NOT reveal Staff & Administration
    unranked_embed = h.home_embed(author_rank=0)
    unranked_field_names = [f.name for f in unranked_embed.fields]
    assert "🛠️ Public & Community Modules" in unranked_field_names
    assert "🛡️ Staff & Administration" not in unranked_field_names

    # 2. Staff (rank 1+) home embed reveals Staff & Administration
    staff_embed = h.home_embed(author_rank=1)
    staff_field_names = [f.name for f in staff_embed.fields]
    assert "🛠️ Public & Community Modules" in staff_field_names
    assert "🛡️ Staff & Administration" in staff_field_names

    # 3. Unranked HelpView must ONLY contain public category buttons
    categories = {"Utility": [], "Music": [], "Moderation": [], "Roles": [], "Antinuke": []}
    unranked_view = HelpView(h, categories, author_rank=0, is_owner=False)
    unranked_labels = [b.label for b in unranked_view.children if isinstance(b, CategoryButton)]
    assert "Utility" in unranked_labels
    assert "Music" in unranked_labels
    assert "Moderation" not in unranked_labels
    assert "Roles" not in unranked_labels
    assert "Antinuke" not in unranked_labels

    # 4. Admin (rank 3) HelpView contains Moderation and Roles, but NOT Antinuke (rank 4)
    admin_view = HelpView(h, categories, author_rank=3, is_owner=False)
    admin_labels = [b.label for b in admin_view.children if isinstance(b, CategoryButton)]
    assert "Utility" in admin_labels
    assert "Music" in admin_labels
    assert "Moderation" in admin_labels
    assert "Roles" in admin_labels
    assert "Antinuke" not in admin_labels


def test_help_view_on_timeout_deletes_message():
    h = _help()
    view = HelpView(h, {"Utility": []}, author_rank=0)
    msg = SimpleNamespace(delete=AsyncMock())
    view.message = msg

    asyncio.run(view.on_timeout())
    assert msg.delete.called






