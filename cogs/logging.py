"""Server event logging with Midtown Utilities-style join logs, bans, and command logging."""
from __future__ import annotations

import io

import discord
from discord.ext import commands

from core.utils import Colors, Context, UserError, has_perms, shorten, ts

TYPES = ("messages", "members", "roles", "channels", "voice", "server", "bans", "joinleave", "commands")

AUTO_CHANNELS: dict[str, list[str]] = {
    "joinleave": ["join-logs", "join-log", "joins-leaves", "member-logs"],
    "bans": ["bans", "ban-logs", "ban-log", "punishments", "mod-logs", "modlog"],
    "commands": ["command-logs", "cmd-logs", "commands", "command-log"],
    "messages": ["message-logs", "msg-logs"],
    "members": ["member-logs", "members"],
    "roles": ["role-logs", "roles"],
    "channels": ["channel-logs", "channels"],
    "server": ["veil-utilities-logging", "server-logs", "bot-logs", "logs"],
    "voice": ["voice-logs", "vc-logs"],
}


class BanLogView(discord.ui.View):
    def __init__(self, guild_id: int, user_id: int, is_banned: bool = True):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.user_id = user_id
        self.is_banned = is_banned
        self._sync()

    def _sync(self):
        self.toggle_btn.label = "Unban" if self.is_banned else "Reban"
        self.toggle_btn.style = discord.ButtonStyle.success if self.is_banned else discord.ButtonStyle.danger
        self.toggle_btn.custom_id = f"banlog:{self.guild_id}:{self.user_id}:{int(self.is_banned)}"

    @discord.ui.button(label="Unban", style=discord.ButtonStyle.success, custom_id="banlog:btn")
    async def toggle_btn(self, it: discord.Interaction, btn: discord.ui.Button):
        guild = it.guild or it.client.get_guild(self.guild_id)
        if not guild:
            return await it.response.send_message("Guild not found", ephemeral=True)
        member = guild.get_member(it.user.id)
        if not member:
            return await it.response.send_message("You are not in this guild", ephemeral=True)
        perms = member.guild_permissions
        if not (perms.administrator or perms.ban_members or member.id == guild.owner_id or await it.client.is_owner(member)):
            from core import perms as _perms
            cfg = await it.client.db.get_config(guild.id, "permlevels")
            if _perms.rank_of(cfg, member) < 3:
                return await it.response.send_message("You need Ban Members permission or rank 3+ to use this button", ephemeral=True)

        user = it.client.get_user(self.user_id)
        if user is None:
            try:
                user = await it.client.fetch_user(self.user_id)
            except Exception:
                user = discord.Object(id=self.user_id)

        if self.is_banned:
            try:
                await guild.unban(user, reason=f"Unbanned via button by {it.user}")
                self.is_banned = False
                self._sync()
                await it.response.edit_message(view=self)
                user_name = getattr(user, "name", str(self.user_id))
                await it.followup.send(f"{it.user.mention} unbanned **{user_name}** (`{self.user_id}`).")
            except discord.NotFound:
                user_name = getattr(user, "name", str(self.user_id))
                await it.response.send_message(f"Failed — **{user_name}** is not currently banned.", ephemeral=True)
            except discord.HTTPException as err:
                await it.response.send_message(f"Failed to unban: {err}", ephemeral=True)
        else:
            try:
                await guild.ban(user, reason=f"Rebanned via button by {it.user}")
                self.is_banned = True
                self._sync()
                await it.response.edit_message(view=self)
                user_name = getattr(user, "name", str(self.user_id))
                await it.followup.send(f"{it.user.mention} rebanned **{user_name}** (`{self.user_id}`).")
            except discord.HTTPException as err:
                await it.response.send_message(f"Failed to reban: {err}", ephemeral=True)


DEFAULT_LOG_CHANNELS = [
    ("join-logs", "joinleave", "Member join and leave notifications"),
    ("bans", "bans", "Ban, unban, and moderation punishment logs"),
    ("command-logs", "commands", "Command usage and staff activity logs"),
    ("veil-utilities-logging", "server", "Server events and bot updates"),
]


