"""Compact role-command replies: a green "added @Role to @Member" line, a red "failed …" line on problems."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord.ext import commands

from cogs.moderation import Moderation
from core.bot import Veil
from core.utils import Colors, Context, MissingRank, UserError, compact_failure, uses_compact_replies
from test_roleblacklist_enforcement import _mock_context, _mock_guild, _mock_member, _mock_role


@pytest.fixture
def bot():
    b = Veil()
    store: dict = {}

    async def get_config(gid, module, default=None):
        return store.get((gid, module), default if default is not None else {})

    async def set_config(gid, module, data):
        store[(gid, module)] = data

    b.db.get_config = AsyncMock(side_effect=get_config)
    b.db.set_config = AsyncMock(side_effect=set_config)
    return b


# ------------------------------------------------------------------ which commands are compact

def test_role_commands_use_compact_replies_and_others_dont():
    for cmd in (Moderation.role, Moderation.role_add, Moderation.role_remove, Moderation.role_create,
                Moderation.role_all, Moderation.cmd_addrole, Moderation.cmd_removerole):
        assert uses_compact_replies(SimpleNamespace(command=cmd)), cmd.qualified_name
    assert not uses_compact_replies(SimpleNamespace(command=Moderation.stripstaff))
    assert not uses_compact_replies(SimpleNamespace(command=None))


# ------------------------------------------------------------------ the reply itself

def test_compact_reply_is_a_bare_coloured_line_sent_as_a_reply():
    message = MagicMock()
    message.to_reference.return_value = "REF"
    ctx = SimpleNamespace(send=AsyncMock(), message=message)
    asyncio.run(Context.compact(ctx, Colors.success, "added <@&1> to <@2>"))

    kwargs = ctx.send.call_args.kwargs
    embed = kwargs["embed"]
    assert embed.description == "added <@&1> to <@2>"
    assert embed.colour.value == Colors.success
    assert embed.author.name is None and embed.footer.text is None and embed.title is None
    assert kwargs["reference"] == "REF" and kwargs["mention_author"] is False
    message.to_reference.assert_called_once_with(fail_if_not_exists=False)


# ------------------------------------------------------------------ failure wording

def ctx_for(action=None, role=None):
    return SimpleNamespace(compact_action=action, compact_role=role)


def test_failure_lines():
    role = SimpleNamespace(mention="<@&5>")
    cases = [
        (ctx_for(), MissingRank("Admin"), "failed: you need the **Admin** rank or higher (`uperm`)"),
        (ctx_for(), commands.BotMissingPermissions(["manage_roles"]), "failed: I'm missing **Manage Roles**"),
        (ctx_for(), commands.MissingPermissions(["manage_guild"]), "failed: you need **Manage Server**"),
        (ctx_for("add <@&5> to <@2>", role), UserError("<@&5> is above my top role"),
         "failed to add <@&5> to <@2>: <@&5> is above my top role"),
        (ctx_for("add <@&5> to <@2>", role), UserError("You can't modify roles on yourself"),
         "failed to add <@&5> to <@2>: you can't modify roles on yourself"),
        (ctx_for(), UserError("🔒 Access Denied: You are missing required bot permissions: `x`"),
         "failed: you are missing required bot permissions: `x`"),
        (ctx_for(), UserError("Member `bob` not found."), "failed: member `bob` not found"),
    ]
    for ctx, error, expected in cases:
        assert compact_failure(ctx, error) == expected


def test_forbidden_tells_you_to_move_the_bot_role():
    response = MagicMock(status=403, reason="Forbidden")
    error = discord.Forbidden(response, "Missing Permissions")
    text = compact_failure(ctx_for("add <@&5> to <@2>", SimpleNamespace(mention="<@&5>")), error)
    assert text.startswith("failed to add <@&5> to <@2>: ")
    assert "Move my role above <@&5>" in text


def test_unexpected_errors_fall_back_to_the_normal_handler():
    assert compact_failure(ctx_for(), RuntimeError("boom")) is None


# ------------------------------------------------------------------ wired into the error handler

def test_error_handler_sends_a_red_line_for_every_command(bot):
    async def go():
        for command in (Moderation.role, Moderation.stripstaff):
            ctx = MagicMock()
            ctx.command = command
            ctx.compact_action = None
            ctx.fail, ctx.warn, ctx.deny = AsyncMock(), AsyncMock(), AsyncMock()
            await bot.on_command_error(ctx, MissingRank("Admin"))
            ctx.fail.assert_awaited_once_with("failed: you need the **Admin** rank or higher (`uperm`)")
            ctx.warn.assert_not_awaited()
    asyncio.run(go())


def test_missing_discord_permission_keeps_rank_wording_outside_role_commands(bot):
    async def go():
        ctx = MagicMock()
        ctx.command = Moderation.stripstaff
        ctx.fail, ctx.deny = AsyncMock(), AsyncMock()
        await bot.on_command_error(ctx, commands.MissingPermissions(["ban_members"]))
        ctx.fail.assert_not_awaited()
        assert "the **Admin** level or higher" in ctx.deny.await_args.args[0]
    asyncio.run(go())


# ------------------------------------------------------------------ the commands

def test_r_toggles_with_compact_success_lines(bot):
    async def go():
        guild = _mock_guild(1)
        author = _mock_member(guild.owner_id)
        target = _mock_member(50)
        role = _mock_role(60, "Media", pos=10)
        cog = Moderation(bot)
        cog._resolve_member_role = AsyncMock(return_value=(target, role))

        ctx = _mock_context(bot, guild, author)
        await Moderation.role.callback(cog, ctx, "50", arg2="60")
        target.add_roles.assert_awaited_once()
        ctx.done.assert_awaited_once_with("added <@&60> to <@50>")

        target.roles.append(role)
        ctx = _mock_context(bot, guild, author)
        await Moderation.role.callback(cog, ctx, "50", arg2="60")
        target.remove_roles.assert_awaited_once()
        ctx.done.assert_awaited_once_with("removed <@&60> from <@50>")
    asyncio.run(go())


def test_role_above_bot_fails_with_context(bot):
    async def go():
        guild = _mock_guild(1)
        author = _mock_member(guild.owner_id)
        target = _mock_member(50)
        role = _mock_role(60, "TooHigh", pos=150)  # above the bot's top role (100)
        cog = Moderation(bot)
        cog._resolve_member_role = AsyncMock(return_value=(target, role))
        ctx = _mock_context(bot, guild, author)
        with pytest.raises(UserError) as info:
            await Moderation.role.callback(cog, ctx, "50", arg2="60")
        target.add_roles.assert_not_awaited()
        assert compact_failure(ctx, info.value) == "failed to add <@&60> to <@50>: <@&60> is above my top role"
    asyncio.run(go())


def test_mass_role_rejects_anything_but_add_or_remove(bot):
    async def go():
        guild = _mock_guild(1)
        member = _mock_member(50)
        role = _mock_role(60, "Everyone", pos=10)
        member.roles.append(role)
        guild.members = [member]
        cog = Moderation(bot)
        ctx = _mock_context(bot, guild, _mock_member(guild.owner_id))
        ctx.command = Moderation.role_all
        with pytest.raises(UserError, match="use `add` or `remove`"):
            await cog._mass_role(ctx, "foo", role, lambda m: True, "everyone")
        member.remove_roles.assert_not_awaited()
    asyncio.run(go())


# ------------------------------------------------------------------ with the bot's real Context class
# The mocks above stand in for Context; these use the real class, so a missing helper (like the AttributeError
# seen after a half-reloaded update) fails here instead of in Discord.

def real_ctx(bot, guild, author, command):
    from discord.ext.commands.view import StringView
    message = MagicMock()
    message.guild, message.author = guild, author
    message.to_reference.return_value = "REF"
    ctx = Context(message=message, bot=bot, view=StringView(""), prefix=",", command=command)
    ctx.send = AsyncMock()  # stop at the Discord API boundary
    return ctx


def sent_embed(ctx):
    return ctx.send.await_args.kwargs["embed"]


def test_real_context_has_the_compact_helpers():
    for name in ("compact", "done", "fail"):
        assert callable(getattr(Context, name, None)), name


def test_r_end_to_end_with_real_context(bot):
    async def go():
        guild = _mock_guild(1)
        author = _mock_member(guild.owner_id)
        target = _mock_member(50)
        role = _mock_role(60, "Media", pos=10)
        cog = Moderation(bot)
        cog._resolve_member_role = AsyncMock(return_value=(target, role))
        ctx = real_ctx(bot, guild, author, Moderation.role)
        await Moderation.role.callback(cog, ctx, "50", arg2="60")
        embed = sent_embed(ctx)
        assert embed.description == "✅ <@11111>: added <@&60> to <@50>"
        assert embed.colour.value == Colors.approve
        assert embed.author.name is None and embed.footer.text is None
    asyncio.run(go())


def test_failure_end_to_end_with_real_context(bot):
    async def go():
        guild = _mock_guild(1)
        ctx = real_ctx(bot, guild, _mock_member(77), Moderation.role)
        await bot.on_command_error(ctx, MissingRank("Admin"))
        embed = sent_embed(ctx)
        assert embed.description == "❌ <@77>: you need the **Admin** rank or higher (`uperm`)"
        assert embed.colour.value == Colors.deny
    asyncio.run(go())


def test_giving_a_blacklisted_role_says_the_member_was_authorized(bot):
    async def go():
        guild = _mock_guild(1)
        author = _mock_member(guild.owner_id)
        target = _mock_member(50)
        role = _mock_role(60, "Bled", pos=10)
        await bot.db.set_config(1, "roleblacklist", {"roles": [60], "authorized": []})
        cog = Moderation(bot)
        cog._resolve_member_role = AsyncMock(return_value=(target, role))
        ctx = real_ctx(bot, guild, author, Moderation.role)
        await Moderation.role.callback(cog, ctx, "50", arg2="60")
        assert sent_embed(ctx).description == (
            "✅ <@11111>: added <@&60> to <@50> · they're now authorized to hold this blacklisted role")
    asyncio.run(go())


def test_role_commands_show_member_role_usage():
    for cmd in (Moderation.role, Moderation.role_add, Moderation.role_remove,
                Moderation.cmd_addrole, Moderation.cmd_removerole):
        assert cmd.signature == "<member> <role>", cmd.qualified_name
