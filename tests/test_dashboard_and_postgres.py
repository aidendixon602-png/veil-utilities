"""Tests for per-server web dashboard and PostgreSQL backend option."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

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
