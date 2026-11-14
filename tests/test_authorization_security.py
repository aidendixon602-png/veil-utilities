"""No live Discord operations: authorize, audit, and containment are exercised with fakes."""
import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from cogs.authorization import Authorization
from core.db import Database


@pytest.fixture(autouse=True)
def fast_default_rejection(monkeypatch):
    # Existing containment tests exercise outcomes without waiting for the grace period.
    monkeypatch.setattr("cogs.authorization.APPROVAL_GRACE_SECONDS", 0)


def bot_for(db):
    bot = SimpleNamespace(db=db, authorized=set(), auth_required=True, guilds=[], user=SimpleNamespace(id=99))
    bot.is_authorized = lambda gid: gid in bot.authorized or not bot.auth_required
    return bot


def guild_for(gid=123):
    return SimpleNamespace(id=gid, name="Test guild", owner_id=777, member_count=1, icon=None,
                           me=SimpleNamespace(joined_at=discord.utils.utcnow()), leave=AsyncMock())


def test_authorization_cache_is_not_changed_when_database_write_fails():
    async def go():
        db = SimpleNamespace(execute=AsyncMock(side_effect=RuntimeError("DB down")))
        bot = bot_for(db)
        cog = Authorization(bot)
        with pytest.raises(RuntimeError):
            await cog.authorize(123, 1)
        assert bot.authorized == set()
        bot.authorized.add(123)
        with pytest.raises(RuntimeError):
            await cog.unauthorize(123)
        assert bot.authorized == {123}
    asyncio.run(go())


def test_inviter_requires_current_join_time_and_matching_target():
    async def go():
        bot = bot_for(None)
        cog = Authorization(bot)
        guild = guild_for()
        actor = SimpleNamespace(id=5)
        entry = SimpleNamespace(target=SimpleNamespace(id=99), user=actor, created_at=guild.me.joined_at)

        async def audit_logs(**kwargs):
            yield entry
        guild.audit_logs = audit_logs
        assert await cog.find_inviter(guild) is actor
        entry.created_at -= timedelta(minutes=3)
        assert await cog.find_inviter(guild) is None
        entry.created_at = guild.me.joined_at
        entry.target.id = 55
        assert await cog.find_inviter(guild) is None
        entry.target.id = 99
        guild.me.joined_at = None
        assert await cog.find_inviter(guild) is None
        guild.me.joined_at = discord.utils.utcnow() - timedelta(days=1)
        assert await cog.find_inviter(guild) is None
    asyncio.run(go())


def test_rejection_leaves_even_when_audit_and_guild_notification_fail():
    async def go():
        bot = bot_for(None)
        bot.incident_manager = SimpleNamespace(record=AsyncMock(return_value="incident"), finalize=AsyncMock())
        cog = Authorization(bot)
        cog.find_inviter = AsyncMock(side_effect=RuntimeError("audit broke"))
        cog.notify_guild = AsyncMock(side_effect=RuntimeError("notice broke"))
        cog.alert_developers = AsyncMock()
        guild = guild_for()
        await cog.reject(guild)
        guild.leave.assert_awaited_once()
        fields = bot.incident_manager.record.await_args.kwargs
        assert fields["action"] == "Leave not confirmed; outcome unknown."
        bot.incident_manager.finalize.assert_awaited_once_with("incident", "Left the server.")
        assert fields["actor_id"] is None
    asyncio.run(go())


def test_failed_leave_is_not_reported_as_success_and_db_failure_uses_direct_alerts():
    async def go():
        bot = bot_for(None)
        bot.incident_manager = SimpleNamespace(record=AsyncMock(side_effect=RuntimeError("DB down")))
        cog = Authorization(bot)
        cog.find_inviter, cog.notify_guild, cog.alert_developers = AsyncMock(), AsyncMock(), AsyncMock()
        guild = guild_for()
        guild.leave.side_effect = RuntimeError("leave failed")
        await cog.reject(guild)
        kwargs = cog.alert_developers.await_args.kwargs
        assert "failed" in kwargs["action"].lower()
        assert kwargs["include_dms"] is True
    asyncio.run(go())


def test_authorized_during_notice_is_not_removed():
    async def go():
        bot = bot_for(None)
        cog = Authorization(bot)
        guild = guild_for()
        cog.find_inviter = AsyncMock(return_value=None)
        async def approve(g):
            bot.authorized.add(g.id)
        cog.notify_guild = approve
        cog.alert_developers = AsyncMock()
        await cog.reject(guild)
        guild.leave.assert_not_awaited()
    asyncio.run(go())


