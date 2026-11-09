"""Role restrictions are additive, inherited, durable, and reflected in prefix help."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
import pytest
from discord.ext import commands

from cogs.settings import Settings
from core.bot import Veil
from core.command_access import roles_allow_command
from core.db import Database
from core.help import Help, HelpView
from core.utils import UserError, has_perms


@commands.group()
async def tools(ctx):
    """Tools"""


@tools.command()
@has_perms(manage_messages=True)
async def clean(ctx):
    """Clean"""


def test_restrictions_are_additional_and_parent_and_child_rules_both_apply():
    assert roles_allow_command(clean, set(), {})
    assert not roles_allow_command(clean, {"1"}, {"tools": ["2"]})
    assert roles_allow_command(clean, {"2"}, {"tools": ["2"]})
    rules = {"tools": ["2", "3"], "tools clean": ["4"]}
    assert not roles_allow_command(clean, {"2"}, rules)
    assert not roles_allow_command(clean, {"4"}, rules)
    assert roles_allow_command(clean, {"2", "4"}, rules)
    for invalid in (None, [], "invalid", {"tools": []}, {"tools": ["bad", "2"]}):
        assert not roles_allow_command(clean, {"2"}, invalid)


def test_global_check_denies_missing_roles_and_does_not_grant_permissions():
    async def go():
        bot = Veil()
        bot.is_owner = AsyncMock(return_value=False)
        bot.is_staff_member = AsyncMock(return_value=True)
        bot.is_authorized = lambda guild: True
        rules = {"tools": ["2"], "tools clean": ["4"]}
        store = {"settings": {}, "command_restrictions": rules,
                 "permlevels": {"users": {"500": "helper"}, "overrides": {"helper": {"perms": []}}},
                 "fakeperms": {}}
        bot.db = SimpleNamespace(get_config=AsyncMock(side_effect=lambda gid, module: store.get(module, {})))
        author = SimpleNamespace(id=500, roles=[SimpleNamespace(id=2)], guild_permissions=discord.Permissions.none())
        guild = SimpleNamespace(id=42, owner_id=7)
        channel = SimpleNamespace(id=3, permissions_for=lambda member: discord.Permissions.none())
        ctx = SimpleNamespace(command=clean, author=author, guild=guild, bot=bot, channel=channel, cog=clean.cog)
        with pytest.raises(UserError, match="restricted to specific roles"):
            await bot._global_check(ctx)
        author.roles.append(SimpleNamespace(id=4))
        assert await bot._global_check(ctx)
        # Passing a role restriction never bypasses the existing permission check.
        with pytest.raises(UserError):
            await clean.checks[0](ctx)
        author.roles.clear()
        author.guild_permissions = discord.Permissions(administrator=True)
        with pytest.raises(UserError, match="restricted to specific roles"):
            await bot._global_check(ctx)
        guild.owner_id = author.id
        assert await bot._global_check(ctx)
        guild.owner_id = 7
        bot.is_owner = AsyncMock(return_value=True)
        assert await bot._global_check(ctx)
        bot.is_owner = AsyncMock(return_value=False)
        store["command_restrictions"] = {"tools": []}
        with pytest.raises(UserError, match="restricted to specific roles"):
            await bot._global_check(ctx)
        store["command_restrictions"] = {}
        assert await bot._global_check(ctx)

    asyncio.run(go())


def test_help_hides_restricted_commands_for_every_level_but_not_owner():
    async def go():
        h = Help()
        role = SimpleNamespace(id=2, name="Staff", mention="<@&2>")
        author = SimpleNamespace(id=500, roles=[role], guild_permissions=discord.Permissions.none())
        guild = SimpleNamespace(id=42, owner_id=7)
        store = {"settings": {}, "command_restrictions": {"tools clean": ["4"]},
                 "permlevels": {"users": {"500": "helper"}}, "fakeperms": {}}
        bot = SimpleNamespace(user=None, db=SimpleNamespace(get_config=AsyncMock(side_effect=lambda gid, key: store[key])),
                              is_owner=AsyncMock(return_value=False), walk_commands=lambda: [tools, clean])
        h.context = SimpleNamespace(bot=bot, author=author, guild=guild, clean_prefix=",",
                                    channel=SimpleNamespace(permissions_for=lambda user: discord.Permissions.none()),
                                    send=AsyncMock())
        for key, rank in (("helper", 1), ("moderator", 2), ("admin", 3), ("headadmin", 4),
                          ("overseer", 6), ("supremeleader", 7)):
            store["permlevels"] = {"users": {"500": key}}
            access = await h.help_access(rank)
            view = HelpView(h, {"Utility": [tools]}, start="Utility", author_rank=rank, access=access)
            it = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock(), send_message=AsyncMock()))
            await view.show_command(it, "tools")
            assert view.current_subcommands() == []
            assert not await h._can_see(clean)
            assert "tools clean" not in await h._suggest("tools clea")
            await h.send_group_help(tools)
            assert "tools clean" not in h.context.send.await_args.kwargs["embed"].description
        author.roles.append(SimpleNamespace(id=4, name="Extra", mention="<@&4>"))
        assert await h._can_see(clean)
        guild.owner_id = author.id
        author.roles.clear()
        assert await h._can_see(clean)

    asyncio.run(go())


def test_restriction_management_persists_and_is_owner_only(tmp_path):
    async def go():
        db = Database(str(tmp_path / "commands.db"))
        await db.connect()
        bot = SimpleNamespace(db=db, get_command=lambda name: {"tools": tools, "tools clean": clean,
                              "clear": clean}.get(name), is_owner=AsyncMock(return_value=False), owner_ids=set())
        cog = Settings(bot)
        guild = SimpleNamespace(id=42, owner_id=7)
        role = SimpleNamespace(id=2, mention="<@&2>", is_default=lambda: False)
        ctx = SimpleNamespace(bot=bot, guild=guild, author=SimpleNamespace(id=7), clean_prefix=",",
                              approve=AsyncMock(), warn=AsyncMock(), paginate=AsyncMock())
        try:
            # The command decorator independently protects every management subcommand.
            for command in (cog.restrict, cog.restrict_add, cog.restrict_remove, cog.restrict_clear, cog.restrict_list):
                ctx.author.id = 8
                with pytest.raises(UserError):
                    await command.checks[0](ctx)
                ctx.author.id = 7
                assert await command.checks[0](ctx)
            with patch.object(commands.RoleConverter, "convert", AsyncMock(return_value=role)):
                await cog.restrict_add.callback(cog, ctx, target="clear, @Staff")
                assert await db.get_config(42, "command_restrictions") == {"tools clean": ["2"]}
                with pytest.raises(UserError, match="already allowed"):
                    await cog.restrict_add.callback(cog, ctx, target="tools clean, @Staff")
            await cog.restrict_list.callback(cog, ctx)
            ctx.paginate.assert_awaited_once()
            db._cache.clear()
            assert await db.get_config(42, "command_restrictions") == {"tools clean": ["2"]}
            # A deleted role can still be removed by clearing its command rule.
            await cog.restrict_clear.callback(cog, ctx, command="tools clean")
            db._cache.clear()
            assert await db.get_config(42, "command_restrictions") == {}
        finally:
            await db.close()

    asyncio.run(go())


def test_role_restriction_management_validation_and_removal(tmp_path):
    async def go():
        db = Database(str(tmp_path / "restrictions.db"))
        await db.connect()
        bot = SimpleNamespace(db=db, get_command=lambda name: {"tools": tools, "tools clean": clean}.get(name),
                              is_owner=AsyncMock(return_value=False), owner_ids=set())
        cog = Settings(bot)
        ctx = SimpleNamespace(bot=bot, guild=SimpleNamespace(id=42, owner_id=7), author=SimpleNamespace(id=7),
                              clean_prefix=",", approve=AsyncMock())
        first = SimpleNamespace(id=2, mention="<@&2>", is_default=lambda: False)
        second = SimpleNamespace(id=3, mention="<@&3>", is_default=lambda: False)
        try:
            with pytest.raises(UserError, match="Use"):
                await cog.restrict_add.callback(cog, ctx, target="tools @Staff")
            with patch.object(commands.RoleConverter, "convert", AsyncMock(return_value=first)):
                with pytest.raises(UserError, match="not found"):
                    await cog.restrict_add.callback(cog, ctx, target="unknown, @Staff")
                await cog.restrict_add.callback(cog, ctx, target="tools, @Staff")
            with patch.object(commands.RoleConverter, "convert", AsyncMock(return_value=second)):
                await cog.restrict_add.callback(cog, ctx, target="tools, @Other")
                await cog.restrict_remove.callback(cog, ctx, target="tools, @Other")
                assert await db.get_config(42, "command_restrictions") == {"tools": ["2"]}
            with patch.object(commands.RoleConverter, "convert", AsyncMock(return_value=first)):
                await cog.restrict_remove.callback(cog, ctx, target="tools, @Staff")
                assert await db.get_config(42, "command_restrictions") == {}
                assert "no role restriction remains" in ctx.approve.await_args.args[0]
            everyone = SimpleNamespace(id=42, mention="@everyone", is_default=lambda: True)
            with patch.object(commands.RoleConverter, "convert", AsyncMock(return_value=everyone)):
                with pytest.raises(UserError, match="specific role"):
                    await cog.restrict_add.callback(cog, ctx, target="tools, @everyone")
            await db.set_config(42, "command_restrictions", {"tools": ["invalid"]})
            with patch.object(commands.RoleConverter, "convert", AsyncMock(return_value=first)):
                with pytest.raises(UserError, match="invalid"):
                    await cog.restrict_add.callback(cog, ctx, target="tools, @Staff")
            await cog.restrict_clear.callback(cog, ctx, command="tools")
            assert await db.get_config(42, "command_restrictions") == {}
        finally:
            await db.close()

    asyncio.run(go())


def test_no_categories_does_not_construct_an_invalid_empty_select():
    h = Help()
    h.context = SimpleNamespace(author=SimpleNamespace(id=1), bot=SimpleNamespace(user=None), clean_prefix=",")
    view = HelpView(h, {})
    assert not view.children


def test_failed_or_pending_clear_does_not_unlock_cached_restrictions(tmp_path):
    async def go():
        db = Database(str(tmp_path / "durable.db"))
        await db.connect()
        bot = SimpleNamespace(db=db, get_command=lambda name: {"tools": tools}.get(name))
        cog = Settings(bot)
        ctx = SimpleNamespace(guild=SimpleNamespace(id=42), approve=AsyncMock())
        original = {"tools": ["2"]}
        try:
            await db.set_config(42, "command_restrictions", original)
            started, release = asyncio.Event(), asyncio.Event()
            real_execute = db.execute

            async def slow_execute(*args):
                started.set()
                await release.wait()
                return await real_execute(*args)

            with patch.object(db, "execute", side_effect=slow_execute):
                task = asyncio.create_task(cog.restrict_clear.callback(cog, ctx, command="tools"))
                await started.wait()
                assert await db.get_config(42, "command_restrictions") == original
                release.set()
                await task
            assert await db.get_config(42, "command_restrictions") == {}
            await db.set_config(42, "command_restrictions", original)
            with patch.object(db, "execute", AsyncMock(side_effect=RuntimeError("write failed"))):
                with pytest.raises(RuntimeError, match="write failed"):
                    await cog.restrict_clear.callback(cog, ctx, command="tools")
            assert await db.get_config(42, "command_restrictions") == original
            db._cache.clear()
            assert await db.get_config(42, "command_restrictions") == original
        finally:
            await db.close()

    asyncio.run(go())


def test_concurrent_role_additions_do_not_lose_either_rule(tmp_path):
    async def go():
        db = Database(str(tmp_path / "concurrent.db"))
        await db.connect()
        bot = SimpleNamespace(db=db, get_command=lambda name: {"tools": tools}.get(name))
        cog = Settings(bot)
        ctx = SimpleNamespace(guild=SimpleNamespace(id=42), clean_prefix=",", approve=AsyncMock())
        roles = {"@One": SimpleNamespace(id=2, mention="<@&2>", is_default=lambda: False),
                 "@Two": SimpleNamespace(id=3, mention="<@&3>", is_default=lambda: False)}
        try:
            with patch.object(commands.RoleConverter, "convert", AsyncMock(side_effect=lambda ctx, name: roles[name])):
                await asyncio.gather(cog.restrict_add.callback(cog, ctx, target="tools, @One"),
                                     cog.restrict_add.callback(cog, ctx, target="tools, @Two"))
            db._cache.clear()
            assert set((await db.get_config(42, "command_restrictions"))["tools"]) == {"2", "3"}
        finally:
            await db.close()

    asyncio.run(go())


def test_staff_panel_checks_command_role_restrictions_too():
    from core.staffpanel import can_use

    async def go():
        bot = Veil()
        bot.is_owner = AsyncMock(return_value=False)
        bot.is_staff_member = AsyncMock(return_value=True)
        bot.is_authorized = lambda guild: True
        bot.get_command = lambda name: clean if name == "tools clean" else None
        store = {"settings": {}, "command_restrictions": {"tools": ["2"]},
                 "permlevels": {"users": {"500": "moderator"}}, "fakeperms": {}}
        bot.db = SimpleNamespace(get_config=AsyncMock(side_effect=lambda gid, key: store[key]))
        bot.add_check(bot._global_check)
        author = SimpleNamespace(id=500, roles=[], guild_permissions=discord.Permissions.none())
        ctx = SimpleNamespace(bot=bot, guild=SimpleNamespace(id=42, owner_id=7), author=author,
                              channel=SimpleNamespace(id=3, permissions_for=lambda user: discord.Permissions.none()),
                              command=tools, cog=None)
        assert not await can_use(ctx, "tools clean")
        author.roles.append(SimpleNamespace(id=2))
        assert await can_use(ctx, "tools clean")
        store["permlevels"]["overrides"] = {"moderator": {"perms": []}}
        assert not await can_use(ctx, "tools clean")

    asyncio.run(go())
