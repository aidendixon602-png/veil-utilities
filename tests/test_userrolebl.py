"""Tests for developer-only per-server user role blacklist (,userrolebl / ,urbl)."""
import asyncio
from unittest.mock import AsyncMock

from discord.ext import commands
import pytest

from cogs.devtools import DevTools
from cogs.moderation import Moderation
from core.utils import UserError, is_user_role_blacklisted
from test_roleblacklist_enforcement import _mock_context, _mock_guild, _mock_member, _mock_role, bot  # noqa: F401


def test_userrolebl_is_developer_only(bot):  # noqa: F811
    async def _run():
        guild = _mock_guild(123)
        non_dev = _mock_member(guild.owner_id)  # even server owner cannot run it if not a bot dev
        target = _mock_member(777)
        ctx = _mock_context(bot, guild, non_dev)
        bot.is_owner = AsyncMock(return_value=False)
        dt = DevTools(bot)

        with pytest.raises(commands.NotOwner):
            await dt.cog_check(ctx)
        with pytest.raises(commands.NotOwner):
            await DevTools.userrolebl.callback(dt, ctx, target)

    asyncio.run(_run())


def test_userrolebl_all_roles_strips_current_and_blocks_future_roles(bot):  # noqa: F811
    async def _run():
        guild = _mock_guild(123)
        dev = _mock_member(424242)
        r1 = _mock_role(101, "Member", pos=5)
        r2 = _mock_role(102, "VIP", pos=10)
        target = _mock_member(777, roles=[r1, r2])
        target.guild = guild
        guild.get_member.side_effect = lambda uid: target if uid == target.id else None
        guild.get_role.side_effect = lambda rid: {101: r1, 102: r2}.get(rid)
        bot.is_owner = AsyncMock(side_effect=lambda u: getattr(u, "id", u) == dev.id)

        ctx = _mock_context(bot, guild, dev)
        dt = DevTools(bot)
        mod = Moderation(bot)

        # Option 1: ,userrolebl @target strips current roles and blocks all roles in this guild
        await DevTools.userrolebl.callback(dt, ctx, target)
        target.remove_roles.assert_awaited_once_with(r1, r2, reason=f"user role blacklist by {dev} ({dev.id})")
        assert await is_user_role_blacklisted(123, target.id, bot.db, r1.id) is True
        assert await is_user_role_blacklisted(123, target.id, bot.db, r2.id) is True

        # Bot role command (,r) is blocked for this user
        target.roles = []
        with pytest.raises(UserError, match="blacklisted from receiving"):
            await mod._give_role(ctx, target, r1)
        target.add_roles.assert_not_awaited()

        # Manual Discord role addition (on_member_update) is automatically stripped
        target.remove_roles.reset_mock()
        before = _mock_member(target.id, roles=[])
        after = _mock_member(target.id, roles=[r1])
        before.nick = after.nick = None
        before.guild = after.guild = guild
        await mod.on_member_update(before, after)
        after.remove_roles.assert_awaited_once_with(r1, reason="Veil: Role is blacklisted")

    asyncio.run(_run())


def test_userrolebl_keep_mode_preserves_existing_roles_and_blocks_new_ones(bot):  # noqa: F811
    async def _run():
        guild = _mock_guild(123)
        dev = _mock_member(424242)
        existing_role = _mock_role(101, "Existing", pos=5)
        new_role = _mock_role(102, "NewRole", pos=6)
        target = _mock_member(777, roles=[existing_role])
        target.guild = guild
        guild.get_member.side_effect = lambda uid: target if uid == target.id else None
        bot.is_owner = AsyncMock(side_effect=lambda u: getattr(u, "id", u) == dev.id)

        ctx = _mock_context(bot, guild, dev)
        dt = DevTools(bot)
        mod = Moderation(bot)

        # Option 2: ,userrolebl keep @target keeps existing roles, blocks any new roles
        await DevTools.urbl_keep.callback(dt, ctx, target)
        target.remove_roles.assert_not_awaited()
        entry = bot._storage[(123, "userrolebl")]["entries"][str(target.id)]
        assert entry["all"] is True
        assert entry["kept_roles"] == [existing_role.id]

        # Existing role is allowed in allow_kept=True (e.g. on rejoin), but new role is blocked
        assert await is_user_role_blacklisted(123, target.id, bot.db, existing_role.id, allow_kept=True) is False
        assert await is_user_role_blacklisted(123, target.id, bot.db, new_role.id, allow_kept=True) is True
        assert await is_user_role_blacklisted(123, target.id, bot.db, new_role.id, allow_kept=False) is True

        # Adding a new role in Discord is stripped
        before = _mock_member(target.id, roles=[existing_role])
        after = _mock_member(target.id, roles=[existing_role, new_role])
        before.nick = after.nick = None
        before.guild = after.guild = guild
        await mod.on_member_update(before, after)
        after.remove_roles.assert_awaited_once_with(new_role, reason="Veil: Role is blacklisted")

        # Once a kept role is removed from the member, it is pruned from kept_roles
        after_removed = _mock_member(target.id, roles=[])
        after_removed.nick = None
        after_removed.guild = guild
        await mod.on_member_update(before, after_removed)
        assert bot._storage[(123, "userrolebl")]["entries"][str(target.id)]["kept_roles"] == []
        assert await is_user_role_blacklisted(123, target.id, bot.db, existing_role.id, allow_kept=True) is True

    asyncio.run(_run())


def test_userrolebl_specific_role_blocks_only_that_role_and_supports_remove_list_clear(bot):  # noqa: F811
    async def _run():
        guild = _mock_guild(123)
        dev = _mock_member(424242)
        blocked_role = _mock_role(101, "Blocked", pos=5)
        other_role = _mock_role(102, "Allowed", pos=6)
        target = _mock_member(777, roles=[blocked_role, other_role])
        target.guild = guild
        guild.get_member.side_effect = lambda uid: target if uid == target.id else None
        bot.is_owner = AsyncMock(side_effect=lambda u: getattr(u, "id", u) == dev.id)

        ctx = _mock_context(bot, guild, dev)
        ctx.paginate = AsyncMock()
        dt = DevTools(bot)
        mod = Moderation(bot)

        # Option 3: ,userrolebl @target @BlockedRole strips only @BlockedRole and blocks it
        await DevTools.userrolebl.callback(dt, ctx, target, extra=blocked_role)
        target.remove_roles.assert_awaited_once_with(blocked_role, reason=f"user role blacklist by {dev} ({dev.id})")
        assert await is_user_role_blacklisted(123, target.id, bot.db, blocked_role.id) is True
        assert await is_user_role_blacklisted(123, target.id, bot.db, other_role.id) is False

        # Target can still be given other_role, but not blocked_role
        target.roles = []
        with pytest.raises(UserError, match="blacklisted from receiving"):
            await mod._give_role(ctx, target, blocked_role)
        await mod._give_role(ctx, target, other_role)
        target.add_roles.assert_awaited_once()

        # List shows the blacklisted user and role
        await DevTools.urbl_list.callback(dt, ctx)
        ctx.paginate.assert_awaited_once()
        pages = ctx.paginate.await_args.args[0]
        assert f"<@{target.id}>" in pages[0].description
        assert f"<@&{blocked_role.id}>" in pages[0].description

        # Remove specific role lifts the blacklist
        await DevTools.urbl_remove.callback(dt, ctx, target, role=blocked_role)
        assert await is_user_role_blacklisted(123, target.id, bot.db, blocked_role.id) is False

    asyncio.run(_run())
