"""Server event logging with Midtown Utilities-style join logs, bans, and command logging."""
from __future__ import annotations

import asyncio
import io

import aiohttp
import discord
from discord.ext import commands

from core.utils import Colors, Context, UserError, has_perms, shorten, ts

TYPES = ("messages", "members", "roles", "channels", "voice", "server", "bans", "joinleave", "commands", "antinuke")

AUTO_CHANNELS: dict[str, list[str]] = {
    "joinleave": ["join-logs", "join-log", "joins-leaves", "member-logs"],
    "bans": ["bans", "ban-logs", "ban-log", "punishments", "mod-logs", "modlog"],
    "commands": ["command-logs", "cmd-logs", "commands", "command-log"],
    "antinuke": ["antinuke-logs", "antinuke-log", "security-logs", "veil-security", "veil-utilities-logging"],
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

SECURITY_LOG_CHANNELS = [
    ("antinuke-logs", "antinuke", "Antinuke alerts and zero-trust security defense logs"),
    ("message-logs", "messages", "Deleted and edited message audit logs"),
]


class Logging(commands.Cog):
    """Log server events to channels"""
    emoji = "📜"

    def __init__(self, bot):
        self.bot = bot
        self._role_update_tasks: dict[tuple[int, int], asyncio.Task] = {}
        self._role_update_states: dict[tuple[int, int], tuple[discord.Member, discord.Member]] = {}

    async def ensure_default_channels(self, guild: discord.Guild, include_security: bool = False) -> list[discord.TextChannel]:
        """Automatically create the default logging channels if they do not exist."""
        if not guild:
            return []
        me = getattr(guild, "me", None)
        if not me or not getattr(me.guild_permissions, "manage_channels", False):
            return []

        text_channels = list(getattr(guild, "text_channels", []))
        categories = list(getattr(guild, "categories", []))
        if not text_channels and hasattr(guild, "fetch_channels"):
            try:
                fetched = await guild.fetch_channels()
                text_channels = [c for c in fetched if isinstance(c, discord.TextChannel)]
                categories = [c for c in fetched if isinstance(c, discord.CategoryChannel)]
            except (discord.HTTPException, AttributeError):
                pass

        existing = {c.name.lower(): c for c in text_channels}
        created = []

        channel_targets = list(DEFAULT_LOG_CHANNELS)
        if include_security:
            channel_targets += SECURITY_LOG_CHANNELS

        # Find or create category "VEIL LOGS"
        category = discord.utils.get(categories, name="VEIL LOGS")
        missing_names = [name for name, _, _ in channel_targets if name not in existing]
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

        for name, kind, topic in channel_targets:
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
        """Automatically create default logging channels when bot joins a server."""
        await self.ensure_default_channels(guild)

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
    async def log_setup(self, ctx: Context, mode: str = "default"):
        """Automatically create default log channels under VEIL LOGS. Use `log setup all` to include security logs (#antinuke-logs and #message-logs)."""
        if not ctx.guild.me.guild_permissions.manage_channels:
            raise UserError("I need the `Manage Channels` permission to create the default log channels.")
        include_sec = mode.lower() in ("all", "security", "extended", "full")
        created = await self.ensure_default_channels(ctx.guild, include_security=include_sec)
        channels = [
            ("Join Logs", await self.channel_for(ctx.guild, "joinleave")),
            ("Bans & Punishments", await self.channel_for(ctx.guild, "bans")),
            ("Command Logs", await self.channel_for(ctx.guild, "commands")),
            ("Server Logging", await self.channel_for(ctx.guild, "server")),
        ]
        if include_sec:
            channels.insert(3, ("Antinuke Defense", await self.channel_for(ctx.guild, "antinuke")))
            channels.insert(4, ("Message Audit Logs", await self.channel_for(ctx.guild, "messages")))

        desc = "The logging channels are configured and ready under **VEIL LOGS**:\n\n" + "\n".join(
            f"• **{title}**: {getattr(ch, 'mention', '`Not set`')}" for title, ch in channels
        )
        if created:
            desc += f"\n\n*(Created {len(created)} new channel(s) under `VEIL LOGS`)*"
        else:
            desc += "\n\n*(All specified channels were already configured)*"

        embed = discord.Embed(
            title="Logging Channels Setup",
            color=Colors.mint,
            description=desc,
        )
        await ctx.send(embed=embed)

    @commands.command(name="setuplogs", aliases=["setupchannels", "autologs"])
    @has_perms(manage_guild=True)
    async def cmd_setuplogs(self, ctx: Context, mode: str = "default"):
        """Automatically create default log channels: #join-logs, #bans, #command-logs, #veil-utilities-logging"""
        await self.log_setup(ctx, mode=mode)

    @log.command(name="cleanup", aliases=["clean", "purgeduplicates", "cleanupchannels"])
    @has_perms(manage_guild=True)
    async def log_cleanup(self, ctx: Context):
        """Clean up duplicate logging channels created by the bot"""
        if not ctx.guild.me.guild_permissions.manage_channels:
            raise UserError("I need the `Manage Channels` permission to delete duplicate channels.")

        target_names = {name.lower() for name, _, _ in DEFAULT_LOG_CHANNELS + SECURITY_LOG_CHANNELS}
        seen_names = set()
        deleted_channels = 0

        # Scan text channels for duplicates
        for ch in list(ctx.guild.text_channels):
            name_lower = ch.name.lower()
            if name_lower in target_names:
                if name_lower in seen_names:
                    try:
                        await ch.delete(reason="Veil Utilities: cleanup duplicate log channels")
                        deleted_channels += 1
                    except discord.HTTPException:
                        pass
                else:
                    seen_names.add(name_lower)

        # Scan for empty duplicate categories
        deleted_categories = 0
        for cat in list(ctx.guild.categories):
            if cat.name.upper() == "VEIL LOGS" and not cat.channels:
                try:
                    await cat.delete(reason="Veil Utilities: cleanup empty VEIL LOGS category")
                    deleted_categories += 1
                except discord.HTTPException:
                    pass

        msg = f"Deleted **{deleted_channels}** duplicate log channel(s)"
        if deleted_categories:
            msg += f" and **{deleted_categories}** empty category/categories"
        await ctx.approve(f"{msg}. Logging is now clean!")

    @commands.command(name="cleanuplogs", aliases=["cleanuplogchannels", "purgeduplicatelogs"])
    @has_perms(manage_guild=True)
    async def cmd_cleanuplogs(self, ctx: Context):
        """Clean up duplicate logging channels created by the bot"""
        await self.log_cleanup(ctx)

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
        is_dev = _perms.is_bot_developer(ctx.author, self.bot) or await self.bot.is_owner(ctx.author)
        is_owner = ctx.author.id == ctx.guild.owner_id
        pl_name, pl_granted_by, _ = _perms.member_level_info(cfg, ctx.author, is_owner=is_owner, is_dev=is_dev)

        if is_dev:
            perm_str = "Bot Developer"
            granted_by = "Built-in"
        elif pl_name and pl_granted_by:
            perm_str = pl_name
            granted_by = pl_granted_by
        elif is_owner:
            perm_str = "Server Owner"
            granted_by = "Built-in"
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
        except (discord.HTTPException, aiohttp.ClientError, asyncio.TimeoutError):
            pass

    # ------------------------------------------------------------ messages
    @commands.Cog.listener()
    async def on_message_delete(self, m: discord.Message):
        if not m.guild or m.author.bot:
            return
        e = discord.Embed(color=Colors.error)
        channel_name = getattr(m.channel, "name", "unknown-channel")
        e.set_author(name=f"Message Deleted — #{channel_name}", icon_url=m.author.display_avatar.url)
        e.set_thumbnail(url=m.author.display_avatar.url)
        e.add_field(name="Author", value=f"{m.author.mention} (`{m.author.name}` • `{m.author.id}`)", inline=True)
        e.add_field(name="Channel", value=m.channel.mention, inline=True)

        actor_str = None
        try:
            me = getattr(m.guild, "me", None)
            if me and getattr(me.guild_permissions, "view_audit_log", False):
                async for entry in m.guild.audit_logs(limit=4, action=discord.AuditLogAction.message_delete):
                    if getattr(entry.target, "id", None) == m.author.id:
                        delta = (discord.utils.utcnow() - entry.created_at).total_seconds()
                        if delta < 10:
                            actor_str = f"{entry.user.mention} (`{entry.user.name}`)"
                            break
        except Exception:
            pass

        if actor_str:
            e.add_field(name="Deleted By", value=actor_str, inline=True)

        content = m.content or "*No text content (embed or attachment only)*"
        e.add_field(name="Content", value=shorten(content, 1024), inline=False)
        if m.attachments:
            att_list = "\n".join(f"• [`{a.filename}`]({a.url})" for a in m.attachments)[:1024]
            e.add_field(name=f"Attachments ({len(m.attachments)})", value=att_list, inline=False)

        now = discord.utils.utcnow()
        e.set_footer(text=f"Author ID: {m.author.id} • Message ID: {m.id} • Today at {now.strftime('%I:%M %p')}")
        e.timestamp = now
        await self.send(m.guild, "messages", e)

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages: list[discord.Message]):
        if not messages or not messages[0].guild:
            return
        body = "\n".join(f"[{m.created_at:%Y-%m-%d %H:%M}] {m.author}: {m.content}" for m in sorted(messages, key=lambda x: x.id))
        e = discord.Embed(color=Colors.error, description=f"**{len(messages)} messages bulk deleted in {messages[0].channel.mention}**")
        now = discord.utils.utcnow()
        e.set_footer(text=f"{len(messages)} messages • Today at {now.strftime('%I:%M %p')}")
        e.timestamp = now
        await self.send(messages[0].guild, "messages", e, discord.File(io.BytesIO(body.encode()), "messages.txt"))

    @commands.Cog.listener()
    async def on_message_edit(self, b: discord.Message, a: discord.Message):
        if not a.guild or a.author.bot or b.content == a.content:
            return
        e = discord.Embed(color=Colors.warn)
        channel_name = getattr(a.channel, "name", "unknown-channel")
        e.set_author(name=f"Message Edited — #{channel_name}", icon_url=a.author.display_avatar.url)
        e.set_thumbnail(url=a.author.display_avatar.url)
        e.add_field(name="Author", value=f"{a.author.mention} (`{a.author.name}`)", inline=True)
        e.add_field(name="Channel", value=f"{a.channel.mention} • [Jump to Message]({a.jump_url})", inline=True)
        e.add_field(name="Before", value=shorten(b.content or "*Empty message*", 1024), inline=False)
        e.add_field(name="After", value=shorten(a.content or "*Empty message*", 1024), inline=False)
        now = discord.utils.utcnow()
        e.set_footer(text=f"Author ID: {a.author.id} • Message ID: {a.id} • Today at {now.strftime('%I:%M %p')}")
        e.timestamp = now
        await self.send(a.guild, "messages", e)

    # ------------------------------------------------------------ join-logs
    @commands.Cog.listener()
    async def on_member_join(self, m: discord.Member):
        ch = await self.channel_for(m.guild, "joinleave")
        if not ch:
            return
        now = discord.utils.utcnow()
        age_days = (now - m.created_at).days
        e = discord.Embed(color=Colors.mint)
        e.set_author(name=f"Member Joined — {m.display_name}", icon_url=m.display_avatar.url)
        e.set_thumbnail(url=m.display_avatar.url)
        e.add_field(name="User", value=f"{m.mention} (`{m.name}` • `{m.id}`)", inline=True)

        member_count = getattr(m.guild, "member_count", None)
        if member_count:
            e.add_field(name="Member Count", value=f"#{member_count}", inline=True)

        created_ts = int(m.created_at.timestamp())
        account_age_val = f"<t:{created_ts}:R>\n(<t:{created_ts}:D>)"
        if age_days < 7:
            account_age_val += "\n⚠️ *New account (< 7 days)*"
        e.add_field(name="Account Created", value=account_age_val, inline=True)

        guild_icon = m.guild.icon.url if m.guild.icon else None
        e.set_footer(text=f"User ID: {m.id} • Today at {now.strftime('%I:%M %p')}", icon_url=guild_icon)
        e.timestamp = now
        try:
            await ch.send(embed=e)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_member_remove(self, m: discord.Member):
        ch = await self.channel_for(m.guild, "joinleave")
        if not ch:
            return

        now = discord.utils.utcnow()
        kicked_by = None
        kick_reason = None
        try:
            me = getattr(m.guild, "me", None)
            if me and getattr(me.guild_permissions, "view_audit_log", False):
                async for entry in m.guild.audit_logs(limit=4, action=discord.AuditLogAction.kick):
                    if getattr(entry.target, "id", None) == m.id:
                        delta = (now - entry.created_at).total_seconds()
                        if delta < 15:
                            kicked_by = entry.user
                            kick_reason = entry.reason
                            break
        except Exception:
            pass

        if kicked_by:
            color = Colors.warn
            title = f"Member Kicked — {m.display_name}"
        else:
            color = Colors.error
            title = f"Member Left — {m.display_name}"

        e = discord.Embed(color=color)
        e.set_author(name=title, icon_url=m.display_avatar.url)
        e.set_thumbnail(url=m.display_avatar.url)
        e.add_field(name="User", value=f"{m.mention} (`{m.name}` • `{m.id}`)", inline=True)

        member_count = getattr(m.guild, "member_count", None)
        if member_count:
            e.add_field(name="Member Count", value=f"#{member_count}", inline=True)

        if kicked_by:
            e.add_field(name="Moderator", value=f"{kicked_by.mention} (`{kicked_by.name}`)", inline=True)
            if kick_reason:
                e.add_field(name="Reason", value=shorten(kick_reason, 500), inline=False)

        user_roles = [r for r in m.roles if not r.is_default()]
        if user_roles:
            if len(user_roles) <= 8:
                role_val = "\n".join(f"• {r.mention} (`{r.name}`)" for r in reversed(user_roles))
            else:
                role_val = ", ".join(r.mention for r in reversed(user_roles))
            e.add_field(name=f"Roles Had ({len(user_roles)})", value=shorten(role_val, 1024), inline=False)

        guild_icon = m.guild.icon.url if m.guild.icon else None
        e.set_footer(text=f"User ID: {m.id} • Today at {now.strftime('%I:%M %p')}", icon_url=guild_icon)
        e.timestamp = now
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
        mod_str = "Automatic / Discord API"
        reason = "No reason provided"
        try:
            me = getattr(g, "me", None)
            if me and getattr(me.guild_permissions, "view_audit_log", False):
                async for entry in g.audit_logs(limit=4, action=discord.AuditLogAction.ban):
                    if getattr(entry.target, "id", None) == u.id:
                        delta = (discord.utils.utcnow() - entry.created_at).total_seconds()
                        if delta < 20:
                            mod = entry.user
                            if getattr(mod, "bot", False):
                                mod_str = f"🤖 {mod.mention} (`{mod.name}`)"
                            else:
                                mod_str = f"{mod.mention} (`{mod.name}` • `{mod.id}`)"
                            reason = entry.reason or "No reason provided"
                            break
        except Exception:
            pass

        now = discord.utils.utcnow()
        e = discord.Embed(color=Colors.error)
        e.set_author(name=f"Member Banned — {u.name}", icon_url=u.display_avatar.url)
        e.set_thumbnail(url=u.display_avatar.url)
        e.add_field(name="User", value=f"{u.mention} (`{u.name}` • `{u.id}`)", inline=True)
        e.add_field(name="Moderator", value=mod_str, inline=True)
        e.add_field(name="Reason", value=shorten(reason, 500), inline=False)
        guild_icon = g.icon.url if g.icon else None
        e.set_footer(text=f"User ID: {u.id} • Today at {now.strftime('%I:%M %p')}", icon_url=guild_icon)
        e.timestamp = now
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
        mod_str = "Automatic / Discord API"
        reason = "No reason provided"
        try:
            me = getattr(g, "me", None)
            if me and getattr(me.guild_permissions, "view_audit_log", False):
                async for entry in g.audit_logs(limit=4, action=discord.AuditLogAction.unban):
                    if getattr(entry.target, "id", None) == u.id:
                        delta = (discord.utils.utcnow() - entry.created_at).total_seconds()
                        if delta < 20:
                            mod = entry.user
                            if getattr(mod, "bot", False):
                                mod_str = f"🤖 {mod.mention} (`{mod.name}`)"
                            else:
                                mod_str = f"{mod.mention} (`{mod.name}` • `{mod.id}`)"
                            reason = entry.reason or "No reason provided"
                            break
        except Exception:
            pass

        now = discord.utils.utcnow()
        e = discord.Embed(color=Colors.mint)
        e.set_author(name=f"Member Unbanned — {u.name}", icon_url=u.display_avatar.url)
        e.set_thumbnail(url=u.display_avatar.url)
        e.add_field(name="User", value=f"{u.mention} (`{u.name}` • `{u.id}`)", inline=True)
        e.add_field(name="Moderator", value=mod_str, inline=True)
        e.add_field(name="Reason", value=shorten(reason, 500), inline=False)
        guild_icon = g.icon.url if g.icon else None
        e.set_footer(text=f"User ID: {u.id} • Today at {now.strftime('%I:%M %p')}", icon_url=guild_icon)
        e.timestamp = now
        view = BanLogView(g.id, u.id, is_banned=False)
        try:
            await ch.send(embed=e, view=view)
        except discord.HTTPException:
            pass

    # ------------------------------------------------------------ member updates & roles
    async def _build_role_update_embed(
        self,
        member: discord.Member,
        added_roles: list[discord.Role],
        removed_roles: list[discord.Role],
    ) -> discord.Embed:
        if added_roles and not removed_roles:
            color = Colors.success
            action_title = "Roles Added"
        elif removed_roles and not added_roles:
            color = Colors.error
            action_title = "Roles Removed"
        else:
            color = Colors.blurple
            action_title = "Roles Updated"

        actor_str = "Automatic / Discord API"
        reason_str = None
        guild = member.guild
        try:
            me = getattr(guild, "me", None)
            if me and getattr(me.guild_permissions, "view_audit_log", False):
                async for entry in guild.audit_logs(limit=6, action=discord.AuditLogAction.member_role_update):
                    if getattr(entry.target, "id", None) == member.id:
                        delta = (discord.utils.utcnow() - entry.created_at).total_seconds()
                        if delta < 15:
                            actor = entry.user
                            if getattr(actor, "bot", False):
                                actor_str = f"🤖 {actor.mention} (`{actor.name}`)"
                            else:
                                actor_str = f"{actor.mention} (`{actor.name}` • `{actor.id}`)"
                            reason_str = entry.reason
                            break
        except Exception:
            pass

        e = discord.Embed(color=color)
        e.set_author(name=f"{action_title} — {member.display_name}", icon_url=member.display_avatar.url)
        e.set_thumbnail(url=member.display_avatar.url)

        e.add_field(name="User", value=f"{member.mention} (`{member.name}`)", inline=True)
        e.add_field(name="Responsible Actor", value=actor_str, inline=True)

        def format_roles(roles: list[discord.Role]) -> str:
            if not roles:
                return "None"
            if len(roles) <= 8:
                return "\n".join(f"• {r.mention} (`{r.name}`)" for r in roles)[:1024]
            return ", ".join(r.mention for r in roles)[:1024]

        if added_roles:
            e.add_field(
                name=f"➕ Added ({len(added_roles)})",
                value=format_roles(added_roles),
                inline=False,
            )
        if removed_roles:
            e.add_field(
                name=f"➖ Removed ({len(removed_roles)})",
                value=format_roles(removed_roles),
                inline=False,
            )

        if reason_str:
            e.add_field(name="Reason", value=shorten(reason_str, 500), inline=False)

        now_str = discord.utils.utcnow().strftime("%I:%M %p")
        guild_icon = guild.icon.url if guild.icon else None
        e.set_footer(text=f"User ID: {member.id} • Today at {now_str}", icon_url=guild_icon)
        e.timestamp = discord.utils.utcnow()
        return e

    @commands.Cog.listener()
    async def on_member_update(self, b: discord.Member, a: discord.Member):
        if b.nick != a.nick:
            old_nick = b.nick or "None (Username)"
            new_nick = a.nick or "None (Reset)"
            e = discord.Embed(color=Colors.blurple)
            e.set_author(name=f"Nickname Changed — {a.display_name}", icon_url=a.display_avatar.url)
            e.set_thumbnail(url=a.display_avatar.url)
            e.add_field(name="User", value=f"{a.mention} (`{a.name}`)", inline=True)

            actor_str = "Automatic / Discord API"
            try:
                me = getattr(a.guild, "me", None)
                if me and getattr(me.guild_permissions, "view_audit_log", False):
                    async for entry in a.guild.audit_logs(limit=4, action=discord.AuditLogAction.member_update):
                        if getattr(entry.target, "id", None) == a.id:
                            delta = (discord.utils.utcnow() - entry.created_at).total_seconds()
                            if delta < 15:
                                actor = entry.user
                                actor_str = f"{actor.mention} (`{actor.name}`)"
                                break
            except Exception:
                pass

            e.add_field(name="Action By", value=actor_str, inline=True)
            e.add_field(name="Before", value=f"`{old_nick}`", inline=True)
            e.add_field(name="After", value=f"`{new_nick}`", inline=True)
            e.set_footer(text=f"User ID: {a.id} • {discord.utils.utcnow().strftime('%I:%M %p')}")
            e.timestamp = discord.utils.utcnow()
            await self.send(a.guild, "members", e)

        if b.roles != a.roles:
            key = (a.guild.id, a.id)
            if key in self._role_update_states:
                initial_b, _ = self._role_update_states[key]
                self._role_update_states[key] = (initial_b, a)
            else:
                self._role_update_states[key] = (b, a)

            # Cancel previous debounced task if still waiting
            task = self._role_update_tasks.get(key)
            if task and not task.done():
                task.cancel()

            async def _debounced_role_dispatch(g_id: int, u_id: int):
                try:
                    await asyncio.sleep(1.2)
                except asyncio.CancelledError:
                    return
                state = self._role_update_states.pop(key, None)
                self._role_update_tasks.pop(key, None)
                if not state:
                    return
                first_before, last_after = state
                net_added = [r for r in last_after.roles if r not in first_before.roles]
                net_removed = [r for r in first_before.roles if r not in last_after.roles]
                if not net_added and not net_removed:
                    return

                embed = await self._build_role_update_embed(last_after, net_added, net_removed)
                await self.send(last_after.guild, "roles", embed)

            self._role_update_tasks[key] = asyncio.create_task(_debounced_role_dispatch(a.guild.id, a.id))

        if b.timed_out_until != a.timed_out_until:
            now = discord.utils.utcnow()
            if a.timed_out_until and a.timed_out_until > now:
                e = discord.Embed(color=Colors.warn)
                e.set_author(name=f"Member Timed Out — {a.display_name}", icon_url=a.display_avatar.url)
                e.set_thumbnail(url=a.display_avatar.url)
                e.add_field(name="User", value=f"{a.mention} (`{a.name}`)", inline=True)

                actor_str = "Automatic / Automod"
                reason_str = None
                try:
                    me = getattr(a.guild, "me", None)
                    if me and getattr(me.guild_permissions, "view_audit_log", False):
                        async for entry in a.guild.audit_logs(limit=4, action=discord.AuditLogAction.member_update):
                            if getattr(entry.target, "id", None) == a.id:
                                delta = (now - entry.created_at).total_seconds()
                                if delta < 15:
                                    actor_str = f"{entry.user.mention} (`{entry.user.name}`)"
                                    reason_str = entry.reason
                                    break
                except Exception:
                    pass

                e.add_field(name="Moderator", value=actor_str, inline=True)
                e.add_field(name="Expires", value=f"{ts(a.timed_out_until, 'R')} ({ts(a.timed_out_until, 'f')})", inline=False)
                if reason_str:
                    e.add_field(name="Reason", value=shorten(reason_str, 500), inline=False)
                e.set_footer(text=f"User ID: {a.id} • {now.strftime('%I:%M %p')}")
                e.timestamp = now
                await self.send(a.guild, "members", e)
            elif b.timed_out_until and (not a.timed_out_until or a.timed_out_until <= now):
                e = discord.Embed(color=Colors.mint)
                e.set_author(name=f"Timeout Removed — {a.display_name}", icon_url=a.display_avatar.url)
                e.set_thumbnail(url=a.display_avatar.url)
                e.add_field(name="User", value=f"{a.mention} (`{a.name}`)", inline=True)
                e.set_footer(text=f"User ID: {a.id} • {now.strftime('%I:%M %p')}")
                e.timestamp = now
                await self.send(a.guild, "members", e)

    # ------------------------------------------------------------ roles / channels / server
    @commands.Cog.listener()
    async def on_guild_role_create(self, r: discord.Role):
        guild = r.guild
        icon_url = guild.icon.url if guild.icon else None
        e = discord.Embed(color=r.color if r.color.value else Colors.success)
        e.set_author(name="Role Created", icon_url=icon_url)
        e.add_field(name="Role", value=f"{r.mention} (`{r.name}`)", inline=True)
        e.add_field(name="Role ID", value=f"`{r.id}`", inline=True)
        hex_color = f"#{r.color.value:06X}" if r.color.value else "Default"
        e.add_field(name="Color", value=f"`{hex_color}`", inline=True)

        actor_str = "Automatic / Integration"
        try:
            me = getattr(guild, "me", None)
            if me and getattr(me.guild_permissions, "view_audit_log", False):
                async for entry in guild.audit_logs(limit=4, action=discord.AuditLogAction.role_create):
                    if getattr(entry.target, "id", None) == r.id:
                        actor = entry.user
                        actor_str = f"{actor.mention} (`{actor.name}`)"
                        break
        except Exception:
            pass

        e.add_field(name="Created By", value=actor_str, inline=True)
        e.set_footer(text=f"Role ID: {r.id} • {discord.utils.utcnow().strftime('%I:%M %p')}")
        e.timestamp = discord.utils.utcnow()
        await self.send(guild, "roles", e)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, r: discord.Role):
        guild = r.guild
        icon_url = guild.icon.url if guild.icon else None
        e = discord.Embed(color=Colors.error)
        e.set_author(name="Role Deleted", icon_url=icon_url)
        e.add_field(name="Role Name", value=f"**@{r.name}**", inline=True)
        e.add_field(name="Role ID", value=f"`{r.id}`", inline=True)

        actor_str = "Automatic / Integration"
        try:
            me = getattr(guild, "me", None)
            if me and getattr(me.guild_permissions, "view_audit_log", False):
                async for entry in guild.audit_logs(limit=4, action=discord.AuditLogAction.role_delete):
                    if getattr(entry.target, "id", None) == r.id:
                        actor = entry.user
                        actor_str = f"{actor.mention} (`{actor.name}`)"
                        break
        except Exception:
            pass

        e.add_field(name="Deleted By", value=actor_str, inline=True)
        e.set_footer(text=f"Role ID: {r.id} • {discord.utils.utcnow().strftime('%I:%M %p')}")
        e.timestamp = discord.utils.utcnow()
        await self.send(guild, "roles", e)

    @commands.Cog.listener()
    async def on_guild_role_update(self, b: discord.Role, a: discord.Role):
        changes = []
        if b.name != a.name:
            changes.append(f"• **Name:** `{b.name}` → `{a.name}`")
        if b.color != a.color:
            b_hex = f"#{b.color.value:06X}" if b.color.value else "Default"
            a_hex = f"#{a.color.value:06X}" if a.color.value else "Default"
            changes.append(f"• **Color:** `{b_hex}` → `{a_hex}`")
        if b.hoist != a.hoist:
            changes.append(f"• **Hoisted:** `{'Yes' if a.hoist else 'No'}`")
        if b.mentionable != a.mentionable:
            changes.append(f"• **Mentionable:** `{'Yes' if a.mentionable else 'No'}`")
        if b.permissions != a.permissions:
            gained = [p.replace('_', ' ') for p, v in a.permissions if v and not getattr(b.permissions, p)]
            lost = [p.replace('_', ' ') for p, v in b.permissions if v and not getattr(a.permissions, p)]
            if gained:
                changes.append(f"• **Permissions Added:** {', '.join(f'`{p}`' for p in gained)}")
            if lost:
                changes.append(f"• **Permissions Removed:** {', '.join(f'`{p}`' for p in lost)}")
        if not changes:
            return

        guild = a.guild
        icon_url = guild.icon.url if guild.icon else None
        e = discord.Embed(color=a.color if a.color.value else Colors.blurple)
        e.set_author(name=f"Role Updated — @{a.name}", icon_url=icon_url)
        e.add_field(name="Role", value=f"{a.mention} (`{a.id}`)", inline=True)

        actor_str = "Automatic / Integration"
        try:
            me = getattr(guild, "me", None)
            if me and getattr(me.guild_permissions, "view_audit_log", False):
                async for entry in guild.audit_logs(limit=4, action=discord.AuditLogAction.role_update):
                    if getattr(entry.target, "id", None) == a.id:
                        actor = entry.user
                        actor_str = f"{actor.mention} (`{actor.name}`)"
                        break
        except Exception:
            pass

        e.add_field(name="Updated By", value=actor_str, inline=True)
        e.add_field(name="Changes", value="\n".join(changes)[:1024], inline=False)
        e.set_footer(text=f"Role ID: {a.id} • {discord.utils.utcnow().strftime('%I:%M %p')}")
        e.timestamp = discord.utils.utcnow()
        await self.send(guild, "roles", e)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, c: discord.abc.GuildChannel):
        guild = c.guild
        icon_url = guild.icon.url if guild.icon else None
        e = discord.Embed(color=Colors.success)
        e.set_author(name="Channel Created", icon_url=icon_url)
        e.add_field(name="Channel", value=f"{c.mention} (`#{c.name}`)", inline=True)
        category_name = c.category.name if getattr(c, "category", None) else "None"
        e.add_field(name="Category", value=category_name, inline=True)

        actor_str = "Automatic / Integration"
        try:
            me = getattr(guild, "me", None)
            if me and getattr(me.guild_permissions, "view_audit_log", False):
                async for entry in guild.audit_logs(limit=4, action=discord.AuditLogAction.channel_create):
                    if getattr(entry.target, "id", None) == c.id:
                        actor = entry.user
                        actor_str = f"{actor.mention} (`{actor.name}`)"
                        break
        except Exception:
            pass

        e.add_field(name="Created By", value=actor_str, inline=True)
        e.set_footer(text=f"Channel ID: {c.id} • {discord.utils.utcnow().strftime('%I:%M %p')}")
        e.timestamp = discord.utils.utcnow()
        await self.send(guild, "channels", e)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, c: discord.abc.GuildChannel):
        guild = c.guild
        icon_url = guild.icon.url if guild.icon else None
        e = discord.Embed(color=Colors.error)
        e.set_author(name="Channel Deleted", icon_url=icon_url)
        e.add_field(name="Channel Name", value=f"**#{c.name}**", inline=True)
        category_name = c.category.name if getattr(c, "category", None) else "None"
        e.add_field(name="Category", value=category_name, inline=True)

        actor_str = "Automatic / Integration"
        try:
            me = getattr(guild, "me", None)
            if me and getattr(me.guild_permissions, "view_audit_log", False):
                async for entry in guild.audit_logs(limit=4, action=discord.AuditLogAction.channel_delete):
                    if getattr(entry.target, "id", None) == c.id:
                        actor = entry.user
                        actor_str = f"{actor.mention} (`{actor.name}`)"
                        break
        except Exception:
            pass

        e.add_field(name="Deleted By", value=actor_str, inline=True)
        e.set_footer(text=f"Channel ID: {c.id} • {discord.utils.utcnow().strftime('%I:%M %p')}")
        e.timestamp = discord.utils.utcnow()
        await self.send(guild, "channels", e)

    @commands.Cog.listener()
    async def on_guild_update(self, b: discord.Guild, a: discord.Guild):
        changes = []
        if b.name != a.name:
            changes.append(f"• **Server Name:** `{b.name}` → `{a.name}`")
        if b.vanity_url_code != a.vanity_url_code:
            changes.append(f"• **Vanity URL:** `discord.gg/{b.vanity_url_code}` → `discord.gg/{a.vanity_url_code}`")
        if b.icon != a.icon:
            changes.append("• **Server Icon Updated**")
        if b.banner != a.banner:
            changes.append("• **Server Banner Updated**")
        if not changes:
            return

        icon_url = a.icon.url if a.icon else None
        e = discord.Embed(color=Colors.blurple)
        e.set_author(name="Server Settings Updated", icon_url=icon_url)
        if icon_url:
            e.set_thumbnail(url=icon_url)
        e.add_field(name="Changes", value="\n".join(changes), inline=False)
        e.set_footer(text=f"Server ID: {a.id} • {discord.utils.utcnow().strftime('%I:%M %p')}")
        e.timestamp = discord.utils.utcnow()
        await self.send(a, "server", e)

    # ------------------------------------------------------------ voice
    @commands.Cog.listener()
    async def on_voice_state_update(self, m: discord.Member, b: discord.VoiceState, a: discord.VoiceState):
        if b.channel == a.channel:
            return
        if not b.channel and a.channel:
            color = Colors.success
            action = "Voice Channel Joined"
            desc = f"{m.mention} joined {a.channel.mention}"
        elif b.channel and not a.channel:
            color = Colors.error
            action = "Voice Channel Left"
            desc = f"{m.mention} left {b.channel.mention}"
        else:
            color = Colors.blurple
            action = "Voice Channel Moved"
            desc = f"{m.mention} moved from {b.channel.mention} ➔ {a.channel.mention}"

        e = discord.Embed(color=color)
        e.set_author(name=f"{m.display_name} — {action}", icon_url=m.display_avatar.url)
        e.set_thumbnail(url=m.display_avatar.url)
        e.description = desc
        e.add_field(name="User", value=f"{m.mention} (`{m.name}`)", inline=True)
        e.set_footer(text=f"User ID: {m.id} • {discord.utils.utcnow().strftime('%I:%M %p')}")
        e.timestamp = discord.utils.utcnow()
        await self.send(m.guild, "voice", e)


async def setup(bot):
    await bot.add_cog(Logging(bot))
