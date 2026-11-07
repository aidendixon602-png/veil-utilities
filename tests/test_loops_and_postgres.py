"""Background loops must survive a bad iteration, and the PostgreSQL backend must actually work.

Both are quiet failures in production: a loop that dies leaves reminders, giveaways and counters switched off
with one traceback in the log, and a half-working database backend only shows up on the host that uses it.
"""
import asyncio

import pytest
from discord.ext import tasks

from core.loops import resilient


# ------------------------------------------------------------------ loops that keep running

def test_a_loop_that_raises_keeps_going():
    runs = []

    @tasks.loop(seconds=0.01)
    @resilient
    async def ticker():
        runs.append(1)
        if len(runs) in (2, 3):
            raise RuntimeError("one bad guild")

    async def go():
        ticker.start()
        await asyncio.sleep(0.35)
        try:
            assert ticker.is_running(), "the loop stopped after an error"
            assert not ticker.failed()
            assert len(runs) > 3, f"the loop only ran {len(runs)} times"
        finally:
            ticker.cancel()

    asyncio.run(go())


def test_without_resilient_the_same_loop_dies():
    """The reason core/loops.py exists: this is Discord's own behaviour without the wrapper."""
    runs = []

    @tasks.loop(seconds=0.01)
    async def ticker():
        runs.append(1)
        raise RuntimeError("one bad guild")

    async def go():
        ticker.start()
        await asyncio.sleep(0.2)
        try:
            assert not ticker.is_running()
            assert ticker.failed()
        finally:
            ticker.cancel()

    asyncio.run(go())


def test_every_periodic_loop_in_the_bot_is_wrapped():
    """A new @tasks.loop added without @resilient would bring the old silent-stop bug back."""
    from cogs.config import Config
    from cogs.feeds import Feeds
    from cogs.giveaways import Giveaways
    from cogs.levels import Levels
    from cogs.music import Music
    from cogs.utility import Utility
    from core.bot import Veil

    cogs = (Config, Feeds, Giveaways, Levels, Music, Utility, Veil)
    loops = [(cls.__name__, name, obj) for cls in cogs for name, obj in vars(cls).items()
             if isinstance(obj, tasks.Loop)]
    assert len(loops) >= 8, loops
    unwrapped = [(cog, name) for cog, name, loop in loops if not hasattr(loop.coro, "__wrapped__")]
    assert not unwrapped, f"these loops stop for good on one error: {unwrapped}"


# ------------------------------------------------------------------ PostgreSQL

pytest.importorskip("asyncpg")
pgserver = pytest.importorskip("pgserver")  # a throwaway PostgreSQL to test against


@pytest.fixture(scope="module")
def pg_dsn(tmp_path_factory):
    data = tmp_path_factory.mktemp("pgdata")
    return pgserver.get_server(str(data), cleanup_mode=None).get_uri()


async def with_db(dsn, work):
    from core.db import PostgresDatabase
    db = PostgresDatabase(dsn)
    await db.connect()
    try:
        return await work(db)
    finally:
        await db.close()


def test_postgres_creates_every_table_the_bot_uses(pg_dsn):
    from core.db import TABLE_COLUMNS

    async def work(db):
        rows = await db.fetchall("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
        return {r["table_name"] for r in rows}

    made = asyncio.run(with_db(pg_dsn, work))
    assert set(TABLE_COLUMNS) <= made, set(TABLE_COLUMNS) - made


def test_postgres_stores_and_reloads_per_guild_config(pg_dsn):
    async def work(db):
        cfg = await db.get_config(5, "antinuke", {"enabled": True, "modules": {}})
        cfg["enabled"] = False
        await db.set_config(5, "antinuke", cfg)
        db._cache.clear()  # force a real read
        return (await db.get_config(5, "antinuke"), await db.guilds_with("antinuke"))

    reloaded, listed = asyncio.run(with_db(pg_dsn, work))
    assert reloaded["enabled"] is False
    assert listed == [(5, {"enabled": False, "modules": {}})]


def test_postgres_rowcount_matches_sqlite(pg_dsn):
    """`clearwarns`, entering a giveaway and deleting a reminder all branch on rowcount."""
    async def work(db):
        await db.execute("INSERT INTO warns(guild_id,user_id,mod_id,reason,created_at) VALUES(?,?,?,?,?)",
                         1, 2, 3, "spam", 0.0)
        first = await db.execute("DELETE FROM warns WHERE guild_id=? AND user_id=?", 1, 2)
        second = await db.execute("DELETE FROM warns WHERE guild_id=? AND user_id=?", 1, 2)
        return first.rowcount, second.rowcount

    first, second = asyncio.run(with_db(pg_dsn, work))
    assert (first, second) == (1, 0)


def test_postgres_understands_insert_or_replace_and_insert_or_ignore(pg_dsn):
    async def work(db):
        await db.execute("INSERT OR IGNORE INTO lastfm VALUES(?,?)", 7, "old")
        await db.execute("INSERT OR REPLACE INTO lastfm VALUES(?,?)", 7, "new")
        await db.execute("INSERT OR IGNORE INTO giveaway_entries VALUES(?,?)", 9, 9)
        duplicate = await db.execute("INSERT OR IGNORE INTO giveaway_entries VALUES(?,?)", 9, 9)
        return ([r["username"] for r in await db.fetchall("SELECT username FROM lastfm")], duplicate.rowcount)

    usernames, duplicate_count = asyncio.run(with_db(pg_dsn, work))
    assert usernames == ["new"], "INSERT OR REPLACE didn't replace"
    assert duplicate_count == 0, "INSERT OR IGNORE didn't ignore the duplicate"


def test_postgres_auto_increment_ids(pg_dsn):
    async def work(db):
        for content in ("x", "y"):
            await db.execute("INSERT INTO reminders(user_id,channel_id,due,content,created) VALUES(?,?,?,?,?)",
                             1, 2, 3.0, content, 4.0)
        return [r["id"] for r in await db.fetchall("SELECT id FROM reminders ORDER BY id")]

    assert asyncio.run(with_db(pg_dsn, work)) == [1, 2]


def test_postgres_rows_read_by_column_name_and_position(pg_dsn):
    """Cogs use `row["data"]`; a couple of helpers index positionally."""
    async def work(db):
        row = await db.fetchone("SELECT 1 AS one, 2 AS two")
        return row["two"], row[0]

    assert asyncio.run(with_db(pg_dsn, work)) == (2, 1)


def test_postgres_and_sqlite_answer_the_same_calls():
    """Anything a cog calls on the SQLite backend must exist on PostgreSQL too."""
    from core.db import Database, PostgresDatabase

    sqlite_api = {name for name in dir(Database) if not name.startswith("_")}
    postgres_api = {name for name in dir(PostgresDatabase) if not name.startswith("_")}
    assert not sqlite_api - postgres_api, sorted(sqlite_api - postgres_api)


def test_postgres_placeholders_are_numbered_in_one_pass():
    from core.db import _pg_query

    assert _pg_query("SELECT a FROM t WHERE b=? AND c=?", 2) == "SELECT a FROM t WHERE b=$1 AND c=$2"
    assert _pg_query("SELECT '?' AS literal, a FROM t WHERE b=?", 1) == "SELECT '?' AS literal, a FROM t WHERE b=$1"
