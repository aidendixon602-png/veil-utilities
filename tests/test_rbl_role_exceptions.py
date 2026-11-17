"""Owner role grants are scoped to one member and one role, never Antinuke trust."""
import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from cogs.antinuke import Antinuke
from cogs.moderation import Moderation
from core.utils import UserError
from test_roleblacklist_enforcement import _mock_context, _mock_guild, _mock_member, _mock_role, bot as role_bot  # noqa: F401


def run(coro):
    return asyncio.run(coro)


def setup(role_bot):  # noqa: F811
    guild = _mock_guild(123)
    role, other = _mock_role(888, 'protected', 5), _mock_role(889, 'other', 6)
    target = _mock_member(666)
    target.guild = guild
    role_bot._storage[(123, 'roleblacklist')] = {'roles': [888, 889], 'authorized': []}
    ctx = _mock_context(role_bot, guild, _mock_member(guild.owner_id))
    return guild, role, other, target, ctx, Moderation(role_bot)


def test_owner_bot_grant_is_role_specific_and_not_antinuke_trust(role_bot):  # noqa: F811
    async def go():
        guild, role, other, target, ctx, cog = setup(role_bot)
        await cog._give_role(ctx, target, role)
        assert role_bot._storage[(123, 'rbl_role_exceptions')] == {'666': [888]}
        assert role_bot._storage[(123, 'roleblacklist')]['authorized'] == []
        role_bot._connection.user = SimpleNamespace(id=99999)
        assert not await Antinuke(role_bot)._trusted(guild, {}, target.id)
        cfg = role_bot._storage[(123, 'roleblacklist')]
        ex = await cog._role_exceptions(123)
        assert cog._unauthorized_bled_roles(guild, target, cfg, ex, [role, other]) == [other]
        assert cog._unauthorized_bled_roles(guild, _mock_member(667), cfg, ex, [role]) == [role]
    run(go())


def test_failed_discord_grant_rolls_back_new_exception(role_bot):  # noqa: F811
    async def go():
        _, role, _, target, ctx, cog = setup(role_bot)
        target.add_roles.side_effect = RuntimeError('Discord failed')
        with pytest.raises(RuntimeError, match='Discord failed'):
            await cog._give_role(ctx, target, role)
        assert role_bot._storage[(123, 'rbl_role_exceptions')] == {}
        assert role_bot._storage[(123, 'roleblacklist')]['authorized'] == []
    run(go())


def test_failed_save_does_not_grant_role_or_publish_exception(role_bot):  # noqa: F811
    async def go():
        _, role, _, target, ctx, cog = setup(role_bot)
        role_bot.db.set_config_committed.side_effect = RuntimeError('db unavailable')
        with pytest.raises(RuntimeError, match='db unavailable'):
            await cog._give_role(ctx, target, role)
        target.add_roles.assert_not_awaited()
        assert (123, 'rbl_role_exceptions') not in role_bot._storage
    run(go())


def test_exception_removed_on_role_removal_and_unallow_leaves_discord_role(role_bot):  # noqa: F811
    async def go():
        guild, role, other, target, ctx, cog = setup(role_bot)
        role_bot._storage[(123, 'rbl_role_exceptions')] = {'666': [888, 889]}
        before, after = _mock_member(666, [role, other]), _mock_member(666, [other])
        before.nick = after.nick = None
        before.guild = after.guild = guild
        await cog.on_member_update(before, after)
        assert role_bot._storage[(123, 'rbl_role_exceptions')] == {'666': [889]}
        await Moderation.rbl_unallow.callback(cog, ctx, target, other)
        assert role_bot._storage[(123, 'rbl_role_exceptions')] == {}
        target.remove_roles.assert_not_awaited()
    run(go())


def audit_entry(actor, target, role, *, entry_id=999, age=0, added=True):
    return SimpleNamespace(id=entry_id, user=SimpleNamespace(id=actor), target=SimpleNamespace(id=target),
                           created_at=discord.utils.utcnow() - timedelta(seconds=age),
                           after=SimpleNamespace(roles=[role] if added else []),
                           before=SimpleNamespace(roles=[] if added else [role]))


def set_audit(guild, entries):
    guild.me.guild_permissions = discord.Permissions(manage_roles=True, view_audit_log=True)
    async def logs(**kwargs):
        for entry in entries:
            yield entry
    guild.audit_logs = logs


