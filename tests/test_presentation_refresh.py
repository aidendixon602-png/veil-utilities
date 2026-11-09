"""Presentation refresh must not hide grants, alter inputs, or weaken interactive ownership."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from cogs.permissions import Permissions
from core.bot import Veil
from core.utils import Context, Paginator, chunk_embeds
from discord.ext.commands.view import StringView


def test_permission_assignment_pages_show_every_saved_entry_and_clear_scope():
    async def run():
        config = {"users": {str(i): "helper" for i in range(100, 115)},
                  "roles": {"77": "admin", "78": "moderator"}}
        db = SimpleNamespace(get_config=AsyncMock(return_value=config), set_config_committed=AsyncMock())
        cog = Permissions(SimpleNamespace(db=db))
        ctx = SimpleNamespace(guild=SimpleNamespace(id=1), paginate=AsyncMock())
        await Permissions.up_list.callback(cog, ctx)
        pages = ctx.paginate.await_args.args[0]
        assert len(pages) == 3
        text = "\n".join(page.description for page in pages)
        assert text.count("Automatic access and fake permissions are not shown") == 3
        assert "<@&77> — **Admin** · role" in text and "<@&78> — **Moderator** · role" in text
        for i in range(100, 115):
            assert text.count(f"<@{i}>") == 1
        assert all(page.footer.text == "15 members · 2 roles" for page in pages)
        assert all(len(page.description) <= 4096 for page in pages)
        db.set_config_committed.assert_not_awaited()
    asyncio.run(run())


def test_permission_assignment_empty_and_role_only_states():
    async def run():
        config = {"users": {"20": "helper"}, "roles": {}}
        db = SimpleNamespace(get_config=AsyncMock(return_value=config))
        cog = Permissions(SimpleNamespace(db=db))
        ctx = SimpleNamespace(guild=SimpleNamespace(id=1), paginate=AsyncMock())
        await Permissions.up_roles.callback(cog, ctx)
        page = ctx.paginate.await_args.args[0][0]
        assert "No direct assignments yet" in page.description
        assert "<@20>" not in page.description
        assert page.footer.text == "0 members · 0 roles"
    asyncio.run(run())


def test_shared_numbered_lists_and_pagination_copy_and_update():
    async def run():
        pages = chunk_embeds("List", ["first", "second", "third"], per_page=2)
        assert pages[0].description == "`01` · first\n`02` · second"
        assert pages[1].description == "`03` · third"
        pages[0].set_footer(text="Existing", icon_url="https://example.com/icon.png")
        bot = Veil()
        message = MagicMock()
        message.to_reference.return_value = "ref"
        ctx = Context(message=message, bot=bot, view=StringView(""), prefix=",")
        ctx.send = AsyncMock(return_value=MagicMock())
        await ctx.paginate(pages)
        view = ctx.send.await_args.kwargs["view"]
        assert isinstance(view, Paginator)
        assert pages[0].footer.text == "Existing"  # input is reusable
        assert view.embeds[0].footer.text == "Existing · Page 1/2"
        assert view.embeds[0].footer.icon_url == "https://example.com/icon.png"
        assert view.page_indicator.disabled and view.page_indicator.label == "1 / 2"
        it = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()))
        await view._go(it, 1)
        assert view.page_indicator.label == "2 / 2"
        assert it.response.edit_message.await_args.kwargs["embed"].description == pages[1].description
        await view._go(it, 2)
        assert view.page_indicator.label == "1 / 2"  # navigation wraps as before
        view.stop()
    asyncio.run(run())


def test_paginator_still_restricts_interactions():
    async def run():
        view = Paginator(1, [discord.Embed(description="a"), discord.Embed(description="b")])
        it = SimpleNamespace(user=SimpleNamespace(id=2), client=SimpleNamespace(is_owner=AsyncMock(return_value=False)),
                             response=SimpleNamespace(send_message=AsyncMock()))
        assert not await view.interaction_check(it)
        assert it.response.send_message.await_args.kwargs["ephemeral"] is True
        it.user.id = 1
        assert await view.interaction_check(it)
        view.stop()
    asyncio.run(run())


def test_permission_commands_keep_their_authorization_checks():
    for command in (Permissions.up_list, Permissions.up_roles):
        assert "administrator" in command.callback.__veil_perms__
    assert any("has_perms" in check.__qualname__ for check in Permissions.up_list.checks)
    assert any("has_perms" in check.__qualname__ for check in Permissions.up_roles.checks)


def test_fake_permissions_list_keeps_all_roles_across_pages():
    from cogs.settings import Settings

    async def run():
        fake = {str(i): ["ban_members", "kick_members"] for i in range(100, 112)}
        db = SimpleNamespace(get_config=AsyncMock(return_value=fake), set_config=AsyncMock())
        cog = Settings.__new__(Settings)
        cog.bot = SimpleNamespace(db=db)
        ctx = SimpleNamespace(guild=SimpleNamespace(id=1), paginate=AsyncMock(), warn=AsyncMock(), clean_prefix=",")
        await Settings.fp_list.callback(cog, ctx)
        pages = ctx.paginate.await_args.args[0]
        assert len(pages) == 3
        content = "\n".join(page.description for page in pages)
        assert all(content.count(f"<@&{i}>") == 1 for i in range(100, 112))
        assert all("Bot-only" in page.description and page.footer.text == "12 roles" for page in pages)
        assert content.count("`ban members`") == 12
        db.set_config.assert_not_awaited()
    asyncio.run(run())
