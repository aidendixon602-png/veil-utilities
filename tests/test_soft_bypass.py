"""Separate soft-role bypasses must not turn into antinuke trust."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from cogs.moderation import Moderation
from core.utils import Colors, UserError, check_role_assignable, has_soft_role_bypass
from test_roleblacklist_enforcement import _mock_context, _mock_guild, _mock_member, _mock_role, bot as role_bot  # noqa: F401


def test_add_remove_soft_bypass_preserves_legacy_config_and_failure_is_closed(role_bot):  # noqa: F811
    async def run():
        bot = role_bot
        guild = _mock_guild(123)
        owner = _mock_member(guild.owner_id)
        member = _mock_member(555)
        ctx = _mock_context(bot, guild, owner)
        cog = Moderation(bot)
        original = {"roles": [888], "authorized": [777], "other": {"keep": True}}
        bot._storage[(123, "roleblacklist")] = deepcopy(original)

        async def committed(gid, module, cfg):
            bot._storage[(gid, module)] = cfg

        bot.db.set_config_committed = AsyncMock(side_effect=committed)
        await Moderation.bypass_add.callback(cog, ctx, target=member)
        cfg = bot._storage[(123, "roleblacklist")]
        assert cfg["soft_bypass"] == {"users": [555]}
        assert cfg["authorized"] == [777] and cfg["other"] == {"keep": True}
        assert cog._is_role_authorized(guild, member, cfg)
        assert not cog._is_role_authorized(guild, member, cfg, include_soft=False)
        with pytest.raises(UserError, match="already has"):
            await Moderation.bypass_add.callback(cog, ctx, target=member)
        await Moderation.bypass_remove.callback(cog, ctx, target=member)
        assert not cog._is_role_authorized(guild, member, bot._storage[(123, "roleblacklist")])

        bot.db.set_config_committed.side_effect = RuntimeError("database unavailable")
        before = deepcopy(bot._storage[(123, "roleblacklist")])
        with pytest.raises(RuntimeError):
            await Moderation.bypass_add.callback(cog, ctx, target=member)
        assert bot._storage[(123, "roleblacklist")] == before

    asyncio.run(run())


def test_soft_bypass_roles_are_separate_and_do_not_create_legacy_authorizations(role_bot):  # noqa: F811
    async def run():
        bot = role_bot
        guild = _mock_guild(123)
        soft_role = _mock_role(600, "soft", pos=80)
        author = _mock_member(555, [soft_role])
        target = _mock_member(556)
        protected = _mock_role(888, "protected")
        cfg = {"roles": [888], "authorized": [], "soft_bypass": {"roles": [600], "users": [556]}}
        bot._storage[(123, "roleblacklist")] = cfg
        ctx = _mock_context(bot, guild, author)
        cog = Moderation(bot)
        assert has_soft_role_bypass(author, cfg)
        await check_role_assignable(ctx, protected)
        assert await cog._authorize_bled_target(ctx, target, protected) is False
        assert cfg["authorized"] == []
        stranger = _mock_member(557)
        with pytest.raises(UserError, match="already has a bypass"):
            await cog._authorize_bled_target(ctx, stranger, protected)
        assert cfg["authorized"] == []
        with pytest.raises(UserError, match="Cannot mass-assign"):
            await cog._mass_role(ctx, "add", protected, lambda m: True, "everyone")

    asyncio.run(run())


def test_bypass_commands_are_server_owner_or_developer_only():
    async def run():
        ctx = SimpleNamespace(guild=SimpleNamespace(id=123, owner_id=1), author=SimpleNamespace(id=2),
                              bot=SimpleNamespace(owner_ids={3}, owner_id=None))
        for command in (Moderation.bypass, *Moderation.bypass.walk_commands()):
            check = next(c for c in command.checks if getattr(c, "__veil_owner_only__", False))
            with pytest.raises(UserError):
                await check(ctx)
            ctx.author.id = 1
            assert await check(ctx)
            ctx.author.id = 3
            assert await check(ctx)
            ctx.author.id = 2
    asyncio.run(run())


def test_hard_blacklist_beats_owner_and_soft_bypass_but_allows_developers(role_bot):  # noqa: F811
    async def run():
        from core import perms
        bot = role_bot
        guild = _mock_guild(123)
        target_role = _mock_role(888, "protected")
        author = _mock_member(guild.owner_id)
        ctx = _mock_context(bot, guild, author)
        cfg = {"roles": [], "hard_roles": [888], "soft_bypass": {"users": [author.id]}, "authorized": [author.id]}
        bot._storage[(123, "roleblacklist")] = cfg
        with pytest.raises(UserError, match="only a bot developer"):
            await check_role_assignable(ctx, target_role)
        bot.owner_ids = {author.id}
        assert perms.is_bot_developer(author, bot)
        await check_role_assignable(ctx, target_role)

    asyncio.run(run())


def test_hard_blacklist_management_is_owner_or_developer_and_preserves_soft(role_bot):  # noqa: F811
    async def run():
        bot = role_bot
        guild = _mock_guild(123)
        ctx = _mock_context(bot, guild, _mock_member(guild.owner_id))
        cog = Moderation(bot)
        role = _mock_role(888, "protected")
        bot._storage[(123, "roleblacklist")] = {"roles": [999], "authorized": [77], "soft_bypass": {"users": [78]}}

        async def committed(gid, module, cfg):
            bot._storage[(gid, module)] = cfg

        bot.db.set_config_committed = AsyncMock(side_effect=committed)
        await Moderation.hardbl_add.callback(cog, ctx, role=role)
        cfg = bot._storage[(123, "roleblacklist")]
        assert cfg == {"roles": [999], "authorized": [77], "soft_bypass": {"users": [78]}, "hard_roles": [888]}
        with pytest.raises(UserError, match="already hard-blacklisted"):
            await Moderation.hardbl_add.callback(cog, ctx, role=role)
        with pytest.raises(UserError, match="Remove this role from hardbl"):
            await Moderation.rbl_add.callback(cog, ctx, role=role)
        bot.db.set_config_committed.side_effect = RuntimeError("database unavailable")
        before = deepcopy(cfg)
        with pytest.raises(RuntimeError):
            await Moderation.hardbl_remove.callback(cog, ctx, role=role)
        assert bot._storage[(123, "roleblacklist")] == before
        bot.db.set_config_committed.side_effect = committed
        await Moderation.hardbl_remove.callback(cog, ctx, role=role)
        assert bot._storage[(123, "roleblacklist")]["hard_roles"] == []
        soft = _mock_role(999, "soft")
        with pytest.raises(UserError, match="ordinary role blacklist"):
            await Moderation.hardbl_add.callback(cog, ctx, role=soft)

        actor = SimpleNamespace(guild=SimpleNamespace(id=123, owner_id=1), author=SimpleNamespace(id=2),
                                bot=SimpleNamespace(owner_ids={3}, owner_id=None))
        for command in (Moderation.hardbl, *Moderation.hardbl.walk_commands()):
            check = next(c for c in command.checks if getattr(c, "__veil_owner_only__", False))
            with pytest.raises(UserError):
                await check(actor)
            actor.author.id = 1
            assert await check(actor)
            actor.author.id = 3
            assert await check(actor)
            actor.author.id = 2

    asyncio.run(run())


def test_hard_blacklisted_roles_cannot_be_used_in_automatic_role_setup(role_bot):  # noqa: F811
    async def run():
        from cogs.config import Config
        bot = role_bot
        guild = _mock_guild(123)
        ctx = _mock_context(bot, guild, _mock_member(guild.owner_id))
        role = _mock_role(888, "protected")
        bot._storage[(123, "roleblacklist")] = {"roles": [], "hard_roles": [888]}
        with pytest.raises(UserError, match="blacklisted"):
            await Config(bot)._ar(ctx, "humans", role)

    asyncio.run(run())


def test_soft_bypass_is_not_antinuke_trust(role_bot):  # noqa: F811
    async def run():
        from cogs.antinuke import Antinuke
        bot = role_bot
        bot._connection.user = SimpleNamespace(id=99999)
        guild = _mock_guild(123)
        actor = _mock_member(555, [_mock_role(600)])
        guild.get_member.return_value = actor
        bot._storage[(123, "roleblacklist")] = {"authorized": [], "soft_bypass": {"users": [555], "roles": [600]}}
        assert not await Antinuke(bot)._trusted(guild, {}, actor.id)
    asyncio.run(run())


def test_hard_blacklist_blocks_existing_self_role_button(role_bot):  # noqa: F811
    async def run():
        from cogs.config import RoleButton
        bot = role_bot
        guild = _mock_guild(123)
        role = _mock_role(888, "protected")
        user = _mock_member(555)
        guild.get_role.return_value = role
        bot._storage[(123, "roleblacklist")] = {"hard_roles": [888]}
        interaction = SimpleNamespace(guild=guild, client=bot, user=user,
                                      response=SimpleNamespace(send_message=AsyncMock()))
        await RoleButton(888, "protected").callback(interaction)
        user.add_roles.assert_not_awaited()
        assert "blacklisted" in interaction.response.send_message.await_args.args[0]
    asyncio.run(run())


def test_hard_blacklist_blocks_antinuke_role_restoration(role_bot):  # noqa: F811
    async def run():
        from cogs.antinuke import Antinuke
        bot = role_bot
        guild = _mock_guild(123)
        role = _mock_role(888, "protected")
        guild.get_role.return_value = role
        actor = _mock_member(555)
        bot._storage[(123, "roleblacklist")] = {"hard_roles": [888]}
        entry = SimpleNamespace(before=SimpleNamespace(roles=[role]), after=SimpleNamespace(roles=[]))
        lines = await Antinuke(bot)._revert_member_roles(guild, actor, entry, "test")
        actor.add_roles.assert_not_awaited()
        assert any("didn't give" in line for line in lines)
    asyncio.run(run())


def test_soft_bypass_does_not_promote_a_globally_trusted_actor_to_legacy_grant(role_bot, monkeypatch):  # noqa: F811
    async def run():
        from core import perms
        bot = role_bot
        guild = _mock_guild(123)
        author = _mock_member(555)
        target = _mock_member(556)
        protected = _mock_role(888)
        cfg = {"roles": [888], "authorized": [], "soft_bypass": {"users": [555]}}
        bot._storage[(123, "roleblacklist")] = cfg
        monkeypatch.setattr(perms, "WHITELISTED_PEOPLE", {555: "existing trusted account"})
        ctx = _mock_context(bot, guild, author)
        cog = Moderation(bot)
        with pytest.raises(UserError, match="already has a bypass"):
            await cog._authorize_bled_target(ctx, target, protected)
        with pytest.raises(UserError, match="Cannot mass-assign"):
            await cog._mass_role(ctx, "add", protected, lambda m: True, "everyone")
        assert cfg["authorized"] == []
    asyncio.run(run())


def test_legacy_authorized_staff_cannot_silently_authorize_a_new_recipient(role_bot):  # noqa: F811
    bot = role_bot
    async def run():
        from test_roleblacklist_enforcement import _mock_context, _mock_guild, _mock_member, _mock_role
        guild = _mock_guild(123)
        staff, target = _mock_member(555), _mock_member(556)
        cfg = {"roles": [888], "authorized": [555]}
        bot._storage[(123, "roleblacklist")] = cfg
        ctx = _mock_context(bot, guild, staff)
        with pytest.raises(UserError, match="server owner.*bot developer"):
            await Moderation(bot)._authorize_bled_target(ctx, target, _mock_role(888, "protected"))
        assert cfg["authorized"] == [555]
    asyncio.run(run())


def test_rbl_write_failure_does_not_publish_cached_changes(role_bot):  # noqa: F811
    async def run():
        bot = role_bot
        guild = _mock_guild(123)
        owner = _mock_member(guild.owner_id)
        ctx = _mock_context(bot, guild, owner)
        original = {"roles": [888], "authorized": []}
        bot._storage[(123, "roleblacklist")] = deepcopy(original)
        bot.db.set_config_committed = AsyncMock(side_effect=RuntimeError("db unavailable"))
        with pytest.raises(RuntimeError):
            await Moderation.rbl_add.callback(Moderation(bot), ctx, role=_mock_role(889, "new"))
        assert bot._storage[(123, "roleblacklist")] == original
        ctx.approve.assert_not_awaited()
    asyncio.run(run())


def test_bypass_success_card_is_accurate_and_avoids_pings(role_bot):  # noqa: F811
    async def run():
        guild = _mock_guild(123)
        guild.me.display_name = 'Veil Utilities'
        guild.me.display_avatar.url = 'https://cdn.example.test/veil.png'
        ctx = _mock_context(role_bot, guild, _mock_member(guild.owner_id))
        ctx.command.qualified_name = 'bypass'
        member = _mock_member(555)
        await Moderation.bypass.callback(Moderation(role_bot), ctx, target=member)
        embed = ctx.send.await_args.kwargs['embed']
        assert embed.author.name == 'Veil Utilities'
        assert embed.color.value == Colors.caution
        assert member.mention in embed.description
        assert 'recipients already allowed to hold them' in embed.description
        assert 'No Antinuke exemption' in embed.description
        assert embed.footer.text == ',bypass' and embed.timestamp is not None
        assert ctx.send.await_args.kwargs['allowed_mentions'].to_dict() == discord.AllowedMentions.none().to_dict()
    asyncio.run(run())


def test_bypass_shorthand_parsing_and_explicit_subcommands(role_bot):  # noqa: F811
    from discord.ext import commands
    from discord.ext.commands.view import StringView

    async def run():
        guild = _mock_guild(123)
        member = _mock_member(123456789012345678)
        role = _mock_role(223456789012345678, 'Bypass staff')
        guild.get_member.side_effect = lambda uid: member if uid == member.id else None
        guild.get_role.side_effect = lambda rid: role if rid == role.id else None
        owner = _mock_member(guild.owner_id)
        bot = commands.Bot(command_prefix=',', intents=discord.Intents.none())
        bot.owner_ids = set()
        bot.db = role_bot.db
        await bot.add_cog(Moderation(bot))
        root = bot.get_command('bypass')
        async def invoke(text):
            ctx = _mock_context(bot, guild, owner)
            ctx.send_help = AsyncMock()
            ctx.paginate = AsyncMock()
            ctx.message.mentions = [member]
            ctx.message.role_mentions = [role]
            ctx.invoked_parents = []
            ctx.invoked_with = 'bypass'
            ctx.command = root
            ctx.view = StringView(text)
            await root.invoke(ctx)
            return ctx
        try:
            member_ctx = await invoke(f'<@{member.id}>')
            assert role_bot._storage[(123, 'roleblacklist')]['soft_bypass']['users'] == [member.id]
            assert member_ctx.send.await_count == 1
            role_ctx = await invoke(f'<@&{role.id}>')
            assert role_bot._storage[(123, 'roleblacklist')]['soft_bypass']['roles'] == [role.id]
            assert 'Members with' in role_ctx.send.await_args.kwargs['embed'].description
            with pytest.raises(UserError, match='already has'):
                await invoke(f'<@{member.id}>')
            assert role_bot._storage[(123, 'roleblacklist')]['soft_bypass']['users'] == [member.id]
            await invoke(f'remove <@{member.id}>')
            assert role_bot._storage[(123, 'roleblacklist')]['soft_bypass']['users'] == []
            await invoke(f'add <@{member.id}>')
            assert role_bot._storage[(123, 'roleblacklist')]['soft_bypass']['users'] == [member.id]
            await invoke(f'remove <@&{role.id}>')
            assert role_bot._storage[(123, 'roleblacklist')]['soft_bypass']['roles'] == []
            help_ctx = await invoke('')
            help_ctx.send_help.assert_awaited_once()
            list_ctx = await invoke('list')
            list_ctx.paginate.assert_awaited_once()
        finally:
            await bot.close()
    asyncio.run(run())


def test_shorthand_rejects_default_managed_roles_and_failed_saves(role_bot):  # noqa: F811
    async def run():
        guild = _mock_guild(123)
        ctx = _mock_context(role_bot, guild, _mock_member(guild.owner_id))
        cog = Moderation(role_bot)
        role = _mock_role(600, 'role')
        for invalid in ('default', 'managed'):
            role.is_default.return_value = invalid == 'default'
            role.managed = invalid == 'managed'
            with pytest.raises(UserError, match='everyone role and managed'):
                await Moderation.bypass.callback(cog, ctx, target=role)
        assert (123, 'roleblacklist') not in role_bot._storage
        role.is_default.return_value = False
        role.managed = False
        role_bot.db.set_config_committed.side_effect = RuntimeError('database unavailable')
        with pytest.raises(RuntimeError, match='database unavailable'):
            await Moderation.bypass.callback(cog, ctx, target=role)
        assert (123, 'roleblacklist') not in role_bot._storage
        ctx.send.assert_not_awaited()
    asyncio.run(run())


def test_shorthand_is_owner_or_developer_only_on_actual_invoke(role_bot):  # noqa: F811
    from discord.ext import commands
    from discord.ext.commands.view import StringView

    async def run():
        guild = _mock_guild(123)
        member = _mock_member(123456789012345678)
        guild.get_member.side_effect = lambda uid: member if uid == member.id else None
        bot = commands.Bot(command_prefix=',', intents=discord.Intents.none())
        bot.owner_ids = set()
        bot.db = role_bot.db
        await bot.add_cog(Moderation(bot))
        ctx = _mock_context(bot, guild, _mock_member(98765))
        ctx.message.mentions = [member]
        ctx.invoked_parents = []
        ctx.invoked_with = 'bypass'
        ctx.command = bot.get_command('bypass')
        ctx.view = StringView(f'<@{member.id}>')
        try:
            with pytest.raises(UserError, match='server owner'):
                await ctx.command.invoke(ctx)
            assert (123, 'roleblacklist') not in role_bot._storage
        finally:
            await bot.close()
    asyncio.run(run())


def test_embed_send_failure_reports_success_with_fallback(role_bot):  # noqa: F811
    async def run():
        guild = _mock_guild(123)
        member = _mock_member(555)
        ctx = _mock_context(role_bot, guild, _mock_member(guild.owner_id))
        ctx.send.side_effect = discord.HTTPException(SimpleNamespace(status=500, reason='error'), 'send failed')
        await Moderation.bypass.callback(Moderation(role_bot), ctx, target=member)
        assert role_bot._storage[(123, 'roleblacklist')]['soft_bypass']['users'] == [555]
        ctx.approve.assert_awaited_once()
        assert 'bypass added' in ctx.approve.await_args.args[0]
    asyncio.run(run())
