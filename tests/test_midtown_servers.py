"""Joined-server inventory stays private and requires current developer access."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import discord
import pytest
from discord.ext import commands
from discord.ext.commands.view import StringView

from cogs.devtools import DevTools
from core.midtown import MidtownView, result_pages, scan_joined


def guild(name, gid=1, owner=7, members=10):
    return SimpleNamespace(name=name, id=gid, owner_id=owner, member_count=members)


def interaction(uid=10, guild=None):
    return SimpleNamespace(user=SimpleNamespace(id=uid), guild=guild,
                           response=SimpleNamespace(edit_message=AsyncMock(), send_message=AsyncMock()))


def context(uid=10, *, in_guild=True):
    author = SimpleNamespace(id=uid, send=AsyncMock(return_value=SimpleNamespace(edit=AsyncMock())))
    return SimpleNamespace(author=author, guild=SimpleNamespace(id=5) if in_guild else None,
                           clean_prefix='!', approve=AsyncMock(), warn=AsyncMock(), send=AsyncMock())


def bot(guilds=(), allowed=True):
    return SimpleNamespace(guilds=list(guilds), is_owner=AsyncMock(return_value=allowed))


def test_matching_exact_partial_case_punctuation_and_unavailable():
    class BrokenOwner:
        name = 'Midtown!'
        id = 8
        member_count = None
        @property
        def owner_id(self):
            raise RuntimeError('unavailable')
    scanned, matches, unavailable = scan_joined([
        guild('Midtown', 1), guild('MIDTOWN', 2), guild('Pre-midtown 24!', 3), guild('not mid town', 4),
        guild(None, 5), BrokenOwner(), guild('East MIDTOWN West', 9, None, None),
    ])
    assert scanned == 7 and len(matches) == 5 and unavailable == 1
    assert sum(m.exact for m in matches) == 2
    assert any(m.name == 'Midtown!' and m.owner_id is None and m.members is None for m in matches)
    description = result_pages([guild('MIDTOWN', 3, None, None)])[0].description
    assert 'Exact' in description and 'Owner ID: Unavailable' in description and 'Members: Unavailable' in description


def test_empty_and_uninspectable_results_are_honest():
    page = result_pages([guild('Other'), guild(None)])[0]
    assert 'Scanned:** 2' in page.description and 'Matches:** 0' in page.description
    assert 'No matching joined servers' in page.description and 'counts may be incomplete' in page.description


def test_pagination_sizes_and_hostile_names_never_ping():
    bad = 'Midtown `** @everyone <@123>\n\u202e ' + ('X' * 200)
    pages = result_pages([guild(bad, i + 1, 3, 0) for i in range(53)])
    assert len(pages) == 9 and 'Matches:** 53' in pages[0].description
    assert all(len(page) < 6000 and len(page.description) <= 4096 for page in pages)
    assert '@everyone' not in pages[0].description and '<@123>' not in pages[0].description
    assert '\u202e' not in pages[0].description
    assert 'Page 9/9' in pages[-1].footer.text


def test_command_dm_only_and_permission_checks():
    async def go():
        ctx = context()
        b = bot([guild('MIDTOWN', 123), guild('Other', 987)])
        cog = DevTools(b)
        await DevTools.midtown_servers.callback(cog, ctx)
        sent = ctx.author.send.await_args.kwargs
        assert '123' in sent['embed'].description and '987' not in sent['embed'].description
        assert sent['allowed_mentions'].everyone is False and isinstance(sent['view'], MidtownView)
        ctx.send.assert_not_awaited()
        assert 'MIDTOWN' not in ctx.approve.await_args.args[0]
        assert await cog.cog_check(ctx)
        for check in DevTools.midtown.checks + DevTools.midtown_servers.checks:
            assert await check(SimpleNamespace(bot=b, author=ctx.author))
        b.is_owner.return_value = False
        with pytest.raises(commands.NotOwner):
            await cog.cog_check(ctx)
        with pytest.raises(commands.NotOwner):
            await DevTools.midtown_servers.callback(cog, ctx)
        assert ctx.author.send.await_count == 1
    asyncio.run(go())


def test_dm_failure_never_falls_back_to_public_results():
    async def go():
        ctx = context()
        ctx.author.send.side_effect = discord.Forbidden(MagicMock(status=403, reason='Forbidden'), 'no dm')
        await DevTools.midtown_servers.callback(DevTools(bot([guild('Midtown', 123)])), ctx)
        ctx.send.assert_not_awaited()
        ctx.approve.assert_not_awaited()
        assert '123' not in str(ctx.warn.await_args) and 'Midtown' not in str(ctx.warn.await_args)
        assert ctx.command_log_warning
    asyncio.run(go())


def test_dm_and_access_failures_are_generic_and_do_not_leak():
    async def go():
        ctx = context(in_guild=False)
        ctx.author.send.side_effect = aiohttp.ClientConnectionError('secret guild name')
        await DevTools.midtown_servers.callback(DevTools(bot([guild('Midtown secret', 123)])), ctx)
        ctx.approve.assert_not_awaited()
        assert 'secret' not in str(ctx.warn.await_args)
        denied = bot([guild('Midtown secret')])
        denied.is_owner.side_effect = RuntimeError('secret guild name')
        with pytest.raises(commands.NotOwner):
            await DevTools.midtown_servers.callback(DevTools(denied), context())
    asyncio.run(go())


def test_all_controls_recheck_invoker_developer_and_dm_context():
    async def go():
        b = bot([guild('Midtown')])
        view = MidtownView(b, 10, result_pages(b.guilds))
        assert await view.interaction_check(interaction())
        other = interaction(uid=11)
        assert not await view.interaction_check(other)
        other.response.send_message.assert_awaited_once()
        public = interaction(guild=SimpleNamespace(id=5))
        assert not await view.interaction_check(public)
        b.is_owner.return_value = False
        revoked = interaction()
        assert not await view.interaction_check(revoked)
        b.is_owner.side_effect = RuntimeError('secret')
        assert not await view.interaction_check(interaction())
        assert revoked.response.edit_message.await_count == 0
    asyncio.run(go())


def test_pagination_and_refresh_rescan_joined_cache():
    async def go():
        b = bot([guild(f'Midtown {i}', i + 1) for i in range(8)])
        view = MidtownView(b, 10, result_pages(b.guilds))
        it = interaction()
        assert await view.interaction_check(it)
        await view.next_page.callback(it)
        assert view.index == 1 and view.previous.disabled is False and view.next_page.disabled
        b.guilds = [guild('Midtown new', 999)]
        it = interaction()
        assert await view.interaction_check(it)
        await view.refresh.callback(it)
        assert view.index == 0 and len(view.pages) == 1
        assert '999' in it.response.edit_message.await_args.kwargs['embed'].description
        assert 'Matches:** 1' in view.pages[0].description
        it = interaction()
        await view.refresh.callback(it)
        it.response.send_message.assert_awaited_once()
        assert it.response.send_message.await_args.kwargs['ephemeral']
    asyncio.run(go())


def test_refresh_failure_is_private_and_generic():
    async def go():
        class BrokenBot:
            is_owner = AsyncMock(return_value=True)
            @property
            def guilds(self):
                raise RuntimeError('sensitive cross-server data')
        view = MidtownView(BrokenBot(), 10, result_pages([]))
        it = interaction()
        assert await view.interaction_check(it)
        await view.refresh.callback(it)
        assert 'sensitive' not in str(it.response.send_message.await_args)
        assert it.response.send_message.await_args.kwargs['ephemeral']
    asyncio.run(go())


def test_registration_and_extra_argument_validation():
    async def go():
        b = commands.Bot(command_prefix=',', intents=discord.Intents.none())
        await b.add_cog(DevTools(b))
        assert b.get_command('midtown') is not None
        command = b.get_command('midtown servers')
        assert command is not None and command.hidden and command.parent.hidden
        ctx = SimpleNamespace(bot=b, message=SimpleNamespace(attachments=[]), view=StringView(' unexpected'))
        with pytest.raises(commands.TooManyArguments):
            await command._parse_arguments(ctx)
        await b.close()
    asyncio.run(go())


def test_scan_failure_and_timeout_do_not_publish_sensitive_details(caplog):
    async def go():
        class BrokenBot:
            is_owner = AsyncMock(return_value=True)
            @property
            def guilds(self):
                raise RuntimeError('private guild details')
        ctx = context()
        with pytest.raises(commands.CommandError) as exc:
            await DevTools.midtown_servers.callback(DevTools(BrokenBot()), ctx)
        assert 'private guild details' not in str(exc.value)
        ctx.send.assert_not_awaited()
        ctx.author.send.assert_not_awaited()
        view = MidtownView(bot(), 10, result_pages([]))
        view.message = SimpleNamespace(edit=AsyncMock())
        await view.on_timeout()
        view.message.edit.assert_awaited_once_with(view=None)
    asyncio.run(go())
    assert 'private guild details' not in caplog.text


def test_hidden_command_log_omits_arguments_and_scan_results():
    from core.logembeds import command_used
    author = SimpleNamespace(id=5, name='dev', display_name='dev', mention='<@5>',
                             display_avatar=SimpleNamespace(url='https://cdn/x.png'))
    ctx = SimpleNamespace(message=SimpleNamespace(content=',midtown servers secret', jump_url=None),
                          command=SimpleNamespace(qualified_name='midtown servers', hidden=True, cog_name='DevTools'),
                          author=author, channel=SimpleNamespace(mention='#general'), prefix=',',
                          command_log_outcome='Private scan sent by DM')
    card = command_used(ctx, 'Developer', 'Built-in')
    text = str(card.to_dict())
    assert 'secret' not in text and 'arguments hidden' in text
