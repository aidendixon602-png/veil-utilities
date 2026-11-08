"""Native Discord permissions must not silently grant the shared Veil permission checks."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from cogs.tickets import Tickets
from core.bot import Veil
from core.help import Help
from core.utils import Ignored, UserError, missing_perms


@pytest.mark.parametrize("permission", ["administrator", "ban_members", "kick_members", "moderate_members",
                                        "manage_messages", "manage_roles", "manage_channels", "manage_guild"])
def test_native_permissions_do_not_authorize_shared_checks_on_empty_guild(permission):
    async def go():
        author = SimpleNamespace(id=20, roles=[], guild_permissions=discord.Permissions.all())
        bot = SimpleNamespace(db=SimpleNamespace(get_config=AsyncMock(return_value={})), owner_ids=set())
        ctx = SimpleNamespace(author=author, bot=bot, guild=SimpleNamespace(id=1, owner_id=10),
                              channel=SimpleNamespace(permissions_for=lambda _: discord.Permissions.all()))
        assert await missing_perms(ctx, **{permission: True}) == [permission]
        ctx.guild.owner_id = author.id
        assert await missing_perms(ctx, **{permission: True}) == []
    asyncio.run(go())


def test_fake_grants_remain_explicit_and_guild_scoped():
    async def go():
        role = SimpleNamespace(id=55)
        author = SimpleNamespace(id=20, roles=[role])
        configs = {(1, "fakeperms"): {"55": ["ban_members"]}, (2, "fakeperms"): {}}
        db = SimpleNamespace(get_config=AsyncMock(side_effect=lambda gid, mod: configs.get((gid, mod), {})))
        ctx = SimpleNamespace(author=author, bot=SimpleNamespace(db=db, owner_ids=set()),
                              guild=SimpleNamespace(id=1, owner_id=10))
        assert await missing_perms(ctx, ban_members=True, kick_members=True) == ["kick_members"]
        ctx.guild.id = 2
        assert await missing_perms(ctx, ban_members=True) == ["ban_members"]
        db.get_config.side_effect = RuntimeError("database unavailable")
        with pytest.raises(RuntimeError):
            await missing_perms(ctx, ban_members=True)
    asyncio.run(go())


def test_native_admin_cannot_bypass_disabled_or_ignored_command_or_staff_mode():
    async def go():
        bot = Veil()
        bot.auth_required = False
        bot.private_mode = False
        bot.owner_ids = frozenset()
        bot.is_owner = AsyncMock(return_value=False)
        settings = {"disabled": ["ping"]}
        bot.db = SimpleNamespace(get_config=AsyncMock(side_effect=lambda gid, module: settings if module == "settings" else {}))
        author = SimpleNamespace(id=20, roles=[], guild_permissions=discord.Permissions.all())
        guild = SimpleNamespace(id=1, owner_id=10)
        cmd = SimpleNamespace(name="ping", qualified_name="ping", root_parent=None, hidden=False, parents=[])
        ctx = SimpleNamespace(guild=guild, author=author, command=cmd, channel=SimpleNamespace(id=30),
                              cog=None, send=AsyncMock())
        assert not await bot.is_staff_member(ctx)
        with pytest.raises(UserError, match="disabled"):
            await bot._global_check(ctx)
        settings.clear()
        settings["ignored_users"] = [20]
        with pytest.raises(Ignored):
            await bot._global_check(ctx)
        settings.clear()
        settings["staff_only"] = True
        with pytest.raises(UserError, match="staff-only"):
            await bot._global_check(ctx)
    asyncio.run(go())


def test_native_manage_channels_alone_does_not_grant_ticket_support():
    async def go():
        db = SimpleNamespace(get_config=AsyncMock(return_value={}))
        bot = SimpleNamespace(db=db, owner_ids=set())
        guild = SimpleNamespace(id=1, owner_id=10)
        member = SimpleNamespace(id=20, guild=guild, roles=[], guild_permissions=discord.Permissions(manage_channels=True))
        assert not await Tickets(bot).is_support(member)
        db.get_config.side_effect = lambda gid, module, default=None: {"support": [55]} if module == "tickets" else {}
        member.roles = [SimpleNamespace(id=55)]
        assert await Tickets(bot).is_support(member)
    asyncio.run(go())


def test_help_hides_protected_command_without_a_veil_grant():
    async def go():
        help_command = Help()
        author = SimpleNamespace(id=20, roles=[], guild_permissions=discord.Permissions.all())
        cmd = SimpleNamespace(name="kick", qualified_name="kick", root_parent=None, parents=[],
                              callback=SimpleNamespace(__veil_perms__=["kick_members"]))
        db = SimpleNamespace(get_config=AsyncMock(return_value={}))
        help_command.context = SimpleNamespace(author=author, guild=SimpleNamespace(id=1, owner_id=10),
                                               channel=SimpleNamespace(permissions_for=lambda _: discord.Permissions.all()),
                                               bot=SimpleNamespace(db=db))
        assert not (await help_command.help_access(0)).permits(cmd)
    asyncio.run(go())


def test_uperm_rejects_self_and_owner_assignments():
    from unittest.mock import MagicMock
    from cogs.permissions import Permissions

    async def go():
        db = SimpleNamespace(get_config=AsyncMock(return_value={}), set_config_committed=AsyncMock())
        bot = SimpleNamespace(db=db, owner_ids=set(), is_owner=AsyncMock(return_value=False))
        cog = Permissions(bot)
        author = SimpleNamespace(id=20, roles=[], top_role=10)
        guild = SimpleNamespace(id=1, owner_id=10)
        ctx = SimpleNamespace(author=author, guild=guild, approve=AsyncMock())
        for target_id, message in ((20, "yourself"), (10, "server owner")):
            target = MagicMock(spec=discord.Member)
            target.id = target_id
            with pytest.raises(UserError, match=message):
                await Permissions.up_add.callback(cog, ctx, target, level="helper")
        target.id = 10
        with pytest.raises(UserError, match="server owner"):
            await Permissions.up_remove.callback(cog, ctx, target)
        db.set_config_committed.assert_not_awaited()
    asyncio.run(go())


def test_uperm_rejects_role_self_grant_and_higher_veil_level():
    from unittest.mock import MagicMock
    from cogs.permissions import Permissions

    async def go():
        store = {"users": {"20": "headadmin"}, "roles": {"55": "supremeleader"}}
        db = SimpleNamespace(get_config=AsyncMock(side_effect=lambda gid, mod, default=None: store if mod == "permlevels" else {}),
                             set_config_committed=AsyncMock())
        bot = SimpleNamespace(db=db, owner_ids=set(), is_owner=AsyncMock(return_value=False))
        cog = Permissions(bot)
        author = SimpleNamespace(id=20, roles=[SimpleNamespace(id=55)], top_role=10)
        ctx = SimpleNamespace(author=author, guild=SimpleNamespace(id=1, owner_id=10), approve=AsyncMock())
        role = MagicMock(spec=discord.Role)
        role.id, role.mention, role.managed = 55, "<@&55>", False
        role.is_default.return_value = False
        role.__ge__.return_value = False
        with pytest.raises(UserError, match="role you hold"):
            await Permissions.up_add.callback(cog, ctx, role, level="helper")
        author.roles = []
        with pytest.raises(UserError, match="equal to or above"):
            await Permissions.up_add.callback(cog, ctx, role, level="helper")
        with pytest.raises(UserError, match="equal to or above"):
            await Permissions.up_remove.callback(cog, ctx, role)
        db.set_config_committed.assert_not_awaited()
    asyncio.run(go())
