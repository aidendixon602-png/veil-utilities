"""Role permission changes require clear intent, real authority and explicit confirmation."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest
from discord.ext import commands
from discord.ext.commands.view import StringView

from cogs.moderation import Moderation, role_permissions_help
from core.utils import UserError


class Role:
    def __init__(self, rid, position, value=0, *, default=False, managed=False):
        self.id, self.name, self.mention = rid, f'role{rid}', f'<@&{rid}>'
        self.position, self.permissions = position, discord.Permissions(value)
        self.default, self.managed = default, managed
        self.edit = AsyncMock()

    def is_default(self):
        return self.default

    def __lt__(self, other):
        return self.position < other.position

    def __ge__(self, other):
        return self.position >= other.position


def setup():
    flag = discord.Permissions(manage_guild=True, manage_messages=True).value | (1 << 60)
    a, b = Role(101, 10, flag), Role(102, 20, flag)
    managed, everyone, high = Role(103, 30, flag, managed=True), Role(104, 0, flag, default=True), Role(105, 60, flag)
    actor = SimpleNamespace(id=1, top_role=Role(500, 50), guild_permissions=discord.Permissions(administrator=True, manage_roles=True))
    me = SimpleNamespace(top_role=Role(900, 100), guild_permissions=discord.Permissions(manage_roles=True))
    guild = SimpleNamespace(id=5, owner_id=2, me=me, roles=[a, b, managed, everyone, high])
    ctx = SimpleNamespace(author=actor, guild=guild, me=me, bot=SimpleNamespace(get_cog=lambda _: None), clean_prefix=',',
                          send=AsyncMock(), paginate=AsyncMock(), confirm=AsyncMock(return_value=True), approve=AsyncMock(),
                          neutral=AsyncMock(), warn=AsyncMock())
    return ctx, a, b, managed, everyone, high


def run(coro):
    return asyncio.run(coro)


def test_help_lists_real_permissions_by_category_and_both_forms():
    embed = role_permissions_help(',')
    fields = {f.name: f.value for f in embed.fields}
    assert all(key in fields for key in ('General', 'Text', 'Voice'))
    assert '`administrator`' in fields['General'] and '`view_channel`' in fields['Text'] and '`connect`' in fields['Voice']
    assert ',rpa @Role <permission>' in embed.description and ',rpa <permission> [skip role]' in embed.description
    assert all(len(f.value) <= 1024 for f in embed.fields)


def test_rpa_help_and_real_discord_command_argument_parsing():
    async def go():
        bot = commands.Bot(command_prefix=',', intents=discord.Intents.none())
        cog = Moderation(bot)
        await bot.add_cog(cog)
        command = bot.get_command('rpa')
        ctx, *_ = setup()
        ctx.bot = bot
        ctx.message = SimpleNamespace(attachments=[])
        ctx.view = StringView('manage_guild')
        await command._parse_arguments(ctx)
        assert ctx.args[-1] == 'manage_guild' and ctx.kwargs['permissions'] is None
        ctx.view = StringView('<@&101> manage_messages')
        await command._parse_arguments(ctx)
        assert ctx.args[-1] == '<@&101>' and ctx.kwargs['permissions'] == 'manage_messages'
        await command.callback(cog, ctx, 'help', permissions=None)
        assert ctx.send.await_args.kwargs['embed'].title == 'Discord role permissions'
        await bot.close()
    run(go())


def test_confirmed_removal_preserves_unknown_bits_and_excludes_unmanageable():
    ctx, a, b, managed, everyone, high = setup()
    cog = Moderation(ctx.bot)
    run(cog._remove_permission_across_roles(ctx, 'manage_guild'))
    ctx.paginate.assert_awaited_once()
    ctx.confirm.assert_awaited_once()
    for role in (a, b):
        new = role.edit.await_args.kwargs['permissions']
        assert not new.manage_guild and new.manage_messages and new.value & (1 << 60)
        assert 'bulk role permission removal' in role.edit.await_args.kwargs['reason']
    for role in (managed, everyone, high):
        role.edit.assert_not_awaited()
    assert ctx.command_log_outcome == 'Updated 2 role(s) · 0 not confirmed'


def test_cancel_or_timeout_does_not_change_any_role():
    ctx, a, b, *_ = setup()
    ctx.confirm.return_value = False
    run(Moderation(ctx.bot)._remove_permission_across_roles(ctx, 'manage_guild'))
    a.edit.assert_not_awaited()
    b.edit.assert_not_awaited()
    assert 'Cancelled' in ctx.command_log_outcome


def test_skip_role_and_legacy_command_use_same_confirmed_path(monkeypatch):
    async def go():
        ctx, a, b, *_ = setup()
        monkeypatch.setattr(commands.RoleConverter, 'convert', AsyncMock(return_value=b))
        cog = Moderation(ctx.bot)
        await Moderation.role_permission_add.callback(cog, ctx, 'manage_guild', permissions='<@&102>')
        a.edit.assert_awaited_once()
        b.edit.assert_not_awaited()
        a.edit.reset_mock()
        await Moderation.removepermall.callback(cog, ctx, permission='manage server')
        assert a.edit.await_count == 1 and b.edit.await_count == 1
        assert ctx.confirm.await_count == 2
    run(go())


@pytest.mark.parametrize('change', ['permission', 'hierarchy', 'actor', 'bot'])
def test_stale_or_revoked_authority_aborts_before_any_edit(change):
    ctx, a, b, *_ = setup()
    async def confirm(*args):
        if change == 'permission':
            a.permissions.manage_guild = False
        elif change == 'hierarchy':
            a.position = 99
        elif change == 'actor':
            ctx.author.guild_permissions = discord.Permissions(manage_roles=True)
        else:
            ctx.guild.me.guild_permissions = discord.Permissions.none()
        return True
    ctx.confirm.side_effect = confirm
    with pytest.raises(UserError, match='changed'):
        run(Moderation(ctx.bot)._remove_permission_across_roles(ctx, 'manage_guild'))
    a.edit.assert_not_awaited()
    b.edit.assert_not_awaited()


def test_fake_administrator_does_not_allow_bulk_removal():
    ctx, a, b, *_ = setup()
    ctx.author.guild_permissions = discord.Permissions(manage_roles=True)
    with pytest.raises(UserError, match='actual Discord Administrator'):
        run(Moderation(ctx.bot)._remove_permission_across_roles(ctx, 'manage_guild'))
    ctx.confirm.assert_not_awaited()
    a.edit.assert_not_awaited()
    b.edit.assert_not_awaited()


def test_bad_names_and_all_do_not_make_changes():
    ctx, a, b, *_ = setup()
    cog = Moderation(ctx.bot)
    for first, rest in [('all', 'manage_guild'), ('notapermission', None)]:
        with pytest.raises(UserError):
            run(Moderation.role_permission_add.callback(cog, ctx, first, permissions=rest))
    ctx.confirm.assert_not_awaited()
    a.edit.assert_not_awaited()
    b.edit.assert_not_awaited()


def test_partial_discord_failures_are_reported_not_hidden():
    ctx, a, b, *_ = setup()
    b.edit.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'no')
    run(Moderation(ctx.bot)._remove_permission_across_roles(ctx, 'manage_guild'))
    ctx.warn.assert_awaited_once()
    assert 'Updates not confirmed for **1**' in ctx.warn.await_args.args[0]
    assert ctx.command_log_outcome == 'Updated 1 role(s) · 1 not confirmed'


def test_actual_parser_and_role_converter_for_mentions_ids_and_names(monkeypatch):
    async def go():
        bot = commands.Bot(command_prefix=',', intents=discord.Intents.none())
        cog = Moderation(bot)
        await bot.add_cog(cog)
        command = bot.get_command('rpa')
        ctx, a, b, *_ = setup()
        ctx.bot = bot
        ctx.guild.me.guild_permissions.manage_messages = True
        a.permissions.manage_messages = False
        a.id, a.name = 123456789012345, 'Staff Team'
        b.id, b.name = 123456789012346, 'Skip Team'
        ctx.guild._roles = {a.id: a, b.id: b}
        ctx.guild.get_role = lambda rid: ctx.guild._roles.get(rid)
        ctx.message = SimpleNamespace(attachments=[])
        monkeypatch.setattr('cogs.moderation.antinuke_guard', AsyncMock())
        for role_input in (f'<@&{a.id}>', str(a.id), '"Staff Team"'):
            ctx.view = StringView(f'{role_input} manage_messages')
            await command._parse_arguments(ctx)
            await command.callback(*ctx.args, **ctx.kwargs)
        assert a.edit.await_count == 3
        a.edit.reset_mock()
        ctx.view = StringView('manage_guild "Skip Team"')
        await command._parse_arguments(ctx)
        await command.callback(*ctx.args, **ctx.kwargs)
        a.edit.assert_awaited_once()
        b.edit.assert_not_awaited()
        await bot.close()
    run(go())


def test_changes_after_first_edit_are_not_overwritten():
    ctx, a, b, *_ = setup()
    async def first_edit(**kwargs):
        b.permissions.manage_messages = False
    a.edit.side_effect = first_edit
    run(Moderation(ctx.bot)._remove_permission_across_roles(ctx, 'manage_guild'))
    a.edit.assert_awaited_once()
    b.edit.assert_not_awaited()
    assert ctx.command_log_warning
    assert '1 not confirmed' in ctx.command_log_outcome


def test_timeout_is_reported_as_unconfirmed():
    ctx, a, b, *_ = setup()
    b.edit.side_effect = asyncio.TimeoutError()
    run(Moderation(ctx.bot)._remove_permission_across_roles(ctx, 'manage_guild'))
    assert 'not confirmed' in ctx.warn.await_args.args[0]
    assert 'Check their current permissions' in ctx.warn.await_args.args[0]


def apa_setup(monkeypatch):
    ctx, role, *_ = setup()
    ctx.guild.owner_id = ctx.author.id
    ctx.guild.get_role = lambda rid: role if rid == role.id else None
    store = {}
    async def save(gid, key, data):
        store[key] = data
    ctx.bot.db = SimpleNamespace(get_config=AsyncMock(side_effect=lambda *a: store.get('fakeperms', {})),
                                 set_config_committed=AsyncMock(side_effect=save))
    monkeypatch.setattr(commands.RoleConverter, 'convert', AsyncMock(return_value=role))
    return ctx, role, store


def test_apa_all_grants_only_bot_permissions_to_one_role(monkeypatch):
    from cogs.settings import VALID_PERMS
    ctx, role, store = apa_setup(monkeypatch)
    run(Moderation.addpermall.callback(Moderation(ctx.bot), ctx, permission='all <@&101>'))
    assert store['fakeperms'] == {str(role.id): VALID_PERMS}
    ctx.confirm.assert_awaited_once()
    role.edit.assert_not_awaited()
    assert 'no Discord permissions changed' in ctx.approve.await_args.args[0]


@pytest.mark.parametrize('case', ['not_owner', 'everyone', 'cancel', 'deleted', 'changed_config', 'lost_owner'])
def test_apa_all_is_restricted_and_rechecks(case, monkeypatch):
    ctx, role, store = apa_setup(monkeypatch)
    if case == 'not_owner':
        ctx.guild.owner_id = 123
    elif case == 'everyone':
        role.default = True
    elif case == 'cancel':
        ctx.confirm.return_value = False
    elif case in ('deleted', 'changed_config', 'lost_owner'):
        async def confirm(*args):
            if case == 'deleted':
                ctx.guild.get_role = lambda rid: None
            elif case == 'changed_config':
                store['fakeperms'] = {'99': ['kick_members']}
            else:
                ctx.guild.owner_id = 123
            return True
        ctx.confirm.side_effect = confirm
    if case == 'cancel':
        run(Moderation.addpermall.callback(Moderation(ctx.bot), ctx, permission='all role'))
    else:
        with pytest.raises(UserError):
            run(Moderation.addpermall.callback(Moderation(ctx.bot), ctx, permission='all role'))
    ctx.bot.db.set_config_committed.assert_not_awaited()
    role.edit.assert_not_awaited()
