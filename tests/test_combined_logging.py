"""Single-channel logging setup and migration, without Discord network access."""
import asyncio
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from cogs import logging as logs
from core import logembeds as fmt
from core.utils import UserError
from test_logging_safety import channel, cog_with, guild_with


def context(guild, confirmed=True):
    return SimpleNamespace(guild=guild, author=SimpleNamespace(id=10), bot=SimpleNamespace(),
        command=SimpleNamespace(can_run=AsyncMock(return_value=True)), confirm=AsyncMock(return_value=confirmed),
        neutral=AsyncMock(), approve=AsyncMock(), send=AsyncMock())


def test_setup_uses_one_private_channel_preserving_existing_off_and_shared_routes(monkeypatch):
    async def go():
        monkeypatch.setattr(logs, 'owner_or_developer', lambda *args: True)
        previous = channel(1, 'old')
        new = channel(5, 'veil-logs')
        g = guild_with(previous, new)
        g.create_category = AsyncMock(return_value=SimpleNamespace(id=90))
        g.create_text_channel = AsyncMock(return_value=new)
        cog, store = cog_with({'joinleave': 1, 'commands': logs.OFF, 'approved_routes': {'joinleave': 1}})
        ctx = context(g)
        await logs.Logging.logs_setup.callback(cog, ctx)
        assert g.create_category.await_count == g.create_text_channel.await_count == 1
        assert g.create_text_channel.await_args.args[0] == 'veil-logs'
        assert g.create_text_channel.await_args.kwargs['overwrites'][g.default_role].view_channel is False
        cfg = store['logs']
        assert cfg['joinleave'] == 1 and cfg['commands'] == logs.OFF and 'members' not in cfg
        assert cfg['messages'] == cfg['server'] == cfg['timeouts'] == 5
        assert cfg['approved_routes']['messages'] == 5
        previous.edit.assert_not_awaited()
        previous.delete.assert_not_awaited()
        assert 'one new #veil-logs' in ctx.confirm.await_args.args[0]
        await logs.Logging.logs_setup.callback(cog, ctx)
        assert g.create_text_channel.await_count == 1
    asyncio.run(go())


def test_combine_preview_cancel_off_stale_access_and_single_save(monkeypatch):
    async def go():
        monkeypatch.setattr(logs, 'owner_or_developer', lambda *args: True)
        old, new = channel(1, 'old'), channel(2, 'combined')
        g = guild_with(old, new)
        cfg = {'commands': logs.OFF, 'roles': 1, 'approved_routes': {'roles': 1}, 'ignore': {'users': [44]}}
        cog, store = cog_with(deepcopy(cfg))
        ctx = context(g, False)
        await logs.Logging.logs_combine.callback(cog, ctx, new)
        assert store['logs'] == cfg and cog.bot.db.set_config.await_count == 0
        assert 'Unset categories will become active' in ctx.confirm.await_args.args[0]
        assert 'Categories explicitly off stay off' in ctx.confirm.await_args.args[0]
        async def stale(*args, **kwargs):
            store['logs']['voice'] = logs.OFF
            return True
        ctx.confirm.side_effect = stale
        with pytest.raises(UserError, match='changed since preview'):
            await logs.Logging.logs_combine.callback(cog, ctx, new)
        assert cog.bot.db.set_config.await_count == 0
        store['logs'] = deepcopy(cfg)
        ctx.confirm.side_effect = None
        ctx.confirm.return_value = True
        monkeypatch.setattr(logs, 'owner_or_developer', lambda *args: False)
        with pytest.raises(UserError, match='access changed'):
            await logs.Logging.logs_combine.callback(cog, ctx, new)
        assert cog.bot.db.set_config.await_count == 0
        monkeypatch.setattr(logs, 'owner_or_developer', lambda *args: True)
        await logs.Logging.logs_combine.callback(cog, ctx, new)
        result = store['logs']
        assert result['commands'] == logs.OFF and result['ignore'] == {'users': [44]}
        assert all(result[key] == 2 and result['approved_routes'][key] == 2 for key in logs.TYPES if key != 'commands')
        assert cog.bot.db.set_config.await_count == 1
        old.edit.assert_not_awaited()
        old.delete.assert_not_awaited()
    asyncio.run(go())


def test_setup_and_combine_recheck_state_and_permissions(monkeypatch):
    async def go():
        monkeypatch.setattr(logs, 'owner_or_developer', lambda *args: True)
        out = channel(2, 'combined')
        g = guild_with(out)
        g.create_category, g.create_text_channel = AsyncMock(), AsyncMock()
        cog, store = cog_with({})
        ctx = context(g)
        async def change(*args, **kwargs):
            store['logs']['roles'] = 123
            return True
        ctx.confirm.side_effect = change
        with pytest.raises(UserError, match='changed since preview'):
            await logs.Logging.logs_setup.callback(cog, ctx)
        g.create_category.assert_not_awaited()
        store['logs'] = {}
        ctx.confirm.side_effect = None
        ctx.command.can_run.return_value = False
        with pytest.raises(UserError, match='access changed'):
            await logs.Logging.logs_combine.callback(cog, ctx, out)
        ctx.command.can_run.return_value = True
        async def remove_permission(*args, **kwargs):
            out.permissions_for.return_value.send_messages = False
            return True
        ctx.confirm.side_effect = remove_permission
        with pytest.raises(UserError, match='no longer available'):
            await logs.Logging.logs_combine.callback(cog, ctx, out)
        assert cog.bot.db.set_config.await_count == 0
    asyncio.run(go())


