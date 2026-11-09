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


def test_rbl_help_denies_fake_owner_rank_and_is_one_private_card():
    from cogs.moderation import Moderation
    from core.help import Help, HelpAccess, visible_command

    async def go():
        bot = SimpleNamespace(user=None, owner_ids=set(), owner_id=None, is_owner=AsyncMock(return_value=False))
        guild = SimpleNamespace(id=1, owner_id=10)
        author = SimpleNamespace(id=20)
        access = HelpAccess({}, 999, {"administrator"}, discord.Permissions.all(), True, set(), False,
                            False, True, True, set(), {})
        assert not visible_command(Moderation.roleblacklist, bot, False, 999, access)
        h = Help()
        h.context = SimpleNamespace(author=author, guild=guild, bot=bot, clean_prefix=",", send=AsyncMock(), warn=AsyncMock())
        assert not await h._can_see(Moderation.roleblacklist)
        author.id = 10
        await h.send_group_help(Moderation.roleblacklist)
        sent = h.context.send.await_args.kwargs
        assert "view" not in sent and h.context._private_rbl
        text = sent["embed"].description
        assert ",rbl add" in text and ",hardbl" in text and ",bypass" in text
    asyncio.run(go())


def test_rbl_context_outputs_are_direct_messages_without_channel_fallback():
    from core.utils import Context
    async def go():
        author = SimpleNamespace(send=AsyncMock())
        channel = SimpleNamespace(send=AsyncMock())
        ctx = SimpleNamespace(_private_rbl=True, author=author, channel=channel)
        await Context.send(ctx, "secret", reference="ref", mention_author=False)
        author.send.assert_awaited_once_with("secret")
        channel.send.assert_not_called()
    asyncio.run(go())


def test_dm_rbl_buttons_recheck_live_server_owner():
    async def go():
        guild = SimpleNamespace(id=1, owner_id=10)
        bot = SimpleNamespace(owner_ids=set(), owner_id=None, get_guild=lambda gid: guild if gid == 1 else None)
        ctx = SimpleNamespace(guild=guild)
        cog = SimpleNamespace(bot=bot)
        view = SingleMemberBledView(cog, ctx, SimpleNamespace(id=55), [])
        it = SimpleNamespace(guild=None, user=SimpleNamespace(id=10), client=bot,
                             response=SimpleNamespace(send_message=AsyncMock()))
        assert await view.interaction_check(it)
        guild.owner_id = 20
        assert not await view.interaction_check(it)
        view.stop()
    asyncio.run(go())


def test_every_rbl_entry_point_sets_private_delivery_and_requires_owner():
    async def run():
        bot = SimpleNamespace(owner_ids={30}, owner_id=None)
        cog = Moderation(bot)
        roots = [Moderation.roleblacklist, Moderation.bypass, Moderation.hardbl,
                 Moderation.cmd_checkbled, Moderation.cmd_stripbled,
                 Moderation.cmd_rblauth, Moderation.cmd_rblunauth]
        commands = roots + [sub for root in roots if hasattr(root, "walk_commands") for sub in root.walk_commands()]
        for command in commands:
            ctx = SimpleNamespace(command=command, guild=SimpleNamespace(id=1, owner_id=10),
                                  author=SimpleNamespace(id=20), bot=bot)
            await cog.cog_before_invoke(ctx)
            assert ctx._private_rbl, command.qualified_name
            checks = [check for check in command.checks if getattr(check, "__veil_owner_only__", False)]
            assert checks, command.qualified_name
            for check in checks:
                with pytest.raises(UserError):
                    await check(ctx)
                ctx.author.id = 10
                assert await check(ctx)
                ctx.author.id = 20
    asyncio.run(run())
