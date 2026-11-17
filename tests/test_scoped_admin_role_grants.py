"""Delegated Administrator role grants must not confer Antinuke or blacklist trust."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from cogs.antinuke import Antinuke
from cogs.moderation import Moderation
from core.utils import UserError
from test_roleblacklist_enforcement import _mock_context, _mock_guild, _mock_member, _mock_role, bot as role_bot  # noqa: F401


def setup(bot):
    guild = _mock_guild(123)
    owner = _mock_member(guild.owner_id)
    actor = _mock_member(10001)
    target = _mock_member(10002)
    target.top_role = _mock_role(9, 'target top', pos=20)
    role = _mock_role(888, 'Server Admin', pos=50)
    role.permissions = discord.Permissions(administrator=True)
    guild.get_role.side_effect = lambda rid: role if rid == role.id else None
    guild.get_member.side_effect = lambda mid: {actor.id: actor, target.id: target}.get(mid)
    bot._storage[(123, 'permlevels')] = {'users': {str(actor.id): 'admin'}}
    bot._storage[(123, 'roleblacklist')] = {'roles': [role.id], 'authorized': []}
    config = {'enabled': True, 'modules': {}, 'admins': [], 'whitelist': [], 'whitelist_roles': []}
    bot._storage[(123, 'antinuke')] = config
    an = SimpleNamespace(_trusted=AsyncMock(return_value=False))
    bot.get_cog = lambda name: an if name == 'Antinuke' else None
    cog = Moderation(bot)
    ctx = _mock_context(bot, guild, owner)
    ctx.confirm = AsyncMock(return_value=True)
    ctx.command.can_run = AsyncMock(return_value=True)
    return cog, ctx, actor, target, role, an, config


def run(coro):
    return asyncio.run(coro)


def test_member_scope_allows_blacklisted_admin_role_without_adding_trust(role_bot, monkeypatch):  # noqa: F811
    async def go():
        cog, ctx, actor, target, role, an, config = setup(role_bot)
        original = deepcopy(role_bot._storage[(123, 'roleblacklist')])
        guard = AsyncMock()
        monkeypatch.setattr('cogs.moderation.antinuke_guard', guard)
        await Moderation.admin_role_grant_add.callback(cog, ctx, actor)
        assert role_bot._storage[(123, 'admin_role_granters')] == {str(actor.id): True}
        ctx.confirm.assert_not_awaited()
        assert 'no Antinuke trust' in ctx.approve.await_args.args[0]
        ctx.author = actor
        await cog._give_role(ctx, target, role)
        target.add_roles.assert_awaited_once()
        guard.assert_not_awaited()
        assert role_bot._storage[(123, 'roleblacklist')] == original
        assert role_bot._storage[(123, 'rbl_role_exceptions')] == {str(target.id): [role.id]}
        assert config == {'enabled': True, 'modules': {}, 'admins': [], 'whitelist': [], 'whitelist_roles': []}
        actual_an = Antinuke(SimpleNamespace(user=SimpleNamespace(id=99999), owner_ids=set(), db=role_bot.db))
        assert not await actual_an._trusted(ctx.guild, config, actor.id)
        assert not await actual_an._trusted(ctx.guild, config, target.id)
        assert an._trusted.await_count >= 3
    run(go())


def test_active_permission_guard_allows_scoped_grant_without_changing_settings(role_bot, monkeypatch):  # noqa: F811
    async def go():
        cog, ctx, actor, target, role, _, config = setup(role_bot)
        role_bot._storage[(123, 'admin_role_granters')] = {str(actor.id): True}
        config['modules']['permissions'] = {'on': True}
        before = deepcopy(config)
        guard = AsyncMock()
        monkeypatch.setattr('cogs.moderation.antinuke_guard', guard)
        ctx.author = actor
        await cog._give_role(ctx, target, role)
        guard.assert_not_awaited()
        target.add_roles.assert_awaited_once()
        assert config == before
    run(go())


@pytest.mark.parametrize('condition', ['actor_trusted', 'target_trusted', 'whitelisted_role', 'no_antinuke', 'disabled', 'hard'])
def test_scope_fails_closed_if_other_trust_or_restrictions_apply(role_bot, condition):  # noqa: F811
    async def go():
        cog, ctx, actor, target, role, an, config = setup(role_bot)
        role_bot._storage[(123, 'admin_role_granters')] = {str(actor.id): True}
        if condition == 'actor_trusted':
            an._trusted.side_effect = lambda guild, cfg, uid: uid == actor.id
        elif condition == 'target_trusted':
            an._trusted.side_effect = lambda guild, cfg, uid: uid == target.id
        elif condition == 'whitelisted_role':
            config['whitelist_roles'] = [role.id]
        elif condition == 'no_antinuke':
            role_bot.get_cog = lambda _: None
        elif condition == 'disabled':
            config['enabled'] = False
        else:
            role_bot._storage[(123, 'roleblacklist')]['hard_roles'] = [role.id]
        ctx.author = actor
        with pytest.raises(UserError):
            await cog._give_role(ctx, target, role)
        target.add_roles.assert_not_awaited()
        assert (123, 'rbl_role_exceptions') not in role_bot._storage
    run(go())


def test_revocation_removes_only_scope_and_does_not_remove_existing_roles(role_bot):  # noqa: F811
    async def go():
        cog, ctx, actor, target, role, _, _ = setup(role_bot)
        role_bot._storage[(123, 'admin_role_granters')] = {str(actor.id): True, '10004': True}
        await Moderation.admin_role_grant_remove.callback(cog, ctx, actor)
        assert role_bot._storage[(123, 'admin_role_granters')] == {'10004': True}
        target.remove_roles.assert_not_awaited()
        ctx.author = actor
        with pytest.raises(UserError):
            await cog._give_role(ctx, target, role)
        target.add_roles.assert_not_awaited()
    run(go())


def test_scope_add_rejects_bots_and_existing_trust(role_bot):  # noqa: F811
    async def go():
        cog, ctx, actor, _, _, an, _ = setup(role_bot)
        actor.bot = True
        with pytest.raises(UserError, match='human'):
            await Moderation.admin_role_grant_add.callback(cog, ctx, actor)
        actor.bot = False
        an._trusted.return_value = True
        with pytest.raises(UserError, match='already has Antinuke trust'):
            await Moderation.admin_role_grant_add.callback(cog, ctx, actor)
        assert (123, 'admin_role_granters') not in role_bot._storage
    run(go())


def test_owner_gates_apply_to_every_management_subcommand():
    for command in (Moderation.admin_role_grant, Moderation.admin_role_grant_add,
                    Moderation.admin_role_grant_remove, Moderation.admin_role_grant_remove_id, Moderation.admin_role_grant_list):
        assert any(getattr(check, '__veil_owner_only__', False) for check in command.checks)


def test_departed_member_can_be_revoked_by_id(role_bot):  # noqa: F811
    async def go():
        cog, ctx, actor, _, role, _, _ = setup(role_bot)
        role_bot._storage[(123, 'admin_role_granters')] = {str(actor.id): True}
        ctx.guild.get_member.side_effect = lambda _: None
        ctx.guild.get_role.side_effect = lambda _: None
        await Moderation.admin_role_grant_remove_id.callback(cog, ctx, actor.id)
        assert role_bot._storage[(123, 'admin_role_granters')] == {}
        with pytest.raises(UserError, match='positive'):
            await Moderation.admin_role_grant_remove_id.callback(cog, ctx, -1)
    run(go())


def test_scoped_grant_database_and_discord_failures(role_bot):  # noqa: F811
    async def go():
        cog, ctx, actor, target, role, _, _ = setup(role_bot)
        role_bot.db.set_config_committed.side_effect = RuntimeError('database failed')
        with pytest.raises(RuntimeError, match='database failed'):
            await Moderation.admin_role_grant_add.callback(cog, ctx, actor)
        assert (123, 'admin_role_granters') not in role_bot._storage
        role_bot.db.set_config_committed.side_effect = lambda gid, key, data: role_bot._storage.__setitem__((gid, key), data)
        role_bot._storage[(123, 'admin_role_granters')] = {str(actor.id): True}
        ctx.author = actor
        target.add_roles.side_effect = RuntimeError('Discord failed')
        with pytest.raises(RuntimeError, match='Discord failed'):
            await cog._give_role(ctx, target, role)
        assert role_bot._storage[(123, 'rbl_role_exceptions')] == {}
        assert str(actor.id) in role_bot._storage[(123, 'admin_role_grant_cooldowns')]
    run(go())


def test_mixed_permissions_and_hierarchy_still_enforced(role_bot):  # noqa: F811
    async def go():
        cog, ctx, actor, target, role, _, _ = setup(role_bot)
        role.permissions.manage_guild = True
        role_bot._storage[(123, 'admin_role_granters')] = {str(actor.id): True}
        ctx.author = actor
        with pytest.raises(UserError, match='Head Admin'):
            await cog._give_role(ctx, target, role)
        target.add_roles.assert_not_awaited()
    run(go())


def test_scoped_grants_survive_database_reopen(tmp_path):
    from core.db import Database

    async def go():
        path = str(tmp_path / 'grants.db')
        db = Database(path)
        await db.connect()
        try:
            cog = Moderation(SimpleNamespace(db=db))
            await db.set_config_committed(123, 'admin_role_granters', {'10001': True})
            assert await cog._admin_grants(123) == {'10001': True}
            await cog._claim_admin_grant_slot(123, 10001)
        finally:
            await db.close()
        db = Database(path)
        await db.connect()
        try:
            cog = Moderation(SimpleNamespace(db=db))
            assert await cog._admin_grants(123) == {'10001': True}
            with pytest.raises(UserError, match='one per 60 seconds'):
                await cog._claim_admin_grant_slot(123, 10001)
        finally:
            await db.close()
    run(go())


def test_supreme_leader_scope_can_pass_instant_rule(role_bot, monkeypatch):  # noqa: F811
    async def go():
        cog, ctx, actor, target, role, _, config = setup(role_bot)
        role_bot._storage[(123, 'permlevels')] = {'users': {str(actor.id): 'supremeleader'}}
        role_bot._storage[(123, 'roleblacklist')]['roles'] = []
        role_bot._storage[(123, 'admin_role_granters')] = {str(actor.id): True}
        config['modules']['permissions'] = {'on': True}
        guard = AsyncMock()
        monkeypatch.setattr('cogs.moderation.antinuke_guard', guard)
        ctx.author = actor
        await cog._give_role(ctx, target, role)
        guard.assert_not_awaited()
        target.add_roles.assert_awaited_once()
    run(go())


def test_existing_trusted_high_rank_keeps_normal_behavior(role_bot, monkeypatch):  # noqa: F811
    async def go():
        cog, ctx, actor, target, role, an, _ = setup(role_bot)
        role_bot._storage[(123, 'permlevels')] = {'users': {str(actor.id): 'supremeleader'}}
        role_bot._storage[(123, 'roleblacklist')]['roles'] = []
        role_bot._storage[(123, 'admin_role_granters')] = {str(actor.id): True}
        an._trusted.return_value = True
        guard = AsyncMock()
        monkeypatch.setattr('cogs.moderation.antinuke_guard', guard)
        ctx.author = actor
        await cog._give_role(ctx, target, role)
        guard.assert_awaited_once_with(ctx, 'permissions')
        target.add_roles.assert_awaited_once()
    run(go())


def test_scoped_grants_are_direct_and_rate_limited_across_targets_and_roles(role_bot, monkeypatch):  # noqa: F811
    async def go():
        cog, ctx, actor, target, role, _, _ = setup(role_bot)
        role_bot._storage[(123, 'admin_role_granters')] = {str(actor.id): True}
        role_bot._storage[(123, 'antinuke')]['modules']['permissions'] = {'on': True}
        monkeypatch.setattr('cogs.moderation.time.time', lambda: 1000.0)
        ctx.author = actor
        ctx.confirm.reset_mock()
        await cog._give_role(ctx, target, role)
        ctx.confirm.assert_not_awaited()
        other = _mock_member(10003)
        other.top_role = _mock_role(10, 'target top', pos=20)
        another_role = _mock_role(889, 'Another admin role', pos=40)
        another_role.permissions = discord.Permissions(administrator=True)
        with pytest.raises(UserError, match='one per 60 seconds'):
            await cog._give_role(ctx, other, another_role)
        other.add_roles.assert_not_awaited()
        monkeypatch.setattr('cogs.moderation.time.time', lambda: 1060.0)
        await cog._give_role(ctx, other, another_role)
        other.add_roles.assert_awaited_once()
    run(go())


def test_concurrent_scoped_grants_cannot_skip_rate_limit(role_bot):  # noqa: F811
    async def go():
        cog, ctx, actor, target, role, _, _ = setup(role_bot)
        role_bot._storage[(123, 'admin_role_granters')] = {str(actor.id): True}
        ctx.author = actor
        other = _mock_member(10003)
        other.top_role = _mock_role(10, 'target top', pos=20)
        results = await asyncio.gather(cog._give_role(ctx, target, role), cog._give_role(ctx, other, role),
                                       return_exceptions=True)
        assert sum(isinstance(x, UserError) for x in results) == 1
        assert target.add_roles.await_count + other.add_roles.await_count == 1
    run(go())


def test_scoped_rate_limit_save_failure_prevents_grant(role_bot):  # noqa: F811
    async def go():
        cog, ctx, actor, target, role, _, _ = setup(role_bot)
        role_bot._storage[(123, 'admin_role_granters')] = {str(actor.id): True}
        ctx.author = actor
        role_bot.db.set_config_committed.side_effect = RuntimeError('database failed')
        with pytest.raises(RuntimeError, match='database failed'):
            await cog._give_role(ctx, target, role)
        target.add_roles.assert_not_awaited()
    run(go())


def test_authorization_is_member_only_does_not_bypass_non_admin_guard(role_bot, monkeypatch):  # noqa: F811
    async def go():
        cog, ctx, actor, target, _, _, _ = setup(role_bot)
        await Moderation.admin_role_grant_add.callback(cog, ctx, actor)
        ctx.author = actor
        role = _mock_role(990, 'Manage roles', pos=40)
        role.permissions = discord.Permissions(manage_roles=True)
        guard = AsyncMock(side_effect=UserError('guard blocked'))
        monkeypatch.setattr('cogs.moderation.antinuke_guard', guard)
        with pytest.raises(UserError, match='guard blocked'):
            await cog._give_role(ctx, target, role)
        guard.assert_awaited_once_with(ctx, 'permissions')
        target.add_roles.assert_not_awaited()
    run(go())


def test_corrupt_or_old_per_role_config_fails_closed(role_bot):  # noqa: F811
    async def go():
        cog, ctx, actor, target, role, _, _ = setup(role_bot)
        role_bot._storage[(123, 'admin_role_granters')] = {str(actor.id): [role.id]}
        ctx.author = actor
        with pytest.raises(UserError, match='could not be verified'):
            await cog._give_role(ctx, target, role)
        target.add_roles.assert_not_awaited()
    run(go())


def test_management_commands_take_only_member_or_id():
    assert list(Moderation.admin_role_grant_add.clean_params) == ['member']
    assert list(Moderation.admin_role_grant_remove.clean_params) == ['member']
    assert list(Moderation.admin_role_grant_remove_id.clean_params) == ['member_id']