@pytest.mark.parametrize('variant', ['owner', 'other', 'stale', 'wrong_target', 'removal', 'ambiguous', 'replay', 'no_permission', 'failure'])
def test_direct_discord_change_needs_a_unique_fresh_owner_audit_entry(role_bot, monkeypatch, variant):  # noqa: F811
    async def go():
        guild, role, _, target, _, cog = setup(role_bot)
        before = _mock_member(666)
        after = _mock_member(666, [role])
        before.nick = after.nick = None
        before.guild = after.guild = guild
        entry = audit_entry(guild.owner_id, target.id, role)
        entries = [entry]
        if variant == 'other':
            entry.user.id = 1234
        elif variant == 'stale':
            entry.created_at -= timedelta(seconds=30)
        elif variant == 'wrong_target':
            entry.target.id = 667
        elif variant == 'removal':
            entries = [audit_entry(guild.owner_id, target.id, role, added=False)]
        elif variant == 'ambiguous':
            entries.append(audit_entry(1234, target.id, role, entry_id=998))
        elif variant == 'replay':
            role_bot._storage[(123, 'rbl_audit_receipts')] = {'ids': [entry.id]}
        set_audit(guild, entries)
        if variant == 'no_permission':
            guild.me.guild_permissions = discord.Permissions(manage_roles=True)
        elif variant == 'failure':
            def failed(**kwargs):
                raise RuntimeError('audit unavailable')
            guild.audit_logs = failed
        monkeypatch.setattr('cogs.moderation.asyncio.sleep', AsyncMock())
        await cog.on_member_update(before, after)
        if variant == 'owner':
            after.remove_roles.assert_not_awaited()
            assert role_bot._storage[(123, 'rbl_role_exceptions')] == {'666': [888]}
            assert role_bot._storage[(123, 'rbl_audit_receipts')] == {'ids': [999]}
            assert role_bot._storage[(123, 'roleblacklist')]['authorized'] == []
        else:
            after.remove_roles.assert_awaited_once()
            assert (123, 'rbl_role_exceptions') not in role_bot._storage
    run(go())


def test_mixed_roles_scan_strip_and_security_counts_only_unallowed_roles(role_bot):  # noqa: F811
    async def go():
        guild, role, other, target, ctx, cog = setup(role_bot)
        target.roles = [role, other]
        guild.members = [target]
        role_bot._storage[(123, 'rbl_role_exceptions')] = {'666': [888]}
        await cog._do_rbl_strip(ctx)
        target.remove_roles.assert_awaited_once()
        assert target.remove_roles.await_args.args == (other,)
        target.remove_roles.reset_mock()
        await cog._scan_guild_bled(ctx, role_bot._storage[(123, 'roleblacklist')], {888, 889})
        view = ctx.send.await_args.kwargs['view']
        assert view.unauthorized == [(target, [other])]
    run(go())


def test_hard_blacklist_still_denies_owner_and_exceptions(role_bot):  # noqa: F811
    async def go():
        guild, role, _, target, ctx, cog = setup(role_bot)
        role_bot._storage[(123, 'roleblacklist')] = {'roles': [], 'hard_roles': [888]}
        with pytest.raises(UserError, match='hard-blacklisted'):
            await cog._give_role(ctx, target, role)
        target.add_roles.assert_not_awaited()
        assert (123, 'rbl_role_exceptions') not in role_bot._storage
    run(go())


def test_audit_log_lag_is_retried_and_full_log_page_fails_closed(role_bot, monkeypatch):  # noqa: F811
    async def go():
        guild, role, _, target, _, cog = setup(role_bot)
        monkeypatch.setattr('cogs.moderation.asyncio.sleep', AsyncMock())
        entry = audit_entry(guild.owner_id, target.id, role)
        calls = 0
        guild.me.guild_permissions = discord.Permissions(view_audit_log=True)

        async def delayed(**kwargs):
            nonlocal calls
            calls += 1
            if calls >= 2:
                yield entry
        guild.audit_logs = delayed
        assert await cog._owner_audit_role_additions(target, [role], discord.utils.utcnow()) == {888: 999}
        assert calls == 2

        async def truncated(**kwargs):
            yield entry
            for i in range(99):
                yield audit_entry(333, 777, role, entry_id=1000 + i)
        guild.audit_logs = truncated
        assert await cog._owner_audit_role_additions(target, [role], discord.utils.utcnow()) == {}
    run(go())


def test_receipt_failure_fails_closed_without_new_trust(role_bot, monkeypatch):  # noqa: F811
    async def go():
        guild, role, _, target, _, cog = setup(role_bot)
        before, after = _mock_member(666), _mock_member(666, [role])
        before.nick = after.nick = None
        before.guild = after.guild = guild
        set_audit(guild, [audit_entry(guild.owner_id, target.id, role)])
        monkeypatch.setattr('cogs.moderation.asyncio.sleep', AsyncMock())
        role_bot.db.set_config_committed.side_effect = RuntimeError('write failed')
        await cog.on_member_update(before, after)
        after.remove_roles.assert_awaited_once()
        assert (123, 'rbl_role_exceptions') not in role_bot._storage
        assert role_bot._storage[(123, 'roleblacklist')]['authorized'] == []
    run(go())


def test_adding_an_unallowed_blacklisted_role_does_not_strip_an_allowed_one(role_bot):  # noqa: F811
    async def go():
        guild, role, other, _, _, cog = setup(role_bot)
        role_bot._storage[(123, 'rbl_role_exceptions')] = {'666': [888]}
        before, after = _mock_member(666, [role]), _mock_member(666, [role, other])
        before.nick = after.nick = None
        before.guild = after.guild = guild
        await cog.on_member_update(before, after)
        assert after.remove_roles.await_args.args == (other,)
        assert role_bot._storage[(123, 'rbl_role_exceptions')] == {'666': [888]}
    run(go())
