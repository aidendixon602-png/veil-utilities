"""Server authorization: the bot only works in servers a developer has approved.

Flow
- Someone invites the bot to a server that isn't authorized -> the bot posts a notice there,
  alerts every developer (DM + optional log channel) with an "Authorize" button, and leaves.
- A developer authorizes the server (button or `authorize <server id>`), then the server re-invites the bot.
- Developers are the IDs in OWNER_IDS (or the application owner/team if that's empty).
"""
from __future__ import annotations

import logging
import os

import discord
from discord.ext import commands

from core.utils import Colors, Context, UserError, chunk_embeds, now, ts

log = logging.getLogger("veil.auth")


async def developers(bot) -> set[int]:
    if bot.owner_ids:
        return set(bot.owner_ids)
    if bot.owner_id:
        return {bot.owner_id}
    app = await bot.application_info()
    if app.team:
        return {m.id for m in app.team.members}
    return {app.owner.id}


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
        if not await bot.is_owner(it.user):
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
        self.support = os.getenv("SUPPORT_URL")
        self.log_channel = int(os.getenv("AUTH_LOG_CHANNEL") or 0)

    async def cog_load(self):
        self.bot.add_dynamic_items(AuthorizeButton)

    async def cog_check(self, ctx: Context) -> bool:
        if not await self.bot.is_owner(ctx.author):
            raise commands.NotOwner()
        return True

    # ------------------------------------------------------------ core
    async def authorize(self, guild_id: int, by: int, note: str | None = None) -> bool:
        if guild_id in self.bot.authorized:
            return False
        self.bot.authorized.add(guild_id)
        await self.bot.db.execute("INSERT OR REPLACE INTO authorized VALUES(?,?,?,?)", guild_id, by, now(), note)
        return True

    async def unauthorize(self, guild_id: int) -> bool:
        if guild_id not in self.bot.authorized:
            return False
        self.bot.authorized.discard(guild_id)
        await self.bot.db.execute("DELETE FROM authorized WHERE guild_id=?", guild_id)
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

    async def alert_developers(self, guild: discord.Guild, inviter: discord.abc.User | None):
        e = discord.Embed(color=Colors.warn, title="Unauthorized server tried to add the bot",
                          timestamp=discord.utils.utcnow())
        e.add_field(name="Server", value=f"{guild.name}\n`{guild.id}`")
        e.add_field(name="Owner", value=f"<@{guild.owner_id}>\n`{guild.owner_id}`")
        e.add_field(name="Members", value=f"{guild.member_count:,}")
        if inviter:
            e.add_field(name="Invited by", value=f"{inviter} (`{inviter.id}`)")
        if guild.icon:
            e.set_thumbnail(url=guild.icon.url)
        e.set_footer(text="Authorize, then they need to re-invite the bot")
        view = discord.ui.View(timeout=None)
        view.add_item(AuthorizeButton(guild.id))
        dests = []
        ch = self.bot.get_channel(self.log_channel) if self.log_channel else None
        if ch:
            dests.append(ch)
        for uid in await developers(self.bot):
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
        try:
            async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.bot_add):
                if entry.target and entry.target.id == self.bot.user.id:
                    return entry.user
        except discord.HTTPException:
            pass
        return None

    async def reject(self, guild: discord.Guild):
        inviter = await self.find_inviter(guild)
        await self.notify_guild(guild)
        await self.alert_developers(guild, inviter)
        try:
            await guild.leave()
        except discord.HTTPException:
            pass
        log.info("Left unauthorized guild %s (%s)", guild.name, guild.id)

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
        if self.swept or not self.bot.auth_required:
            return
        self.swept = True
        devs = await developers(self.bot)
        # First run: grandfather every server the bot is already in, so turning this on doesn't wipe you out.
        first_run = (await self.bot.db.fetchone("SELECT COUNT(*) c FROM authorized"))["c"] == 0
        if first_run and os.getenv("AUTH_GRANDFATHER", "true").lower() != "false":
            for g in self.bot.guilds:
                await self.authorize(g.id, 0, "grandfathered on first run")
            log.info("Authorization: grandfathered %d existing servers", len(self.bot.guilds))
            return
        for g in list(self.bot.guilds):
            if self.bot.is_authorized(g.id):
                continue
            if g.owner_id in devs:
                await self.authorize(g.id, g.owner_id, "developer-owned")
                continue
            await self.reject(g)

    # ------------------------------------------------------------ commands
    @commands.command(name="authorize", hidden=True, aliases=["auth", "whitelistserver"])
    async def authorize_server(self, ctx: Context, guild_id: int, *, note: str = None):
        """Authorize a server ID to use the bot"""
        if not await self.authorize(guild_id, ctx.author.id, note):
            raise UserError(f"`{guild_id}` is already authorized")
        await ctx.approve(f"Authorized `{guild_id}` — they can invite the bot now")

    @commands.command(name="unauthorize", hidden=True, aliases=["deauth", "unauth"])
    async def unauthorize_server(self, ctx: Context, guild_id: int):
        """Revoke a server's authorization (the bot leaves it)"""
        if not await self.unauthorize(guild_id):
            raise UserError(f"`{guild_id}` isn't authorized")
        guild = self.bot.get_guild(guild_id)
        if guild:
            await self.notify_guild(guild)
            await guild.leave()
        await ctx.approve(f"Revoked `{guild_id}`" + (f" and left **{guild.name}**" if guild else ""))

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
