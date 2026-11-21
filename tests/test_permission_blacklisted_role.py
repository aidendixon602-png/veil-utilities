"""Blacklisted-role level assignments require an accountable privileged confirmation."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from cogs.permissions import Permissions
from core.utils import UserError


def setup(*, owner=True, developer=False, held=False, hard=False):
    role = MagicMock(spec=discord.Role)
    role.id, role.mention, role.managed = 55, '<@&55>', False
    role.is_default.return_value = False
    role.__ge__.return_value = False
    store = {'permlevels': {'users': {}, 'roles': {}},
             'roleblacklist': {'roles': [] if hard else [55], 'hard_roles': [55] if hard else []}}

    async def get_config(gid, module, default=None):
        return store.get(module, default or {})

    async def set_config(gid, module, data):
        store[module] = deepcopy(data)

    db = SimpleNamespace(get_config=AsyncMock(side_effect=get_config), set_config_committed=AsyncMock(side_effect=set_config))
    bot = SimpleNamespace(db=db, owner_ids=set(), is_owner=AsyncMock(return_value=developer))
    author = SimpleNamespace(id=20, roles=[role] if held else [], top_role=100)
    guild = SimpleNamespace(id=1, owner_id=20 if owner else 10, get_role=lambda rid: role if rid == role.id else None)
    ctx = SimpleNamespace(author=author, guild=guild, bot=bot, confirm=AsyncMock(return_value=True),
                          approve=AsyncMock(), neutral=AsyncMock())
    return Permissions(bot), ctx, role, store


def run(coro):
    return asyncio.run(coro)


def test_owner_can_assign_blacklisted_even_if_holding_it_after_confirmation():
    cog, ctx, role, store = setup(held=True)
    old_rbl = deepcopy(store['roleblacklist'])
    run(Permissions.up_add.callback(cog, ctx, role, level='admin'))
    assert store['permlevels']['roles']['55'] == 'admin'
    assert store['roleblacklist'] == old_rbl
    prompt = ctx.confirm.await_args.args[0]
    assert 'blacklisted' in prompt and 'current and future role holders' in prompt and 'enforcement remain unchanged' in prompt
    ctx.approve.assert_awaited_once()


@pytest.mark.parametrize('answer', [False, None])
def test_declining_or_timing_out_does_not_assign(answer):
    cog, ctx, role, store = setup()
    ctx.confirm.return_value = answer
    run(Permissions.up_add.callback(cog, ctx, role, level='admin'))
    assert store['permlevels']['roles'] == {}
    ctx.bot.db.set_config_committed.assert_not_awaited()
    assert 'cancelled' in ctx.command_log_outcome


def test_nonprivileged_cannot_override_blacklist_or_assign_held_role():
    cog, ctx, role, store = setup(owner=False)
    store['permlevels']['users']['20'] = 'coowner'
    with pytest.raises(UserError, match='blacklisted'):
        run(Permissions.up_add.callback(cog, ctx, role, level='admin'))
    ctx.author.roles = [role]
    with pytest.raises(UserError, match='role you hold'):
        run(Permissions.up_add.callback(cog, ctx, role, level='admin'))
    ctx.confirm.assert_not_awaited()
    ctx.bot.db.set_config_committed.assert_not_awaited()


def test_hard_blacklist_remains_developer_only_with_confirmation():
    cog, ctx, role, _ = setup(owner=True, hard=True)
    with pytest.raises(UserError, match='blacklisted'):
        run(Permissions.up_add.callback(cog, ctx, role, level='admin'))
    ctx.confirm.assert_not_awaited()
    cog, ctx, role, store = setup(owner=False, developer=True, held=True, hard=True)
    run(Permissions.up_add.callback(cog, ctx, role, level='admin'))
    ctx.confirm.assert_awaited_once()
    assert store['permlevels']['roles']['55'] == 'admin'


@pytest.mark.parametrize('change', ['blacklist', 'level', 'deleted', 'managed', 'owner', 'developer'])
def test_stale_confirmation_fails_closed(change):
    cog, ctx, role, store = setup(owner=change != 'developer', developer=change == 'developer')
    async def confirm(*args):
        if change == 'blacklist':
            store['roleblacklist']['roles'].append(66)
        elif change == 'level':
            store['permlevels']['roles']['66'] = 'helper'
        elif change == 'deleted':
            ctx.guild.get_role = lambda rid: None
        elif change == 'managed':
            role.managed = True
        elif change == 'owner':
            ctx.guild.owner_id = 99
        else:
            ctx.bot.is_owner.return_value = False
        return True
    ctx.confirm.side_effect = confirm
    with pytest.raises(UserError, match='changed during confirmation'):
        run(Permissions.up_add.callback(cog, ctx, role, level='admin'))
    assert '55' not in store['permlevels']['roles']
    ctx.bot.db.set_config_committed.assert_not_awaited()
