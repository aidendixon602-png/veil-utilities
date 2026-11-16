"""Real Discord role permission grants must not rely on Veil's fake permissions."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest
from discord.ext import commands

from cogs.moderation import Moderation, parse_real_role_permissions
from core.utils import UserError


class Role:
    def __init__(self, position=10, permissions=None, *, default=False, managed=False):
        self.position = position
        self.permissions = permissions or discord.Permissions.none()
        self.managed = managed
        self.default = default
        self.mention = '<@&123>'
        self.edit = AsyncMock()

    def is_default(self):
        return self.default

    def __ge__(self, other):
        return self.position >= other.position


def context(*, actor=None, bot=None, owner=False, actor_position=50, bot_position=100):
    actor = actor if actor is not None else discord.Permissions(manage_roles=True, kick_members=True,
                                                                 manage_messages=True)
    bot = bot if bot is not None else discord.Permissions(manage_roles=True, kick_members=True,
                                                           manage_messages=True)
    user = SimpleNamespace(id=1, top_role=Role(actor_position), guild_permissions=actor)
    me = SimpleNamespace(top_role=Role(bot_position), guild_permissions=bot)
    guild = SimpleNamespace(id=5, owner_id=1 if owner else 2, me=me)
    return SimpleNamespace(author=user, guild=guild, me=me, bot=SimpleNamespace(get_cog=lambda _: None),
                           approve=AsyncMock(), neutral=AsyncMock())


def run(ctx, role, text):
    return asyncio.run(Moderation.role_permission_add.callback(Moderation(ctx.bot), ctx, role, permissions=text))


def test_parser_supports_real_discord_names_and_aliases():
    found, unknown = parse_real_role_permissions('Manage Server, timeout members, VIEW channels, view_channel, use_external_emojis')
    assert found == ['manage_guild', 'moderate_members', 'view_channel', 'use_external_emojis']
    assert unknown == []
    assert parse_real_role_permissions('kick members, typo')[1] == ['typo']


def test_grant_preserves_other_and_unknown_bits_and_records_actor(monkeypatch):
    guard = AsyncMock()
    monkeypatch.setattr('cogs.moderation.antinuke_guard', guard)
    ctx = context()
    role = Role(permissions=discord.Permissions((1 << 60) | discord.Permissions(send_messages=True).value))
    run(ctx, role, 'kick members, manage_messages')
    updated = role.edit.await_args.kwargs['permissions']
    assert updated.send_messages and updated.kick_members and updated.manage_messages
    assert updated.value & (1 << 60)
    assert '1' in role.edit.await_args.kwargs['reason']
    guard.assert_awaited_once_with(ctx, 'permissions')
    ctx.approve.assert_awaited_once()


def test_non_dangerous_role_edit_uses_roleupdate_guard(monkeypatch):
    guard = AsyncMock()
    monkeypatch.setattr('cogs.moderation.antinuke_guard', guard)
    ctx = context()
    run(ctx, Role(), 'manage messages')
    guard.assert_awaited_once_with(ctx, 'roleupdate')


@pytest.mark.parametrize('kind', ['unknown', 'default', 'managed', 'actor_hierarchy', 'bot_hierarchy',
                                  'actor_missing', 'bot_missing'])
def test_refuses_unsafe_grants_without_editing(kind):
    ctx = context()
    role = Role()
    text = 'kick members'
    if kind == 'unknown':
        text = 'kick members, typo'
    elif kind == 'default':
        role.default = True
    elif kind == 'managed':
        role.managed = True
    elif kind == 'actor_hierarchy':
        role.position = 50
    elif kind == 'bot_hierarchy':
        role.position = 100
    elif kind == 'actor_missing':
        ctx.author.guild_permissions = discord.Permissions(manage_roles=True)
    else:
        ctx.guild.me.guild_permissions = discord.Permissions(manage_roles=True)
    with pytest.raises(UserError):
        run(ctx, role, text)
    role.edit.assert_not_awaited()
    ctx.approve.assert_not_awaited()


def test_owner_bypasses_only_own_hierarchy_not_bot_permissions():
    ctx = context(owner=True, bot=discord.Permissions(manage_roles=True))
    role = Role(position=75)
    with pytest.raises(UserError, match='I need'):
        run(ctx, role, 'kick members')
    role.edit.assert_not_awaited()


def test_administrator_allows_requested_permissions_but_cannot_be_inferred_from_other_bits():
    ctx = context(actor=discord.Permissions(administrator=True, manage_roles=True),
                  bot=discord.Permissions(administrator=True, manage_roles=True))
    role = Role()
    run(ctx, role, 'administrator')
    assert role.edit.await_args.kwargs['permissions'].administrator
    ctx = context()
    role = Role()
    with pytest.raises(UserError, match='actual Discord permissions'):
        run(ctx, role, 'administrator')
    role.edit.assert_not_awaited()


def test_noop_and_guard_failure_do_not_edit(monkeypatch):
    ctx = context()
    role = Role(permissions=discord.Permissions(kick_members=True))
    run(ctx, role, 'kick members')
    ctx.neutral.assert_awaited_once()
    role.edit.assert_not_awaited()
    monkeypatch.setattr('cogs.moderation.antinuke_guard', AsyncMock(side_effect=UserError('blocked')))
    with pytest.raises(UserError, match='blocked'):
        run(ctx, Role(), 'kick members')


def test_discord_forbidden_is_not_reported_as_success():
    ctx = context()
    role = Role()
    role.edit.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'no')
    with pytest.raises(UserError, match='Discord rejected'):
        run(ctx, role, 'kick members')
    ctx.approve.assert_not_awaited()


def test_commands_require_real_guild_manage_roles_and_alias_is_not_bulk_remove():
    async def check():
        bot = commands.Bot(command_prefix=',', intents=discord.Intents.none())
        await bot.add_cog(Moderation(bot))
        assert bot.get_command('rpa').callback is Moderation.role_permission_add.callback
        assert bot.get_command('removepermall').callback is Moderation.removepermall.callback
        assert 'rpa' not in Moderation.removepermall.aliases
        ctx = context(actor=discord.Permissions.none())
        with pytest.raises(commands.MissingPermissions):
            for predicate in Moderation.role_permission_add.checks:
                predicate(ctx)
        ctx = context(bot=discord.Permissions.none())
        with pytest.raises(commands.BotMissingPermissions):
            for predicate in Moderation.role_permission_add.checks:
                predicate(ctx)
        await bot.close()
    asyncio.run(check())