def test_creation_and_write_uncertainty_are_reported_without_retry():
    async def go():
        g = guild_with()
        g.create_category = AsyncMock(return_value=SimpleNamespace(id=80))
        g.create_text_channel = AsyncMock(side_effect=asyncio.TimeoutError())
        cog, _ = cog_with({})
        result = await cog.organize_combined(g, {})
        assert 'not confirmed' in result.failures[0] and g.create_text_channel.await_count == 1
        new = channel(10, 'veil-logs')
        g.create_text_channel = AsyncMock(return_value=new)
        cog.bot.db.set_config_committed = AsyncMock(side_effect=OSError('sensitive'))
        result = await cog.organize_combined(g, {})
        assert result.created == [new] and 'Save not confirmed' in result.failures[0]
        assert 'sensitive' not in result.failures[0] and cog.bot.db.set_config_committed.await_count == 1
    asyncio.run(go())


def test_screenshot_style_cards_keep_details_and_unknown_actor():
    author = SimpleNamespace(id=7, name='user', display_name='user', mention='<@7>', display_avatar=SimpleNamespace(url='https://example.com/a.png'))
    ch = SimpleNamespace(id=3, mention='<#3>')
    before = SimpleNamespace(content='old')
    after = SimpleNamespace(content='new', author=author, channel=ch, id=8,
                            jump_url='https://discord.com/channels/1/3/8', created_at=None)
    edit = fmt.message_edited(before, after)
    fields = {f.name: f.value for f in edit.fields}
    assert edit.title == 'Message Edited' and fields['Before'] == '> old' and fields['After'] == '> new'
    assert fields['Channel'] == '<#3>' and fields['Jump'].endswith('(https://discord.com/channels/1/3/8)')
    card = fmt.timed_out(author, discord.utils.utcnow() + timedelta(minutes=10), None, None)
    assert card.title == 'Timeout Applied' and {f.name for f in card.fields} >= {'User', 'Until', 'Reason'}
    assert 'By' not in {f.name for f in card.fields}
    assert all(len(f.value) <= 1024 for f in edit.fields + card.fields)


def test_combined_routes_deliver_real_edit_and_timeout_cards(monkeypatch):
    async def go():
        monkeypatch.setattr(logs, 'owner_or_developer', lambda *args: True)
        out = channel(2, 'combined')
        g = guild_with(out)
        cog, _ = cog_with({})
        await logs.Logging.logs_combine.callback(cog, context(g), out)
        member = SimpleNamespace(id=42, name='subject', display_name='subject', mention='<@42>', bot=False,
                                 display_avatar=SimpleNamespace(url='https://example.com/a.png'), guild=g)
        source = SimpleNamespace(id=3, mention='<#3>')
        before = SimpleNamespace(content='old')
        after = SimpleNamespace(content='new', author=member, channel=source, id=8, guild=g,
                                jump_url='https://discord.com/channels/1/3/8', created_at=None)
        await cog.on_message_edit(before, after)
        assert out.send.await_count == 1 and out.send.await_args.kwargs['embed'].title == 'Message Edited'
        assert out.send.await_args.kwargs['allowed_mentions'].users is False
        member.nick, member.roles, member.timed_out_until = None, [], None
        later = SimpleNamespace(**{**vars(member), 'timed_out_until': discord.utils.utcnow() + timedelta(minutes=10)})
        monkeypatch.setattr(fmt, 'audit_actor', AsyncMock(return_value=(None, None)))
        await cog.on_member_update(member, later)
        assert out.send.await_count == 2 and out.send.await_args.kwargs['embed'].title == 'Timeout Applied'
    asyncio.run(go())


def test_setup_does_not_save_if_config_changes_during_creation():
    async def go():
        g = guild_with()
        cog, store = cog_with({})
        g.create_category = AsyncMock(return_value=SimpleNamespace(id=80))
        async def create(*args, **kwargs):
            store['logs']['roles'] = logs.OFF
            return channel(10, 'veil-logs')
        g.create_text_channel = AsyncMock(side_effect=create)
        report = await cog.organize_combined(g, {})
        assert report.created and 'no routes were saved' in report.failures[0]
        assert store['logs'] == {'roles': logs.OFF} and cog.bot.db.set_config.await_count == 0
    asyncio.run(go())
