"""Individual self-role changes for actual Discord administrators."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from cogs.moderation import Moderation
from core.utils import UserError
from test_discord_admin_roles import audit_context, event, role_context
from test_roleblacklist_enforcement import bot  # noqa: F401


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize('held', [True, False])
def test_admin_can_toggle_own_lower_role_via_role_command(bot, held, monkeypatch):  # noqa: F811
    async def go():
        _, actor, _, role, ctx = role_context(bot)
        actor.roles = [actor.top_role, role] if held else [actor.top_role]
        cog = Moderation(bot)
        guard = AsyncMock(side_effect=UserError('antinuke should not block this'))
        monkeypatch.setattr('cogs.moderation.antinuke_guard', guard)
        cog._resolve_member_role = AsyncMock(return_value=(actor, role))
        await Moderation.role.callback(cog, ctx, '@me', arg2='+')
        guard.assert_not_awaited()
        if held:
            actor.remove_roles.assert_awaited_once()
            actor.add_roles.assert_not_awaited()
        else:
            actor.add_roles.assert_awaited_once()
            actor.remove_roles.assert_not_awaited()
    run(go())


@pytest.mark.parametrize('kind', ['equal', 'higher', 'above_bot', 'managed', 'everyone', 'bot_target'])
@pytest.mark.parametrize('operation', ['add', 'remove'])
def test_self_role_does_not_bypass_hierarchy_or_role_safety(bot, kind, operation):  # noqa: F811
    async def go():
        guild, actor, _, role, ctx = role_context(bot)
        if kind == 'equal':
            role.position = actor.top_role.position
        elif kind == 'higher':
            role.position = actor.top_role.position + 1
        elif kind == 'above_bot':
            actor.top_role.position = guild.me.top_role.position
        elif kind == 'managed':
            role.managed = True
        elif kind == 'everyone':
            role.is_default.return_value = True
        else:
            actor.id = guild.me.id
        actor.roles = [actor.top_role, role] if operation == 'remove' else [actor.top_role]
        with pytest.raises(UserError):
            await (Moderation(bot)._take_role(ctx, actor, role) if operation == 'remove'
                   else Moderation(bot)._give_role(ctx, actor, role))
        actor.add_roles.assert_not_awaited()
        actor.remove_roles.assert_not_awaited()
    run(go())


@pytest.mark.parametrize('permission', ['fake', 'veil', 'manage_roles'])
def test_non_native_admin_cannot_modify_own_role(bot, permission):  # noqa: F811
    async def go():
        guild, actor, _, role, ctx = role_context(bot)
        actor.guild_permissions = discord.Permissions(manage_roles=True)
        actor.roles = [actor.top_role, role]
        if permission == 'fake':
            bot._storage[(guild.id, 'fakeperms')] = {str(actor.top_role.id): ['administrator']}
        if permission == 'veil':
            bot._storage[(guild.id, 'permlevels')] = {'users': {str(actor.id): 'supremeleader'}}
        with pytest.raises(UserError, match='yourself'):
            await Moderation(bot)._take_role(ctx, actor, role)
        actor.remove_roles.assert_not_awaited()
    run(go())


def test_self_add_still_respects_blacklist_and_bulk_excludes_self(bot):  # noqa: F811
    async def go():
        guild, actor, _, role, ctx = role_context(bot)
        bot._storage[(guild.id, 'roleblacklist')] = {'roles': [role.id]}
        cog = Moderation(bot)
        with pytest.raises(UserError, match='blacklisted'):
            await cog._give_role(ctx, actor, role)
        with pytest.raises(UserError, match='yourself'):
            await cog._role_member_hierarchy(ctx, actor)  # bulk does not opt in
        actor.add_roles.assert_not_awaited()
    run(go())


@pytest.mark.parametrize('change', ['lost_admin', 'role_moved'])
def test_self_remove_rechecks_after_waiting_for_lock(bot, change):  # noqa: F811
    async def go():
        _, actor, _, role, ctx = role_context(bot)
        actor.roles = [actor.top_role, role]
        cog = Moderation(bot)

        class Lock:
            async def __aenter__(self):
                if change == 'lost_admin':
                    actor.guild_permissions = discord.Permissions(manage_roles=True)
                else:
                    role.position = actor.top_role.position

            async def __aexit__(self, *args):
                pass

        cog._exception_lock = lambda _: Lock()
        with pytest.raises(UserError):
            await cog._take_role(ctx, actor, role)
        actor.remove_roles.assert_not_awaited()
    run(go())


def test_self_role_audit_exemption_requires_independent_admin_and_lower_role(bot):  # noqa: F811
    async def go():
        cog, guild, actor, _, role = audit_context(bot)
        # Discord's audit member-role entry uses before for removed roles and after for added ones.
        for before, after in ((SimpleNamespace(roles=[role]), None), (None, SimpleNamespace(roles=[role]))):
            change = event(guild, actor, discord.AuditLogAction.member_role_update, actor, before=before, after=after)
            assert cog._native_admin_role_action(change)
            await cog.on_audit_log_entry_create(change)
        assert not cog.hits and not cog.recent
        cog.respond.assert_not_awaited()
        change = event(guild, actor, discord.AuditLogAction.member_role_update, actor,
                       after=SimpleNamespace(roles=[role]))
        role.position = actor.top_role.position
        assert not cog._native_admin_role_action(change)
        role.position = 50
        actor.roles = [role]  # a role change must not establish its own exemption
        assert not cog._native_admin_role_action(change)
    run(go())
