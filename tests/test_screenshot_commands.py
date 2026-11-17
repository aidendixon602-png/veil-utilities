"""Coverage for authorization, disabled command listing and unstrip screenshot flows."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest
from discord.ext import commands
from discord.ext.commands.view import StringView

from cogs.authorization import Authorization
from cogs.settings import Settings
from cogs.devtools import DevTools
from core.utils import UserError


def test_authserver_alias_defaults_to_current_guild_and_id_still_works():
    async def go():
        guild = SimpleNamespace(id=123, name='Test @everyone')
        db = SimpleNamespace(execute=AsyncMock())
        bot = SimpleNamespace(db=db, authorized=set(), get_guild=lambda gid: guild if gid == 123 else None)
        cog = Authorization(bot)
        ctx = SimpleNamespace(guild=guild, author=SimpleNamespace(id=4), approve=AsyncMock(), warn=AsyncMock())
        assert 'authserver' in Authorization.authorize_server.aliases
        await Authorization.authorize_server.callback(cog, ctx)
        assert 123 in bot.authorized
        assert '@everyone' not in ctx.approve.await_args.args[0]
        await Authorization.authorize_server.callback(cog, ctx)
        ctx.warn.assert_awaited_once()
        await Authorization.authorize_server.callback(cog, ctx, 456)
        assert bot.authorized == {123, 456}
        ctx.guild = None
        with pytest.raises(UserError, match='server ID'):
            await Authorization.authorize_server.callback(cog, ctx)
        with pytest.raises(UserError, match='valid server ID'):
            await Authorization.authorize_server.callback(cog, ctx, -1)
        assert db.execute.await_count == 2
    asyncio.run(go())


def test_authserver_remains_developer_only():
    async def go():
        bot = SimpleNamespace(owner_ids=set(), owner_id=None, is_owner=AsyncMock(return_value=False))
        cog = Authorization(bot)
        with pytest.raises(commands.NotOwner):
            await cog.cog_check(SimpleNamespace(author=SimpleNamespace(id=777777)))
    asyncio.run(go())


def test_dcmd_no_argument_parses_and_lists_all_pages_without_changes():
    async def go():
        bot = commands.Bot(command_prefix=',', intents=discord.Intents.none())
        cfg = {'disabled': [f'command{i:02}' for i in range(45)]}
        bot.db = SimpleNamespace(get_config=AsyncMock(return_value=cfg), set_config=AsyncMock())
        cog = Settings(bot)
        await bot.add_cog(cog)
        cmd = bot.get_command('dcmd')
        ctx = SimpleNamespace(bot=bot, guild=SimpleNamespace(id=1), message=SimpleNamespace(attachments=[]),
                              view=StringView(''), paginate=AsyncMock(), warn=AsyncMock())
        await cmd._parse_arguments(ctx)
        assert ctx.kwargs['command'] is None
        await cmd.callback(cog, ctx, **ctx.kwargs)
        pages = ctx.paginate.await_args.args[0]
        assert len(pages) == 3 and all(p.title == 'Disabled Commands' for p in pages)
        assert sum(p.description.count('`') for p in pages) == 90
        assert all(p.footer.text == '45 disabled' for p in pages)
        bot.db.set_config.assert_not_awaited()
        await bot.close()
    asyncio.run(go())


def test_dcmd_no_argument_handles_empty_list():
    async def go():
        bot = SimpleNamespace(db=SimpleNamespace(get_config=AsyncMock(return_value={})))
        ctx = SimpleNamespace(guild=SimpleNamespace(id=1), warn=AsyncMock(), paginate=AsyncMock())
        await Settings.disablecommand.callback(Settings(bot), ctx)
        ctx.warn.assert_awaited_once()
        ctx.paginate.assert_not_awaited()
    asyncio.run(go())


class Role:
    def __init__(self, rid, name, position=1, managed=False):
        self.id, self.name, self.position, self.managed = rid, name, position, managed
    def is_default(self):
        return False
    def __lt__(self, other):
        return self.position < other.position


def unstrip_setup():
    roles = {10: Role(10, 'Founder/Owner'), 11: Role(11, '@everyone')}
    store = {'strips': {'5': [10, 11]}, 'roleblacklist': {}}
    async def get_config(gid, key):
        return store[key]
    async def save(gid, key, value):
        store[key] = deepcopy(value)
    bot = SimpleNamespace(db=SimpleNamespace(get_config=AsyncMock(side_effect=get_config),
                                              set_config_committed=AsyncMock(side_effect=save)))
    guild = SimpleNamespace(id=1, get_role=roles.get, me=SimpleNamespace(top_role=Role(99, 'bot', 50)))
    member = SimpleNamespace(id=5, mention='<@5>', add_roles=AsyncMock())
    ctx = SimpleNamespace(guild=guild, author=SimpleNamespace(id=9), send=AsyncMock(), warn=AsyncMock())
    return DevTools(bot), ctx, member, roles, store


def test_unstrip_lists_restored_roles_and_clears_snapshot():
    async def go():
        cog, ctx, member, roles, store = unstrip_setup()
        await DevTools.unstrip.callback(cog, ctx, member)
        description = ctx.send.await_args.kwargs['embed'].description
        assert '**Restored (2)**' in description and 'Founder/Owner' in description
        assert '@everyone' not in description
        assert store['strips'] == {}
        assert ctx.send.await_args.kwargs['allowed_mentions'].roles is False
    asyncio.run(go())


def test_unstrip_keeps_ineligible_role_ids_for_review():
    async def go():
        cog, ctx, member, roles, store = unstrip_setup()
        store['roleblacklist'] = {'roles': [11]}
        await DevTools.unstrip.callback(cog, ctx, member)
        assert store['strips'] == {'5': [11]}
        member.add_roles.assert_awaited_once_with(roles[10], reason=f'unstrip by {ctx.author}')
        assert 'could not be restored' in ctx.send.await_args.kwargs['embed'].description
    asyncio.run(go())


def test_unstrip_unknown_discord_result_retains_snapshot():
    async def go():
        cog, ctx, member, roles, store = unstrip_setup()
        member.add_roles.side_effect = asyncio.TimeoutError()
        await DevTools.unstrip.callback(cog, ctx, member)
        assert store['strips'] == {'5': [10, 11]}
        assert 'some roles may already' in ctx.warn.await_args.args[0]
        cog.bot.db.set_config_committed.assert_not_awaited()
    asyncio.run(go())


def test_unstrip_database_failure_after_discord_is_reported_honestly():
    async def go():
        cog, ctx, member, roles, store = unstrip_setup()
        cog.bot.db.set_config_committed.side_effect = RuntimeError('db down')
        await DevTools.unstrip.callback(cog, ctx, member)
        assert store['strips'] == {'5': [10, 11]}
        assert 'Discord accepted' in ctx.warn.await_args.args[0]
        ctx.send.assert_not_awaited()
    asyncio.run(go())


def test_unstrip_changed_snapshot_is_not_overwritten():
    async def go():
        cog, ctx, member, roles, store = unstrip_setup()
        async def change(*args, **kwargs):
            store['strips']['5'] = [20]
        member.add_roles.side_effect = change
        await DevTools.unstrip.callback(cog, ctx, member)
        assert store['strips'] == {'5': [20]}
        assert 'snapshot changed' in ctx.send.await_args.kwargs['embed'].description
        cog.bot.db.set_config_committed.assert_not_awaited()
    asyncio.run(go())


def test_unstrip_no_eligible_roles_keeps_snapshot():
    async def go():
        cog, ctx, member, roles, store = unstrip_setup()
        store['roleblacklist'] = {'roles': [10, 11]}
        await DevTools.unstrip.callback(cog, ctx, member)
        assert store['strips'] == {'5': [10, 11]}
        member.add_roles.assert_not_awaited()
        cog.bot.db.set_config_committed.assert_not_awaited()
        assert 'snapshot was retained' in ctx.warn.await_args.args[0]
    asyncio.run(go())


def test_authserver_alias_parses_optional_id():
    async def go():
        bot = commands.Bot(command_prefix=',', intents=discord.Intents.none())
        await bot.add_cog(Authorization(bot))
        cmd = bot.get_command('authserver')
        ctx = SimpleNamespace(bot=bot, message=SimpleNamespace(attachments=[]), view=StringView(''))
        await cmd._parse_arguments(ctx)
        assert ctx.args[-1] is None
        ctx.view = StringView('1544703040811049052')
        await cmd._parse_arguments(ctx)
        assert ctx.args[-1] == 1544703040811049052
        await bot.close()
    asyncio.run(go())
