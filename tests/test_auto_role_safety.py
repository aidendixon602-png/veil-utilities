"""Automatic and self-service roles must not grant bot or Discord staff privileges."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from cogs.config import Config, RoleButton
from cogs.levels import Levels
from cogs.voicemaster import VoiceMaster
from core.role_safety import unsafe_automatic_roles
from core.utils import UserError
from test_roleblacklist_enforcement import _mock_context, _mock_guild, _mock_member, _mock_role, bot  # noqa: F401


@pytest.mark.parametrize("module, data", [
    ("permlevels", {"roles": {"888": "moderator"}}),
    ("fakeperms", {"888": ["ban_members"]}),
    ("roleblacklist", {"authorized": [888]}),
    ("roleblacklist", {"soft_bypass": {"roles": [888]}}),
    ("antinuke", {"whitelist_roles": [888]}),
])
def test_safety_rejects_bot_grants_and_exemptions(bot, module, data):  # noqa: F811
    async def run():
        role = _mock_role(888)
        guild = _mock_guild(123)
        bot._storage[(123, module)] = data
        assert 888 in await unsafe_automatic_roles(bot.db, guild, [role])
        assert 889 not in await unsafe_automatic_roles(bot.db, guild, [_mock_role(889)])
    asyncio.run(run())


@pytest.mark.parametrize("permission", ["moderate_members", "manage_messages", "manage_threads", "view_audit_log", "ban_members"])
def test_safety_rejects_discord_staff_permissions(bot, permission):  # noqa: F811
    async def run():
        guild = _mock_guild(123)
        role = _mock_role(888)
        role.permissions = discord.Permissions(**{permission: True})
        assert 888 in await unsafe_automatic_roles(bot.db, guild, [role])
    asyncio.run(run())


@pytest.mark.parametrize("source", ["autorole", "button", "reaction", "boost", "vanity", "levels", "voice"])
def test_new_automatic_role_config_rejects_veil_staff_role(bot, source):  # noqa: F811
    async def run():
        guild = _mock_guild(123)
        role = _mock_role(888)
        ctx = _mock_context(bot, guild, _mock_member(555))
        bot._storage[(123, "permlevels")] = {"roles": {"888": "moderator"}}
        config = Config(bot)
        with pytest.raises(UserError, match="Veil permission level"):
            if source == "autorole":
                await config._ar(ctx, "all", role)
            elif source == "button":
                await Config.buttonrole.callback(config, ctx, [role])
            elif source == "reaction":
                await Config.rr_add.callback(config, ctx, "message", "emoji", role=role)
            elif source == "boost":
                await Config.boost_role.callback(config, ctx, role)
            elif source == "vanity":
                await Config.vanity.callback(config, ctx, role, text="guild")
            elif source == "levels":
                await Levels.lv_reward.callback(Levels(bot), ctx, 1, role)
            else:
                await VoiceMaster.vm_defaultrole.callback(VoiceMaster(bot), ctx, role)
        ctx.approve.assert_not_awaited()
    asyncio.run(run())


def test_existing_button_rechecks_policy_and_allows_removal_during_outage(bot):  # noqa: F811
    async def run():
        guild, role, member = _mock_guild(123), _mock_role(888), _mock_member(555)
        guild.get_role.return_value = role
        it = SimpleNamespace(guild=guild, client=bot, user=member,
                             response=SimpleNamespace(send_message=AsyncMock()))
        button = RoleButton(888, "test")
        await button.callback(it)
        member.add_roles.assert_awaited_once()
        member.add_roles.reset_mock()
        bot._storage[(123, "fakeperms")] = {"888": ["ban_members"]}
        await button.callback(it)
        member.add_roles.assert_not_awaited()
        assert "fake permissions" in it.response.send_message.await_args.args[0]
        bot.db.get_config.side_effect = RuntimeError("DB offline")
        await button.callback(it)
        member.add_roles.assert_not_awaited()
        assert "can't verify" in it.response.send_message.await_args.args[0]
        member.roles.append(role)
        await button.callback(it)
        member.remove_roles.assert_awaited_once()
    asyncio.run(run())


def test_existing_reaction_role_rechecks_new_level(bot):  # noqa: F811
    async def run():
        guild, role, member = _mock_guild(123), _mock_role(888), _mock_member(555)
        guild.get_role.return_value = role
        guild.get_member.return_value = member
        bot._connection.user = SimpleNamespace(id=999)
        bot.get_guild = MagicMock(return_value=guild)
        bot._storage[(123, "reactionroles")] = {"50": {"⭐": 888}}
        payload = SimpleNamespace(guild_id=123, user_id=555, message_id=50, emoji="⭐")
        config = Config(bot)
        await config._rr_event(payload, True)
        member.add_roles.assert_awaited_once()
        member.add_roles.reset_mock()
        bot._storage[(123, "permlevels")] = {"roles": {"888": "admin"}}
        await config._rr_event(payload, True)
        member.add_roles.assert_not_awaited()
        await config._rr_event(payload, False)
        member.remove_roles.assert_awaited_once()
    asyncio.run(run())


def test_delayed_autorole_rechecks_before_assignment(bot, monkeypatch):  # noqa: F811
    async def run():
        guild, role, member = _mock_guild(123), _mock_role(888), _mock_member(555)
        member.guild = guild
        guild.get_role.return_value = role
        bot._storage[(123, "autorole")] = {"humans": [888], "delay": 1}
        config = Config(bot)
        config._greet_send = AsyncMock()
        async def change_policy(_):
            bot._storage[(123, "fakeperms")] = {"888": ["manage_roles"]}
        monkeypatch.setattr("cogs.config.asyncio.sleep", change_policy)
        await config.on_member_join(member)
        member.add_roles.assert_not_awaited()
    asyncio.run(run())


def test_old_level_reward_is_blocked_and_existing_roles_are_not_removed(bot):  # noqa: F811
    async def run():
        guild, role, member = _mock_guild(123), _mock_role(888), _mock_member(555)
        member.guild = guild
        guild.get_role.return_value = role
        bot._storage[(123, "permlevels")] = {"roles": {"888": "admin"}}
        await Levels(bot).level_up(member, 5, {"roles": {"1": 888}, "stack": False}, None)
        member.add_roles.assert_not_awaited()
        member.remove_roles.assert_not_awaited()
    asyncio.run(run())


def test_malformed_or_unavailable_policy_fails_closed(bot):  # noqa: F811
    async def run():
        guild, role = _mock_guild(123), _mock_role(888)
        bot._storage[(123, "permlevels")] = {"roles": []}
        with pytest.raises(ValueError):
            await unsafe_automatic_roles(bot.db, guild, [role])
        bot.db.get_config.side_effect = RuntimeError("unavailable")
        with pytest.raises(RuntimeError):
            await unsafe_automatic_roles(bot.db, guild, [role])
    asyncio.run(run())


def test_existing_boost_and_vanity_rewards_recheck_policy(bot):  # noqa: F811
    async def run():
        guild, role, member = _mock_guild(123), _mock_role(888), _mock_member(555)
        member.guild, member.premium_since = guild, object()
        member.activities = [discord.CustomActivity(name="myguild")]
        guild.get_role.return_value = role
        bot._storage[(123, "fakeperms")] = {"888": ["kick_members"]}
        bot._storage[(123, "boost")] = {"role": 888}
        bot._storage[(123, "vanity")] = {"role": 888, "text": "myguild"}
        config = Config(bot)
        config._greet_send = AsyncMock()
        before = SimpleNamespace(premium_since=None, activities=[])
        await config.on_member_update(before, member)
        await config.on_presence_update(before, member)
        member.add_roles.assert_not_awaited()
    asyncio.run(run())


def test_existing_voicemaster_role_rechecks_policy(bot):  # noqa: F811
    async def run():
        guild, role, member = _mock_guild(123), _mock_role(888), _mock_member(555)
        member.guild, member.display_name = guild, "member"
        member.move_to = AsyncMock()
        guild.get_role.return_value = role
        guild.get_channel.return_value = None
        guild.create_voice_channel = AsyncMock(return_value=SimpleNamespace(id=333))
        guild.bitrate_limit = 96000
        bot._storage[(123, "antinuke")] = {"whitelist_roles": [888]}
        await VoiceMaster(bot).create_for(member, {"role": 888})
        member.move_to.assert_awaited_once()
        member.add_roles.assert_not_awaited()
    asyncio.run(run())


def test_reaction_add_fails_closed_if_policy_store_unavailable(bot):  # noqa: F811
    async def run():
        guild, role, member = _mock_guild(123), _mock_role(888), _mock_member(555)
        guild.get_role.return_value = role
        guild.get_member.return_value = member
        bot._connection.user = SimpleNamespace(id=999)
        bot.get_guild = MagicMock(return_value=guild)
        bot._storage[(123, "reactionroles")] = {"50": {"⭐": 888}}
        get_config = bot.db.get_config.side_effect
        async def failing(gid, module, default=None):
            if module == "fakeperms":
                raise RuntimeError("database offline")
            return await get_config(gid, module, default)
        bot.db.get_config.side_effect = failing
        payload = SimpleNamespace(guild_id=123, user_id=555, message_id=50, emoji="⭐")
        with pytest.raises(RuntimeError):
            await Config(bot)._rr_event(payload, True)
        member.add_roles.assert_not_awaited()
    asyncio.run(run())
