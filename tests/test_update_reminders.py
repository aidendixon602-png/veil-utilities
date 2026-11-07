"""Reminder delivery must survive transient failures and fall back to DMs."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from cogs.utility import Utility
from core.db import Database


def run_scenario(scenario):
    async def go():
        db = Database(":memory:")
        await db.connect()
        try:
            await scenario(db)
        finally:
            await db.close()
    asyncio.run(go())


async def add(db, uid, channel):
    await db.execute("INSERT INTO reminders(user_id,channel_id,due,content,created) VALUES(?,?,?,?,?)",
                     uid, channel, 1, f"reminder {uid}", 1)


def test_failures_are_retained_and_do_not_block_other_reminders(monkeypatch):
    async def go(db):
        await add(db, 1, 10)
        await add(db, 2, 20)
        clock = [100.0]
        monkeypatch.setattr("cogs.utility.time.monotonic", lambda: clock[0])
        first = SimpleNamespace(send=AsyncMock(side_effect=OSError("network")))
        second = SimpleNamespace(send=AsyncMock())
        bot = SimpleNamespace(db=db, get_channel=lambda cid: {10: first, 20: second}[cid], fetch_user=AsyncMock())
        cog = Utility(bot)
        await Utility.reminder_loop.coro(cog)
        rows = await db.fetchall("SELECT user_id FROM reminders")
        assert [r["user_id"] for r in rows] == [1]
        second.send.assert_awaited_once()
        first.send.side_effect = None
        await Utility.reminder_loop.coro(cog)
        assert first.send.await_count == 1  # backoff
        clock[0] += 21
        await Utility.reminder_loop.coro(cog)
        assert not await db.fetchall("SELECT user_id FROM reminders")
        assert not cog._reminder_retries
    run_scenario(go)


def test_inaccessible_channel_falls_back_to_dm():
    async def go(db):
        await add(db, 1, 10)
        response = SimpleNamespace(status=403, reason="Forbidden")
        channel = SimpleNamespace(send=AsyncMock(side_effect=discord.Forbidden(response, "no access")))
        user = SimpleNamespace(send=AsyncMock())
        bot = SimpleNamespace(db=db, get_channel=lambda _: channel, fetch_user=AsyncMock(return_value=user))
        await Utility.reminder_loop.coro(Utility(bot))
        user.send.assert_awaited_once()
        assert not await db.fetchall("SELECT user_id FROM reminders")
    run_scenario(go)


def test_failed_dm_stays_pending_and_cancellation_clears_backoff():
    async def go(db):
        await add(db, 1, 10)
        bot = SimpleNamespace(db=db, get_channel=lambda _: None, fetch_user=AsyncMock(side_effect=OSError("offline")))
        cog = Utility(bot)
        await Utility.reminder_loop.coro(cog)
        rows = await db.fetchall("SELECT id FROM reminders")
        assert len(rows) == 1 and rows[0]["id"] in cog._reminder_retries
        await db.execute("DELETE FROM reminders WHERE id=?", rows[0]["id"])
        await Utility.reminder_loop.coro(cog)
        assert not cog._reminder_retries
    run_scenario(go)
