"""Unit tests for roleblacklist (BLED) strict enforcement across all role commands and new security features."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from cogs.antinuke import Antinuke
from cogs.automod import Automod
from cogs.config import Config, RoleButton
from cogs.devtools import DevTools
from cogs.levels import Levels
from cogs.moderation import Moderation
from cogs.voicemaster import VoiceMaster
from core.bot import Veil
from core.utils import UserError, check_role_assignable, is_role_blacklisted


def _mock_role(role_id: int, name: str = "TestRole", pos: int = 50):
    r = MagicMock(spec=discord.Role)
    r.id = role_id
    r.name = name
    r.mention = f"<@&{role_id}>"
    r.position = pos
    r.managed = False
    r.is_default.return_value = False
    r.permissions = discord.Permissions.none()
    r.__ge__ = lambda s, o: s.position >= getattr(o, "position", 0)
    r.__le__ = lambda s, o: s.position <= getattr(o, "position", 0)
    r.__gt__ = lambda s, o: s.position > getattr(o, "position", 0)
    r.__lt__ = lambda s, o: s.position < getattr(o, "position", 0)
    return r


def _mock_guild(guild_id: int = 12345):
    g = MagicMock(spec=discord.Guild)
    g.id = guild_id
    g.name = "Test Security Guild"
    g.owner_id = 11111
    g.me = MagicMock(spec=discord.Member)
    g.me.id = 99999
    g.me.top_role = _mock_role(99999, "VeilTopRole", pos=100)
    g.me.guild_permissions = discord.Permissions(administrator=True, manage_roles=True)
    g.default_role = _mock_role(0, "@everyone", pos=0)
    g.default_role.is_default.return_value = True
    g.default_role.permissions = discord.Permissions.none()
    g.roles = [g.default_role, g.me.top_role]
    g.text_channels = []
    g.members = [g.me]
    g.icon = None
    g.get_channel = MagicMock(return_value=None)
    return g


def _mock_member(user_id: int, roles=None):
    m = MagicMock(spec=discord.Member)
    m.id = user_id
    m.mention = f"<@{user_id}>"
    m.roles = list(roles or [])
    m.top_role = m.roles[0] if m.roles else _mock_role(user_id, "UserTopRole", pos=90)
    m.bot = False
    m.guild_permissions = discord.Permissions(manage_roles=True)
    m.add_roles = AsyncMock()
    m.remove_roles = AsyncMock()
    m.edit = AsyncMock()
    return m


def _mock_context(bot, guild, author):
    ctx = MagicMock()
    ctx.bot = bot
    ctx.guild = guild
    ctx.author = author
    ctx.clean_prefix = ","
    ctx.approve = AsyncMock()
    ctx.neutral = AsyncMock()
    ctx.warn = AsyncMock()
    ctx.send = AsyncMock()
    return ctx


@pytest.fixture
def bot():
    b = Veil()
    storage: dict[tuple[int, str], dict] = {}

    async def get_config(guild_id: int, module: str, default=None) -> dict:
        val = storage.get((guild_id, module))
        return val if val is not None else (default if default is not None else {})

    async def set_config(guild_id: int, module: str, data: dict):
        storage[(guild_id, module)] = data

    b.db.get_config = AsyncMock(side_effect=get_config)
    b.db.set_config = AsyncMock(side_effect=set_config)
    b.db.fetchone = AsyncMock(return_value={"n": 1})
    b.db.fetchall = AsyncMock(return_value=[])
    b.db.execute = AsyncMock()
    b._storage = storage
    return b


def test_is_role_blacklisted_helper(bot):
    async def _run():
        bot._storage[(123, "roleblacklist")] = {"roles": [888, 999]}
        assert await is_role_blacklisted(123, 888, bot.db) is True
        assert await is_role_blacklisted(123, 777, bot.db) is False

    asyncio.run(_run())


def test_check_role_assignable_raises_on_blacklisted(bot):
    async def _run():
        guild = _mock_guild(123)
        author = _mock_member(555)
        ctx = _mock_context(bot, guild, author)
        r_bled = _mock_role(888, "BledRole")
        r_clean = _mock_role(777, "CleanRole")

        bot._storage[(123, "roleblacklist")] = {"roles": [888]}

        with pytest.raises(UserError, match="is blacklisted \\(bled\\) and cannot be assigned"):
            await check_role_assignable(ctx, r_bled)

        # Clean role should pass check_role_assignable
        await check_role_assignable(ctx, r_clean)

    asyncio.run(_run())


def test_moderation_role_and_changerole_blocked_when_blacklisted(bot):
    async def _run():
        guild = _mock_guild(123)
        author = _mock_member(555)
        target = _mock_member(666)
        r_bled = _mock_role(888, "BledRole")
        guild.roles.append(r_bled)

        cog = Moderation(bot)
        ctx = _mock_context(bot, guild, author)
        cog._resolve_member_role = AsyncMock(return_value=(target, r_bled))

        bot._storage[(123, "roleblacklist")] = {"roles": [888]}

        # Test toggle ,role / ,changerole
        with pytest.raises(UserError, match="is blacklisted \\(bled\\) and cannot be assigned"):
            await Moderation.role.callback(cog, ctx, "666", arg2="888")

        # Test ,role add
        with pytest.raises(UserError, match="is blacklisted \\(bled\\) and cannot be assigned"):
            await Moderation.role_add.callback(cog, ctx, "666", arg2="888")

        # Test ,addrole
        with pytest.raises(UserError, match="is blacklisted \\(bled\\) and cannot be assigned"):
            await Moderation.cmd_addrole.callback(cog, ctx, "666", arg2="888")

        # Test massrole ,role all add
        with pytest.raises(UserError, match="is blacklisted \\(bled\\) and cannot be assigned"):
            await Moderation.role_all.callback(cog, ctx, "add", role=r_bled)

    asyncio.run(_run())


def test_config_autorole_buttonrole_reactionrole_blocked_when_blacklisted(bot):
    async def _run():
        guild = _mock_guild(123)
        author = _mock_member(555)
        r_bled = _mock_role(888, "BledRole")
        guild.roles.append(r_bled)

        cog = Config(bot)
        ctx = _mock_context(bot, guild, author)
        bot._storage[(123, "roleblacklist")] = {"roles": [888]}

        # Autorole
        with pytest.raises(UserError, match="is blacklisted \\(bled\\) and cannot be assigned"):
            await Config.autorole_add.callback(cog, ctx, role=r_bled)

        # Buttonrole
        with pytest.raises(UserError, match="is blacklisted \\(bled\\) and cannot be assigned"):
            await Config.buttonrole.callback(cog, ctx, [r_bled])

        # Reactionrole
        with pytest.raises(UserError, match="is blacklisted \\(bled\\) and cannot be assigned"):
            await Config.rr_add.callback(cog, ctx, "https://discord.com/channels/123/456/789", "🎮", role=r_bled)

        # Boost role
        with pytest.raises(UserError, match="is blacklisted \\(bled\\) and cannot be assigned"):
            await Config.boost_role.callback(cog, ctx, role=r_bled)

        # Vanity role
        with pytest.raises(UserError, match="is blacklisted \\(bled\\) and cannot be assigned"):
            await Config.vanity.callback(cog, ctx, role=r_bled, text=".gg/server")

        # RoleButton interaction callback
        btn = RoleButton(888, "BledRole")
        it = MagicMock(spec=discord.Interaction)
        it.guild = guild
        it.client = bot
        it.response = MagicMock()
        it.response.send_message = AsyncMock()
        guild.get_role = MagicMock(return_value=r_bled)
        await btn.callback(it)
        it.response.send_message.assert_called_once()
        assert "blacklisted (bled)" in it.response.send_message.call_args[0][0]

    asyncio.run(_run())


def test_leveling_and_voicemaster_blocked_when_blacklisted(bot):
    async def _run():
        guild = _mock_guild(123)
        author = _mock_member(555)
        r_bled = _mock_role(888, "BledRole")
        guild.roles.append(r_bled)

        # Leveling reward
        lvl_cog = Levels(bot)
        ctx = _mock_context(bot, guild, author)
        bot._storage[(123, "roleblacklist")] = {"roles": [888]}

        with pytest.raises(UserError, match="is blacklisted \\(bled\\) and cannot be assigned"):
            await Levels.lv_reward.callback(lvl_cog, ctx, 5, role=r_bled)

        # VoiceMaster default role
        vm_cog = VoiceMaster(bot)
        with pytest.raises(UserError, match="is blacklisted \\(bled\\) and cannot be assigned"):
            await VoiceMaster.vm_defaultrole.callback(vm_cog, ctx, role=r_bled)

    asyncio.run(_run())


def test_unstrip_and_unjail_filter_out_blacklisted_roles(bot):
    async def _run():
        guild = _mock_guild(123)
        author = _mock_member(555)
        target = _mock_member(666)
        r_bled = _mock_role(888, "BledRole")
        r_clean = _mock_role(777, "CleanRole")

        guild.get_role = MagicMock(side_effect=lambda rid: {888: r_bled, 777: r_clean}.get(rid))
        bot._storage[(123, "roleblacklist")] = {"roles": [888]}

        # Test unstrip
        dt_cog = DevTools(bot)
        ctx = _mock_context(bot, guild, author)
        bot._storage[(123, "strips")] = {"666": [888, 777]}

        await DevTools.unstrip.callback(dt_cog, ctx, target)
        # Should only have restored clean role (777), never blacklisted role (888)
        target.add_roles.assert_called_once_with(r_clean, reason=f"unstrip by {author}")

        # Test unjail
        mod_cog = Moderation(bot)
        target.roles = []
        bot._storage[(123, "jail")] = {"role": 999, "jailed": {"666": [888, 777]}}
        await Moderation.unjail.callback(mod_cog, ctx, target)
        target.edit.assert_called_once()
        restored = target.edit.call_args[1]["roles"]
        assert r_clean in restored
        assert r_bled not in restored

    asyncio.run(_run())


def test_quarantine_and_unquarantine(bot):
    async def _run():
        guild = _mock_guild(123)
        author = _mock_member(555)
        r_old = _mock_role(101, "OldRole")
        r_bled = _mock_role(888, "BledRole")
        target = _mock_member(666, roles=[r_old, r_bled])

        guild.get_role = MagicMock(side_effect=lambda rid: {101: r_old, 888: r_bled}.get(rid))
        bot._storage[(123, "roleblacklist")] = {"roles": [888]}

        mod_cog = Moderation(bot)
        ctx = _mock_context(bot, guild, author)

        # Quarantine
        await Moderation.quarantine.callback(mod_cog, ctx, target, reason="Malicious activity detected")
        target.edit.assert_called_once()
        q_cfg = bot._storage[(123, "quarantine")]
        assert "666" in q_cfg["quarantined"]

        # Simulate member having only quarantine role
        target.roles = [guild.roles[0]]

        # Release from quarantine: must restore 101 but filter out 888 (blacklisted)
        target.edit.reset_mock()
        await Moderation.unquarantine.callback(mod_cog, ctx, target)
        target.edit.assert_called_once()
        restored = target.edit.call_args[1]["roles"]
        assert r_old in restored
        assert r_bled not in restored

    asyncio.run(_run())


def test_server_security_audit_scan(bot):
    async def _run():
        guild = _mock_guild(123)
        author = _mock_member(555)
        ctx = _mock_context(bot, guild, author)
        mod_cog = Moderation(bot)

        # Run security audit scan
        await Moderation.security.callback(mod_cog, ctx)
        ctx.neutral.assert_called_once()
        call_embed = ctx.neutral.return_value.edit.call_args[1]["embed"]
        assert "Security Health Audit" in call_embed.title
        assert "Overall Security Rating" in call_embed.description
        assert "Score" in call_embed.description

    asyncio.run(_run())


def test_antialt_command_group(bot):
    async def _run():
        guild = _mock_guild(123)
        author = _mock_member(555)
        ctx = _mock_context(bot, guild, author)
        an_cog = Antinuke(bot)

        # Set minage
        await Antinuke.aa_minage.callback(an_cog, ctx, 7)
        cfg = bot._storage[(123, "antiraid")]
        assert cfg["age"] == 7

        # Set action
        await Antinuke.aa_action.callback(an_cog, ctx, "ban")
        cfg = bot._storage[(123, "antiraid")]
        assert cfg["punishment"] == "ban"

        # Toggle avatar
        await Antinuke.aa_avatar.callback(an_cog, ctx, "on")
        cfg = bot._storage[(123, "antiraid")]
        assert cfg["avatar"] is True

        # Toggle whitelist
        user = MagicMock(spec=discord.User)
        user.id = 9999
        await Antinuke.aa_whitelist.callback(an_cog, ctx, user)
        cfg = bot._storage[(123, "antiraid")]
        assert 9999 in cfg["whitelist"]

    asyncio.run(_run())


def test_antighostping_detection(bot):
    async def _run():
        am_cog = Automod(bot)
        guild = _mock_guild(123)
        author = _mock_member(555)
        target = _mock_member(777)
        channel = MagicMock(spec=discord.TextChannel)
        channel.send = AsyncMock()

        bot._storage[(123, "filters")] = {"antighostping": True}

        # Create deleted message with a mention
        msg = MagicMock(spec=discord.Message)
        msg.guild = guild
        msg.author = author
        msg.content = "Hey <@777> check this out!"
        msg.mentions = [target]
        msg.role_mentions = []
        msg.channel = channel
        msg.created_at = discord.utils.utcnow()

        await am_cog.on_message_delete(msg)
        channel.send.assert_called_once()
        embed = channel.send.call_args[1]["embed"]
        assert "Ghostping Detected" in embed.title
        assert target.mention in embed.fields[2].value

    asyncio.run(_run())


def test_dev_automatic_authorization(bot):
    async def _run():
        from core import perms
        dev_id = 888123
        perms.BOT_DEVELOPERS.append(dev_id)

        guild = _mock_guild(123)
        dev_member = _mock_member(dev_id)
        dev_member.guild = guild
        regular_member = _mock_member(444555)
        regular_member.guild = guild

        bot._storage[(123, "roleblacklist")] = {"roles": [501, 502], "authorized": []}

        mod_cog = Moderation(bot)
        rbl_cfg = bot._storage[(123, "roleblacklist")]

        # Dev is automatically authorized
        assert mod_cog._is_role_authorized(guild, dev_member, rbl_cfg) is True
        # Regular member is not authorized
        assert mod_cog._is_role_authorized(guild, regular_member, rbl_cfg) is False

        # Dev can assign blacklisted role via check_role_assignable
        blacklisted_role = _mock_role(501, "BledRole", pos=10)
        guild.me.top_role = _mock_role(999, "BotTopRole", pos=100)
        dev_ctx = _mock_context(bot, guild=guild, author=dev_member)

        # Dev check_role_assignable succeeds without raising
        await check_role_assignable(dev_ctx, blacklisted_role)

        # Non-dev check_role_assignable raises UserError
        non_dev_ctx = _mock_context(bot, guild=guild, author=regular_member)
        with pytest.raises(UserError, match="blacklisted"):
            await check_role_assignable(non_dev_ctx, blacklisted_role)

        # Bot Developer cannot be quarantined
        with pytest.raises(UserError, match="cannot quarantine a bot developer"):
            await Moderation.quarantine.callback(mod_cog, non_dev_ctx, dev_member)

        # Bot Developer cannot be jailed
        with pytest.raises(UserError, match="cannot jail a bot developer"):
            await Moderation.jail.callback(mod_cog, non_dev_ctx, dev_member)

    asyncio.run(_run())


def test_strict_roleblacklist_uperm_gating(bot):
    async def _run():
        guild = _mock_guild(123)
        mod_cog = Moderation(bot)
        target = _mock_member(777)
        bot._storage[(123, "roleblacklist")] = {"roles": [888], "authorized": []}
        bot._storage[(123, "permlevels")] = {
            "users": {
                "200": "admin",      # rank 3 (Admin)
                "300": "headadmin",  # rank 4 (Head Admin)
            }
        }

        # 1. Random user with raw Discord manage_roles but NO uperm rank (rank 0)
        unranked_author = _mock_member(100)
        unranked_author.guild = guild
        ctx_unranked = _mock_context(bot, guild, unranked_author)
        with pytest.raises(UserError, match=r"Head Admin.*uperm"):
            await mod_cog._do_rbl_authorize(ctx_unranked, target)

        # 2. Admin rank (rank 3) lacks Head Admin rank (rank 4) -> BLOCKED from authorizing
        admin_author = _mock_member(200)
        admin_author.guild = guild
        ctx_admin = _mock_context(bot, guild, admin_author)
        with pytest.raises(UserError, match=r"Head Admin.*uperm"):
            await mod_cog._do_rbl_authorize(ctx_admin, target)

        # 3. Head Admin rank (rank 4) -> SUCCEEDS
        headadmin_author = _mock_member(300)
        headadmin_author.guild = guild
        ctx_headadmin = _mock_context(bot, guild, headadmin_author)
        await mod_cog._do_rbl_authorize(ctx_headadmin, target)
        assert 777 in bot._storage[(123, "roleblacklist")]["authorized"]

        # 4. Server Owner -> SUCCEEDS
        owner_author = _mock_member(guild.owner_id)
        owner_author.guild = guild
        ctx_owner = _mock_context(bot, guild, owner_author)
        target2 = _mock_member(8888)
        await mod_cog._do_rbl_authorize(ctx_owner, target2)
        assert 8888 in bot._storage[(123, "roleblacklist")]["authorized"]

    asyncio.run(_run())

