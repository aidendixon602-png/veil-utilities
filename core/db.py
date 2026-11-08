"""Storage: SQLite by default, PostgreSQL for large deployments.

Both backends answer to the same small set of calls (`get_config`, `set_config`, `execute`, `fetchone`,
`fetchall`, `guilds_with`, self prefixes), so nothing in the cogs knows which one is in use. The cogs write
SQLite's placeholder style (`?`) and its row shape; the PostgreSQL backend translates the handful of
SQLite-only spellings (`INSERT OR REPLACE`, `INSERT OR IGNORE`, `AUTOINCREMENT`) and returns mapping rows.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import aiosqlite

log = logging.getLogger("veil.db")

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
-- An incident and its delivery states are written atomically in one row.
CREATE TABLE IF NOT EXISTS security_incidents (
    incident_id TEXT PRIMARY KEY, kind TEXT NOT NULL, severity TEXT NOT NULL,
    guild_id TEXT, actor_id TEXT, summary TEXT NOT NULL, action TEXT NOT NULL,
    details TEXT NOT NULL, deliveries TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL,
    next_attempt DOUBLE PRECISION, state TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_security_incidents_due ON security_incidents (next_attempt);
CREATE TABLE IF NOT EXISTS member_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    user_id INTEGER,
    author_id INTEGER,
    note TEXT,
    created_at REAL
);

-- lookups the bot does constantly (warn counts, case history, the reminder/giveaway loops)
CREATE INDEX IF NOT EXISTS idx_warns_member ON warns (guild_id, user_id);
CREATE INDEX IF NOT EXISTS idx_notes_member ON member_notes (guild_id, user_id);
CREATE INDEX IF NOT EXISTS idx_cases_member ON cases (guild_id, user_id);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders (due);
CREATE INDEX IF NOT EXISTS idx_reminders_user ON reminders (user_id);
CREATE INDEX IF NOT EXISTS idx_giveaways_open ON giveaways (ended, ends);
CREATE INDEX IF NOT EXISTS idx_levels_rank ON levels (guild_id, xp DESC);
CREATE INDEX IF NOT EXISTS idx_msgstats_day ON msgstats (guild_id, day);
"""

# SQLite INTEGER stores signed 64-bit values, whereas PostgreSQL INTEGER is only 32-bit.
# Discord snowflakes exceed that range; use BIGINT for all corresponding columns.
POSTGRES_SCHEMA = SCHEMA.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
POSTGRES_SCHEMA = re.sub(r"\bINTEGER\b", "BIGINT", POSTGRES_SCHEMA)


class Row(dict):
    """A row that reads like a SQLite one: `row["data"]`, and also `row[0]` (by column order)."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class WriteResult:
    """What `execute` returns so callers can ask `if cur.rowcount:` the same way on both backends."""

    def __init__(self, rowcount: int = 0):
        self.rowcount = rowcount


def _split_statements(script: str) -> list[str]:
    """SCHEMA -> single statements (PostgreSQL's simple query protocol rejects `CREATE INDEX ... (a, b)`)."""
    without_comments = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("--"))
    return [s.strip() for s in without_comments.split(";") if s.strip()]


def _pg_query(query: str, count: int) -> str:
    """SQLite's `?` placeholders -> `$1, $2, ...`, skipping any `?` inside a quoted string literal."""
    out, index, i, quoted = [], 1, 0, None
    while i < len(query):
        char = query[i]
        if quoted:
            out.append(char)
            if char == quoted:
                quoted = None
        elif char in ("'", '"'):
            quoted = char
            out.append(char)
        elif char == "?":
            out.append(f"${index}")
            index += 1
        else:
            out.append(char)
        i += 1
    if index - 1 != count:  # a placeholder the caller didn't pass (or a bare '?'): leave the SQL alone
        return query
    return "".join(out)


_INSERT_OR_REPLACE = re.compile(r"^\s*INSERT\s+OR\s+REPLACE\s+INTO\s+(\w+)\s*(\([^)]*\))?\s*VALUES", re.I)
_INSERT_OR_IGNORE = re.compile(r"^\s*INSERT\s+OR\s+IGNORE\s+INTO\s+(\w+)\s*(\([^)]*\))?\s*VALUES", re.I)
_CREATE_TABLE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\((.*?)\)\s*;", re.I | re.S)