def test_grandfather_marker_survives_empty_authorization_list_and_restart(tmp_path, monkeypatch):
    import cogs.authorization as auth
    monkeypatch.setattr(auth, "developers", AsyncMock(return_value=set()))

    async def go():
        db = Database(str(tmp_path / "auth.db"))
        await db.connect()
        bot = bot_for(db)
        bot.guilds = [guild_for()]
        cog = Authorization(bot)
        await cog.on_ready()
        assert bot.authorized == {123}
        marker = await db.fetchone("SELECT data FROM config WHERE guild_id=0 AND module='authorization_init'")
        assert json.loads(marker["data"]) == {"complete": True}
        await cog.unauthorize(123)
        await db.close()
        db = Database(str(tmp_path / "auth.db"))
        await db.connect()
        bot = bot_for(db)
        bot.guilds = [guild_for()]
        cog = Authorization(bot)
        cog.reject = AsyncMock()
        await cog.on_ready()
        assert not bot.authorized
        cog.reject.assert_awaited_once()
        await db.close()
    asyncio.run(go())


def test_incomplete_enrollment_resumes_only_saved_snapshot(tmp_path, monkeypatch):
    import cogs.authorization as auth
    monkeypatch.setattr(auth, "developers", AsyncMock(return_value=set()))

    async def go():
        db = Database(str(tmp_path / "resume.db"))
        await db.connect()
        await db.execute("INSERT INTO config VALUES(?,?,?)", 0, "authorization_init",
                         json.dumps({"complete": False, "pending": [123, 124]}))
        bot = bot_for(db)
        bot.guilds = [guild_for(123), guild_for(124), guild_for(125)]
        cog = Authorization(bot)
        cog.reject = AsyncMock()
        await cog.on_ready()
        assert bot.authorized == {123, 124}
        assert cog.reject.await_args.args[0].id == 125
        await db.close()
    asyncio.run(go())


def test_concurrent_rejections_do_not_duplicate_a_successful_leave_and_new_join_can_be_rejected():
    async def go():
        bot = bot_for(None)
        cog = Authorization(bot)
        cog.find_inviter, cog.notify_guild, cog.alert_developers = AsyncMock(), AsyncMock(), AsyncMock()
        guild = guild_for()
        await asyncio.gather(cog.reject(guild), cog.reject(guild))
        guild.leave.assert_awaited_once()
        cog.alert_developers.assert_awaited_once()
        new_join = guild_for()
        await cog.reject(new_join)
        new_join.leave.assert_awaited_once()
        assert cog.alert_developers.await_count == 2
    asyncio.run(go())


def test_unauthorized_join_persists_then_sends_actual_outcome(tmp_path, monkeypatch):
    from core import incidents
    monkeypatch.setattr(incidents, "recipients", lambda bot: {11})

    async def go():
        db = Database(str(tmp_path / "join.db"))
        await db.connect()
        bot = bot_for(db)
        bot.get_user = lambda uid: SimpleNamespace(send=AsyncMock())
        bot.incident_manager = incidents.IncidentManager(bot)
        cog = Authorization(bot)
        cog.find_inviter = AsyncMock(return_value=None)
        cog.notify_guild = AsyncMock()
        cog.alert_developers = AsyncMock()
        guild = guild_for()

        async def leave():
            row = await db.fetchone("SELECT * FROM security_incidents")
            assert row["state"] == "containing"
            assert row["action"] == "Leave not confirmed; outcome unknown."
        guild.leave.side_effect = leave
        await cog.reject(guild)
        row = await db.fetchone("SELECT * FROM security_incidents")
        assert row["action"] == "Left the server." and row["state"] == "open"
        assert cog.alert_developers.await_args.kwargs["include_dms"] is False
        bot.incident_manager._send = AsyncMock()
        await bot.incident_manager.deliver_due()
        sent = bot.incident_manager._send.await_args.args[1]
        assert sent["action"] == "Left the server."
        assert sent["actor_id"] is None
        await db.close()
    asyncio.run(go())


