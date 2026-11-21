"""The read-only permission lists are available to Admin uperm without opening assignment edits."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest
from discord.ext import commands

from cogs.permissions import Permissions
from core.help import category_for_command, command_category_gate, visible_command
from core.utils import MissingRank


async def make_bot():
    bot = commands.Bot(command_prefix=',', intents=discord.Intents.default())
    bot.owner_ids = {777}
    bot.db = SimpleNamespace(get_config=AsyncMock(), set_config_committed=AsyncMock())
    await bot.add_cog(Permissions(bot))
    return bot


def make_ctx(bot, *, level=None, role_level=None, fake=None, author_id=20, native=None, overrides=None):
    cfg = {'users': {str(author_id): level} if level else {},
           'roles': {'55': role_level} if role_level else {}, 'overrides': overrides or {}}
    fake = fake or {}
    bot.db.get_config.side_effect = lambda gid, module, default=None: {'permlevels': cfg, 'fakeperms': fake}.get(module, {})
    return SimpleNamespace(bot=bot, guild=SimpleNamespace(id=1, owner_id=10),
                           author=SimpleNamespace(id=author_id, roles=[SimpleNamespace(id=55, name='Staff', mention='<@&55>')],
                                                  guild_permissions=native or discord.Permissions.none()),
                           command=None, paginate=AsyncMock())


def test_alias_and_real_command_checks_allow_admin_direct_and_via_role():
    async def go():
        bot = await make_bot()
        try:
            command = bot.get_command('perm list')
            assert command is bot.get_command('uperm all')
            assert category_for_command(command, bot) == 'Roles'
            assert visible_command(command, bot, False, 3)
            for kwargs in ({'level': 'admin'}, {'role_level': 'admin'}):
                ctx = make_ctx(bot, **kwargs)
                assert await command.can_run(ctx)
                await command.callback(command.cog, ctx)
                page = ctx.paginate.await_args.args[0][0]
                assert 'Permission assignments' == page.title
                assert 'Admin' in page.description
            bot.db.set_config_committed.assert_not_awaited()
        finally:
            await bot.close()
    asyncio.run(go())


@pytest.mark.parametrize('level', [None, 'helper', 'moderator'])
def test_lower_levels_without_permission_are_denied(level):
    async def go():
        bot = await make_bot()
        try:
            ctx = make_ctx(bot, level=level)
            for name in ('perm list', 'perm roles'):
                with pytest.raises(MissingRank, match='Admin'):
                    await bot.get_command(name).can_run(ctx)
            bot.db.set_config_committed.assert_not_awaited()
        finally:
            await bot.close()
    asyncio.run(go())


def test_preserves_owner_developer_and_existing_fake_administrator_access():
    async def go():
        bot = await make_bot()
        try:
            for kwargs in ({'author_id': 10}, {'author_id': 777}, {'fake': {'55': ['administrator']}}, {'native': discord.Permissions(administrator=True)}):
                ctx = make_ctx(bot, **kwargs)
                for name in ('perm list', 'perm roles'):
                    assert await bot.get_command(name).can_run(ctx)
            ctx = make_ctx(bot, fake={'55': ['manage_roles']})
            with pytest.raises(MissingRank):
                await bot.get_command('perm list').can_run(ctx)
        finally:
            await bot.close()
    asyncio.run(go())


def test_role_mutations_remain_at_prior_permissions_gate_with_fake_administrator():
    async def go():
        bot = await make_bot()
        try:
            for name in ('perm add', 'perm remove', 'perm remove deleted'):
                command = bot.get_command(name)
                assert command_category_gate(command, bot) == 5
                ctx = make_ctx(bot, level='headadmin', fake={'55': ['administrator']})
                with pytest.raises(MissingRank, match='Manager'):
                    await command.can_run(ctx)
            bot.db.set_config_committed.assert_not_awaited()
        finally:
            await bot.close()
    asyncio.run(go())


def test_zero_custom_admin_rank_does_not_make_list_public():
    async def go():
        bot = await make_bot()
        try:
            ctx = make_ctx(bot, overrides={'admin': {'rank': 0}})
            with pytest.raises(MissingRank):
                await bot.get_command('perm list').can_run(ctx)
        finally:
            await bot.close()
    asyncio.run(go())