def _split_top_level(body: str) -> list[str]:
    """Split a CREATE TABLE body on the commas that separate columns, not the ones inside `(a, b)`."""
    parts, depth, current = [], 0, []
    for char in body:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if "".join(current).strip():
        parts.append("".join(current).strip())
    return parts


def _table_columns() -> dict[str, tuple[list[str], list[str]]]:
    """{table: (columns in order, primary-key columns)} read straight from SCHEMA.

    The schema is the single place that defines the tables, so `INSERT OR REPLACE INTO t VALUES(...)`
    can be translated to PostgreSQL's ON CONFLICT form without a second, hand-kept copy of the columns.
    """
    tables: dict[str, tuple[list[str], list[str]]] = {}
    for name, body in _CREATE_TABLE.findall(SCHEMA):
        columns, keys = [], []
        for part in _split_top_level(body):
            upper = part.upper()
            if upper.startswith("PRIMARY KEY"):
                keys.extend(c.strip() for c in part[part.index("(") + 1:part.rindex(")")].split(","))
            else:
                columns.append(part.split()[0])
                if "PRIMARY KEY" in upper:
                    keys.append(part.split()[0])
        tables[name] = (columns, keys)
    return tables


TABLE_COLUMNS = _table_columns()


def _integer_columns() -> dict[str, list[str]]:
    """Columns declared INTEGER in our own schema (never identifiers supplied by a user)."""
    result = {}
    for name, body in _CREATE_TABLE.findall(SCHEMA):
        result[name] = [part.split()[0] for part in _split_top_level(body)
                        if re.match(r"^\w+\s+INTEGER\b", part, re.I)]
    return result


INTEGER_COLUMNS = _integer_columns()


def _pg_statement(query: str, args: tuple) -> tuple[str, bool]:
    """(statement, whether it is an INSERT OR IGNORE) for PostgreSQL.

    SQLite spells "upsert" as `INSERT OR REPLACE`, and "do nothing on conflict" as `INSERT OR IGNORE`.
    PostgreSQL does both with an ON CONFLICT clause, keyed by the table's primary key.
    """
    replace = _INSERT_OR_REPLACE.match(query)
    ignore = _INSERT_OR_IGNORE.match(query)
    if not replace and not ignore:
        return query, False
    match = replace or ignore
    table = match.group(1)
    given = match.group(2)
    columns, keys = TABLE_COLUMNS.get(table, ([], []))
    if given:  # `INSERT OR REPLACE INTO t (a, b) VALUES` — the caller said which columns
        names = [c.strip() for c in given.strip("()").split(",")]
    else:
        names = columns
    target = ", ".join(keys)
    if ignore or not target:
        suffix = f" ON CONFLICT ({target}) DO NOTHING" if target else ""
    else:
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in names if c and c not in keys)
        suffix = (f" ON CONFLICT ({target}) DO UPDATE SET {updates}" if updates
                  else f" ON CONFLICT ({target}) DO NOTHING")
    head = f"INSERT INTO {table} ({', '.join(names)}) VALUES" if names else f"INSERT INTO {table} VALUES"
    return head + query[match.end():] + suffix, ignore is not None



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
        # WAL + NORMAL is crash-safe (a power cut can lose only the last moments, never corrupt the db)
        # and makes the per-statement commits much cheaper than the default FULL sync
        await self.conn.execute("PRAGMA synchronous=NORMAL")
        await self.conn.execute("PRAGMA busy_timeout=5000")
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            try:
                await self.conn.execute("PRAGMA optimize")
            except Exception:
                pass
            await self.conn.close()
            self.conn = None

    # ---- raw helpers -------------------------------------------------
    async def execute(self, query: str, *args) -> aiosqlite.Cursor:
        cur = await self.conn.execute(query, args)
        await self.conn.commit()
        return cur

    async def executemany(self, query: str, rows) -> None:
        """Run one statement for many rows in a single transaction (one commit instead of N)."""
        await self.conn.executemany(query, rows)
        await self.conn.commit()

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

    async def set_config_committed(self, guild_id: int, module: str, data: Any) -> None:
        """Publish a new cached value only after the database write succeeds."""
        await self.execute(
            "INSERT INTO config(guild_id, module, data) VALUES(?,?,?) "
            "ON CONFLICT(guild_id, module) DO UPDATE SET data=excluded.data",
            guild_id, module, json.dumps(data),
        )
        self._cache[(guild_id, module)] = data

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


