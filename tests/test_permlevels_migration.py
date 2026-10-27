"""Saved levels from before the rename (Supreme Leader, Overseer, Bot Manager, Mod) move to the new names when the
bot starts, and the level list shows the eight levels in order."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from cogs.permissions import Permissions
from core.db import Database


def test_saved_levels_are_migrated_when_the_cog_loads():
    async def go():
        db = Database(":memory:")
        await db.connect()
        try:
            await db.set_config(10, "permlevels", {"users": {"1": "supremeleader", "2": "botmanager"},
                                                   "roles": {"3": "mod"}, "overrides": {}})
            await db.set_config(11, "permlevels", {"users": {"4": "admin"}})
            db._cache.clear()  # as after a restart: nothing cached yet
            cog = Permissions(SimpleNamespace(db=db))
            await cog.cog_load()
            db._cache.clear()
            assert (await db.get_config(10, "permlevels"))["users"] == {"1": "coowner", "2": "manager"}
            assert (await db.get_config(10, "permlevels"))["roles"] == {"3": "moderator"}
            assert (await db.get_config(11, "permlevels"))["users"] == {"4": "admin"}
        finally:
            await db.close()
    asyncio.run(go())


def test_the_level_list_is_numbered_highest_first():
    async def go():
        db = SimpleNamespace(get_config=AsyncMock(return_value={"users": {"5": "manager", "6": "manager"},
                                                                "roles": {"7": "moderator"}}),
                             set_config=AsyncMock())
        cog = Permissions(SimpleNamespace(db=db))
        ctx = SimpleNamespace(guild=SimpleNamespace(id=10), clean_prefix=",", send=AsyncMock())
        await Permissions.permlevels.callback(cog, ctx)
        text = ctx.send.await_args.kwargs["embed"].description
        order = [line.split("**")[1] for line in text.splitlines() if line.startswith("**")]
        assert order == ["1. Developer", "2. Owner", "3. Co-Owner (`coowner`)", "4. Manager (`manager`)",
                         "5. Head Admin (`headadmin`)", "6. Admin (`admin`)", "7. Moderator (`moderator`)",
                         "8. Helper (`helper`)"]
        assert "everything except Administrator\n-# 2 members" in text
        assert "-# 1 role" in text and "rank 7" not in text  # internal numbers stay internal
    asyncio.run(go())
