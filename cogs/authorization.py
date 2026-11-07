"""Server authorization: the bot only works in servers a developer has approved.

Flow
- Someone invites the bot to a server that isn't authorized -> the bot posts a notice there,
  alerts every developer (DM + optional log channel) with an "Authorize" button, and leaves.
- A developer authorizes the server (button or `authorize <server id>`), then the server re-invites the bot.
- Developers are the owner (or team) of the bot's Discord application, plus any IDs in OWNER_IDS.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import weakref
from datetime import timedelta

import discord
from discord.ext import commands

from core import devchannels
from core.config import settings
from core.utils import Colors, Context, UserError, chunk_embeds, now, ts

log = logging.getLogger("veil.auth")


async def developers(bot) -> set[int]:
    from core import perms as _perms
    dev_set = set(_perms.BOT_DEVELOPERS)
    if bot.owner_ids:
        dev_set.update(bot.owner_ids)
    if bot.owner_id:
        dev_set.add(bot.owner_id)
    if settings.owner_ids:
        dev_set.update(settings.owner_ids)
    try:
        app = await asyncio.wait_for(bot.application_info(), timeout=5)
        if app.team:
            dev_set.update(m.id for m in app.team.members)
        elif app.owner:
            dev_set.add(app.owner.id)
    except Exception:
        pass
    return dev_set


class AuthorizeButton(discord.ui.DynamicItem[discord.ui.Button], template=r"auth:(?P<gid>\d+)"):
    def __init__(self, guild_id: int):
        super().__init__(discord.ui.Button(label="Authorize", emoji="✅", style=discord.ButtonStyle.success,
                                           custom_id=f"auth:{guild_id}"))
        self.guild_id = guild_id

    @classmethod
    async def from_custom_id(cls, it, item, match):
        return cls(int(match["gid"]))

    async def callback(self, it: discord.Interaction):
        bot = it.client
        from core import perms as _perms
        if not (_perms.is_bot_developer(it.user, bot) or await bot.is_owner(it.user)):
            return await it.response.send_message("Only bot developers can authorize servers.", ephemeral=True)
        cog: Authorization = bot.get_cog("Authorization")
        added = await cog.authorize(self.guild_id, it.user.id, "via alert button")
        self.item.disabled = True
        self.item.label = "Authorized"
        await it.response.edit_message(view=self.view)
        await it.followup.send(f"✅ Server `{self.guild_id}` {'authorized' if added else 'was already authorized'}. "
                               "They can re-invite the bot now.", ephemeral=True)


class Authorization(commands.Cog):
    """Developer-only server whitelist"""
    emoji = "🔐"

    def __init__(self, bot):
        self.bot = bot
        self.swept = False
        self.support = settings.support_url
        self._auth_lock = asyncio.Lock()
        self._reject_locks: weakref.WeakValueDictionary[int, asyncio.Lock] = weakref.WeakValueDictionary()
        self._left_guilds: dict[int, tuple[object, float]] = {}
        self._sweep_lock = asyncio.Lock()

    async def cog_load(self):
        self.bot.add_dynamic_items(AuthorizeButton)

    async def cog_check(self, ctx: Context) -> bool:
        from core import perms as _perms
        if not (_perms.is_bot_developer(ctx.author, self.bot) or await self.bot.is_owner(ctx.author)):
            raise commands.NotOwner()
        return True

    # ------------------------------------------------------------ core
    async def authorize(self, guild_id: int, by: int, note: str | None = None) -> bool:
        async with self._auth_lock:
            if guild_id in self.bot.authorized:
                return False
            await self.bot.db.execute("INSERT OR REPLACE INTO authorized VALUES(?,?,?,?)", guild_id, by, now(), note)
            self.bot.authorized.add(guild_id)
            return True

    async def unauthorize(self, guild_id: int) -> bool:
        async with self._auth_lock:
            if guild_id not in self.bot.authorized:
                return False
            await self.bot.db.execute("DELETE FROM authorized WHERE guild_id=?", guild_id)
            self.bot.authorized.discard(guild_id)
            return True

    async def notify_guild(self, guild: discord.Guild):
        text = ("This server isn't authorized to use **veil utilities**, so I'm leaving.\n"
                "Ask a bot developer to authorize this server ID, then invite me again:\n"
                f"```{guild.id}```")
        if self.support:
            text += f"\nSupport: {self.support}"
        embed = discord.Embed(color=Colors.error, title="🔐 Server not authorized", description=text)
        targets = [guild.system_channel] + list(guild.text_channels)
        for ch in targets:
            if ch and ch.permissions_for(guild.me).send_messages and ch.permissions_for(guild.me).embed_links:
                try:
                    await ch.send(embed=embed)
                    return
                except discord.HTTPException:
                    continue

    async def alert_developers(self, guild: discord.Guild, inviter: discord.abc.User | None, *,
                               action: str = "Unknown", include_dms: bool = True):
        e = discord.Embed(color=Colors.warn, title="Unauthorized server tried to add the bot",
                          timestamp=discord.utils.utcnow())
        e.add_field(name="Server", value=f"{guild.name}\n`{guild.id}`")
        e.add_field(name="Owner", value=f"<@{guild.owner_id}>\n`{guild.owner_id}`")
        e.add_field(name="Members", value=f"{guild.member_count:,}" if guild.member_count is not None else "Unknown")
        if inviter:
            e.add_field(name="Invited by", value=f"{inviter} (`{inviter.id}`)")
        else:
            e.add_field(name="Invited by", value="Unknown")
        e.add_field(name="Action", value=action, inline=False)
        if guild.icon:
            e.set_thumbnail(url=guild.icon.url)
        e.set_footer(text="Authorize, then they need to re-invite the bot")
        view = discord.ui.View(timeout=None)
        view.add_item(AuthorizeButton(guild.id))
        dests = []
        ch = devchannels.channel_for(self.bot, "auth")
        if ch:
            dests.append(ch)
        for uid in await developers(self.bot) if include_dms else ():
            try:
                dests.append(self.bot.get_user(uid) or await self.bot.fetch_user(uid))
            except discord.HTTPException:
                pass
        for d in dests:
            try:
                await d.send(embed=e, view=view)
            except discord.HTTPException:
                pass

    async def find_inviter(self, guild: discord.Guild):
        # A previous bot-add entry is not evidence of the current invitation. Require
        # both a recent entry and the current bot member's join time; otherwise say unknown.
        joined = getattr(guild.me, "joined_at", None)
        if joined is None:
            return None
        current = discord.utils.utcnow()
        if joined.tzinfo is None or not (current - timedelta(minutes=2) <= joined <= current + timedelta(seconds=5)):
            return None
        try:
            async for entry in guild.audit_logs(limit=10, action=discord.AuditLogAction.bot_add):
                created = entry.created_at
                if (entry.target and entry.target.id == self.bot.user.id and entry.user
                        and created and created.tzinfo is not None
                        and joined - timedelta(seconds=5) <= created <= joined + timedelta(seconds=5)
                        and current - timedelta(minutes=2) <= created <= current + timedelta(seconds=5)):
                    return entry.user
        except discord.HTTPException:
            log.warning("Cannot inspect bot-add audit log in guild %s; inviter unknown", guild.id)
        return None

    async def reject(self, guild: discord.Guild):
        lock = self._reject_locks.setdefault(guild.id, asyncio.Lock())
        async with lock:
            cutoff = time.monotonic() - 300
            self._left_guilds = {gid: item for gid, item in self._left_guilds.items() if item[1] >= cutoff}
            previous = self._left_guilds.get(guild.id)
            join_time = getattr(guild.me, "joined_at", None)
            if self.bot.is_authorized(guild.id) or (join_time and previous and previous[0] == join_time):
                return
            inviter = None
            try:
                inviter = await asyncio.wait_for(self.find_inviter(guild), timeout=5)
            except Exception:
                log.exception("Could not identify inviter for unauthorized guild %s", guild.id)
            try:
                await asyncio.wait_for(self.notify_guild(guild), timeout=5)
            except Exception:
                log.exception("Could not send unauthorized-guild notice to %s", guild.id)
            # Authorization may have been granted while the audit/notice was in flight.
            if self.bot.is_authorized(guild.id):
                return
            # Persist before leaving, but do not allow a slow or failed DB to block containment.
            # If the process stops before finalization, the queued outcome remains explicitly unknown.
            manager = getattr(self.bot, "incident_manager", None)
            incident_id = None
            queued = False
            if manager:
                try:
                    incident_id = await asyncio.wait_for(manager.record(
                        kind="unauthorized_server", severity="high", guild_id=guild.id,
                        actor_id=getattr(inviter, "id", None),
                        summary="The bot joined an unauthorized server.",
                        action="Leave not confirmed; outcome unknown.", defer_seconds=45,
                        details={"Server": guild.name, "Owner ID": guild.owner_id,
                                 "Inviter": "verified audit entry" if inviter else "unknown"}), timeout=3)
                    from core.incidents import recipients
                    queued = bool(recipients(self.bot))
                except Exception:
                    log.exception("Failed to persist unauthorized-server incident for %s; using direct alerts", guild.id)
            # Serialize the final authorization check with approval while leaving.
            # Notification and audit failures never prevent containment.
            async with self._auth_lock:
                if self.bot.is_authorized(guild.id):
                    action = "Authorized before leaving; the bot stayed."
                else:
                    try:
                        await asyncio.wait_for(guild.leave(), timeout=15)
                    except Exception:
                        action = "Leave failed or timed out; the bot may still be in the server."
                        log.exception("Failed to leave unauthorized guild %s (%s)", guild.name, guild.id)
                    else:
                        action = "Left the server."
                        if join_time is not None:
                            self._left_guilds[guild.id] = (join_time, time.monotonic())
                        log.warning("Left unauthorized guild %s (%s)", guild.name, guild.id)
            if incident_id is not None:
                try:
                    await asyncio.wait_for(manager.finalize(incident_id, action), timeout=3)
                except Exception:
                    queued = False
                    log.exception("Failed to finalize unauthorized-server incident %s", incident_id)
            try:
                await asyncio.wait_for(self.alert_developers(guild, inviter, action=action, include_dms=not queued),
                                       timeout=15)
            except Exception:
                log.exception("Could not alert developers about unauthorized guild %s", guild.id)

    # ------------------------------------------------------------ events
    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        if self.bot.is_authorized(guild.id):
            return
        devs = await developers(self.bot)
        if guild.owner_id in devs:  # developers' own servers are always allowed
            await self.authorize(guild.id, guild.owner_id, "developer-owned")
            return
        await self.reject(guild)

    @commands.Cog.listener()
    async def on_ready(self):
        async with self._sweep_lock:
            if self.swept or not self.bot.auth_required:
                return
            await self._sweep_authorization()

    async def _sweep_authorization(self):
        devs = await developers(self.bot)
        # Record the enrollment snapshot before making changes, so a crash during
        # grandfathering resumes that same snapshot rather than approving new joins.
        row = await self.bot.db.fetchone(
            "SELECT data FROM config WHERE guild_id=? AND module=?", 0, "authorization_init")
        if row:
            state = json.loads(row["data"])
        else:
            count = (await self.bot.db.fetchone("SELECT COUNT(*) c FROM authorized"))["c"]
            pending = [g.id for g in self.bot.guilds] if count == 0 and settings.auth_grandfather else []
            state = {"complete": False, "pending": pending}
            await self.bot.db.execute("INSERT INTO config(guild_id,module,data) VALUES(?,?,?) "
                                      "ON CONFLICT(guild_id,module) DO NOTHING", 0, "authorization_init",
                                      json.dumps(state))
            row = await self.bot.db.fetchone(
                "SELECT data FROM config WHERE guild_id=? AND module=?", 0, "authorization_init")
            state = json.loads(row["data"])
        if not state.get("complete"):
            for guild_id in state.get("pending", []):
                await self.authorize(guild_id, 0, "grandfathered on first run")
            state = {"complete": True}
            await self.bot.db.execute("UPDATE config SET data=? WHERE guild_id=? AND module=?",
                                      json.dumps(state), 0, "authorization_init")
            log.info("Authorization: initial enrollment completed")
        for g in list(self.bot.guilds):
            if self.bot.is_authorized(g.id):
                continue
            if g.owner_id in devs:
                await self.authorize(g.id, g.owner_id, "developer-owned")
                continue
            await self.reject(g)
        self.swept = True

    # ------------------------------------------------------------ commands
    @commands.command(name="authorize", hidden=True, aliases=["auth", "whitelistserver"])
    async def authorize_server(self, ctx: Context, guild_id: int, *, note: str = None):
        """Authorize a server ID to use the bot"""
        if not await self.authorize(guild_id, ctx.author.id, note):
            raise UserError(f"`{guild_id}` is already authorized")
        await ctx.approve(f"authorized `{guild_id}` · they can invite the bot now")

    @commands.command(name="unauthorize", hidden=True, aliases=["deauth", "unauth"])
    async def unauthorize_server(self, ctx: Context, guild_id: int):
        """Revoke a server's authorization (the bot leaves it)"""
        if not await self.unauthorize(guild_id):
            raise UserError(f"`{guild_id}` isn't authorized")
        guild = self.bot.get_guild(guild_id)
        if guild:
            await self.notify_guild(guild)
            await guild.leave()
        await ctx.approve(f"revoked `{guild_id}`" + (f" and left **{guild.name}**" if guild else ""))

    @commands.command(hidden=True, aliases=["authlist", "authorizedservers"])
    async def authorized(self, ctx: Context):
        """List authorized servers"""
        rows = await self.bot.db.fetchall("SELECT * FROM authorized ORDER BY at DESC")
        lines = []
        for r in rows:
            g = self.bot.get_guild(r["guild_id"])
            name = f"**{g.name}**" if g else "*not joined*"
            by = f"<@{r['authorized_by']}>" if r["authorized_by"] else "system"
            lines.append(f"{name} `{r['guild_id']}` — by {by} {ts(r['at'])}" + (f"\n> {r['note']}" if r["note"] else ""))
        await ctx.paginate(chunk_embeds(f"Authorized servers ({len(rows)})", lines, 8, numbered=False))


async def setup(bot):
    await bot.add_cog(Authorization(bot))