class Logging(commands.Cog):
    """Log server events to channels"""
    emoji = "📜"

    def __init__(self, bot):
        self.bot = bot

    async def ensure_default_channels(self, guild: discord.Guild) -> list[discord.TextChannel]:
        """Automatically create the default logging channels if they do not exist."""
        if not guild:
            return []
        me = getattr(guild, "me", None)
        if not me or not getattr(me.guild_permissions, "manage_channels", False):
            return []

        existing = {c.name.lower(): c for c in getattr(guild, "text_channels", [])}
        created = []

        # Find or create category "VEIL LOGS"
        category = discord.utils.get(getattr(guild, "categories", []), name="VEIL LOGS")
        missing_names = [name for name, _, _ in DEFAULT_LOG_CHANNELS if name not in existing]
        if not category and missing_names:
            try:
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(view_channel=False, send_messages=False),
                    me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, attach_files=True),
                }
                category = await guild.create_category("VEIL LOGS", overwrites=overwrites, reason="Veil Utilities: default logging category")
            except discord.HTTPException:
                category = None

        cfg = await self.bot.db.get_config(guild.id, "logs")
        changed = False

        for name, kind, topic in DEFAULT_LOG_CHANNELS:
            ch = existing.get(name)
            if not ch:
                try:
                    overwrites = {
                        guild.default_role: discord.PermissionOverwrite(view_channel=False, send_messages=False),
                        me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, attach_files=True),
                    }
                    ch = await guild.create_text_channel(
                        name,
                        category=category,
                        topic=f"Veil Utilities - {topic}",
                        overwrites=overwrites,
                        reason="Veil Utilities: default logging channel",
                    )
                    created.append(ch)
                    existing[name] = ch
                except discord.HTTPException:
                    continue

            if ch and not cfg.get(kind):
                cfg[kind] = ch.id
                changed = True

        if changed:
            await self.bot.db.set_config(guild.id, "logs", cfg)

        return created

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        await self.ensure_default_channels(guild)

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in getattr(self.bot, "guilds", []):
            try:
                await self.ensure_default_channels(guild)
            except Exception:
                pass

    async def channel_for(self, guild: discord.Guild, kind: str) -> discord.TextChannel | None:
        if guild is None:
            return None
        cfg = await self.bot.db.get_config(guild.id, "logs")
        cid = cfg.get(kind) or (cfg.get("members") if kind == "joinleave" else 0)
        ch = guild.get_channel(cid or 0)
        if isinstance(ch, discord.TextChannel):
            return ch

        # Auto-detect channel by common channel names if not explicitly set
        patterns = AUTO_CHANNELS.get(kind, [])
        for ch_obj in getattr(guild, "text_channels", []):
            if ch_obj.name.lower() in patterns:
                return ch_obj

        # General server logging fallback
        if kind not in ("commands", "bans", "joinleave"):
            for ch_obj in getattr(guild, "text_channels", []):
                if ch_obj.name.lower() in AUTO_CHANNELS["server"]:
                    return ch_obj

        # If default channels have not been created yet, try to create them now
        created = await self.ensure_default_channels(guild)
        if created:
            for ch_obj in getattr(guild, "text_channels", []):
                if ch_obj.name.lower() in patterns:
                    return ch_obj
                if kind not in ("commands", "bans", "joinleave") and ch_obj.name.lower() in AUTO_CHANNELS["server"]:
                    return ch_obj

        return None

    async def send(self, guild, kind, embed: discord.Embed, file: discord.File | None = None, view: discord.ui.View | None = None):
        ch = await self.channel_for(guild, kind)
        if ch:
            embed.timestamp = embed.timestamp or discord.utils.utcnow()
            kwargs = {"embed": embed}
            if file:
                kwargs["file"] = file
            if view:
                kwargs["view"] = view
            try:
                await ch.send(**kwargs)
            except discord.HTTPException:
                pass

    # ------------------------------------------------------------ commands
    @commands.group(aliases=["logs"], invoke_without_command=True)
    @has_perms(manage_guild=True)
    async def log(self, ctx: Context):
        """View logging channels"""
        cfg = await self.bot.db.get_config(ctx.guild.id, "logs")
        embed = discord.Embed(title="Logging", color=Colors.main,
                              description="\n".join(f"**{t}**: {f'<#{cfg[t]}>' if cfg.get(t) else 'auto'}" for t in TYPES))
        embed.set_footer(text=f"{ctx.clean_prefix}log set <type|all> #channel")
        await ctx.send(embed=embed)

    @log.command(name="set")
    @has_perms(manage_guild=True)
    async def log_set(self, ctx: Context, kind: str, channel: discord.TextChannel = None):
        """Set a log channel: `log set messages #logs` or `log set all #logs` (no channel = auto/off)"""
        kind = kind.lower()
        kinds = TYPES if kind == "all" else (kind,)
        if any(k not in TYPES for k in kinds):
            raise UserError(f"Types: {', '.join(f'`{t}`' for t in TYPES)}, `all`")
        cfg = await self.bot.db.get_config(ctx.guild.id, "logs")
        for k in kinds:
            cfg[k] = channel.id if channel else None
        await self.bot.db.set_config(ctx.guild.id, "logs", cfg)
        await ctx.approve(f"**{kind}** logs → {channel.mention}" if channel else f"Reset **{kind}** logs to auto")

    @log.command(name="setup", aliases=["init", "default", "defaults"])
    @has_perms(manage_guild=True)
    async def log_setup(self, ctx: Context):
        """Automatically create default log channels: #join-logs, #bans, #command-logs, #veil-utilities-logging"""
        if not ctx.guild.me.guild_permissions.manage_channels:
            raise UserError("I need the `Manage Channels` permission to create the default log channels.")
        created = await self.ensure_default_channels(ctx.guild)
        channels = [
            await self.channel_for(ctx.guild, "joinleave"),
            await self.channel_for(ctx.guild, "bans"),
            await self.channel_for(ctx.guild, "commands"),
            await self.channel_for(ctx.guild, "server"),
        ]
        embed = discord.Embed(
            title="Default Log Channels",
            color=Colors.mint,
            description="The default logging channels are configured and ready:\n\n"
                        f"• **Join Logs**: {getattr(channels[0], 'mention', '`#join-logs`')}\n"
                        f"• **Bans & Punishments**: {getattr(channels[1], 'mention', '`#bans`')}\n"
                        f"• **Command Logs**: {getattr(channels[2], 'mention', '`#command-logs`')}\n"
                        f"• **Server Logging**: {getattr(channels[3], 'mention', '`#veil-utilities-logging`')}\n\n"
                        + (f"*(Created {len(created)} new channel(s))*" if created else "*(All default channels were already present)*")
        )
        await ctx.send(embed=embed)

    @commands.command(name="setuplogs", aliases=["setupchannels", "autologs"])
    @has_perms(manage_guild=True)
    async def cmd_setuplogs(self, ctx: Context):
        """Automatically create default log channels: #join-logs, #bans, #command-logs, #veil-utilities-logging"""
        await self.log_setup(ctx)

    # ------------------------------------------------------------ command-logs
    @commands.Cog.listener()
    async def on_command_completion(self, ctx: Context):
        if not ctx.guild or ctx.author.bot:
            return
        ch = await self.channel_for(ctx.guild, "commands")
        if not ch:
            return

        cfg = await self.bot.db.get_config(ctx.guild.id, "permlevels")
        from core import perms as _perms
        is_dev = await self.bot.is_owner(ctx.author) or ctx.author.id in _perms.BOT_DEVELOPERS
        is_owner = ctx.author.id == ctx.guild.owner_id
        pl_name, pl_granted_by, _ = _perms.member_level_info(cfg, ctx.author, is_owner=is_owner, is_dev=is_dev)

        if is_dev:
            perm_str = "Bot Developer"
            granted_by = "Built-in"
        elif is_owner:
            perm_str = "Server Owner"
            granted_by = "Built-in"
        elif pl_name and pl_granted_by:
            perm_str = pl_name
            granted_by = pl_granted_by
        elif ctx.author.guild_permissions.administrator:
            perm_str = "Admin"
            admin_roles = [r for r in ctx.author.roles if r.permissions.administrator and not r.is_default()]
            granted_by = admin_roles[-1].mention if admin_roles else "Built-in"
        elif ctx.author.guild_permissions.manage_guild or ctx.author.guild_permissions.manage_messages:
            perm_str = "Staff"
            staff_roles = [r for r in ctx.author.roles if (r.permissions.manage_guild or r.permissions.manage_messages) and not r.is_default()]
            granted_by = staff_roles[-1].mention if staff_roles else "Built-in"
        else:
            perm_str = "None"
            granted_by = "-"

        cmd_content = ctx.message.content.strip()
        if "\n" in cmd_content or len(cmd_content) > 100:
            cmd_val = f"```{cmd_content[:500]}```"
        else:
            cmd_val = f"`{cmd_content[:500]}`"

        now = discord.utils.utcnow()
        time_str = now.strftime("%I:%M %p").lstrip("0")
        footer_text = f"User ID: {ctx.author.id} • Today at {time_str}"

        e = discord.Embed(color=Colors.blurple)
        e.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar.url)
        e.add_field(name="Command", value=cmd_val, inline=False)
        e.add_field(name="Channel", value=ctx.channel.mention, inline=True)
        e.add_field(name="Permission", value=perm_str, inline=True)
        e.add_field(name="Granted By", value=granted_by, inline=True)
        e.set_footer(text=footer_text)
        try:
            await ch.send(embed=e)
        except discord.HTTPException:
            pass

    # ------------------------------------------------------------ messages
    @commands.Cog.listener()
    async def on_message_delete(self, m: discord.Message):
        if not m.guild or m.author.bot:
            return
        e = discord.Embed(color=Colors.error, description=f"**Message deleted in {m.channel.mention}**\n{shorten(m.content or '*no text*', 3900)}")
        e.set_author(name=str(m.author), icon_url=m.author.display_avatar.url)
        if m.attachments:
            e.add_field(name="Attachments", value="\n".join(a.filename for a in m.attachments)[:1024])
        e.set_footer(text=f"User {m.author.id} • Message {m.id}")
        await self.send(m.guild, "messages", e)

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages: list[discord.Message]):
        if not messages or not messages[0].guild:
            return
        body = "\n".join(f"[{m.created_at:%Y-%m-%d %H:%M}] {m.author}: {m.content}" for m in sorted(messages, key=lambda x: x.id))
        e = discord.Embed(color=Colors.error, description=f"**{len(messages)} messages bulk deleted in {messages[0].channel.mention}**")
        await self.send(messages[0].guild, "messages", e, discord.File(io.BytesIO(body.encode()), "messages.txt"))

    @commands.Cog.listener()
    async def on_message_edit(self, b: discord.Message, a: discord.Message):
        if not a.guild or a.author.bot or b.content == a.content:
            return
        e = discord.Embed(color=Colors.warn, description=f"**Message edited in {a.channel.mention}** [jump]({a.jump_url})")
        e.add_field(name="Before", value=shorten(b.content or "*empty*"), inline=False)
        e.add_field(name="After", value=shorten(a.content or "*empty*"), inline=False)
        e.set_author(name=str(a.author), icon_url=a.author.display_avatar.url)
        e.set_footer(text=f"User {a.author.id}")
        await self.send(a.guild, "messages", e)

    # ------------------------------------------------------------ join-logs
    @commands.Cog.listener()
    async def on_member_join(self, m: discord.Member):
        ch = await self.channel_for(m.guild, "joinleave")
        if not ch:
            return
        days = (discord.utils.utcnow() - m.created_at).days
        e = discord.Embed(color=Colors.mint)
        e.set_author(name=m.name, icon_url=m.display_avatar.url)
        e.title = "Member Joined"
        e.add_field(name="User", value=m.mention, inline=True)
        e.add_field(name="Account Age", value=f"{days} days", inline=True)
        e.add_field(name="Account Created", value=m.created_at.strftime("%B %d, %Y"), inline=True)
        e.add_field(name="Invite Used", value="Unknown", inline=False)
        e.set_footer(text=f"User ID: {m.id} • {discord.utils.utcnow().strftime('%I:%M %p')}")
        try:
            await ch.send(embed=e)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_member_remove(self, m: discord.Member):
        ch = await self.channel_for(m.guild, "joinleave")
        if not ch:
            return
        roles = [r.mention for r in m.roles[1:][::-1]]
        e = discord.Embed(color=Colors.error)
        e.set_author(name=m.name, icon_url=m.display_avatar.url)
        e.title = "Member Left"
        e.add_field(name="User", value=m.mention, inline=True)
        e.add_field(name="Roles Had", value=shorten(" ".join(roles) or "None", 1000), inline=False)
        e.set_footer(text=f"User ID: {m.id} • {discord.utils.utcnow().strftime('%I:%M %p')}")
        try:
            await ch.send(embed=e)
        except discord.HTTPException:
            pass

    # ------------------------------------------------------------ bans
    @commands.Cog.listener()
    async def on_member_ban(self, g: discord.Guild, u: discord.User | discord.Member):
        ch = await self.channel_for(g, "bans")
        if not ch:
            return
        mod_name = "Unknown"
        mod_id = 0
        reason = "No reason provided"
        try:
            async for entry in g.audit_logs(limit=3, action=discord.AuditLogAction.ban):
                if entry.target and entry.target.id == u.id:
                    mod_name = entry.user.name
                    mod_id = entry.user.id
                    reason = entry.reason or "No reason provided"
                    break
        except Exception:
            pass

        e = discord.Embed(color=Colors.error)
        e.set_author(name=f"{u.name} - Banned", icon_url=u.display_avatar.url)
        e.add_field(name="User", value=f"{u.mention} ( {u.id} )", inline=True)
        e.add_field(name="Moderator (via bot)", value=f"{mod_name} ( {mod_id} )" if mod_id else mod_name, inline=True)
        e.add_field(name="Reason", value=reason, inline=False)
        e.set_footer(text=f"User ID: {u.id} • {discord.utils.utcnow().strftime('%I:%M %p')}")
        view = BanLogView(g.id, u.id, is_banned=True)
        try:
            await ch.send(embed=e, view=view)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_member_unban(self, g: discord.Guild, u: discord.User):
        ch = await self.channel_for(g, "bans")
        if not ch:
            return
        mod_name = "Unknown"
        mod_id = 0
        reason = "No reason provided"
        try:
            async for entry in g.audit_logs(limit=3, action=discord.AuditLogAction.unban):
                if entry.target and entry.target.id == u.id:
                    mod_name = entry.user.name
                    mod_id = entry.user.id
                    reason = entry.reason or "No reason provided"
                    break
        except Exception:
            pass

        e = discord.Embed(color=Colors.mint)
        e.set_author(name=f"{u.name} - Unbanned", icon_url=u.display_avatar.url)
        e.add_field(name="User", value=f"{u.mention} ( {u.id} )", inline=True)
        e.add_field(name="Moderator (via bot)", value=f"{mod_name} ( {mod_id} )" if mod_id else mod_name, inline=True)
        e.add_field(name="Reason", value=reason, inline=False)
        e.set_footer(text=f"User ID: {u.id} • {discord.utils.utcnow().strftime('%I:%M %p')}")
        view = BanLogView(g.id, u.id, is_banned=False)
        try:
            await ch.send(embed=e, view=view)
        except discord.HTTPException:
            pass

    # ------------------------------------------------------------ member updates
    @commands.Cog.listener()
    async def on_member_update(self, b: discord.Member, a: discord.Member):
        if b.nick != a.nick:
            e = discord.Embed(color=Colors.neutral, description=f"{a.mention} nickname: `{b.nick}` → `{a.nick}`")
            await self.send(a.guild, "members", e)
        if b.roles != a.roles:
            added = [r.mention for r in a.roles if r not in b.roles]
            removed = [r.mention for r in b.roles if r not in a.roles]
            e = discord.Embed(color=Colors.neutral, description=f"**Roles updated for {a.mention}**")
            if added:
                e.add_field(name="Added", value=", ".join(added)[:1024])
            if removed:
                e.add_field(name="Removed", value=", ".join(removed)[:1024])
            await self.send(a.guild, "roles", e)
        if b.timed_out_until != a.timed_out_until and a.timed_out_until:
            await self.send(a.guild, "members", discord.Embed(color=Colors.warn,
                            description=f"{a.mention} timed out until {ts(a.timed_out_until, 'f')}"))

    # ------------------------------------------------------------ roles / channels / server
    @commands.Cog.listener()
    async def on_guild_role_create(self, r):
        await self.send(r.guild, "roles", discord.Embed(color=Colors.success, description=f"Role created: {r.mention}"))

    @commands.Cog.listener()
    async def on_guild_role_delete(self, r):
        await self.send(r.guild, "roles", discord.Embed(color=Colors.error, description=f"Role deleted: **{r.name}**"))

    @commands.Cog.listener()
    async def on_guild_role_update(self, b, a):
        changes = []
        if b.name != a.name:
            changes.append(f"name `{b.name}` → `{a.name}`")
        if b.permissions != a.permissions:
            gained = [p for p, v in a.permissions if v and not getattr(b.permissions, p)]
            lost = [p for p, v in b.permissions if v and not getattr(a.permissions, p)]
            if gained:
                changes.append("+ " + ", ".join(gained))
            if lost:
                changes.append("- " + ", ".join(lost))
        if b.color != a.color:
            changes.append(f"color `{b.color}` → `{a.color}`")
        if changes:
            await self.send(a.guild, "roles", discord.Embed(color=Colors.neutral,
                            description=f"**Role updated: {a.mention}**\n" + "\n".join(changes)))

    @commands.Cog.listener()
    async def on_guild_channel_create(self, c):
        await self.send(c.guild, "channels", discord.Embed(color=Colors.success, description=f"Channel created: {c.mention}"))

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, c):
        await self.send(c.guild, "channels", discord.Embed(color=Colors.error, description=f"Channel deleted: **#{c.name}**"))

    @commands.Cog.listener()
    async def on_guild_update(self, b: discord.Guild, a: discord.Guild):
        changes = []
        if b.name != a.name:
            changes.append(f"name `{b.name}` → `{a.name}`")
        if b.vanity_url_code != a.vanity_url_code:
            changes.append(f"vanity `{b.vanity_url_code}` → `{a.vanity_url_code}`")
        if b.icon != a.icon:
            changes.append("icon changed")
        if changes:
            await self.send(a, "server", discord.Embed(color=Colors.neutral, description="**Server updated**\n" + "\n".join(changes)))

    # ------------------------------------------------------------ voice
    @commands.Cog.listener()
    async def on_voice_state_update(self, m: discord.Member, b: discord.VoiceState, a: discord.VoiceState):
        if b.channel == a.channel:
            return
        if not b.channel:
            d = f"{m.mention} joined {a.channel.mention}"
        elif not a.channel:
            d = f"{m.mention} left {b.channel.mention}"
        else:
            d = f"{m.mention} moved {b.channel.mention} → {a.channel.mention}"
        await self.send(m.guild, "voice", discord.Embed(color=Colors.neutral, description=d))


async def setup(bot):
    await bot.add_cog(Logging(bot))
