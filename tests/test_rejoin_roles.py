"""Saved-role restore is opt-in, temporary, and passes each role through normal grant checks."""
import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

from cogs.moderation import Moderation


def test_rejoinroles_restores_snapshot_through_regular_role_grant_path():
    async def go():
        role = SimpleNamespace(id=23, managed=False, is_default=lambda: False)
        member = SimpleNamespace(id=7, mention="<@7>", roles=[])
        guild = SimpleNamespace(id=5, get_role=lambda rid: role if rid == role.id else None)
        db = SimpleNamespace(
            get_config=AsyncMock(return_value={"enabled": True}),
            fetchone=AsyncMock(return_value={"role_ids": json.dumps([role.id]), "left_at": time.time()}),
            execute=AsyncMock(),
        )
        bot = SimpleNamespace(db=db)
        cog = Moderation(bot)
        cog._give_role = AsyncMock()
        ctx = SimpleNamespace(guild=guild, author=SimpleNamespace(id=99), clean_prefix=",",
                              approve=AsyncMock(), warn=AsyncMock(), neutral=AsyncMock())

        await Moderation.rejoinroles.callback(cog, ctx, member)

        cog._give_role.assert_awaited_once_with(ctx, member, role, announce=False)
        ctx.approve.assert_awaited_once()
        assert "restored **1** role" in ctx.approve.await_args.args[0]

    asyncio.run(go())


def test_departure_snapshot_is_only_saved_when_enabled_and_excludes_everyone_and_managed_roles():
    async def go():
        guild = SimpleNamespace(id=5)
        basic = SimpleNamespace(id=1, managed=False, is_default=lambda: True)
        regular = SimpleNamespace(id=2, managed=False, is_default=lambda: False)
        managed = SimpleNamespace(id=3, managed=True, is_default=lambda: False)
        db = SimpleNamespace(get_config=AsyncMock(return_value={"enabled": True}), execute=AsyncMock())
        cog = Moderation(SimpleNamespace(db=db))
        member = SimpleNamespace(guild=guild, id=7, bot=False, roles=[basic, regular, managed])

        await Moderation.on_member_remove(cog, member)

        insert = db.execute.await_args_list[0]
        assert "INSERT INTO departed_member_roles" in insert.args[0]
        assert insert.args[1:4] == (guild.id, member.id, json.dumps([regular.id]))
        assert "DELETE FROM departed_member_roles" in db.execute.await_args_list[1].args[0]

    asyncio.run(go())


def test_departure_snapshot_is_not_saved_when_feature_is_off():
    async def go():
        db = SimpleNamespace(get_config=AsyncMock(return_value={"enabled": False}), execute=AsyncMock())
        cog = Moderation(SimpleNamespace(db=db))
        member = SimpleNamespace(guild=SimpleNamespace(id=5), id=7, bot=False, roles=[])
        await Moderation.on_member_remove(cog, member)
        db.execute.assert_not_awaited()
    asyncio.run(go())


def test_rejoinroles_expired_snapshot_is_deleted_not_restored():
    async def go():
        member = SimpleNamespace(id=7, mention="<@7>", roles=[])
        guild = SimpleNamespace(id=5, get_role=lambda _rid: None)
        db = SimpleNamespace(
            get_config=AsyncMock(return_value={"enabled": True}),
            fetchone=AsyncMock(return_value={"role_ids": "[23]", "left_at": time.time() - 31 * 86400}),
            execute=AsyncMock(),
        )
        cog = Moderation(SimpleNamespace(db=db))
        cog._give_role = AsyncMock()
        ctx = SimpleNamespace(guild=guild, author=SimpleNamespace(id=99), clean_prefix=",",
                              approve=AsyncMock(), warn=AsyncMock(), neutral=AsyncMock())

        await Moderation.rejoinroles.callback(cog, ctx, member)

        cog._give_role.assert_not_awaited()
        assert db.execute.await_count == 2  # purge expired guild snapshots, then delete this expired row
        assert db.execute.await_args.args == (
            "DELETE FROM departed_member_roles WHERE guild_id=? AND user_id=?", guild.id, member.id)
        ctx.warn.assert_awaited_once()

    asyncio.run(go())
