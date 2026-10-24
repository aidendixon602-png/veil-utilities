"""SQLite storage with an in-memory cache for per-guild JSON config."""
from __future__ import annotations

import json
from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS config (
    guild_id INTEGER NOT NULL,
    module   TEXT    NOT NULL,
    data     TEXT    NOT NULL,
    PRIMARY KEY (guild_id, module)
);
CREATE TABLE IF NOT EXISTS selfprefix (user_id INTEGER PRIMARY KEY, prefix TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cases (
    guild_id INTEGER, case_num INTEGER, user_id INTEGER, mod_id INTEGER,
    action TEXT, reason TEXT, created_at REAL,
    PRIMARY KEY (guild_id, case_num)
);
CREATE TABLE IF NOT EXISTS warns (
    id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER, user_id INTEGER,
    mod_id INTEGER, reason TEXT, created_at REAL
);
CREATE TABLE IF NOT EXISTS levels (
    guild_id INTEGER, user_id INTEGER, xp INTEGER DEFAULT 0, level INTEGER DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);
CREATE TABLE IF NOT EXISTS msgstats (
    guild_id INTEGER, user_id INTEGER, day TEXT, count INTEGER DEFAULT 0,
    PRIMARY KEY (guild_id, user_id, day)
);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, channel_id INTEGER,
    due REAL, content TEXT, created REAL
);
CREATE TABLE IF NOT EXISTS giveaways (
    message_id INTEGER PRIMARY KEY, guild_id INTEGER, channel_id INTEGER, host_id INTEGER,
    prize TEXT, winners INTEGER, ends REAL, ended INTEGER DEFAULT 0, required_role INTEGER
);
CREATE TABLE IF NOT EXISTS giveaway_entries (
    message_id INTEGER, user_id INTEGER, PRIMARY KEY (message_id, user_id)
);
CREATE TABLE IF NOT EXISTS afk (
    guild_id INTEGER, user_id INTEGER, reason TEXT, since REAL, PRIMARY KEY (guild_id, user_id)
);
CREATE TABLE IF NOT EXISTS lastfm (user_id INTEGER PRIMARY KEY, username TEXT);
CREATE TABLE IF NOT EXISTS tickets (channel_id INTEGER PRIMARY KEY, guild_id INTEGER, user_id INTEGER, opened REAL);
CREATE TABLE IF NOT EXISTS vm_channels (channel_id INTEGER PRIMARY KEY, guild_id INTEGER, owner_id INTEGER);
CREATE TABLE IF NOT EXISTS blacklist (id INTEGER PRIMARY KEY, reason TEXT);
CREATE TABLE IF NOT EXISTS authorized (guild_id INTEGER PRIMARY KEY, authorized_by INTEGER, at REAL, note TEXT);
CREATE TABLE IF NOT EXISTS globalbans (user_id INTEGER PRIMARY KEY, mod_id INTEGER, reason TEXT, at REAL);
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None
        self._cache: dict[tuple[int, str], Any] = {}
        self._prefix_cache: dict[int, str | None] = {}

    async def connect(self) -> None:
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    # ---- raw helpers -------------------------------------------------
    async def execute(self, query: str, *args) -> aiosqlite.Cursor:
        cur = await self.conn.execute(query, args)
        await self.conn.commit()
        return cur

    async def fetchone(self, query: str, *args):
        async with self.conn.execute(query, args) as cur:
            return await cur.fetchone()

    async def fetchall(self, query: str, *args):
        async with self.conn.execute(query, args) as cur:
            return await cur.fetchall()

    # ---- JSON config -------------------------------------------------
    async def get_config(self, guild_id: int, module: str, default: Any = None) -> Any:
        """Return the (cached, mutable) config doc. Call set_config to persist changes."""
        key = (guild_id, module)
        if key in self._cache:
            return self._cache[key]
        row = await self.fetchone("SELECT data FROM config WHERE guild_id=? AND module=?", guild_id, module)
        if row:
            data = json.loads(row["data"])
        else:
            data = json.loads(json.dumps(default)) if default is not None else {}
        self._cache[key] = data
        return data

    async def set_config(self, guild_id: int, module: str, data: Any) -> None:
        self._cache[(guild_id, module)] = data
        await self.execute(
            "INSERT INTO config(guild_id, module, data) VALUES(?,?,?) "
            "ON CONFLICT(guild_id, module) DO UPDATE SET data=excluded.data",
            guild_id, module, json.dumps(data),
        )

    async def guilds_with(self, module: str) -> list[tuple[int, Any]]:
        rows = await self.fetchall("SELECT guild_id, data FROM config WHERE module=?", module)
        return [(r["guild_id"], json.loads(r["data"])) for r in rows]

    # ---- self prefix -------------------------------------------------
    async def get_selfprefix(self, user_id: int) -> str | None:
        if user_id in self._prefix_cache:
            return self._prefix_cache[user_id]
        row = await self.fetchone("SELECT prefix FROM selfprefix WHERE user_id=?", user_id)
        self._prefix_cache[user_id] = row["prefix"] if row else None
        return self._prefix_cache[user_id]

    async def set_selfprefix(self, user_id: int, prefix: str | None) -> None:
        self._prefix_cache[user_id] = prefix
        if prefix is None:
            await self.execute("DELETE FROM selfprefix WHERE user_id=?", user_id)
        else:
            await self.execute(
                "INSERT INTO selfprefix VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET prefix=excluded.prefix",
                user_id, prefix,
            )
