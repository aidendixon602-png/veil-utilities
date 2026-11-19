"""Native Administrator role management remains scoped to actual Discord authority."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from cogs.antinuke import Antinuke
from cogs.moderation import Moderation
from core.utils import UserError, check_role_assignable
from test_roleblacklist_enforcement import _mock_context, _mock_guild, _mock_member, _mock_role, bot  # noqa: F401


def run(coro):
    return asyncio.run(coro)


def role_context(bot):  # noqa: F811
    guild = _mock_guild(123)
    actor = _mock_member(333)
    actor.guild_permissions = discord.Permissions(administrator=True)
    target = _mock_member(444)
    target.top_role = _mock_role(101, pos=20)
    role = _mock_role(888, pos=50)
    role.permissions = discord.Permissions(administrator=True, manage_guild=True, manage_roles=True)
    ctx = _mock_context(bot, guild, actor)
    return guild, actor, target, role, ctx


def test_admin_can_grant_administrator_without_veil_rank_or_delegation(bot, monkeypatch):  # noqa: F811
    async def go():
        guild, actor, target, role, ctx = role_context(bot)
        bot._storage[(guild.id, 'fakeperms')] = {str(role.id): ['manage_roles']}
        bot._storage[(guild.id, 'permlevels')] = {'users': {str(target.id): 'supremeleader'}}
        guard = AsyncMock()
        monkeypatch.setattr('cogs.moderation.antinuke_guard', guard)
        await Moderation(bot)._give_role(ctx, target, role)
        target.add_roles.assert_awaited_once()
        guard.assert_not_awaited()
        assert (guild.id, 'admin_role_granters') not in bot._storage
    run(go())


@pytest.mark.parametrize('position', [90, 91, 100])
def test_admin_cannot_assign_equal_or_higher_role(bot, position):  # noqa: F811
    async def go():
        _, _, target, role, ctx = role_context(bot)
        role.position = position
        with pytest.raises(UserError):
            await Moderation(bot)._give_role(ctx, target, role)
        target.add_roles.assert_not_awaited()
    run(go())


def test_fake_admin_does_not_grant_native_admin_role_exemption(bot, monkeypatch):  # noqa: F811
    async def go():
        guild, actor, target, role, ctx = role_context(bot)
        actor.guild_permissions = discord.Permissions(manage_roles=True)
        bot._storage[(guild.id, 'fakeperms')] = {str(actor.top_role.id): ['administrator']}
        with pytest.raises(UserError):
            await Moderation(bot)._give_role(ctx, target, role)
        target.add_roles.assert_not_awaited()
        guard = AsyncMock()
        monkeypatch.setattr('cogs.moderation.antinuke_guard', guard)
        await Moderation(bot)._role_antinuke_guard(ctx, 'permissions')
        guard.assert_awaited_once()
    run(go())


def test_admin_does_not_bypass_blacklist_or_target_hierarchy(bot):  # noqa: F811
    async def go():
        guild, actor, target, role, ctx = role_context(bot)
        bot._storage[(guild.id, 'roleblacklist')] = {'roles': [role.id]}
        with pytest.raises(UserError, match='blacklisted'):
            await check_role_assignable(ctx, role)
        bot._storage[(guild.id, 'roleblacklist')] = {}
        target.top_role = actor.top_role
        with pytest.raises(UserError):
            await Moderation(bot)._give_role(ctx, target, role)
        target.add_roles.assert_not_awaited()
    run(go())


def audit_context(bot):  # noqa: F811
    guild, actor, target, role, _ = role_context(bot)
    actor.top_role.permissions = discord.Permissions(administrator=True)
    actor.roles = [actor.top_role]
    role_map = {role.id: role, actor.top_role.id: actor.top_role}
    guild.get_role.side_effect = lambda rid: role_map.get(rid)
    guild.get_member.side_effect = lambda mid: {actor.id: actor, target.id: target}.get(mid)
    bot._storage[(guild.id, 'antinuke')] = {'enabled': True, 'modules': {
        key: {'on': True, 'threshold': 1, 'punishment': 'strip'}
        for key in ('rolecreate', 'roledelete', 'roleupdate', 'memberroles', 'permissions', 'ban')}}
    antinuke_bot = SimpleNamespace(db=bot.db, user=SimpleNamespace(id=99999), owner_ids=set(),
                                   owner_id=None, get_cog=lambda _: None)
    cog = Antinuke(antinuke_bot)
    cog.respond = AsyncMock()
    return cog, guild, actor, target, role


def event(guild, actor, action, target, before=None, after=None):
    return SimpleNamespace(guild=guild, user_id=actor.id, user=actor, action=action, target=target,
                           before=before or SimpleNamespace(), after=after or SimpleNamespace())


@pytest.mark.parametrize('action', [discord.AuditLogAction.role_create, discord.AuditLogAction.role_update,
                                    discord.AuditLogAction.role_delete, discord.AuditLogAction.member_role_update])
def test_native_admin_role_audit_is_exempt_only_with_verifiable_hierarchy(bot, action):  # noqa: F811
    async def go():
        cog, guild, actor, target, role = audit_context(bot)
        if action == discord.AuditLogAction.role_delete:
            cog.snapshots.roles[role.id] = {'position': role.position, 'managed': False}
        subject = target if action == discord.AuditLogAction.member_role_update else role
        after = SimpleNamespace(roles=[role]) if action == discord.AuditLogAction.member_role_update else SimpleNamespace()
        e = event(guild, actor, action, subject, after=after)
        assert cog._native_admin_role_action(e)
        await cog.on_audit_log_entry_create(e)
        assert not cog.hits and not cog.recent
        cog.respond.assert_not_awaited()
        role.position = actor.top_role.position
        if action == discord.AuditLogAction.role_delete:
            cog.snapshots.roles[role.id]['position'] = actor.top_role.position
        assert not cog._native_admin_role_action(e)
    run(go())


def test_nonrole_audit_and_unverifiable_role_changes_are_not_exempt(bot):  # noqa: F811
    async def go():
        cog, guild, actor, target, role = audit_context(bot)
        ban = event(guild, actor, discord.AuditLogAction.ban, target)
        assert not cog._native_admin_role_action(ban)
        await cog.on_audit_log_entry_create(ban)
        cog.respond.assert_awaited_once()
        change = event(guild, actor, discord.AuditLogAction.member_role_update, target,
                       after=SimpleNamespace(roles=[role]))
        guild.get_role.return_value = None
        guild.get_role.side_effect = None
        assert not cog._native_admin_role_action(change)
        guild.get_member.side_effect = None
        guild.get_member.return_value = None
        assert not cog._native_admin_role_action(change)
    run(go())


def test_changed_role_cannot_establish_own_admin_exemption(bot):  # noqa: F811
    cog, guild, actor, target, role = audit_context(bot)
    role.permissions = discord.Permissions(administrator=True)
    actor.roles = [role]
    change = event(guild, actor, discord.AuditLogAction.role_update, role)
    assert not cog._native_admin_role_action(change)


def test_admin_create_requires_role_above_everyone(bot, monkeypatch):  # noqa: F811
    async def go():
        guild, actor, _, _, ctx = role_context(bot)
        cog = Moderation(bot)
        guard = AsyncMock()
        monkeypatch.setattr('cogs.moderation.antinuke_guard', guard)
        actor.top_role.is_default.return_value = True
        with pytest.raises(UserError, match='cannot be below'):
            await Moderation.role_create.callback(cog, ctx, name='Test')
        guild.create_role.assert_not_awaited()
        actor.top_role.is_default.return_value = False
        await Moderation.role_create.callback(cog, ctx, name='Test')
        guild.create_role.assert_awaited_once()
        guard.assert_not_awaited()
    run(go())


def test_fake_or_bot_admin_not_exempt_from_audit(bot):  # noqa: F811
    cog, guild, actor, target, role = audit_context(bot)
    change = event(guild, actor, discord.AuditLogAction.member_role_update, target,
                   after=SimpleNamespace(roles=[role]))
    actor.guild_permissions = discord.Permissions(manage_roles=True)
    assert not cog._native_admin_role_action(change)
    actor.guild_permissions = discord.Permissions(administrator=True)
    actor.bot = True
    assert not cog._native_admin_role_action(change)


def test_missing_deleted_role_snapshot_and_managed_roles_are_not_exempt(bot):  # noqa: F811
    cog, guild, actor, _, role = audit_context(bot)
    deleted = event(guild, actor, discord.AuditLogAction.role_delete, role)
    assert not cog._native_admin_role_action(deleted)
    cog.snapshots.roles[role.id] = {'position': role.position, 'managed': True}
    assert not cog._native_admin_role_action(deleted)
    updated = event(guild, actor, discord.AuditLogAction.role_update, role)
    role.managed = True
    assert not cog._native_admin_role_action(updated)


def test_role_command_accepts_native_administrator_without_saved_level():
    from discord.ext import commands
    from test_discord_fake_permissions import context

    async def go():
        client = commands.Bot(command_prefix=',', intents=discord.Intents.default())
        client.owner_ids = set()
        store = {}
        client.db = SimpleNamespace(get_config=AsyncMock(side_effect=lambda gid, key, default=None: store.get(key, {})))
        await client.add_cog(Moderation(client))
        try:
            ctx = context(client, store, native=discord.Permissions(administrator=True))
            for name in ('role', 'role add', 'role remove', 'role create', 'role delete'):
                assert await client.get_command(name).can_run(ctx), name
        finally:
            await client.close()
    run(go())


def test_channel_permission_overwrite_never_gets_role_exemption(bot):  # noqa: F811
    cog, guild, actor, target, role = audit_context(bot)
    for action in (discord.AuditLogAction.overwrite_create, discord.AuditLogAction.overwrite_update,
                   discord.AuditLogAction.channel_delete):
        assert not cog._native_admin_role_action(event(guild, actor, action, target))


def test_deleted_role_snapshot_can_arrive_after_its_audit_entry(bot):  # noqa: F811
    async def go():
        cog, guild, actor, _, role = audit_context(bot)
        deleted = event(guild, actor, discord.AuditLogAction.role_delete, role)
        task = asyncio.create_task(cog.on_audit_log_entry_create(deleted))
        await asyncio.sleep(0.01)
        cog.snapshots.roles[role.id] = {'position': role.position, 'managed': False}
        await task
        cog.respond.assert_not_awaited()
        assert role.id in cog.snapshots.roles  # the exemption does not consume recovery data
        assert not cog.hits
    run(go())
