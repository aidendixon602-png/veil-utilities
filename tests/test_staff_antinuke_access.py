"""Veil staff ranks are distinct from Antinuke appointments and developer access."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest
from discord.ext import commands

from cogs.antinuke import Antinuke
from core import perms
from core.help import Help, category_labels, visible_command
from core.utils import UserError, antinuke_admin
from test_antinuke_panel import open_panel, press, setup

STAFF_LEVELS = ('helper', 'moderator', 'admin', 'headadmin', 'manager', 'coowner')


def test_new_bot_manager_is_distinct_and_old_saved_key_is_not_promoted():
    cfg = {'users': {'1': 'botmanager', '2': 'headadmin', '4': 'bot_manager'}, 'roles': {'3': 'botmanager'}}
    assert perms.migrate(cfg)
    assert cfg['users'] == {'1': 'headadmin', '2': 'headadmin', '4': 'manager'}
    assert cfg['roles'] == {'3': 'headadmin'}
    assert perms.resolve_key(cfg, 'Bot Manager') == 'manager'
    cfg['users']['4'] = perms.resolve_key(cfg, 'botmanager')
    assert not perms.migrate(cfg)
    assert cfg['users']['4'] == 'manager'
    assert perms.PRESETS['headadmin'][1] < perms.PRESETS['manager'][1] < perms.PRESETS['coowner'][1]


@pytest.mark.parametrize('level', STAFF_LEVELS)
def test_all_staff_levels_are_denied_root_nested_and_direct_access(level):
    async def go():
        bot = commands.Bot(command_prefix=',', intents=discord.Intents.default())
        bot.owner_ids = {90}
        store = {'antinuke': {'admins': []}, 'permlevels': {'users': {'20': level}},
                 'fakeperms': {'55': ['administrator']}, 'settings': {}, 'command_restrictions': {}}
        bot.db = SimpleNamespace(get_config=AsyncMock(side_effect=lambda gid, module, default=None: store.get(module, {})),
                                 set_config=AsyncMock(), set_config_committed=AsyncMock())
        await bot.add_cog(Antinuke(bot))
        guild = SimpleNamespace(id=1, owner_id=10)
        author = SimpleNamespace(id=20, roles=[SimpleNamespace(id=55, name='Staff', mention='<@&55>')],
                                 guild_permissions=discord.Permissions(administrator=True))
        ctx = SimpleNamespace(guild=guild, author=author, bot=bot, command=None)
        try:
            with pytest.raises(UserError, match='antinuke admin'):
                await antinuke_admin(ctx)
            # The real discord.py command checks invoke the cog check for every subcommand.
            for name in ('an', 'an status', 'an ban', 'an preset', 'an preset list', 'an toggle', 'an admin'):
                with pytest.raises(UserError, match='antinuke admin'):
                    await bot.get_command(name).can_run(ctx)
            bot.db.set_config.assert_not_awaited()
            bot.db.set_config_committed.assert_not_awaited()
        finally:
            await bot.close()
    asyncio.run(go())


def test_owner_developer_and_appointed_access_are_independent_of_rank():
    async def go():
        bot = commands.Bot(command_prefix=',', intents=discord.Intents.default())
        bot.owner_ids = {90}
        store = {'antinuke': {'admins': [20]}, 'permlevels': {}, 'fakeperms': {}, 'settings': {},
                 'command_restrictions': {}}
        bot.db = SimpleNamespace(get_config=AsyncMock(side_effect=lambda gid, module, default=None: store.get(module, {})))
        await bot.add_cog(Antinuke(bot))
        try:
            for actor in (10, 20, 90):
                ctx = SimpleNamespace(guild=SimpleNamespace(id=1, owner_id=10),
                                      author=SimpleNamespace(id=actor, roles=[]), bot=bot, command=None)
                assert await antinuke_admin(ctx)
                for name in ('an', 'an status', 'an preset list', 'an ban'):
                    assert await bot.get_command(name).can_run(ctx)
            # Removing the explicit appointment immediately removes access.
            store['antinuke']['admins'].clear()
            ctx.author.id = 20
            with pytest.raises(UserError):
                await bot.get_command('an').can_run(ctx)
        finally:
            await bot.close()
    asyncio.run(go())


def test_appointment_cannot_be_changed_by_appointed_admin_but_developer_can():
    async def go():
        cog, guild = setup()
        cog.bot.owner_ids = {90}
        cog.bot.db.store['antinuke'] = {'admins': [20], 'modules': {}}
        cog.security_notice = AsyncMock()
        target = SimpleNamespace(id=42, mention='<@42>')
        ctx = SimpleNamespace(guild=guild, author=SimpleNamespace(id=20), bot=cog.bot, approve=AsyncMock())
        with pytest.raises(UserError, match='server owner or a bot developer'):
            await Antinuke.an_admin.callback(cog, ctx, target)
        assert cog.bot.db.store['antinuke']['admins'] == [20]
        ctx.author.id = 90
        await Antinuke.an_admin.callback(cog, ctx, target)
        assert 42 in cog.bot.db.store['antinuke']['admins']
    asyncio.run(go())


def test_panel_buttons_recheck_appointment_and_deny_supreme_leader():
    cog, guild = setup()
    cog.bot.db.store['permlevels'] = {'users': {'2': 'supremeleader'}}
    panel, _ = open_panel(cog, guild)
    panel.author_id = 2
    it = press(panel, 'Turn on', user=2)
    it.response.send_message.assert_awaited_once()
    assert not cog.bot.db.store.get('antinuke', {}).get('enabled')
    cog.bot.db.store['antinuke'] = {'admins': [2], 'modules': {}}
    press(panel, 'Turn on', user=2)
    assert cog.bot.db.store['antinuke']['enabled']
    cog.bot.db.store['antinuke']['admins'].clear()
    it = press(panel, 'Turn off', user=2)
    it.response.send_message.assert_awaited_once()
    assert cog.bot.db.store['antinuke']['enabled']


def test_help_hides_antinuke_even_with_extreme_custom_rank_and_shows_appointed_admin():
    async def go():
        bot = commands.Bot(command_prefix=',', intents=discord.Intents.default())
        bot.owner_ids = set()
        store = {'antinuke': {'admins': []}, 'permlevels': {'users': {'20': 'coowner'},
                 'overrides': {'coowner': {'rank': 10000}}}, 'fakeperms': {}, 'settings': {},
                 'command_restrictions': {}}
        bot.db = SimpleNamespace(get_config=AsyncMock(side_effect=lambda gid, module, default=None: store.get(module, {})))
        await bot.add_cog(Antinuke(bot))
        bot.is_owner = AsyncMock(return_value=False)
        h = Help()
        h.context = SimpleNamespace(bot=bot, guild=SimpleNamespace(id=1, owner_id=10),
                                    author=SimpleNamespace(id=20, roles=[]), channel=None)
        try:
            access = await h.help_access(10000)
            for name in ('an', 'an status', 'an preset list'):
                assert not visible_command(bot.get_command(name), bot, False, 10000, access)
            store['antinuke']['admins'].append(20)
            access = await h.help_access(1)
            assert 'Antinuke' in category_labels({'Antinuke': []}, False, 1, access)
            assert visible_command(bot.get_command('an'), bot, False, 1, access)
            assert visible_command(bot.get_command('an preset list'), bot, False, 1, access)
            assert not visible_command(bot.get_command('an namemonitor'), bot, False, 1, access)
        finally:
            await bot.close()
    asyncio.run(go())


def test_access_fails_closed_when_appointment_store_is_unavailable():
    async def go():
        ctx = SimpleNamespace(guild=SimpleNamespace(id=1, owner_id=10), author=SimpleNamespace(id=20),
                              bot=SimpleNamespace(owner_ids=set(), is_owner=AsyncMock(return_value=False), db=None))
        with pytest.raises(UserError, match='Cannot verify'):
            await antinuke_admin(ctx)
    asyncio.run(go())


def test_revoked_appointment_cannot_browse_cached_help():
    from core.help import HelpView, category_commands

    async def go():
        bot = commands.Bot(command_prefix=',', intents=discord.Intents.default())
        bot.owner_ids = set()
        store = {'antinuke': {'admins': [20]}, 'permlevels': {}, 'fakeperms': {}, 'settings': {},
                 'command_restrictions': {}}
        bot.db = SimpleNamespace(get_config=AsyncMock(side_effect=lambda gid, module, default=None: store.get(module, {})))
        bot.is_owner = AsyncMock(return_value=False)
        await bot.add_cog(Antinuke(bot))
        guild = SimpleNamespace(id=1, owner_id=10)
        author = SimpleNamespace(id=20, roles=[])
        h = Help()
        h.context = SimpleNamespace(bot=bot, guild=guild, author=author, channel=None, clean_prefix=',')
        try:
            access = await h.help_access(0)
            view = HelpView(h, category_commands(bot), author_rank=0, access=access)
            assert 'Antinuke' in view.pages
            interaction = SimpleNamespace(guild=guild, user=author, client=bot,
                                          response=SimpleNamespace(send_message=AsyncMock()))
            assert await view.interaction_check(interaction)
            store['antinuke']['admins'].clear()
            assert not await view.interaction_check(interaction)
            interaction.response.send_message.assert_awaited_once()
            view.stop()
        finally:
            await bot.close()
    asyncio.run(go())


def test_staff_levels_do_not_unlock_developer_cogs():
    from cogs.devtools import DevTools
    from cogs.owner import Owner

    async def go():
        bot = SimpleNamespace(is_owner=AsyncMock(return_value=False), owner_ids={90})
        for level in STAFF_LEVELS:
            ctx = SimpleNamespace(author=SimpleNamespace(id=20, roles=[]),
                                  guild=SimpleNamespace(id=1, owner_id=10),
                                  bot=bot)
            for cog in (Owner(bot), DevTools(bot)):
                with pytest.raises(commands.NotOwner):
                    await cog.cog_check(ctx)
        bot.is_owner = AsyncMock(return_value=True)
        ctx.author.id = 90
        assert await Owner(bot).cog_check(ctx)
        assert await DevTools(bot).cog_check(ctx)
    asyncio.run(go())
