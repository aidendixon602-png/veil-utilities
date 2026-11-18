"""Tests for per-server web dashboard and PostgreSQL backend option."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock


from core.dashboard import DashboardServer
from core.db import Database, PostgresDatabase, create_database


def test_database_factory():
    sqlite_db = create_database("sqlite:///veil.db")
    assert isinstance(sqlite_db, Database)

    pg_db = create_database("postgresql://user:pass@localhost:5432/veildb")
    assert isinstance(pg_db, PostgresDatabase)


def test_dashboard_routes():
    bot = MagicMock()
    bot.guilds = [
        SimpleNamespace(id=123, name="Test Server", members=[SimpleNamespace(id=1)]),
    ]
    bot.latency = 0.045
    bot.started_at = 1000.0
    bot.db = MagicMock()
    bot.db.get_config = AsyncMock(return_value={})

    server = DashboardServer(bot, port=9999)
    assert server.app is not None
    routes = [route.resource.canonical for route in server.app.router.routes()]
    assert "/" in routes
    assert "/servers" in routes
    assert "/servers/{guild_id}" in routes
    assert "/api/guilds" in routes
    assert "/api/guilds/{guild_id}/config" in routes


def test_atomic_multi_document_save_and_failed_write_leave_sqlite_and_cache_unchanged(tmp_path):
    import asyncio
    import json

    async def go():
        db = Database(str(tmp_path / 'test.db'))
        await db.connect()
        try:
            await db.set_configs_committed(1, {'logs': {'x': 1}, 'antinuke': {'y': 2}})
            assert await db.get_config(1, 'logs') == {'x': 1}
            assert await db.get_config(1, 'antinuke') == {'y': 2}
            await db.execute("CREATE TRIGGER block_antinuke BEFORE UPDATE ON config "
                             "WHEN NEW.module='antinuke' BEGIN SELECT RAISE(ABORT, 'blocked'); END")
            import pytest
            with pytest.raises(Exception):
                await db.set_configs_committed(1, {'logs': {'x': 3}, 'antinuke': {'y': 4}})
            row = await db.fetchone('SELECT data FROM config WHERE guild_id=? AND module=?', 1, 'logs')
            assert json.loads(row['data']) == {'x': 1}
            assert await db.get_config(1, 'logs') == {'x': 1}
            assert await db.get_config(1, 'antinuke') == {'y': 2}
        finally:
            await db.close()
    asyncio.run(go())


def test_postgres_multi_document_save_is_single_parameterized_statement():
    import asyncio
    async def go():
        db = PostgresDatabase('postgresql://unused')
        db.execute = AsyncMock()
        await db.set_configs_committed(5, {'logs': {'x': 1}, 'antinuke': {'y': 2}})
        args = db.execute.await_args.args
        assert 'VALUES (?,?,?),(?,?,?)' in args[0]
        assert len(args) == 7 and args[1] == 5 and args[4] == 5
        assert db._cache[(5, 'logs')] == {'x': 1}
        db.execute.side_effect = RuntimeError('failure')
        import pytest
        with pytest.raises(RuntimeError):
            await db.set_configs_committed(5, {'logs': {'x': 9}, 'antinuke': {'y': 9}})
        assert db._cache[(5, 'logs')] == {'x': 1}
    asyncio.run(go())
