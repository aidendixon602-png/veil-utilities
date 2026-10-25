"""Ticket system with a button panel, claim/close, and text transcripts."""
from __future__ import annotations

import io

import discord
from discord.ext import commands

from core.utils import Colors, Context, UserError, has_perms, now


class TicketPanel(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Open ticket", emoji="🎫", style=discord.ButtonStyle.primary, custom_id="ticket:open")
    async def open(self, it: discord.Interaction, _):
        await self.cog.open_ticket(it)


class TicketControls(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Claim", emoji="🙋", style=discord.ButtonStyle.secondary, custom_id="ticket:claim")
    async def claim(self, it: discord.Interaction, _):
        if not await self.cog.is_support(it.user):
            return await it.response.send_message("Only support staff can claim tickets.", ephemeral=True)
        await it.response.send_message(embed=discord.Embed(color=Colors.success,
                                                           description=f"🙋 {it.user.mention} claimed this ticket"))

    @discord.ui.button(label="Close", emoji="🔒", style=discord.ButtonStyle.danger, custom_id="ticket:close")
    async def close(self, it: discord.Interaction, _):
        await self.cog.close_ticket(it.channel, it.user, it)


class Tickets(commands.Cog):
    """Private support tickets"""
    emoji = "🎫"

    def __init__(self, bot):
        self.bot = bot
        self._opening: set[tuple[int, int]] = set()  # (guild, user) mid-creation: stops double-click duplicates
        self._closing: set[int] = set()              # channels mid-close: stops double transcripts

    async def cog_load(self):
        self.bot.add_view(TicketPanel(self))
        self.bot.add_view(TicketControls(self))

    async def cfg(self, gid):
        return await self.bot.db.get_config(gid, "tickets", {"support": [], "max": 1})

    async def is_support(self, member: discord.Member) -> bool:
        if member.id == member.guild.owner_id:
            return True
        from core import perms as _perms
        if _perms.is_bot_developer(member, self.bot):
            return True
        cfg = await self.cfg(member.guild.id)
        if any(r.id in cfg.get("support", []) for r in getattr(member, "roles", [])):
            return True
        pl_cfg = await self.bot.db.get_config(member.guild.id, "permlevels")
        if _perms.rank_of(pl_cfg, member) >= 1:
            return True
        return bool(member.guild_permissions.manage_channels)

    async def _ticket_staff(self, ctx: Context):
        """Ticket management commands must only work *inside a ticket* — otherwise a support role could
        `ticket add @me` in any staff channel and the bot would grant them access."""
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        row = await self.bot.db.fetchone("SELECT 1 FROM tickets WHERE channel_id=?", ctx.channel.id)
        if not row:
            raise UserError("This isn't a ticket channel")
        if not await self.is_support(ctx.author):
            raise UserError("Only support staff can do that")

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        # a ticket channel deleted by hand would otherwise count as "open" forever and lock the user out
        await self.bot.db.execute("DELETE FROM tickets WHERE channel_id=?", channel.id)

    async def open_ticket(self, it: discord.Interaction):
        key = (it.guild_id or 0, it.user.id)
        if it.guild is None or key in self._opening:
            return await it.response.send_message("Your ticket is already being created.", ephemeral=True)
        self._opening.add(key)
        try:
            await self._open_ticket(it)
        finally:
            self._opening.discard(key)

    async def _open_ticket(self, it: discord.Interaction):
        guild, user = it.guild, it.user
        cfg = await self.cfg(guild.id)
        open_count = (await self.bot.db.fetchone("SELECT COUNT(*) c FROM tickets WHERE guild_id=? AND user_id=?",
                                                 guild.id, user.id))["c"]
        if open_count >= cfg.get("max", 1):
            # drop rows whose channel no longer exists (e.g. deleted while the bot was offline), then recount
            rows = await self.bot.db.fetchall("SELECT channel_id FROM tickets WHERE guild_id=? AND user_id=?",
                                              guild.id, user.id)
            for r in rows:
                if not guild.get_channel(r["channel_id"]):
                    await self.bot.db.execute("DELETE FROM tickets WHERE channel_id=?", r["channel_id"])
            open_count = (await self.bot.db.fetchone("SELECT COUNT(*) c FROM tickets WHERE guild_id=? AND user_id=?",
                                                     guild.id, user.id))["c"]
        if open_count >= cfg.get("max", 1):
            return await it.response.send_message("You already have an open ticket.", ephemeral=True)
        category = guild.get_channel(cfg.get("category") or 0)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
        }
        for rid in cfg["support"]:
            role = guild.get_role(rid)
            if role:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        try:
            ch = await guild.create_text_channel(f"ticket-{user.name}"[:90],
                                                 category=category if isinstance(category, discord.CategoryChannel) else None,
                                                 overwrites=overwrites, topic=f"Ticket for {user} ({user.id})")
        except discord.HTTPException:
            return await it.response.send_message("I couldn't create a ticket channel.", ephemeral=True)
        await self.bot.db.execute("INSERT INTO tickets VALUES(?,?,?,?)", ch.id, guild.id, user.id, now())
        pings = " ".join(f"<@&{r}>" for r in cfg["support"]) if cfg.get("ping") else ""
        e = discord.Embed(color=Colors.main, title="Ticket opened",
                          description=cfg.get("message") or "Support will be with you shortly. Describe your issue below.")
        await ch.send(content=f"{user.mention} {pings}", embed=e, view=TicketControls(self),
                      allowed_mentions=discord.AllowedMentions(users=True, roles=True))
        await it.response.send_message(f"Created {ch.mention}", ephemeral=True)

    async def close_ticket(self, channel: discord.TextChannel, closer: discord.Member, it: discord.Interaction = None):
        row = await self.bot.db.fetchone("SELECT * FROM tickets WHERE channel_id=?", channel.id)
        if not row:
            if it:
                return await it.response.send_message("This isn't a ticket.", ephemeral=True)
            raise UserError("This isn't a ticket")
        if closer.id != row["user_id"] and not await self.is_support(closer):
            if it:
                return await it.response.send_message("You can't close this ticket.", ephemeral=True)
            raise UserError("You can't close this ticket")
        if channel.id in self._closing:
            if it:
                return await it.response.send_message("This ticket is already closing.", ephemeral=True)
            raise UserError("This ticket is already closing")
        self._closing.add(channel.id)
        try:
            await self._close_ticket(channel, closer, row, it)
        finally:
            self._closing.discard(channel.id)

    async def _close_ticket(self, channel, closer, row, it):
        if it:
            await it.response.send_message(embed=discord.Embed(color=Colors.warn, description="🔒 Closing ticket…"))
        lines = []
        async for m in channel.history(limit=None, oldest_first=True):
            att = " ".join(a.url for a in m.attachments)
            lines.append(f"[{m.created_at:%Y-%m-%d %H:%M}] {m.author}: {m.content} {att}".rstrip())
        transcript = "\n".join(lines).encode()
        cfg = await self.cfg(channel.guild.id)
        log = channel.guild.get_channel(cfg.get("log") or 0)
        e = discord.Embed(color=Colors.neutral, title=f"Ticket closed: #{channel.name}",
                          description=f"Opened by <@{row['user_id']}> • closed by {closer.mention}")
        for dest in filter(None, [log, channel.guild.get_member(row["user_id"])]):
            try:
                await dest.send(embed=e, file=discord.File(io.BytesIO(transcript), f"{channel.name}.txt"))
            except discord.HTTPException:
                pass
        await self.bot.db.execute("DELETE FROM tickets WHERE channel_id=?", channel.id)
        try:
            await channel.delete(reason=f"ticket closed by {closer}")
        except discord.NotFound:
            pass
        except discord.HTTPException:
            await channel.send("I couldn't delete this channel (I need **Manage Channels**). "
                               "The transcript was saved, so staff can delete it by hand.")

    # ------------------------------------------------------------ commands
    @commands.group(invoke_without_command=True)
    async def ticket(self, ctx: Context):
        """Ticket system"""
        await ctx.send_help(ctx.command)

    @ticket.command(name="panel", aliases=["send", "setup"])
    @has_perms(manage_guild=True)
    async def t_panel(self, ctx: Context, channel: discord.TextChannel = None, *, text: str = None):
        """Send the ticket panel"""
        e = discord.Embed(title="Support", color=Colors.main,
                          description=text or "Click the button below to open a private ticket with staff.")
        await (channel or ctx.channel).send(embed=e, view=TicketPanel(self))
        if channel:
            await ctx.approve(f"Panel sent to {channel.mention}")

    async def _set(self, ctx, **kw):
        cfg = await self.cfg(ctx.guild.id)
        cfg.update(kw)
        await self.bot.db.set_config(ctx.guild.id, "tickets", cfg)
        return cfg

    @ticket.command(name="category")
    @has_perms(manage_guild=True)
    async def t_category(self, ctx, *, category: discord.CategoryChannel):
        """Category new tickets are created in"""
        await self._set(ctx, category=category.id)
        await ctx.approve(f"Tickets will open in **{category.name}**")

    @ticket.command(name="support")
    @has_perms(manage_guild=True)
    async def t_support(self, ctx, role: discord.Role):
        """Toggle a support role"""
        cfg = await self.cfg(ctx.guild.id)
        lst = cfg["support"]
        lst.remove(role.id) if role.id in lst else lst.append(role.id)
        await self._set(ctx, support=lst)
        await ctx.approve(f"Support roles: {', '.join(f'<@&{r}>' for r in lst) or 'none'}")

    @ticket.command(name="logs")
    @has_perms(manage_guild=True)
    async def t_logs(self, ctx, channel: discord.TextChannel = None):
        """Where transcripts are posted"""
        await self._set(ctx, log=channel.id if channel else None)
        await ctx.approve(f"Transcripts → {channel.mention if channel else 'nowhere (DM only)'}")

    @ticket.command(name="message")
    @has_perms(manage_guild=True)
    async def t_message(self, ctx, *, message: str):
        """Message shown inside new tickets"""
        await self._set(ctx, message=message)
        await ctx.approve("Updated ticket message")

    @ticket.command(name="ping")
    @has_perms(manage_guild=True)
    async def t_ping(self, ctx, state: bool):
        """Ping support roles on new tickets"""
        await self._set(ctx, ping=state)
        await ctx.approve(f"Ping support: **{state}**")

    @ticket.command(name="close")
    async def t_close(self, ctx: Context):
        """Close the current ticket"""
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        await self.close_ticket(ctx.channel, ctx.author)

    @ticket.command(name="add")
    async def t_add(self, ctx: Context, member: discord.Member):
        """Add someone to this ticket"""
        await self._ticket_staff(ctx)
        await ctx.channel.set_permissions(member, view_channel=True, send_messages=True)
        await ctx.approve(f"Added {member.mention}")

    @ticket.command(name="remove")
    async def t_remove(self, ctx: Context, member: discord.Member):
        """Remove someone from this ticket"""
        await self._ticket_staff(ctx)
        if member == ctx.guild.me:
            raise UserError("I need to stay in the ticket to manage it")
        await ctx.channel.set_permissions(member, overwrite=None)
        await ctx.approve(f"Removed {member.mention}")

    @ticket.command(name="rename")
    async def t_rename(self, ctx: Context, *, name: str):
        """Rename this ticket"""
        await self._ticket_staff(ctx)
        await ctx.channel.edit(name=name[:100])
        await ctx.approve(f"Renamed to **{name}**")


async def setup(bot):
    await bot.add_cog(Tickets(bot))
