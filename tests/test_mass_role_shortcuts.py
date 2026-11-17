"""Real command parsing and safety for bulk role shortcuts."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest
from discord.ext import commands
from discord.ext.commands.view import StringView

from cogs.moderation import Moderation
from core.utils import UserError
from test_roleblacklist_enforcement import _mock_context, _mock_guild, _mock_member, _mock_role


async def setup():
    bot = commands.Bot(command_prefix=',', intents=discord.Intents.none())
    store = {}
    async def config(gid, module, default=None):
        return store.get(module, default or {})
    bot.db = SimpleNamespace(get_config=AsyncMock(side_effect=config))
    bot.owner_ids = set()
    await bot.add_cog(Moderation(bot))
    guild = _mock_guild(1)
    author = _mock_member(guild.owner_id)
    role = _mock_role(123456789012345, 'Cool Team', pos=10)
    guild.roles.append(role)
    guild._roles = {r.id: r for r in guild.roles}
    guild.get_role = lambda rid: guild._roles.get(rid)
    human = _mock_member(51)
    bot_member = _mock_member(52)
    bot_member.bot = True
    guild.members = [human, bot_member]
    ctx = _mock_context(bot, guild, author)
    ctx.message = SimpleNamespace(attachments=[])
    ctx.confirm = AsyncMock(return_value=True)
    ctx.compact.return_value = SimpleNamespace(edit=AsyncMock())
    return bot, ctx, role, human, bot_member, store


async def invoke(bot, ctx, name, args):
    command = bot.get_command(f'r {name}')
    assert command is bot.get_command(f'role {name}')
    ctx.view = StringView(args)
    ctx.command = SimpleNamespace(name=name, can_run=AsyncMock(return_value=True))
    await command._parse_arguments(ctx)
    await command.callback(*ctx.args, **ctx.kwargs)


@pytest.mark.parametrize('scope,example', [
    ('humans', '"Cool Team"'), ('bots', '"Cool Team"'), ('all', '"Cool Team"'),
    ('humans', 'Cool Team'), ('humans', '<@&123456789012345>'), ('humans', '123456789012345'),
])
def test_real_parser_adds_only_selected_cached_members_after_confirmation(scope, example):
    async def go():
        bot, ctx, role, human, bot_member, _ = await setup()
        await invoke(bot, ctx, scope, example)
        ctx.confirm.assert_awaited_once()
        assert 'cached' in ctx.confirm.await_args.args[0]
        assert 'incomplete' in ctx.confirm.await_args.args[0]
        assert human.add_roles.await_count == (scope != 'bots')
        assert bot_member.add_roles.await_count == (scope != 'humans')
        assert not human.remove_roles.await_count and not bot_member.remove_roles.await_count
        assert ctx.command_log_warning is False
        assert role.mention in ctx.confirm.await_args.args[0]
        await bot.close()
    asyncio.run(go())


@pytest.mark.parametrize('args', ['add "Cool Team"', 'add Cool Team', 'add <@&123456789012345>'])
def test_existing_explicit_add_still_works(args):
    async def go():
        bot, ctx, _, human, bot_member, _ = await setup()
        await invoke(bot, ctx, 'humans', args)
        human.add_roles.assert_awaited_once()
        bot_member.add_roles.assert_not_awaited()
        ctx.confirm.assert_not_awaited()
        await bot.close()
    asyncio.run(go())


def test_explicit_remove_remains_remove_not_toggle():
    async def go():
        bot, ctx, role, human, bot_member, _ = await setup()
        human.roles.append(role)
        bot_member.roles.append(role)
        await invoke(bot, ctx, 'bots', 'remove "Cool Team"')
        bot_member.remove_roles.assert_awaited_once()
        human.remove_roles.assert_not_awaited()
        bot_member.add_roles.assert_not_awaited()
        ctx.confirm.assert_not_awaited()
        await bot.close()
    asyncio.run(go())


@pytest.mark.parametrize('answer', [False, None])
def test_cancel_or_timeout_never_edits_roles(answer):
    async def go():
        bot, ctx, _, human, bot_member, _ = await setup()
        ctx.confirm.return_value = answer
        await invoke(bot, ctx, 'all', '"Cool Team"')
        human.add_roles.assert_not_awaited()
        bot_member.add_roles.assert_not_awaited()
        assert 'cancelled' in ctx.command_log_outcome
        await bot.close()
    asyncio.run(go())


@pytest.mark.parametrize('change', ['role', 'permissions', 'members', 'blacklist', 'access', 'fakeperms', 'permlevels'])
def test_changes_during_confirmation_abort_without_edits(change):
    async def go():
        bot, ctx, role, human, bot_member, store = await setup()
        async def confirm(*args):
            if change == 'role':
                ctx.guild._roles.pop(role.id)
            elif change == 'permissions':
                role.permissions.manage_roles = True
            elif change == 'members':
                ctx.guild.members.append(_mock_member(53))
            elif change == 'blacklist':
                store['roleblacklist'] = {'roles': [role.id]}
            elif change == 'access':
                ctx.command.can_run.return_value = False
            else:
                store[change] = {'role': 'changed'}
            return True
        ctx.confirm.side_effect = confirm
        with pytest.raises(UserError):
            await invoke(bot, ctx, 'humans', '"Cool Team"')
        human.add_roles.assert_not_awaited()
        bot_member.add_roles.assert_not_awaited()
        await bot.close()
    asyncio.run(go())


def test_both_blacklists_block_bulk_assignment_even_for_owner():
    async def go():
        bot, ctx, role, human, _, store = await setup()
        for key in ('roles', 'hard_roles'):
            store['roleblacklist'] = {key: [role.id]}
            with pytest.raises(UserError, match='blacklisted'):
                await invoke(bot, ctx, 'humans', '"Cool Team"')
        ctx.confirm.assert_not_awaited()
        human.add_roles.assert_not_awaited()
        await bot.close()
    asyncio.run(go())


def test_partial_discord_failure_is_reported_in_result_and_command_log():
    async def go():
        bot, ctx, _, human, bot_member, _ = await setup()
        bot_member.add_roles.side_effect = asyncio.TimeoutError()
        await invoke(bot, ctx, 'all', '"Cool Team"')
        assert ctx.command_log_warning
        assert ctx.command_log_outcome == 'Role added to 1 member(s) · 1 not confirmed'
        result = ctx.compact.return_value.edit.await_args.kwargs['embed'].description
        assert '1** updates not confirmed' in result and 'check roles before retrying' in result
        human.add_roles.assert_awaited_once()
        await bot.close()
    asyncio.run(go())


def test_bad_or_missing_role_never_edits():
    async def go():
        bot, ctx, _, human, _, _ = await setup()
        for args in ('unknown', 'remove', 'add "Cool Team" extra'):
            with pytest.raises((UserError, commands.UserInputError)):
                await invoke(bot, ctx, 'humans', args)
        ctx.confirm.assert_not_awaited()
        human.add_roles.assert_not_awaited()
        await bot.close()
    asyncio.run(go())


def test_dangerous_shorthand_uses_antinuke_guard(monkeypatch):
    async def go():
        bot, ctx, role, human, _, _ = await setup()
        role.permissions.manage_roles = True
        guard = AsyncMock(side_effect=UserError('blocked by antinuke'))
        monkeypatch.setattr('cogs.moderation.antinuke_guard', guard)
        with pytest.raises(UserError, match='antinuke'):
            await invoke(bot, ctx, 'humans', '"Cool Team"')
        guard.assert_awaited_once_with(ctx, 'permissions', amount=1)
        human.add_roles.assert_not_awaited()
        await bot.close()
    asyncio.run(go())


def test_mass_commands_keep_their_permission_checks_and_concurrency():
    for command in (Moderation.role_all, Moderation.role_humans, Moderation.role_bots):
        assert {'administrator', 'uperm: headadmin'} <= set(command.callback.__veil_perms__)
        assert command._max_concurrency is not None
        assert any('bot_has_permissions' in check.__qualname__ for check in command.checks)


def test_real_command_checks_are_revalidated_after_confirmation():
    async def go():
        bot, ctx, _, human, _, _ = await setup()
        command = bot.get_command('r humans')
        ctx.command = command
        ctx.bot_permissions = discord.Permissions(manage_roles=True)
        ctx.view = StringView('"Cool Team"')
        await command._parse_arguments(ctx)
        async def confirm(*args):
            ctx.bot_permissions = discord.Permissions.none()
            return True
        ctx.confirm.side_effect = confirm
        with pytest.raises(commands.BotMissingPermissions):
            await command.callback(*ctx.args, **ctx.kwargs)
        human.add_roles.assert_not_awaited()
        await bot.close()
    asyncio.run(go())
