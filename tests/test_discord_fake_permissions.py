"""Discord and fake grants authorize ordinary commands, not independent security gates."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest
from discord.ext import commands

from cogs.moderation import Moderation
from cogs.permissions import Permissions
from core.bot import Veil
from core.help import Help, category_labels, visible_command
from core.utils import MissingRank, has_perms, requires_uperm


def run(coro):
    return asyncio.run(coro)


def context(bot, store, *, native=None, level=None, fake=None):
    store.update({'permlevels': {'users': {'20': level} if level else {}},
                  'fakeperms': {'55': fake or []}, 'settings': {}, 'command_restrictions': {}, 'antinuke': {}})
    author = SimpleNamespace(id=20, roles=[SimpleNamespace(id=55)], guild_permissions=native or discord.Permissions.none())
    return SimpleNamespace(bot=bot, guild=SimpleNamespace(id=1, owner_id=10), author=author,
                           channel=SimpleNamespace(id=30, permissions_for=lambda _: author.guild_permissions),
                           command=None, cog=None, bot_permissions=discord.Permissions.all(),
                           me=SimpleNamespace(guild_permissions=discord.Permissions.all()))


def test_real_moderation_command_accepts_native_fake_and_mixed_with_level():
    async def go():
        bot = commands.Bot(command_prefix=',', intents=discord.Intents.default())
        bot.owner_ids = set()
        store = {}
        bot.db = SimpleNamespace(get_config=AsyncMock(side_effect=lambda gid, key, default=None: store.get(key, {})))
        await bot.add_cog(Moderation(bot))
        try:
            cmd = bot.get_command('ban')
            for kwargs in ({'native': discord.Permissions(ban_members=True)}, {'fake': ['ban_members']},
                           {'level': 'helper', 'fake': ['ban_members']},
                           {'level': 'helper', 'native': discord.Permissions(ban_members=True)}, {'level': 'admin'}):
                ctx = context(bot, store, **kwargs)
                assert await cmd.can_run(ctx), kwargs
            ctx = context(bot, store, native=discord.Permissions(kick_members=True))
            with pytest.raises(MissingRank):
                await cmd.can_run(ctx)
            ctx = context(bot, store, native=discord.Permissions(ban_members=True))
            ctx.channel.permissions_for = lambda _: discord.Permissions.none()
            with pytest.raises(MissingRank):
                await cmd.can_run(ctx)
        finally:
            await bot.close()
    run(go())


def test_mixed_permission_sources_and_rank_only_child():
    @commands.group(invoke_without_command=True)
    @has_perms(manage_messages=True)
    async def tools(ctx):
        pass

    @tools.command()
    @requires_uperm('admin')
    async def rankonly(ctx):
        pass

    @commands.command()
    @requires_uperm('admin')
    @has_perms(kick_members=True, manage_messages=True)
    async def mixed(ctx):
        pass

    async def go():
        bot = commands.Bot(command_prefix=',', intents=discord.Intents.default())
        bot.owner_ids = set()
        store = {}
        bot.db = SimpleNamespace(get_config=AsyncMock(side_effect=lambda gid, key, default=None: store.get(key, {})))
        bot.add_command(tools)
        bot.add_command(mixed)
        try:
            ctx = context(bot, store, level='helper', fake=['kick_members'])
            store['permlevels']['overrides'] = {'helper': {'perms': ['manage_messages']}}
            assert await mixed.can_run(ctx)
            ctx = context(bot, store, fake=['administrator'])
            with pytest.raises(MissingRank):
                await rankonly.can_run(ctx)
        finally:
            await bot.close()
    run(go())


def test_help_category_and_rank_mutation_protection():
    async def go():
        bot = commands.Bot(command_prefix=',', intents=discord.Intents.default())
        bot.owner_ids = set()
        store = {}
        bot.db = SimpleNamespace(get_config=AsyncMock(side_effect=lambda gid, key, default=None: store.get(key, {})))
        await bot.add_cog(Moderation(bot))
        await bot.add_cog(Permissions(bot))
        try:
            ctx = context(bot, store, native=discord.Permissions(ban_members=True))
            h = Help()
            h.context = ctx
            access = await h.help_access(0)
            ban = bot.get_command('ban')
            assert visible_command(ban, bot, False, 0, access)
            assert 'Moderation' in category_labels({'Moderation': [ban]}, False, 0, access)
            ctx = context(bot, store, native=discord.Permissions(administrator=True))
            for name in ('perm add', 'perm remove', 'perm remove deleted'):
                with pytest.raises(MissingRank):
                    await bot.get_command(name).can_run(ctx)
                h.context = ctx
                assert not visible_command(bot.get_command(name), bot, False, 0, await h.help_access(0))
        finally:
            await bot.close()
    run(go())


def test_private_mode_admits_real_staff_and_antinuke_appointments_not_every_member():
    async def go():
        bot = Veil()
        bot.owner_ids = frozenset()
        bot.auth_required = False
        bot.private_mode = True
        store = {}
        bot.db = SimpleNamespace(get_config=AsyncMock(side_effect=lambda gid, key, default=None: store.get(key, {})))
        ctx = context(bot, store, native=discord.Permissions(ban_members=True))
        assert await bot.is_trusted(ctx)
        ctx = context(bot, store, native=discord.Permissions(send_messages=True))
        assert not await bot.is_trusted(ctx)
        store['antinuke']['admins'] = [20]
        assert await bot.is_trusted(ctx)
        store['antinuke']['admins'] = []
        assert not await bot.is_trusted(ctx)
    run(go())
