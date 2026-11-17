"""Channel-only role-blacklist list and requester-only pagination."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from cogs.moderation import BlacklistPaginator, Moderation
from core.utils import UserError
from test_roleblacklist_enforcement import _mock_context, _mock_guild, _mock_member, _mock_role, bot as role_bot  # noqa: F401


def test_list_is_in_channel_even_if_other_rbl_responses_use_dm(role_bot):  # noqa: F811
    async def go():
        guild = _mock_guild(123)
        owner = _mock_member(guild.owner_id)
        ctx = _mock_context(role_bot, guild, owner)
        ctx.command = Moderation.rbl_list
        logging = SimpleNamespace(rbl_private=AsyncMock(return_value=True))
        role_bot.get_cog = lambda _: logging
        cog = Moderation(role_bot)
        roles = [_mock_role(1000 + i, f'Role {i}') for i in range(18)]
        guild.get_role.side_effect = lambda rid: next((r for r in roles if r.id == rid), None)
        role_bot._storage[(123, 'roleblacklist')] = {'roles': [r.id for r in roles] + [9999]}
        await cog.cog_before_invoke(ctx)
        assert ctx._private_rbl is False
        await Moderation.rbl_list.callback(cog, ctx)
        sent = ctx.send.await_args.kwargs
        embed, view = sent['embed'], sent['view']
        assert isinstance(view, BlacklistPaginator)
        assert len(view.embeds) == 2 and view.prev.disabled and not view.next.disabled
        assert embed.title == 'Role blacklist' and '19 blacklisted roles' in embed.footer.text
        assert 'Page 1/2' in embed.footer.text and 'Visible in this channel' in embed.footer.text
        assert 'deleted role (`9999`)' in view.embeds[1].description
        assert 'remove deleted' in embed.description
        assert sent['allowed_mentions'].to_dict() == discord.AllowedMentions.none().to_dict()
        ctx.author.send.assert_not_awaited()
        ctx.command = Moderation.rbl_add
        await cog.cog_before_invoke(ctx)
        assert ctx._private_rbl is True
        view.stop()
    asyncio.run(go())


def test_role_blacklist_pages_are_requester_only_and_recheck_owner():
    async def go():
        pages = [discord.Embed(description='first'), discord.Embed(description='second')]
        view = BlacklistPaginator(10, 123, pages)
        guild = SimpleNamespace(owner_id=10)
        client = SimpleNamespace(get_guild=lambda _: guild, owner_ids=set(), owner_id=None)
        response = SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock())
        it = SimpleNamespace(user=SimpleNamespace(id=11), client=client, response=response)
        assert not await view.interaction_check(it)
        it.user.id = 10
        assert await view.interaction_check(it)
        await view._go(it, 1)
        assert view.prev.disabled is False and view.next.disabled is True
        assert response.edit_message.await_args.kwargs['embed'].description == 'second'
        assert response.edit_message.await_args.kwargs['allowed_mentions'].to_dict() == discord.AllowedMentions.none().to_dict()
        guild.owner_id = 22
        assert not await view.interaction_check(it)
        view.stop()
    asyncio.run(go())


def test_role_blacklist_list_keeps_owner_gate():
    async def go():
        ctx = SimpleNamespace(guild=SimpleNamespace(owner_id=1), author=SimpleNamespace(id=2),
                              bot=SimpleNamespace(owner_ids=set(), owner_id=None))
        check = next(c for c in Moderation.rbl_list.checks if getattr(c, '__veil_owner_only__', False))
        with pytest.raises(UserError):
            await check(ctx)
    asyncio.run(go())


def test_single_page_has_no_buttons_and_empty_result_is_in_channel(role_bot):  # noqa: F811
    async def go():
        guild = _mock_guild(123)
        ctx = _mock_context(role_bot, guild, _mock_member(guild.owner_id))
        ctx._private_rbl = True
        ctx.send.return_value = MagicMock()
        cog = Moderation(role_bot)
        role = _mock_role(101, 'Only role')
        guild.get_role.side_effect = lambda rid: role if rid == role.id else None
        role_bot._storage[(123, 'roleblacklist')] = {'roles': [role.id]}
        await Moderation.rbl_list.callback(cog, ctx)
        assert ctx._private_rbl is False and 'view' not in ctx.send.await_args.kwargs
        role_bot._storage[(123, 'roleblacklist')] = {'roles': []}
        ctx._private_rbl = True
        ctx.warn = AsyncMock()
        await Moderation.rbl_list.callback(cog, ctx)
        assert ctx._private_rbl is False
        ctx.warn.assert_awaited_once_with('no roles are blacklisted')
    asyncio.run(go())
