"""Failure, retry and restart tests use fake Discord users and a disposable database."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from core.db import Database
from core.incidents import IncidentManager


def test_incident_survives_restart_and_retries_each_recipient(tmp_path, monkeypatch):
    from core import incidents
    monkeypatch.setattr(incidents, "recipients", lambda bot: {11, 22})
    path = str(tmp_path / "security.db")

    async def go():
        db = Database(path)
        await db.connect()
        manager = IncidentManager(SimpleNamespace(db=db))
        incident_id = await manager.record(kind="unauthorized_server", severity="high", guild_id=123,
                                           actor_id=None, summary="Unauthorized join", action="Left the server.")
        await db.close()
        db = Database(path)
        await db.connect()
        manager = IncidentManager(SimpleNamespace(db=db))
        calls = []

        async def send(uid, row):
            calls.append(uid)
            assert row["incident_id"] == incident_id
            if uid == 11 and calls.count(11) == 1:
                raise RuntimeError("sensitive details must not be stored")

        manager._send = send
        await manager.deliver_due()
        row = await db.fetchone("SELECT * FROM security_incidents WHERE incident_id=?", incident_id)
        states = json.loads(row["deliveries"])
        assert states["11"]["status"] == "pending"
        assert states["11"]["attempts"] == 1
        assert states["11"]["last_error"] == "RuntimeError"
        assert states["22"]["status"] == "delivered"
        assert "sensitive details" not in row["deliveries"]
        states["11"]["next_attempt"] = 0
        await db.execute("UPDATE security_incidents SET deliveries=?,next_attempt=? WHERE incident_id=?",
                         json.dumps(states), 0, incident_id)
        await manager.deliver_due()
        await manager.deliver_due()
        row = await db.fetchone("SELECT * FROM security_incidents WHERE incident_id=?", incident_id)
        states = json.loads(row["deliveries"])
        assert states["11"]["status"] == "delivered" and states["11"]["attempts"] == 2
        assert row["next_attempt"] is None
        assert calls == [11, 22, 11]
        embed = manager._embed(row)
        assert incident_id in embed.fields[0].value
        assert embed.fields[2].value == "`unknown`"
        assert embed.fields[3].value == "Left the server."
        await db.close()
    asyncio.run(go())


def test_incident_record_redacts_and_survives_no_recipients(tmp_path, monkeypatch):
    from core import incidents
    monkeypatch.setattr(incidents, "recipients", lambda bot: set())
    monkeypatch.setenv("INCIDENT_API_KEY", "supersecretvalue123")

    async def go():
        db = Database(str(tmp_path / "empty.db"))
        await db.connect()
        manager = IncidentManager(SimpleNamespace(db=db))
        incident_id = await manager.record(kind="antinuke", severity="critical", guild_id=9,
                                           summary="key supersecretvalue123", action="blocked",
                                           details={"problem": "supersecretvalue123"})
        row = await db.fetchone("SELECT * FROM security_incidents WHERE incident_id=?", incident_id)
        assert "supersecretvalue123" not in row["summary"] + row["details"]
        assert row["next_attempt"] is None and json.loads(row["deliveries"]) == {}
        await db.close()
    asyncio.run(go())


def test_send_includes_persistent_authorize_button(tmp_path, monkeypatch):
    from core import incidents
    monkeypatch.setattr(incidents, "recipients", lambda bot: {11})

    async def go():
        db = Database(str(tmp_path / "button.db"))
        await db.connect()
        user = SimpleNamespace(send=AsyncMock())
        bot = SimpleNamespace(db=db, get_user=lambda uid: user)
        manager = IncidentManager(bot)
        await manager.record(kind="unauthorized_server", severity="high", guild_id=123,
                             summary="Unauthorized", action="Left")
        await manager.deliver_due()
        kwargs = user.send.await_args.kwargs
        assert kwargs["view"].children[0].item.custom_id == "auth:123"
        assert "unauthorized" in kwargs["embed"].description.lower()
        await db.close()
    asyncio.run(go())


def test_deferred_incident_recovers_with_unknown_outcome_after_crash(tmp_path, monkeypatch):
    from core import incidents
    monkeypatch.setattr(incidents, "recipients", lambda bot: {11})

    async def go():
        path = str(tmp_path / "deferred.db")
        db = Database(path)
        await db.connect()
        manager = IncidentManager(SimpleNamespace(db=db))
        incident_id = await manager.record(kind="unauthorized_server", severity="high", guild_id=123,
                                           summary="Unauthorized", action="Leave not confirmed; outcome unknown.",
                                           defer_seconds=45)
        manager._send = AsyncMock()
        await manager.deliver_due()
        manager._send.assert_not_awaited()
        await db.close()
        db = Database(path)
        await db.connect()
        await db.execute("UPDATE security_incidents SET next_attempt=0 WHERE incident_id=?", incident_id)
        manager = IncidentManager(SimpleNamespace(db=db))
        manager._send = AsyncMock()
        await manager.deliver_due()
        row = manager._send.await_args.args[1]
        assert row["action"] == "Leave not confirmed; outcome unknown."
        assert row["state"] == "containing"
        await db.close()
    asyncio.run(go())
