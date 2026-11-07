"""Persistent security incidents and an at-least-once, per-developer DM outbox.

Run one bot process per database. Discord sends and database writes cannot be atomic: a
crash between them can duplicate a DM. Delivery state is retained for inspection.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone

import discord

from core import perms, redact
from core.config import settings

log = logging.getLogger("veil.incidents")


def recipients(bot) -> set[int]:
    """Snapshot configured and already-discovered developer identities; no network lookup."""
    ids = set(settings.owner_ids) | set(perms.BOT_DEVELOPERS) | set(perms.DISCOVERED_DEVELOPERS)
    ids.update(getattr(bot, "owner_ids", None) or ())
    if getattr(bot, "owner_id", None):
        ids.add(bot.owner_id)
    return {int(uid) for uid in ids if int(uid) > 0}


def _clean(value, limit=1000):
    return redact.scrub(str(value))[:limit]


class IncidentManager:
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self._wake = asyncio.Event()
        self._task = None
        self._delivery_lock = asyncio.Lock()

    def start(self):
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="security-incident-outbox")

    async def close(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def record(self, *, kind, severity, guild_id=None, actor_id=None, summary, action,
                     details=None, notify=True, defer_seconds=0):
        incident_id = uuid.uuid4().hex
        created = time.time()
        targets = recipients(self.bot) if notify else set()
        delivery = {str(uid): {"status": "pending", "attempts": 0, "next_attempt": created, "last_error": None}
                    for uid in sorted(targets)}
        if notify and not targets:
            log.error("Incident %s has no configured or discovered developer recipients", incident_id)
        cleaned = {str(k)[:80]: _clean(v) for k, v in (details or {}).items()}
        await self.db.execute(
            "INSERT INTO security_incidents VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            incident_id, _clean(kind, 80), _clean(severity, 20), str(guild_id) if guild_id is not None else None,
            str(actor_id) if actor_id is not None else None, _clean(summary), _clean(action),
            json.dumps(cleaned), json.dumps(delivery), created,
            created + defer_seconds if delivery else None, "containing" if defer_seconds else "open")
        self._wake.set()
        return incident_id

    async def finalize(self, incident_id, action):
        """Publish a containment outcome; deferred records also recover after a crash."""
        await self.db.execute(
            "UPDATE security_incidents SET action=?, state='open', "
            "next_attempt=CASE WHEN next_attempt IS NULL THEN NULL ELSE ? END WHERE incident_id=?",
            _clean(action), time.time(), incident_id)
        self._wake.set()

    def _embed(self, row):
        embed = discord.Embed(title="Security incident", color=discord.Color.red(),
                              description=row["summary"],
                              timestamp=datetime.fromtimestamp(row["created_at"], timezone.utc))
        embed.add_field(name="Incident", value=f"`{row['incident_id']}` · {row['severity']}", inline=False)
        embed.add_field(name="Server ID", value=f"`{row['guild_id'] or 'unknown'}`")
        embed.add_field(name="User ID", value=f"`{row['actor_id'] or 'unknown'}`")
        embed.add_field(name="Action", value=row["action"] or "Unknown", inline=False)
        details = json.loads(row["details"])
        if details:
            text = "\n".join(f"{k}: {v}" for k, v in details.items())
            embed.add_field(name="Details", value=text[:1024], inline=False)
        return embed

    async def _send(self, uid, row):
        user = self.bot.get_user(uid) or await self.bot.fetch_user(uid)
        kwargs = {"embed": self._embed(row)}
        if row["kind"] == "unauthorized_server" and row["guild_id"]:
            from cogs.authorization import AuthorizeButton
            view = discord.ui.View(timeout=None)
            view.add_item(AuthorizeButton(int(row["guild_id"])))
            kwargs["view"] = view
        await user.send(**kwargs)

    async def deliver_due(self):
        # A single worker owns the JSON state; lock also prevents concurrent manual drains.
        async with self._delivery_lock:
            rows = await self.db.fetchall("SELECT * FROM security_incidents WHERE next_attempt IS NOT NULL "
                                          "AND next_attempt<=? ORDER BY next_attempt LIMIT 25", time.time())
            for row in rows:
                delivery = json.loads(row["deliveries"])
                for uid, item in delivery.items():
                    if item["status"] != "pending" or item["next_attempt"] > time.time():
                        continue
                    item["attempts"] += 1
                    try:
                        await asyncio.wait_for(self._send(int(uid), row), timeout=15)
                    except Exception as exc:
                        # No exception text in the database: HTTP/library errors can contain secrets.
                        item["last_error"] = type(exc).__name__
                        item["next_attempt"] = time.time() + min(3600, 30 * 2 ** min(item["attempts"] - 1, 7))
                        log.warning("Incident %s delivery to developer %s failed (%s); will retry",
                                    row["incident_id"], uid, item["last_error"])
                    else:
                        item.update(status="delivered", last_error=None, next_attempt=None)
                    pending = [v["next_attempt"] for v in delivery.values() if v["status"] == "pending"]
                    # Persist after each recipient, so a failure does not block or erase other outcomes.
                    await self.db.execute("UPDATE security_incidents SET deliveries=?, next_attempt=? WHERE incident_id=?",
                                          json.dumps(delivery), min(pending) if pending else None, row["incident_id"])

    async def _run(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                self._wake.clear()
                await self.deliver_due()
            except Exception:
                log.exception("Security incident delivery iteration failed; retrying")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=30)
            except asyncio.TimeoutError:
                pass