class PostgresDatabase:
    """The same storage in PostgreSQL, for deployments that outgrow one SQLite file.

    Set DATABASE_URL=postgresql://user:pass@host/db (and `pip install asyncpg`). Tables are created on the
    first start, exactly like the SQLite ones, so switching is: set DATABASE_URL, start, done.
    """

    def __init__(self, dsn: str):
        self.dsn = dsn
        self.pool = None
        self._cache: dict[tuple[int, str], Any] = {}
        self._prefix_cache: dict[int, str | None] = {}

    async def connect(self) -> None:
        try:
            import asyncpg
        except ImportError:
            raise RuntimeError("DATABASE_URL is set to PostgreSQL but asyncpg isn't installed: "
                               "run `pip install asyncpg` (or remove DATABASE_URL to use SQLite)") from None
        try:
            self.pool = await asyncpg.create_pool(self.dsn)
        except (OSError, asyncpg.PostgresError) as e:
            raise RuntimeError(f"Couldn't reach PostgreSQL at this DATABASE_URL: {e}") from None
        try:
            async with self.pool.acquire() as conn:
                for statement in _split_statements(POSTGRES_SCHEMA):
                    await conn.execute(statement)
                # Only names derived from our schema are interpolated; all other data is bound.
                # Widen existing columns in one transaction so a failure leaves no partial migration.
                async with conn.transaction():
                    for table, columns in INTEGER_COLUMNS.items():
                        rows = await conn.fetch(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_schema=current_schema() AND table_name=$1 AND data_type='integer'", table)
                        narrow = {row["column_name"] for row in rows}
                        for column in columns:
                            if column in narrow:
                                await conn.execute(f'ALTER TABLE "{table}" ALTER COLUMN "{column}" TYPE BIGINT')
        except Exception:
            await self.pool.close()
            self.pool = None
            raise
        log.info("Connected to PostgreSQL")

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()
            self.pool = None

    # ---- raw helpers -------------------------------------------------
    async def execute(self, query: str, *args) -> WriteResult:
        statement, ignore_conflict = _pg_statement(query, args)
        async with self.pool.acquire() as conn:
            status = await conn.execute(_pg_query(statement, len(args)), *args)
        return WriteResult(self._affected(status) + (0 if ignore_conflict else 0))

    async def executemany(self, query: str, rows) -> None:
        statement, _ = _pg_statement(query, ())
        async with self.pool.acquire() as conn:
            await conn.executemany(_pg_query(statement, len(rows[0]) if rows else 0), rows)

    async def fetchone(self, query: str, *args):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(_pg_query(query, len(args)), *args)
        return Row(row) if row is not None else None

    async def fetchall(self, query: str, *args):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(_pg_query(query, len(args)), *args)
        return [Row(r) for r in rows]

    @staticmethod
    def _affected(status: str) -> int:
        """asyncpg reports the row count in the command tag ('DELETE 3', 'INSERT 0 1')."""
        tag = (status or "").rsplit(" ", 1)[-1]
        return int(tag) if tag.isdigit() else 0

    # ---- JSON config -------------------------------------------------
    async def get_config(self, guild_id: int, module: str, default: Any = None) -> Any:
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

    async def set_config_committed(self, guild_id: int, module: str, data: Any) -> None:
        """Publish a new cached value only after the database write succeeds."""
        await self.execute(
            "INSERT INTO config(guild_id, module, data) VALUES(?,?,?) "
            "ON CONFLICT(guild_id, module) DO UPDATE SET data=excluded.data",
            guild_id, module, json.dumps(data),
        )
        self._cache[(guild_id, module)] = data

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


def is_postgres_url(value: str | None) -> bool:
    return bool(value) and value.startswith(("postgres://", "postgresql://"))


def create_database(path_or_url: str) -> Database | PostgresDatabase:
    """Factory creating SQLite Database or PostgreSQL PostgresDatabase based on connection URI."""
    if is_postgres_url(path_or_url):
        return PostgresDatabase(path_or_url)
    return Database(path_or_url)
