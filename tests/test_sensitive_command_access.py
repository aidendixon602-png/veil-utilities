"""Sensitive commands and role-blacklist controls require a real owner or developer."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest
from discord.ext import commands

from cogs.devtools import DevTools
from cogs.logging import Logging
from cogs.moderation import GuildBledView, Moderation, SingleMemberBledView
from core.utils import UserError, owner_or_developer


def test_owner_checks_cover_all_listed_commands_and_equivalent_routes():
    async def run():
        ctx = SimpleNamespace(guild=SimpleNamespace(id=1, owner_id=10),
                              author=SimpleNamespace(id=20, roles=[SimpleNamespace(id=55)],
                                                     guild_permissions=discord.Permissions.all()),
                              bot=SimpleNamespace(owner_ids={30}, owner_id=None,
                                                  db=SimpleNamespace(get_config=AsyncMock(return_value={"55": ["administrator"]}))))
        sensitive = [Moderation.security, Moderation.masskick, Moderation.massban, Moderation.nuke,
                     Moderation.stripstaff, Moderation.roleblacklist, Moderation.cmd_checkbled,
                     Moderation.cmd_stripbled, Moderation.cmd_rblauth, Moderation.cmd_rblunauth,
                     Logging.logs, Logging.cmd_setuplogs, Logging.cmd_cleanuplogs]
        sensitive += list(Moderation.roleblacklist.walk_commands()) + list(Logging.logs.walk_commands())
        assert len(sensitive) > 25
        for command in sensitive:
            checks = [check for check in command.checks if getattr(check, "__veil_owner_only__", False)]
            assert checks, command.qualified_name
            for check in checks:
                for uid in (20, 40):
                    ctx.author.id = uid  # native admin/fake admin and assignable Veil Owner are not server owners
                    with pytest.raises(UserError):
                        await check(ctx)
                ctx.author.id = 10
                assert await check(ctx)
                ctx.author.id = 30
                assert await check(ctx)
        # Aliases resolve to the same checked commands, including dangerous standalone aliases.
        assert "kickrecent" in Moderation.masskick.aliases
        assert "banrecent" in Moderation.massban.aliases
        assert "rbl" in Moderation.roleblacklist.aliases
        assert "rblbypass" in Moderation.cmd_rblauth.aliases
        assert "purgeduplicatelogs" in Logging.cmd_cleanuplogs.aliases
    asyncio.run(run())


def test_owner_check_fails_closed_for_missing_identity_and_non_guild_context():
    assert not owner_or_developer(SimpleNamespace(), SimpleNamespace(), None)
    assert not owner_or_developer(SimpleNamespace(id=10), None, None)
    assert not owner_or_developer(None, SimpleNamespace(owner_id=10), None)


def test_role_blacklist_action_buttons_check_actual_user_and_guild():
    async def run():
        guild = SimpleNamespace(id=1, owner_id=10)
        ctx = SimpleNamespace(guild=guild)
        cog = SimpleNamespace(bot=SimpleNamespace(owner_ids={30}))
        views = [SingleMemberBledView(cog, ctx, SimpleNamespace(id=55), []), GuildBledView(cog, ctx, [])]
        client = SimpleNamespace(owner_ids={30}, owner_id=None)
        for view in views:
            it = SimpleNamespace(guild=guild, user=SimpleNamespace(id=20, guild_permissions=discord.Permissions.all()),
                                 client=client, response=SimpleNamespace(send_message=AsyncMock()))
            assert not await view.interaction_check(it)  # native Administrator / Manage Roles do not suffice
            assert it.response.send_message.await_args.kwargs["ephemeral"]
            it.user.id = 10
            assert await view.interaction_check(it)
            it.user.id = 30
            assert await view.interaction_check(it)
            it.guild = SimpleNamespace(id=2, owner_id=30)
            assert not await view.interaction_check(it)
            view.stop()
    asyncio.run(run())


def test_strip_remains_developer_only_and_is_not_expanded_to_server_owners():
    async def run():
        bot = SimpleNamespace(is_owner=AsyncMock(return_value=False))
        cog = DevTools(bot)
        ctx = SimpleNamespace(author=SimpleNamespace(id=10), guild=SimpleNamespace(owner_id=10))
        assert DevTools.strip.hidden
        with pytest.raises(commands.NotOwner):
            await cog.cog_check(ctx)
        bot.is_owner.return_value = True
        assert await cog.cog_check(ctx)
    asyncio.run(run())
