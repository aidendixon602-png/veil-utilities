"""Developer-only dangerous tools, shown under the red Secret help button.

Everything here is restricted to the bot's developers (OWNER_IDS):
strip/unstrip, massunrole, revert, network hardban/genreban, join-leave logs, and antinuke presets.
"""
from __future__ import annotations

import asyncio

import discord
from discord.ext import commands

from core.utils import Colors, Context, Duration, UserError, chunk_embeds, now, shorten, ts, MidtownConfirm, requires_uperm, has_perms

A = discord.AuditLogAction


class DevTools(commands.Cog):
    """Developer-only tools"""
    emoji = "⚡"

    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx: Context) -> bool:
        from core import perms as _perms
        if _perms.is_bot_developer(ctx.author, self.bot) or await self.bot.is_owner(ctx.author):
            return True
        if ctx.guild and ctx.author.id == ctx.guild.owner_id:
            return True
        db = getattr(self.bot, "db", None)
        if db and ctx.guild:
            pl_cfg = await db.get_config(ctx.guild.id, "permlevels")
            author_rank = _perms.rank_of(pl_cfg, ctx.author, is_owner=False, is_dev=False, has_admin=False)
            if author_rank >= 7:  # Supreme Leader
                return True
        raise commands.NotOwner()

    # ================================================================ strip / unstrip
    @commands.command(hidden=True)
    @commands.guild_only()
    @commands.bot_has_permissions(manage_roles=True)
    async def strip(self, ctx: Context, member: discord.Member):
        """Remove every role from a member (restore with unstrip)"""
        removable = [r for r in member.roles if not r.is_default() and not r.managed and r < ctx.guild.me.top_role]
        if not removable:
            raise UserError(f"I can't remove any of {member.mention}'s roles")
        cfg = await self.bot.db.get_config(ctx.guild.id, "strips")
        cfg[str(member.id)] = [r.id for r in removable]
        await self.bot.db.set_config(ctx.guild.id, "strips", cfg)
        await member.remove_roles(*removable, reason=f"strip by {ctx.author}")
        e = discord.Embed(color=Colors.main, description=f"Stripped **{len(removable)}** role(s) from {member.mention}.")
        e.add_field(name="Removed", value=shorten(" ".join(r.mention for r in removable), 1024), inline=False)
        e.set_footer(text=f"use {ctx.clean_prefix}unstrip to restore")
        await ctx.send(embed=e)

    @commands.command(hidden=True, aliases=["restore"])
    @commands.guild_only()
    @commands.bot_has_permissions(manage_roles=True)
    async def unstrip(self, ctx: Context, member: discord.Member):
        """Give back the roles a strip removed"""
        cfg = await self.bot.db.get_config(ctx.guild.id, "strips")
        saved = cfg.pop(str(member.id), None)
        if not saved:
            raise UserError(f"{member.mention} wasn't stripped")
        b_cfg = await self.bot.db.get_config(ctx.guild.id, "roleblacklist")
        b_roles = set(b_cfg.get("roles", []))
        roles = [r for r in (ctx.guild.get_role(i) for i in saved) if r and r < ctx.guild.me.top_role and not r.managed and r.id not in b_roles]
        if roles:
            await member.add_roles(*roles, reason=f"unstrip by {ctx.author}")
        await self.bot.db.set_config(ctx.guild.id, "strips", cfg)
        await ctx.approve(f"Restored **{len(roles)}** role(s) to {member.mention}.")

    # ================================================================ massunrole
    @commands.command(hidden=True)
    @commands.guild_only()
    @commands.bot_has_permissions(manage_roles=True)
    @commands.max_concurrency(1, commands.BucketType.guild)
    async def massunrole(self, ctx: Context, role: discord.Role = None, flag: str = None):
        """Remove a role from everyone. Add `samerole` to also wipe the role's permissions"""
        if role is None:
            raise UserError(f"`{ctx.clean_prefix}massunrole <role mention or ID> [samerole]`")
        if role >= ctx.guild.me.top_role:
            raise UserError(f"{role.mention} is above my top role")
        members = list(role.members)
        if not members and flag != "samerole":
            raise UserError(f"Nobody has {role.mention}")
        msg = await ctx.neutral(f"Removing {role.mention} from **{len(members)}** member(s)…")
        done = 0
        for m in members:
            try:
                await m.remove_roles(role, reason=f"massunrole by {ctx.author}")
                done += 1
            except discord.HTTPException:
                pass
        extra = ""
        if flag == "samerole" and role.permissions.value:
            try:
                await role.edit(permissions=discord.Permissions.none(), reason=f"massunrole samerole by {ctx.author}")
                extra = " and wiped its permissions"
            except discord.HTTPException:
                pass
        await msg.edit(embed=discord.Embed(color=Colors.success,
                       description=f"Removed {role.mention} from **{done}** member(s){extra}."))

    # ================================================================ revert
    @commands.command(hidden=True)
    @commands.guild_only()
    @commands.bot_has_permissions(view_audit_log=True)
    async def revert(self, ctx: Context, user: discord.User, duration: Duration):
        """Best-effort undo of a user's recent destructive actions (bans, role/channel deletes)"""
        after = discord.utils.utcnow() - duration
        scanned = found = reverted = skipped = 0
        async with ctx.typing():
            async for entry in ctx.guild.audit_logs(limit=1000):
                if entry.created_at < after:
                    break
                scanned += 1
                if entry.user_id != user.id:
                    continue
                if entry.action not in (A.ban, A.role_delete, A.channel_delete):
                    continue
                found += 1
                try:
                    if entry.action == A.ban:
                        target = entry.target
                        try:
                            await ctx.guild.fetch_ban(discord.Object(target.id))
                        except discord.NotFound:
                            skipped += 1
                            continue
                        await ctx.guild.unban(discord.Object(target.id), reason=f"revert by {ctx.author}")
                        reverted += 1
                    elif entry.action == A.role_delete:
                        b = entry.before
                        await ctx.guild.create_role(
                            name=getattr(b, "name", "restored-role"),
                            colour=getattr(b, "colour", None) or discord.Colour.default(),
                            hoist=bool(getattr(b, "hoist", False)),
                            mentionable=bool(getattr(b, "mentionable", False)),
                            permissions=getattr(b, "permissions", None) or discord.Permissions.none(),
                            reason=f"revert by {ctx.author}")
                        reverted += 1
                    elif entry.action == A.channel_delete:
                        b = entry.before
                        name = getattr(b, "name", "restored-channel")
                        if getattr(b, "type", None) == discord.ChannelType.voice:
                            await ctx.guild.create_voice_channel(name, reason=f"revert by {ctx.author}")
                        else:
                            await ctx.guild.create_text_channel(name, reason=f"revert by {ctx.author}")
                        reverted += 1
                except discord.HTTPException:
                    skipped += 1
        window = f"{ts(after, 't')} → {ts(discord.utils.utcnow(), 't')}"
        if reverted:
            e = discord.Embed(color=Colors.success,
                              description=f"Reverted **{reverted}** action(s) by {user.mention}.")
        else:
            e = discord.Embed(color=Colors.warn,
                              description=f"No revertible actions for {user.mention} "
                                          f"({skipped} already reverted or irreversible).")
        e.description += f"\nFound **{found}** entries in window {window} (scanned **{scanned}** total)."
        e.timestamp = discord.utils.utcnow()
        await ctx.send(embed=e)

    # ================================================================ network hardban
    @commands.command(hidden=True, aliases=["reban", "hardban", "globalban", "hb"])
    @requires_uperm("supremeleader")
    @has_perms(administrator=True)
    async def genreban(self, ctx: Context, target: str, *, reason: str = "Genre ban across authorized servers"):
        """Ban a user across every authorized server (re-banned if they rejoin or are unbanned)"""
        msg = await ctx.warn("Processing...")

        user = None
        user_id = None
        clean_target = target.strip("<@!>")
        try:
            user_id = int(clean_target)
            user = self.bot.get_user(user_id)
            if not user:
                try:
                    user = await self.bot.fetch_user(user_id)
                except Exception:
                    pass
        except ValueError:
            try:
                user = await commands.UserConverter().convert(ctx, target)
                user_id = user.id
            except Exception:
                pass

        if not user_id:
            await msg.delete()
            raise UserError(f"Could not find user `{target}`.")

        from core import perms as _perms
        if user and _perms.is_bot_developer(user, self.bot):
            await msg.delete()
            raise UserError("You cannot genreban a bot developer.")

        display_name = f"@{user.name}" if user else f"User `{user_id}`"
        display_user_str = f"{display_name} ( {user_id} )"

        guilds = list(self.bot.guilds)
        guild_count = len(guilds)

        embed = discord.Embed(
            description=f"Genreban {display_user_str} from all **{guild_count}** authorized server(s)?",
            color=Colors.warn,
        )
        if self.bot.user:
            embed.set_author(name=self.bot.user.display_name, icon_url=self.bot.user.display_avatar.url)
        footer = await ctx._footer()
        if footer:
            embed.set_footer(text=footer)
            embed.timestamp = discord.utils.utcnow()

        view = MidtownConfirm(ctx.author.id, timeout=30)
        await msg.edit(content=None, embed=embed, view=view)
        await view.wait()

        if not view.value:
            embed.description = f"Cancelled genreban for {display_user_str}."
            await msg.edit(embed=embed, view=None)
            return

        await self.bot.db.execute(
            "INSERT OR REPLACE INTO globalbans VALUES(?,?,?,?)", user_id, ctx.author.id, reason, now())
        banned, failed = 0, []
        for g in guilds:
            try:
                await g.ban(discord.Object(user_id), reason=f"genreban by {ctx.author}: {reason}")
                banned += 1
            except discord.Forbidden:
                failed.append(g.name)
            except discord.HTTPException:
                failed.append(g.name)

        embed.description = f"**{user.name if user else user_id}** has been genrebaned across **{banned}** server(s)."
        embed.set_footer(text=footer)
        embed.timestamp = discord.utils.utcnow()
        await msg.edit(embed=embed, view=None)

    @commands.command(hidden=True, aliases=["unreban", "unhardban", "globalunban", "unhb"])
    @requires_uperm("supremeleader")
    @has_perms(administrator=True)
    async def ungenreban(self, ctx: Context, target: str):
        """Lift a network genreban across every authorized server"""
        msg = await ctx.warn("Processing...")

        user = None
        user_id = None
        clean_target = target.strip("<@!>")
        try:
            user_id = int(clean_target)
            user = self.bot.get_user(user_id)
            if not user:
                try:
                    user = await self.bot.fetch_user(user_id)
                except Exception:
                    pass
        except ValueError:
            try:
                user = await commands.UserConverter().convert(ctx, target)
                user_id = user.id
            except Exception:
                pass

        if not user_id:
            await msg.delete()
            raise UserError(f"Could not find user `{target}`.")

        display_name = f"@{user.name}" if user else f"User `{user_id}`"
        display_user_str = f"{display_name} ( {user_id} )"

        row = await self.bot.db.fetchone("SELECT 1 FROM globalbans WHERE user_id=?", user_id)
        if not row:
            await msg.delete()
            raise UserError(f"**{user.name if user else user_id}** is not genrebanned.")

        guilds = list(self.bot.guilds)
        guild_count = len(guilds)

        embed = discord.Embed(
            description=f"Ungenreban {display_user_str} from all **{guild_count}** authorized server(s)?",
            color=Colors.warn,
        )
        if self.bot.user:
            embed.set_author(name=self.bot.user.display_name, icon_url=self.bot.user.display_avatar.url)
        footer = await ctx._footer()
        if footer:
            embed.set_footer(text=footer)
            embed.timestamp = discord.utils.utcnow()

        view = MidtownConfirm(ctx.author.id, timeout=30)
        await msg.edit(content=None, embed=embed, view=view)
        await view.wait()

        if not view.value:
            embed.description = f"Cancelled ungenreban for {display_user_str}."
            await msg.edit(embed=embed, view=None)
            return

        await self.bot.db.execute("DELETE FROM globalbans WHERE user_id=?", user_id)
        lifted, failed = 0, []
        for g in guilds:
            try:
                await g.unban(discord.Object(user_id), reason=f"ungenreban by {ctx.author}")
                lifted += 1
            except discord.NotFound:
                pass
            except discord.HTTPException:
                failed.append(g.name)

        embed.description = f"**{user.name if user else user_id}** has been ungenrebaned from **{lifted}** server(s)."
        embed.set_footer(text=footer)
        embed.timestamp = discord.utils.utcnow()
        await msg.edit(embed=embed, view=None)

    @commands.command(hidden=True, aliases=["genrebans", "genrebanlist", "globalbans"])
    async def hardbans(self, ctx: Context):
        """List network hardbans"""
        rows = await self.bot.db.fetchall("SELECT * FROM globalbans ORDER BY at DESC")
        lines = [f"<@{r['user_id']}> (`{r['user_id']}`) — by <@{r['mod_id']}> {ts(r['at'])}\n> {r['reason'] or 'no reason'}"
                 for r in rows]
        await ctx.paginate(chunk_embeds(f"Network hardbans ({len(rows)})", lines, 8, numbered=False))

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        row = await self.bot.db.fetchone("SELECT 1 FROM globalbans WHERE user_id=?", member.id)
        if row:
            try:
                await member.ban(reason="network hardban")
            except discord.HTTPException:
                pass

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        row = await self.bot.db.fetchone("SELECT 1 FROM globalbans WHERE user_id=?", user.id)
        if row:
            await asyncio.sleep(1)
            try:
                await guild.ban(discord.Object(user.id), reason="network hardban (unban attempted)")
            except discord.HTTPException:
                pass

    # ================================================================ join/leave logs
    @commands.command(hidden=True, aliases=["jllogs"])
    @commands.guild_only()
    async def joinleavelogs(self, ctx: Context, channel: discord.TextChannel = None):
        """Send joins, leaves and kicks to their own channel instead of the main log"""
        cfg = await self.bot.db.get_config(ctx.guild.id, "logs")
        cfg["joinleave"] = channel.id if channel else None
        await self.bot.db.set_config(ctx.guild.id, "logs", cfg)
        if channel:
            await ctx.approve(f"Join/leave logs set to {channel.mention}.\n"
                              "Joins, leaves, and kicks will go here instead of the main log.")
        else:
            await ctx.approve("Join/leave logs cleared — they go to the main member log again.")

    # ================================================================ antinuke presets
    @commands.group(hidden=True, aliases=["anpreset"], invoke_without_command=True)
    async def antinukepreset(self, ctx: Context):
        """Save and reuse antinuke configs across servers"""
        await ctx.send_help(ctx.command)

    @antinukepreset.command(name="save")
    @commands.guild_only()
    async def anp_save(self, ctx: Context, name: str):
        """Save this server's antinuke config as a preset"""
        an = await self.bot.db.get_config(ctx.guild.id, "antinuke")
        presets = await self.bot.db.get_config(0, "anpresets")
        presets[name.lower()] = an
        await self.bot.db.set_config(0, "anpresets", presets)
        await ctx.approve(f"Saved antinuke preset **{name.lower()}**.")

    @antinukepreset.command(name="load", aliases=["apply"])
    @commands.guild_only()
    async def anp_load(self, ctx: Context, name: str):
        """Apply a saved preset to this server"""
        presets = await self.bot.db.get_config(0, "anpresets")
        preset = presets.get(name.lower())
        if preset is None:
            raise UserError(f"No preset named `{name.lower()}`")
        # keep this server's own admins/whitelist/log, copy the protection rules
        current = await self.bot.db.get_config(ctx.guild.id, "antinuke")
        merged = dict(preset)
        for keep in ("admins", "whitelist", "log"):
            merged[keep] = current.get(keep, preset.get(keep))
        await self.bot.db.set_config(ctx.guild.id, "antinuke", merged)
        await ctx.approve(f"Applied antinuke preset **{name.lower()}** to this server.")

    @antinukepreset.command(name="list")
    async def anp_list(self, ctx: Context):
        """List antinuke presets"""
        presets = await self.bot.db.get_config(0, "anpresets")
        await ctx.neutral("Antinuke presets: " + (", ".join(f"`{n}`" for n in presets) or "none"))

    @antinukepreset.command(name="delete", aliases=["remove"])
    async def anp_delete(self, ctx: Context, name: str):
        """Delete a preset"""
        presets = await self.bot.db.get_config(0, "anpresets")
        if presets.pop(name.lower(), None) is None:
            raise UserError(f"No preset named `{name.lower()}`")
        await self.bot.db.set_config(0, "anpresets", presets)
        await ctx.approve(f"Deleted preset **{name.lower()}**.")


async def setup(bot):
    await bot.add_cog(DevTools(bot))
