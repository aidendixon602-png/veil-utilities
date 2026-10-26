"""Unit tests for Midtown Antinuke Status embed, rules configuration, presets, and anti-spam rate limiting."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from cogs.antinuke import Antinuke, PRESETS, parse_timeframe
from cogs.moderation import Moderation
from core.bot import Veil
from core.db import Database
from core.utils import Colors, Ignored, UserError

OPEN_DBS = []


def run(coro_fn):
    async def wrapper():
        try:
            await coro_fn()
        finally:
            while OPEN_DBS:
                await OPEN_DBS.pop().close()
    asyncio.run(wrapper())


async def make_test_setup():
    db = Database(":memory:")
    await db.connect()
    OPEN_DBS.append(db)
    bot = SimpleNamespace(
        db=db,
        user=SimpleNamespace(id=999),
        is_owner=AsyncMock(return_value=False),
        get_user=lambda _: None,
        fetch_user=AsyncMock(),
    )
    cog = Antinuke.__new__(Antinuke)
    Antinuke.__init__(cog, bot)
    return db, cog


def test_parse_timeframe():
    assert parse_timeframe(60) == 60
    assert parse_timeframe("60s") == 60
    assert parse_timeframe("120s") == 120
    assert parse_timeframe("2m") == 120
    assert parse_timeframe("1h") == 3600
    with pytest.raises(UserError):
        parse_timeframe("invalid")


def test_midtown_antinuke_status_embed_format():
    async def _run():
        db, cog = await make_test_setup()
        guild_id = 12345
        # 1. Test when disabled
        embed = await cog.build_status_embed(guild_id)
        assert embed.title == "Antinuke Status"
        assert embed.color.value == Colors.mint
        assert "Antinuke is currently **disabled**." in embed.description
        assert "**Rules**" in embed.description

        # Verify all 12 Midtown rules are present
        assert "`Ban` - **3 in 60s → ban** `[revert]`" in embed.description
        assert "`Bot Add` - **1 in 60s → ban** `[revert]`" in embed.description
        assert "`Channel Create` - **2 in 120s → kick** `[revert]`" in embed.description
        assert "`Channel Delete` - **2 in 120s → ban** `[revert]`" in embed.description
        assert "`Kick` - **3 in 120s → ban**" in embed.description
        assert "`Permission Grant` (administrator, ban_members, kick_members, manage_channels, manage_guild, manage_webhooks) - **1 in 60s → strip** `[revert]`" in embed.description
        assert "`Role Create` - **5 in 120s → strip** `[revert]`" in embed.description
        assert "`Role Delete` - **5 in 120s → strip** `[revert]`" in embed.description
        assert "`Invite Spam` - **10 in 60s → strip** `[revert]`" in embed.description
        assert "`Server Update` - **1 in 60s → strip** `[revert]`" in embed.description
        assert "`Timeout` - **5 in 120s → strip** `[revert]`" in embed.description
        assert "`Webhook` - **1 in 60s → kick** `[revert]`" in embed.description

        # Verify footer syntax
        footer = ",an on/off · ,an <action> <count> <punishment> <timeframe> · ,an whitelist/unwhitelist <user> · ,an reset <action> · ,an preset save/load <name>"
        assert footer in embed.description

        # 2. Test when enabled
        cfg = await cog.cfg(guild_id)
        cfg["enabled"] = True
        await cog.save(guild_id, cfg)
        embed_enabled = await cog.build_status_embed(guild_id)
        assert "Antinuke is currently **enabled**." in embed_enabled.description
    run(_run)


def test_rule_updating_and_presets():
    async def _run():
        db, cog = await make_test_setup()
        guild_id = 999
        guild = SimpleNamespace(id=guild_id, owner_id=1, me=SimpleNamespace(top_role=10))
        author = SimpleNamespace(id=1, guild_permissions=SimpleNamespace(administrator=True))
        ctx = SimpleNamespace(guild=guild, author=author, approve=AsyncMock(), neutral=AsyncMock())

        # Update a rule
        await cog._update_rule(ctx, "ban", 5, "kick", "30s")
        cfg = await cog.cfg(guild_id)
        assert cfg["modules"]["ban"]["threshold"] == 5
        assert cfg["modules"]["ban"]["window"] == 30
        assert cfg["modules"]["ban"]["punishment"] == "kick"

        embed = await cog.build_status_embed(guild_id)
        assert "`Ban` - **5 in 30s → kick** `[revert]`" in embed.description

        # Test preset save and load
        await Antinuke.an_preset_save.callback(cog, ctx, "custom_raid")
        cfg = await cog.cfg(guild_id)
        assert "custom_raid" in cfg.get("presets", {})

        # Load strict preset
        await Antinuke.an_preset_load.callback(cog, ctx, "strict")
        cfg = await cog.cfg(guild_id)
        assert cfg["modules"]["ban"]["threshold"] == PRESETS["strict"]["ban"]["threshold"]

        # Reset to defaults
        await Antinuke.an_reset.callback(cog, ctx, "ban")
        cfg = await cog.cfg(guild_id)
        assert cfg["modules"]["ban"]["threshold"] == 3
        assert cfg["modules"]["ban"]["window"] == 60
        assert cfg["modules"]["ban"]["punishment"] == "ban"
    run(_run)


def test_whitelist_and_unwhitelist():
    async def _run():
        db, cog = await make_test_setup()
        guild_id = 888
        guild = SimpleNamespace(id=guild_id, owner_id=1)
        author = SimpleNamespace(id=1, guild_permissions=SimpleNamespace(administrator=True))
        target_user = SimpleNamespace(id=42, mention="<@42>")
        ctx = SimpleNamespace(guild=guild, author=author, approve=AsyncMock(), neutral=AsyncMock(), warn=AsyncMock())

        # Whitelist user
        await Antinuke.an_whitelist.callback(cog, ctx, target_user)
        cfg = await cog.cfg(guild_id)
        assert 42 in cfg["whitelist"]

        # Whitelist again (already whitelisted)
        await Antinuke.an_whitelist.callback(cog, ctx, target_user)
        ctx.warn.assert_awaited_once()

        # Unwhitelist user
        await Antinuke.an_unwhitelist.callback(cog, ctx, target_user)
        cfg = await cog.cfg(guild_id)
        assert 42 not in cfg["whitelist"]
    run(_run)


def test_command_anti_spam_cooldown():
    async def _run():
        db = Database(":memory:")
        await db.connect()
        OPEN_DBS.append(db)

        bot = Veil.__new__(Veil)
        bot.db = db
        bot.auth_required = False
        bot.private_mode = False
        bot.authorized = set()
        bot.maintenance = None
        bot.killswitch = set()
        from collections import defaultdict
        bot._cmd_cooldowns = defaultdict(list)
        bot._spam_warned = {}

        guild = SimpleNamespace(id=100, owner_id=1)
        # Regular user (not owner, not admin, no staff perms)
        regular_author = SimpleNamespace(
            id=500,
            guild_permissions=SimpleNamespace(
                administrator=False,
                manage_guild=False,
                manage_messages=False,
                moderate_members=False,
            ),
            roles=[],
        )
        ctx_regular = SimpleNamespace(
            guild=guild,
            author=regular_author,
            cog=None,
            command=SimpleNamespace(qualified_name="ping", name="ping", root_parent=None, hidden=False, parents=[]),
            channel=SimpleNamespace(id=200),
            send=AsyncMock(),
        )

        # Mock is_owner
        bot.is_owner = AsyncMock(return_value=False)

        # First 4 commands are allowed (0, 1, 2, 3)
        for _ in range(4):
            allowed = await bot._global_check(ctx_regular)
            assert allowed is True

        # 5th command within the 5s window triggers spam rate limit and raises Ignored
        with pytest.raises(Ignored):
            await bot._global_check(ctx_regular)

        # Staff user is exempt from rate limiting
        staff_author = SimpleNamespace(
            id=600,
            guild_permissions=SimpleNamespace(
                administrator=True,
                manage_guild=True,
                manage_messages=True,
                moderate_members=True,
            ),
            roles=[],
        )
        ctx_staff = SimpleNamespace(
            guild=guild,
            author=staff_author,
            cog=None,
            command=SimpleNamespace(qualified_name="ban", name="ban", root_parent=None, hidden=False, parents=[]),
            channel=SimpleNamespace(id=200),
            send=AsyncMock(),
        )

        for _ in range(10):
            allowed = await bot._global_check(ctx_staff)
            assert allowed is True
    run(_run)


def test_roleblacklist_enforcement():
    async def _run():
        bot = MagicMock()
        bot.db = MagicMock()
        bot.db.get_config = AsyncMock(return_value={"roles": [999]})
        bot.is_owner = AsyncMock(return_value=False)
        bot.blacklist = set()
        bot.auth_required = False

        cog = Moderation(bot)

        # 1. on_member_update strips blacklisted role
        blacklisted_role = SimpleNamespace(id=999, name="Forbidden")
        normal_role = SimpleNamespace(id=111, name="Member")

        top_me = SimpleNamespace(position=20)
        blacklisted_role.__lt__ = lambda self, other: True
        normal_role.__lt__ = lambda self, other: True

        me = SimpleNamespace(
            top_role=top_me,
            guild_permissions=SimpleNamespace(manage_roles=True),
        )
        guild = SimpleNamespace(id=1, owner_id=9999, me=me, members=[])

        before = SimpleNamespace(
            id=123,
            guild=guild,
            nick="test",
            roles=[normal_role],
        )
        after = SimpleNamespace(
            id=123,
            guild=guild,
            nick="test",
            roles=[normal_role, blacklisted_role],
            remove_roles=AsyncMock(),
        )

        await cog.on_member_update(before, after)
        after.remove_roles.assert_awaited_once_with(blacklisted_role, reason="Veil: Role is blacklisted")

        # 2. May run commands allows members to run commands (not blocked by server roles)
        msg_user_with_role = SimpleNamespace(
            author=SimpleNamespace(id=123, bot=False, roles=[normal_role, blacklisted_role]),
            guild=guild,
        )
        can_run = await Veil._may_run_commands(bot, msg_user_with_role)
        assert can_run is True

        # 3. rbl_check tests
        ctx = SimpleNamespace(
            bot=bot,
            guild=guild,
            author=SimpleNamespace(id=9999),
            clean_prefix=",",
            send=AsyncMock(),
        )

        clean_member = SimpleNamespace(
            id=456,
            name="cleanuser",
            nick=None,
            mention="<@456>",
            roles=[normal_role],
            display_avatar=SimpleNamespace(url="https://example.com/avatar.png"),
        )
        bled_member = SimpleNamespace(
            id=123,
            name="bleduser",
            nick=None,
            mention="<@123>",
            roles=[normal_role, blacklisted_role],
            display_avatar=SimpleNamespace(url="https://example.com/avatar.png"),
        )
        guild.members = [clean_member, bled_member]
        guild.get_member = lambda uid: clean_member if uid == 456 else (bled_member if uid == 123 else None)

        # Check clean member -> Authorized embed
        await cog._do_rbl_check(ctx, str(clean_member.id))
        assert ctx.send.await_count == 1
        call_kwargs = ctx.send.call_args[1]
        sent_embed = call_kwargs.get("embed")
        assert "Authorized" in sent_embed.title or "Authorized" in sent_embed.description
        assert "✅" in sent_embed.description

        # Check member with blacklisted role -> Unauthorized embed with interactive View
        ctx.send.reset_mock()
        await cog._do_rbl_check(ctx, str(bled_member.id))
        assert ctx.send.await_count == 1
        call_kwargs = ctx.send.call_args[1]
        sent_embed = call_kwargs.get("embed")
        sent_view = call_kwargs.get("view")
        assert "Unauthorized" in sent_embed.description
        assert sent_view is not None  # Offers Strip Roles and Authorize buttons

        # Also test cmd_checkbled directly without arguments (server scan)
        ctx.send.reset_mock()
        await cog.cmd_checkbled.callback(cog, ctx)
        assert ctx.send.await_count == 1
        sent_embed = ctx.send.call_args[1].get("embed")
        sent_view = ctx.send.call_args[1].get("view")
        assert "Unauthorized" in sent_embed.description
        assert sent_view is not None  # Offers Strip All and Authorize All

        # Server scan when all members are clean
        guild.members = [clean_member]
        ctx.send.reset_mock()
        await cog.cmd_checkbled.callback(cog, ctx)
        assert ctx.send.await_count == 1
        sent_embed = ctx.send.call_args[1].get("embed")
        assert "All Members Authorized" in sent_embed.description

    run(_run)