def test_approval_window_notifies_before_leaving_and_authorization_wakes_waiter(monkeypatch):
    monkeypatch.setattr("cogs.authorization.APPROVAL_GRACE_SECONDS", 120)

    async def go():
        bot = bot_for(SimpleNamespace(execute=AsyncMock()))
        bot.incident_manager = SimpleNamespace(record=AsyncMock(return_value="incident"), finalize=AsyncMock())
        cog = Authorization(bot)
        cog.find_inviter, cog.notify_guild = AsyncMock(return_value=None), AsyncMock()
        notified = asyncio.Event()
        async def alert(guild, inviter, *, action, include_dms):
            if "Awaiting approval" in action:
                notified.set()
        cog.alert_developers = AsyncMock(side_effect=alert)
        guild = guild_for()
        task = asyncio.create_task(cog.reject(guild))
        await asyncio.wait_for(notified.wait(), timeout=1)
        guild.leave.assert_not_awaited()
        assert not task.done()
        assert bot.incident_manager.record.await_args.kwargs["defer_seconds"] > 120
        assert await cog.authorize(guild.id, 42) is True
        await asyncio.wait_for(task, timeout=1)
        guild.leave.assert_not_awaited()
        bot.incident_manager.finalize.assert_awaited_once_with("incident", "Authorized before leaving; the bot stayed.")
        assert guild.id not in cog._pending_approvals
    asyncio.run(go())


def test_expired_window_leaves_and_restart_does_not_reset_join_time(monkeypatch):
    monkeypatch.setattr("cogs.authorization.APPROVAL_GRACE_SECONDS", 0.08)

    async def go():
        bot = bot_for(None)
        cog = Authorization(bot)
        cog.find_inviter, cog.notify_guild, cog.alert_developers = AsyncMock(), AsyncMock(), AsyncMock()
        guild = guild_for()
        task = asyncio.create_task(cog.reject(guild))
        await asyncio.sleep(0.01)
        guild.leave.assert_not_awaited()
        await asyncio.wait_for(task, timeout=1)
        guild.leave.assert_awaited_once()
        assert cog.alert_developers.await_count == 2  # pending, then actual outcome
        assert "Awaiting approval" in cog.alert_developers.await_args_list[0].kwargs["action"]
        assert cog.alert_developers.await_args_list[-1].kwargs["action"] == "Left the server."
        old_guild = guild_for(456)
        old_guild.me.joined_at = discord.utils.utcnow() - timedelta(seconds=121)
        await asyncio.wait_for(cog.reject(old_guild), timeout=0.5)
        old_guild.leave.assert_awaited_once()
    asyncio.run(go())


def test_startup_sweep_runs_approval_windows_concurrently(tmp_path, monkeypatch):
    import cogs.authorization as auth
    monkeypatch.setattr(auth, "developers", AsyncMock(return_value=set()))

    async def go():
        db = Database(str(tmp_path / "concurrent.db"))
        await db.connect()
        await db.execute("INSERT INTO config VALUES(?,?,?)", 0, "authorization_init", json.dumps({"complete": True}))
        bot = bot_for(db)
        bot.guilds = [guild_for(123), guild_for(124)]
        cog = Authorization(bot)
        started = []
        both = asyncio.Event()
        finish = asyncio.Event()
        async def reject(g):
            started.append(g.id)
            if len(started) == 2:
                both.set()
            await finish.wait()
        cog.reject = reject
        task = asyncio.create_task(cog.on_ready())
        await asyncio.wait_for(both.wait(), timeout=1)
        assert set(started) == {123, 124}
        finish.set()
        await asyncio.wait_for(task, timeout=1)
        await db.close()
    asyncio.run(go())


def test_pending_leave_does_not_remove_a_new_join_or_a_removed_bot():
    async def go():
        bot = bot_for(None)
        cog = Authorization(bot)
        cog.find_inviter, cog.notify_guild, cog.alert_developers = AsyncMock(), AsyncMock(), AsyncMock()
        original = guild_for()
        rejoined = guild_for()
        bot.get_guild = lambda gid: rejoined
        await cog.reject(original)
        original.leave.assert_not_awaited()
        assert "rejoined" in cog.alert_developers.await_args.kwargs["action"]
        bot.get_guild = lambda gid: None
        await cog.reject(original)
        original.leave.assert_not_awaited()
        assert "already removed" in cog.alert_developers.await_args.kwargs["action"]
    asyncio.run(go())


def test_grace_notice_and_revocation_notice_are_distinct():
    async def go():
        cog = Authorization(bot_for(None))
        channel = SimpleNamespace(permissions_for=lambda member: SimpleNamespace(send_messages=True, embed_links=True),
                                  send=AsyncMock())
        guild = guild_for()
        guild.system_channel, guild.text_channels = channel, []
        await cog.notify_guild(guild)
        text = channel.send.await_args.kwargs["embed"].description
        assert "120 seconds" in text and "If I have already left" in text
        await cog.notify_guild(guild, grace=False)
        text = channel.send.await_args.kwargs["embed"].description
        assert "revoked" in text and "120 seconds" not in text
    asyncio.run(go())
