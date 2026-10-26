"""Moderation: bans, timeouts, warns, purge, locks, roles, jail, cases."""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Optional

import discord
from discord.ext import commands

from core.modlog import case_line, create_case, dm_target
import core.utils as _cu
import importlib
if not hasattr(_cu, "check_role_assignable"):
    try:
        importlib.reload(_cu)
    except Exception:
        pass

from core.utils import (DANGEROUS_PERMS, Colors, Context, Duration, UserError, antinuke_guard, check_hierarchy_async,
                        check_role, check_role_assignable, is_role_blacklisted, chunk_embeds, fmt_duration, has_perms, is_dangerous, missing_perms, now, parse_duration, shorten,
                        ts, requires_uperm)

URL = re.compile(r"https?://\S+", re.I)
INVITE = re.compile(r"(discord\.(gg|io|me|li)|discord(app)?\.com/invite)/\S+", re.I)


# ==================================================================== role blacklist views
class SingleMemberBledView(discord.ui.View):
    def __init__(self, cog, ctx: Context, target: discord.Member, roles_to_strip: list[discord.Role]):
        super().__init__(timeout=180)
        self.cog = cog
        self.ctx = ctx
        self.target = target
        self.roles_to_strip = roles_to_strip

    async def interaction_check(self, it: discord.Interaction) -> bool:
        from core import perms as _perms
        is_staff = (
            getattr(getattr(it.user, "guild_permissions", None), "manage_roles", False)
            or getattr(getattr(it.user, "guild_permissions", None), "administrator", False)
            or it.user.id == getattr(it.guild, "owner_id", None)
            or it.user.id in _perms.BOT_DEVELOPERS
        )
        if not is_staff:
            await it.response.send_message("❌ You need the `Manage Roles` permission to use this button.", ephemeral=True)
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        import logging
        logging.getLogger("veil").error(f"Error in {self.__class__.__name__} button {getattr(item, 'label', item)}: {error}", exc_info=error)
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ An error occurred processing this action.", ephemeral=True)
            else:
                await interaction.followup.send("❌ An error occurred processing this action.", ephemeral=True)
        except Exception:
            pass

    @discord.ui.button(label="Strip Roles", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def strip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True

        embed = interaction.message.embeds[0] if (interaction.message and interaction.message.embeds) else discord.Embed()
        from core import perms as _perms
        if _perms.is_bot_developer(self.target, self.cog.bot):
            embed.color = Colors.error
            embed.description = "⚠️ Cannot strip roles from a bot developer."
            embed.set_footer(text="Veil Utilities • Developer Immunity")
            return await interaction.response.edit_message(embed=embed, view=self)

        embed.color = Colors.gold
        embed.set_footer(text="Veil Utilities • Stripping roles in progress...")
        await interaction.response.edit_message(embed=embed, view=self)

        top_pos = getattr(getattr(self.ctx.guild.me, "top_role", None), "position", 999999)
        valid = [r for r in self.roles_to_strip if getattr(r, "position", 0) < top_pos]
        if not valid:
            embed.color = Colors.error
            embed.description = "⚠️ Cannot strip roles — they are above my highest role."
            embed.set_footer(text="Veil Utilities • Hierarchy Error")
            try:
                return await interaction.edit_original_response(embed=embed, view=self)
            except Exception:
                if interaction.message:
                    return await interaction.message.edit(embed=embed, view=self)

        try:
            await self.target.remove_roles(*valid, reason=f"Veil: BLED role strip by {interaction.user}")
        except discord.HTTPException as e:
            embed.color = Colors.error
            embed.description = f"⚠️ Failed to remove roles: {e}"
            embed.set_footer(text="Veil Utilities • Error")
            try:
                return await interaction.edit_original_response(embed=embed, view=self)
            except Exception:
                if interaction.message:
                    return await interaction.message.edit(embed=embed, view=self)

        embed.color = Colors.mint
        embed.description = f"✅ **Stripped Roles**\n\nSuccessfully removed **{len(valid)}** blacklisted role(s) from {self.target.mention}."
        embed.set_footer(text="Veil Utilities • Roles Stripped")
        try:
            await interaction.edit_original_response(embed=embed, view=self)
        except Exception:
            if interaction.message:
                await interaction.message.edit(embed=embed, view=self)

    @discord.ui.button(label="Authorize User", style=discord.ButtonStyle.success, emoji="➕")
    async def auth_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        cfg = await self.cog.bot.db.get_config(self.ctx.guild.id, "roleblacklist")
        auth = cfg.setdefault("authorized", [])
        if self.target.id not in auth:
            auth.append(self.target.id)
            await self.cog.bot.db.set_config(self.ctx.guild.id, "roleblacklist", cfg)

        embed = interaction.message.embeds[0] if (interaction.message and interaction.message.embeds) else discord.Embed()
        embed.color = Colors.mint
        embed.description = f"✅ **User Authorized**\n\n{self.target.mention} has been added to authorized bypasses and is permitted to hold these roles."
        embed.set_footer(text="Veil Utilities • Authorized")
        await interaction.response.edit_message(embed=embed, view=self)


class GuildBledView(discord.ui.View):
    def __init__(self, cog, ctx: Context, unauthorized: list[tuple[discord.Member, list[discord.Role]]]):
        super().__init__(timeout=180)
        self.cog = cog
        self.ctx = ctx
        self.unauthorized = unauthorized

    async def interaction_check(self, it: discord.Interaction) -> bool:
        from core import perms as _perms
        is_staff = (
            getattr(getattr(it.user, "guild_permissions", None), "manage_roles", False)
            or getattr(getattr(it.user, "guild_permissions", None), "administrator", False)
            or it.user.id == getattr(it.guild, "owner_id", None)
            or it.user.id in _perms.BOT_DEVELOPERS
        )
        if not is_staff:
            await it.response.send_message("❌ You need the `Manage Roles` permission to use this button.", ephemeral=True)
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        import logging
        logging.getLogger("veil").error(f"Error in {self.__class__.__name__} button {getattr(item, 'label', item)}: {error}", exc_info=error)
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ An error occurred processing this action.", ephemeral=True)
            else:
                await interaction.followup.send("❌ An error occurred processing this action.", ephemeral=True)
        except Exception:
            pass

    @discord.ui.button(label="Strip All Unauthorized", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def strip_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True

        embed = interaction.message.embeds[0] if (interaction.message and interaction.message.embeds) else discord.Embed()
        embed.color = Colors.gold
        embed.description = "⏳ **Purging Blacklisted Roles...**\n\nStripping blacklisted roles from unauthorized members. Please wait..."
        embed.set_footer(text="Veil Utilities • Purging in progress...")
        # Respond immediately (<50ms) so interaction token never expires
        await interaction.response.edit_message(embed=embed, view=self)

        top_pos = getattr(getattr(self.ctx.guild.me, "top_role", None), "position", 999999)
        done = 0
        removed = 0
        from core import perms as _perms
        for m, roles in self.unauthorized:
            if _perms.is_bot_developer(m, self.cog.bot):
                continue
            valid = [r for r in roles if getattr(r, "position", 0) < top_pos]
            if valid:
                try:
                    await m.remove_roles(*valid, reason=f"Veil: BLED role purge by {interaction.user}")
                    done += 1
                    removed += len(valid)
                except (discord.HTTPException, discord.Forbidden):
                    pass

        embed.color = Colors.success
        embed.description = f"✅ **Purged Blacklisted Roles**\n\nStripped **{removed}** blacklisted role(s) from **{done}** member(s)."
        embed.set_footer(text="Veil Utilities • Purged")
        try:
            await interaction.edit_original_response(embed=embed, view=self)
        except Exception:
            if interaction.message:
                await interaction.message.edit(embed=embed, view=self)

    @discord.ui.button(label="Authorize All", style=discord.ButtonStyle.success, emoji="➕")
    async def auth_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        cfg = await self.cog.bot.db.get_config(self.ctx.guild.id, "roleblacklist")
        auth = cfg.setdefault("authorized", [])
        count = 0
        for m, _ in self.unauthorized:
            if m.id not in auth:
                auth.append(m.id)
                count += 1
        await self.cog.bot.db.set_config(self.ctx.guild.id, "roleblacklist", cfg)

        embed = interaction.message.embeds[0] if (interaction.message and interaction.message.embeds) else discord.Embed()
        embed.color = Colors.mint
        embed.description = f"✅ **All Authorized**\n\nAdded **{count}** member(s) to authorized bypasses."
        embed.set_footer(text="Veil Utilities • Authorized")
        await interaction.response.edit_message(embed=embed, view=self)


class Moderation(commands.Cog):
    """Keep your server in line"""
    emoji = "🔨"

    def __init__(self, bot):
        self.bot = bot

    async def _cfg(self, gid, mod):
        return await self.bot.db.get_config(gid, mod)

    # ================================================================ bans
    @commands.command()
    @requires_uperm("admin")
    @has_perms(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def ban(self, ctx: Context, user: discord.Member | discord.User, *, reason: str = None):
        """Ban a member (or any user by ID). Add `--purge` to delete their last 7 days of messages"""
        delete = 0
        if reason and "--purge" in reason:
            delete, reason = 604800, reason.replace("--purge", "").strip() or None
        if isinstance(user, discord.Member):
            await check_hierarchy_async(ctx, user, "ban")
        await antinuke_guard(ctx, "ban")
        if isinstance(user, discord.Member):
            await dm_target(user, ctx.guild, "banned", ctx.author, reason)
        await ctx.guild.ban(user, reason=f"{ctx.author} ({ctx.author.id}): {reason}", delete_message_seconds=delete)
        num = await create_case(self.bot, ctx.guild, "ban", user, ctx.author, reason)
        await ctx.approve(f"Banned **{user}** • case `#{num}`")

    @commands.command()
    @requires_uperm("admin")
    @has_perms(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def unban(self, ctx: Context, user: discord.User, *, reason: str = None):
        """Unban a user"""
        gb = await self.bot.db.fetchone("SELECT 1 FROM globalbans WHERE user_id=?", user.id)
        if gb and not await self.bot.is_owner(ctx.author):
            raise UserError(f"**{user}** is hardbanned across the network — a developer must lift it with `unhardban`")
        await antinuke_guard(ctx, "unban")
        try:
            await ctx.guild.unban(user, reason=f"{ctx.author}: {reason}")
        except discord.NotFound:
            raise UserError(f"**{user}** isn't banned")
        num = await create_case(self.bot, ctx.guild, "unban", user, ctx.author, reason)
        await ctx.approve(f"Unbanned **{user}** • case `#{num}`")

    @commands.command()
    @requires_uperm("admin")
    @has_perms(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def softban(self, ctx: Context, member: discord.Member, *, reason: str = None):
        """Ban and immediately unban to wipe a member's recent messages"""
        await check_hierarchy_async(ctx, member, "softban")
        await antinuke_guard(ctx, "ban")
        await member.ban(reason=f"softban by {ctx.author}: {reason}", delete_message_seconds=604800)
        await ctx.guild.unban(member, reason="softban")
        num = await create_case(self.bot, ctx.guild, "softban", member, ctx.author, reason)
        await ctx.approve(f"Softbanned **{member}** • case `#{num}`")

    @commands.command(aliases=["banrecent"])
    @requires_uperm("admin")
    @has_perms(administrator=True)
    @commands.bot_has_permissions(ban_members=True)
    async def massban(self, ctx: Context, minutes: int, *, reason: str = "raid cleanup"):
        """Ban everyone who joined in the last N minutes (raid cleanup)"""
        if not 1 <= minutes <= 10080:
            raise UserError("Minutes must be between 1 and 10080 (one week)")
        cutoff = discord.utils.utcnow() - timedelta(minutes=minutes)
        is_owner = ctx.author.id == ctx.guild.owner_id
        # respect the invoker's hierarchy too — fake-permission admins must not be able to sweep up senior staff
        pl_cfg = await ctx.bot.db.get_config(ctx.guild.id, "permlevels") if (ctx.guild and ctx.bot) else {}
        from core import perms as _perms
        targets = [
            m for m in ctx.guild.members if m.joined_at and m.joined_at >= cutoff
            and not m.bot and m != ctx.author and m.id != ctx.guild.owner_id
            and not _perms.is_bot_developer(m, self.bot)
            and m.top_role < ctx.guild.me.top_role and (is_owner or m.top_role < ctx.author.top_role)
            and (is_owner or _perms.rank_of(pl_cfg, m, is_owner=False, is_dev=False, has_admin=False) == 0)
        ]
        if not targets:
            raise UserError("Nobody joined in that window")
        if not await ctx.confirm(f"Ban **{len(targets)}** members who joined in the last {minutes} minutes?"):
            return await ctx.neutral("Cancelled")
        await antinuke_guard(ctx, "ban", len(targets))
        result = await ctx.guild.bulk_ban(targets, reason=f"massban by {ctx.author}: {reason}")
        await create_case(self.bot, ctx.guild, "massban", ctx.author, ctx.author, f"{len(result.banned)} users: {reason}")
        await ctx.approve(f"Banned **{len(result.banned)}** members ({len(result.failed)} failed)")

    @commands.command(aliases=["kickrecent"])
    @requires_uperm("admin")
    @has_perms(kick_members=True)
    @commands.bot_has_permissions(kick_members=True)
    async def masskick(self, ctx: Context, minutes: int, *, reason: str = "raid cleanup"):
        """Kick everyone who joined in the last N minutes (raid cleanup)"""
        if not 1 <= minutes <= 10080:
            raise UserError("Minutes must be between 1 and 10080 (one week)")
        cutoff = discord.utils.utcnow() - timedelta(minutes=minutes)
        is_owner = ctx.author.id == ctx.guild.owner_id
        pl_cfg = await ctx.bot.db.get_config(ctx.guild.id, "permlevels") if (ctx.guild and ctx.bot) else {}
        from core import perms as _perms
        targets = [
            m for m in ctx.guild.members if m.joined_at and m.joined_at >= cutoff
            and not m.bot and m != ctx.author and m.id != ctx.guild.owner_id
            and not _perms.is_bot_developer(m, self.bot)
            and m.top_role < ctx.guild.me.top_role and (is_owner or m.top_role < ctx.author.top_role)
            and (is_owner or _perms.rank_of(pl_cfg, m, is_owner=False, is_dev=False, has_admin=False) == 0)
        ]
        if not targets:
            raise UserError("Nobody joined in that window")
        if not await ctx.confirm(f"Kick **{len(targets)}** members who joined in the last {minutes} minutes?"):
            return await ctx.neutral("Cancelled")
        await antinuke_guard(ctx, "kick", len(targets))
        done, failed = 0, 0
        for m in targets:
            try:
                await m.kick(reason=f"masskick by {ctx.author}: {reason}")
                done += 1
            except discord.HTTPException:
                failed += 1
        await create_case(self.bot, ctx.guild, "masskick", ctx.author, ctx.author, f"{done} users: {reason}")
        await ctx.approve(f"Kicked **{done}** members ({failed} failed)")

    # ================================================================ kick / timeout
    @commands.command()
    @requires_uperm("mod")
    @has_perms(kick_members=True)
    @commands.bot_has_permissions(kick_members=True)
    async def kick(self, ctx: Context, member: discord.Member, *, reason: str = None):
        """Kick a member"""
        await check_hierarchy_async(ctx, member, "kick")
        await antinuke_guard(ctx, "kick")
        await dm_target(member, ctx.guild, "kicked", ctx.author, reason)
        await member.kick(reason=f"{ctx.author}: {reason}")
        num = await create_case(self.bot, ctx.guild, "kick", member, ctx.author, reason)
        await ctx.approve(f"Kicked **{member}** • case `#{num}`")

    @commands.command(aliases=["mute", "to", "tmute"])
    @requires_uperm("mod")
    @has_perms(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True)
    async def timeout(self, ctx: Context, member: discord.Member, duration: Optional[Duration] = None, *, reason: str = None):
        """Timeout a member (default 5m, max 28d)"""
        await check_hierarchy_async(ctx, member, "timeout")
        duration = duration or timedelta(minutes=5)
        if duration > timedelta(days=28):
            raise UserError("Timeouts can be at most 28 days")
        await antinuke_guard(ctx, "timeout")
        await member.timeout(duration, reason=f"{ctx.author}: {reason}")
        human = fmt_duration(duration.total_seconds())
        await dm_target(member, ctx.guild, "timed out", ctx.author, reason, human)
        num = await create_case(self.bot, ctx.guild, f"timeout ({human})", member, ctx.author, reason)
        await ctx.approve(f"Timed out **{member}** for **{human}** • case `#{num}`")

    @commands.group(aliases=["unmute", "untime", "uto"], invoke_without_command=True)
    @requires_uperm("mod")
    @has_perms(moderate_members=True)
    async def untimeout(self, ctx: Context, member: discord.Member, *, reason: str = None):
        """Remove a timeout (use `untimeout all` for everyone)"""
        await check_hierarchy_async(ctx, member, "untimeout")
        if not member.is_timed_out():
            raise UserError(f"{member.mention} isn't timed out")
        await member.timeout(None, reason=f"{ctx.author}: {reason}")
        await create_case(self.bot, ctx.guild, "untimeout", member, ctx.author, reason)
        await ctx.approve(f"{member.mention}'s timeout has been removed.")

    @untimeout.command(name="all")
    @requires_uperm("admin")
    @has_perms(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True)
    async def untimeout_all(self, ctx: Context, *, reason: str = None):
        """Remove timeouts from every timed-out member"""
        timed = [m for m in ctx.guild.members if m.is_timed_out()]
        if not timed:
            raise UserError("Nobody is timed out")
        done = 0
        for m in timed:
            try:
                await m.timeout(None, reason=f"unmute all by {ctx.author}: {reason}")
                done += 1
            except discord.HTTPException:
                pass
        await ctx.approve(f"Removed timeouts from **{done}** member(s).")

    # ================================================================ warns
    @commands.command()
    @requires_uperm("mod")
    @has_perms(moderate_members=True)
    async def warn(self, ctx: Context, member: discord.Member, *, reason: str = "No reason provided"):
        """Warn a member. Auto-punishments can be set with `warnpunish`"""
        await check_hierarchy_async(ctx, member, "warn")
        await self.bot.db.execute("INSERT INTO warns(guild_id,user_id,mod_id,reason,created_at) VALUES(?,?,?,?,?)",
                                  ctx.guild.id, member.id, ctx.author.id, reason, now())
        count = (await self.bot.db.fetchone("SELECT COUNT(*) c FROM warns WHERE guild_id=? AND user_id=?",
                                            ctx.guild.id, member.id))["c"]
        await dm_target(member, ctx.guild, "warned", ctx.author, reason)
        num = await create_case(self.bot, ctx.guild, "warn", member, ctx.author, reason)
        await ctx.approve(f"Warned **{member}** (warning **{count}**) • case `#{num}`")
        # escalation
        rules = await self._cfg(ctx.guild.id, "warnpunish")
        action = rules.get(str(count))
        if action:
            if action in ("kick", "ban"):  # the warning stands even if antinuke blocks the escalation
                await antinuke_guard(ctx, action)
            try:
                if action == "kick":
                    await member.kick(reason=f"Reached {count} warnings")
                elif action == "ban":
                    await member.ban(reason=f"Reached {count} warnings")
                elif action.startswith("timeout"):
                    secs = int(action.split(":")[1])
                    await member.timeout(timedelta(seconds=secs), reason=f"Reached {count} warnings")
                await ctx.neutral(f"⚡ Auto-punishment: **{action.split(':')[0]}** for reaching {count} warnings")
            except discord.HTTPException:
                pass

    @commands.command()
    @requires_uperm("admin")
    @has_perms(administrator=True)
    async def warnpunish(self, ctx: Context, warnings: int, action: str, duration: Duration = None):
        """Auto-punish at N warnings: `warnpunish 3 timeout 1h`, `warnpunish 5 ban`, `warnpunish 3 none`"""
        rules = await self._cfg(ctx.guild.id, "warnpunish")
        action = action.lower()
        if action == "none":
            rules.pop(str(warnings), None)
        elif action in ("kick", "ban"):
            rules[str(warnings)] = action
        elif action in ("timeout", "mute"):
            rules[str(warnings)] = f"timeout:{int((duration or timedelta(hours=1)).total_seconds())}"
        else:
            raise UserError("Action must be `timeout`, `kick`, `ban` or `none`")
        await self.bot.db.set_config(ctx.guild.id, "warnpunish", rules)
        await ctx.approve(f"At **{warnings}** warnings → **{action}**")

    @commands.command(aliases=["warns"])
    @has_perms(moderate_members=True)
    async def warnings(self, ctx: Context, member: discord.Member | discord.User):
        """List a member's warnings"""
        rows = await self.bot.db.fetchall("SELECT * FROM warns WHERE guild_id=? AND user_id=? ORDER BY id DESC",
                                          ctx.guild.id, member.id)
        lines = [f"**ID {r['id']}** by <@{r['mod_id']}> {ts(r['created_at'])}\n> {r['reason']}" for r in rows]
        await ctx.paginate(chunk_embeds(f"Warnings for {member}", lines, 5, numbered=False))

    @commands.command(aliases=["clearwarnings"])
    @requires_uperm("mod")
    @has_perms(moderate_members=True)
    async def clearwarns(self, ctx: Context, member: discord.Member | discord.User):
        """Clear all of a member's warnings"""
        self._check_warn_target(ctx, member, "clear warnings for")
        cur = await self.bot.db.execute("DELETE FROM warns WHERE guild_id=? AND user_id=?", ctx.guild.id, member.id)
        if cur.rowcount == 0:
            raise UserError(f"**{member}** has no warnings")
        await create_case(self.bot, ctx.guild, "clearwarns", member, ctx.author, f"Cleared {cur.rowcount} warning(s)")
        await ctx.approve(f"Cleared **{cur.rowcount}** warning(s) for **{member}**")

    @commands.command(aliases=["unwarn"])
    @requires_uperm("mod")
    @has_perms(moderate_members=True)
    async def delwarn(self, ctx: Context, warn_id: int):
        """Delete a single warning by ID"""
        row = await self.bot.db.fetchone("SELECT user_id FROM warns WHERE guild_id=? AND id=?", ctx.guild.id, warn_id)
        if row is None:
            raise UserError("No warning with that ID")
        target = ctx.guild.get_member(row["user_id"]) or discord.Object(row["user_id"])
        self._check_warn_target(ctx, target, "remove warnings from")
        await self.bot.db.execute("DELETE FROM warns WHERE guild_id=? AND id=?", ctx.guild.id, warn_id)
        await ctx.approve(f"Deleted warning `{warn_id}`")

    @staticmethod
    def _check_warn_target(ctx: Context, target, action: str) -> None:
        """Mods can't wipe their own record or the record of someone at/above their top role."""
        if ctx.bot.owner_ids and target.id in ctx.bot.owner_ids:
            raise UserError(f"You can't {action} a bot developer")
        if ctx.author.id == ctx.guild.owner_id:
            return
        if target.id == ctx.author.id:
            raise UserError(f"You can't {action} yourself")
        if isinstance(target, discord.Member) and target.top_role >= ctx.author.top_role:
            raise UserError(f"You can't {action} {target.mention}, their top role is equal or higher than yours")

    # ================================================================ cases
    @commands.command(aliases=["history", "modlogs"])
    @has_perms(moderate_members=True)
    async def cases(self, ctx: Context, user: discord.Member | discord.User = None):
        """View moderation history (for the server or a user)"""
        if user:
            rows = await self.bot.db.fetchall("SELECT * FROM cases WHERE guild_id=? AND user_id=? ORDER BY case_num DESC",
                                              ctx.guild.id, user.id)
        else:
            rows = await self.bot.db.fetchall("SELECT * FROM cases WHERE guild_id=? ORDER BY case_num DESC LIMIT 200",
                                              ctx.guild.id)
        await ctx.paginate(chunk_embeds(f"Cases{f' for {user}' if user else ''}", [case_line(r) for r in rows], 6,
                                        numbered=False))

    @commands.command()
    @has_perms(moderate_members=True)
    async def reason(self, ctx: Context, case: int, *, reason: str):
        """Update the reason on a case (your own cases, or any case with Manage Server)"""
        row = await self.bot.db.fetchone("SELECT mod_id, user_id FROM cases WHERE guild_id=? AND case_num=?",
                                         ctx.guild.id, case)
        if row is None:
            raise UserError(f"Case `#{case}` doesn't exist")
        if row["user_id"] == ctx.author.id and ctx.author.id != ctx.guild.owner_id:
            raise UserError("You can't edit a case about yourself")
        if row["mod_id"] != ctx.author.id and await missing_perms(ctx, manage_guild=True):
            raise UserError("You can only edit the reason on your own cases (Manage Server can edit any)")
        await self.bot.db.execute("UPDATE cases SET reason=? WHERE guild_id=? AND case_num=?",
                                  shorten(reason, 1000), ctx.guild.id, case)
        await ctx.approve(f"Updated reason for case `#{case}`")

    # ================================================================ purge
    async def _purge(self, ctx: Context, amount: int, check=lambda m: True):
        amount = max(1, min(amount, 1000))
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass
        matched = 0

        def pred(m):
            nonlocal matched
            if matched >= amount or m.pinned:
                return False
            if check(m):
                matched += 1
                return True
            return False

        deleted = await ctx.channel.purge(limit=min(amount * 5, 2000), check=pred, bulk=True,
                                          reason=f"purge by {ctx.author}")
        await ctx.approve(f"Purged **{len(deleted)}** messages", delete_after=4)

    @commands.group(aliases=["clear", "c", "prune"], invoke_without_command=True)
    @requires_uperm("helper")
    @has_perms(manage_messages=True)
    @commands.bot_has_permissions(manage_messages=True)
    async def purge(self, ctx: Context, amount: int, member: discord.Member = None):
        """Delete messages (optionally only from one member)"""
        await self._purge(ctx, amount, (lambda m: m.author.id == member.id) if member else (lambda m: True))

    @purge.command(name="bots")
    @has_perms(manage_messages=True)
    async def purge_bots(self, ctx: Context, amount: int = 100):
        """Delete messages from bots"""
        await self._purge(ctx, amount, lambda m: m.author.bot)

    @purge.command(name="humans")
    @has_perms(manage_messages=True)
    async def purge_humans(self, ctx: Context, amount: int = 100):
        """Delete messages from humans"""
        await self._purge(ctx, amount, lambda m: not m.author.bot)

    @purge.command(name="links")
    @has_perms(manage_messages=True)
    async def purge_links(self, ctx: Context, amount: int = 100):
        """Delete messages containing links"""
        await self._purge(ctx, amount, lambda m: bool(URL.search(m.content)))

    @purge.command(name="invites")
    @has_perms(manage_messages=True)
    async def purge_invites(self, ctx: Context, amount: int = 100):
        """Delete messages containing invites"""
        await self._purge(ctx, amount, lambda m: bool(INVITE.search(m.content)))

    @purge.command(name="images", aliases=["files", "attachments"])
    @has_perms(manage_messages=True)
    async def purge_images(self, ctx: Context, amount: int = 100):
        """Delete messages with attachments"""
        await self._purge(ctx, amount, lambda m: bool(m.attachments))

    @purge.command(name="embeds")
    @has_perms(manage_messages=True)
    async def purge_embeds(self, ctx: Context, amount: int = 100):
        """Delete messages with embeds"""
        await self._purge(ctx, amount, lambda m: bool(m.embeds))

    @purge.command(name="contains", aliases=["has"])
    @has_perms(manage_messages=True)
    async def purge_contains(self, ctx: Context, substring: str, amount: int = 100):
        """Delete messages containing text"""
        await self._purge(ctx, amount, lambda m: substring.lower() in m.content.lower())

    @purge.command(name="mentions")
    @has_perms(manage_messages=True)
    async def purge_mentions(self, ctx: Context, amount: int = 100):
        """Delete messages that mention users"""
        await self._purge(ctx, amount, lambda m: bool(m.mentions))

    @purge.command(name="after")
    @has_perms(manage_messages=True)
    async def purge_after(self, ctx: Context, message_id: int):
        """Delete every message after a message ID"""
        await ctx.message.delete()
        deleted = await ctx.channel.purge(limit=1000, after=discord.Object(message_id), check=lambda m: not m.pinned)
        await ctx.approve(f"Purged **{len(deleted)}** messages", delete_after=4)

    @commands.command(aliases=["bc"])
    @requires_uperm("helper")
    @has_perms(manage_messages=True)
    async def botclear(self, ctx: Context, amount: int = 100):
        """Delete bot messages and command invocations"""
        prefix = ctx.prefix
        await self._purge(ctx, amount, lambda m: m.author.bot or m.content.startswith(prefix))

    # ================================================================ channels
    @commands.command()
    @requires_uperm("admin")
    @has_perms(manage_channels=True)
    async def lock(self, ctx: Context, channel: discord.TextChannel = None, *, reason: str = None):
        """Prevent @everyone from sending messages"""
        channel = channel or ctx.channel
        ow = channel.overwrites_for(ctx.guild.default_role)
        ow.send_messages = False
        ow.add_reactions = False
        ow.create_public_threads = False
        await channel.set_permissions(ctx.guild.default_role, overwrite=ow, reason=f"lock by {ctx.author}: {reason}")
        await ctx.approve(f"Locked {channel.mention}")

    @commands.command()
    @requires_uperm("admin")
    @has_perms(manage_channels=True)
    async def unlock(self, ctx: Context, channel: discord.TextChannel = None, *, reason: str = None):
        """Allow @everyone to send messages again"""
        channel = channel or ctx.channel
        ow = channel.overwrites_for(ctx.guild.default_role)
        ow.send_messages = None
        ow.add_reactions = None
        ow.create_public_threads = None
        await channel.set_permissions(ctx.guild.default_role, overwrite=ow, reason=f"unlock by {ctx.author}: {reason}")
        await ctx.approve(f"Unlocked {channel.mention}")

    @commands.command()
    @requires_uperm("headadmin")
    @has_perms(administrator=True)
    @commands.max_concurrency(1, commands.BucketType.guild)
    async def lockdown(self, ctx: Context, *, reason: str = "lockdown"):
        """Lock every text channel in the server"""
        settings = await self._cfg(ctx.guild.id, "settings")
        ignore = set(settings.get("lockdown_ignore", []))
        locked = 0
        for ch in ctx.guild.text_channels:
            if ch.id in ignore:
                continue
            ow = ch.overwrites_for(ctx.guild.default_role)
            if ow.send_messages is False:
                continue
            ow.send_messages = False
            try:
                await ch.set_permissions(ctx.guild.default_role, overwrite=ow, reason=f"lockdown by {ctx.author}: {reason}")
                locked += 1
            except discord.HTTPException:
                pass
        await ctx.approve(f"Locked **{locked}** channels")

    @commands.command()
    @requires_uperm("headadmin")
    @has_perms(administrator=True)
    @commands.max_concurrency(1, commands.BucketType.guild)
    async def unlockdown(self, ctx: Context):
        """Unlock every text channel"""
        n = 0
        for ch in ctx.guild.text_channels:
            ow = ch.overwrites_for(ctx.guild.default_role)
            if ow.send_messages is False:
                ow.send_messages = None
                try:
                    await ch.set_permissions(ctx.guild.default_role, overwrite=ow, reason=f"unlockdown by {ctx.author}")
                    n += 1
                except discord.HTTPException:
                    pass
        await ctx.approve(f"Unlocked **{n}** channels")

    @commands.command()
    @requires_uperm("admin")
    @has_perms(manage_channels=True)
    async def hide(self, ctx: Context, channel: discord.abc.GuildChannel = None):
        """Hide a channel from @everyone"""
        channel = channel or ctx.channel
        ow = channel.overwrites_for(ctx.guild.default_role)
        ow.view_channel = False
        await channel.set_permissions(ctx.guild.default_role, overwrite=ow)
        await ctx.approve(f"Hid {channel.mention}")

    @commands.command(aliases=["reveal"])
    @requires_uperm("admin")
    @has_perms(manage_channels=True)
    async def unhide(self, ctx: Context, channel: discord.abc.GuildChannel = None):
        """Reveal a hidden channel"""
        channel = channel or ctx.channel
        ow = channel.overwrites_for(ctx.guild.default_role)
        ow.view_channel = None
        await channel.set_permissions(ctx.guild.default_role, overwrite=ow)
        await ctx.approve(f"Revealed {channel.mention}")

    @commands.command(aliases=["sm"])
    @requires_uperm("admin")
    @has_perms(manage_channels=True)
    async def slowmode(self, ctx: Context, delay: str = "0", channel: discord.TextChannel = None):
        """Set slowmode (e.g. `10s`, `5m`, `0` to disable)"""
        channel = channel or ctx.channel
        secs = 0 if delay in ("0", "off") else int(parse_duration(delay).total_seconds())
        if secs > 21600:
            raise UserError("Slowmode can be at most 6 hours")
        await channel.edit(slowmode_delay=secs)
        await ctx.approve(f"Slowmode in {channel.mention} set to **{fmt_duration(secs)}**" if secs else f"Disabled slowmode in {channel.mention}")

    @commands.command()
    @requires_uperm("headadmin")
    @has_perms(administrator=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def nuke(self, ctx: Context):
        """Clone this channel and delete the original (wipes all messages)"""
        if not await ctx.confirm(f"Nuke {ctx.channel.mention}? This deletes every message."):
            return await ctx.neutral("Cancelled")
        await antinuke_guard(ctx, "channeldelete")
        new = await ctx.channel.clone(reason=f"nuked by {ctx.author}")
        await new.edit(position=ctx.channel.position)
        await ctx.channel.delete(reason=f"nuked by {ctx.author}")
        await new.send(embed=discord.Embed(description=f"💥 Channel nuked by {ctx.author.mention}", color=Colors.main))

    # ================================================================ roles
    async def _resolve_member_role(self, ctx: Context, arg1: str, arg2: str) -> tuple[discord.Member, discord.Role]:
        member = None
        try:
            member = await commands.MemberConverter().convert(ctx, arg1)
        except commands.BadArgument:
            pass

        if member:
            try:
                role = await commands.RoleConverter().convert(ctx, arg2)
            except commands.BadArgument:
                raise UserError(f"Role `{arg2}` not found.")
            return member, role

        try:
            role = await commands.RoleConverter().convert(ctx, arg1)
        except commands.BadArgument:
            raise UserError(f"Member `{arg1}` not found.")
        try:
            member = await commands.MemberConverter().convert(ctx, arg2)
        except commands.BadArgument:
            raise UserError(f"Member `{arg2}` not found.")
        return member, role

    # Role commands reply in the compact style (core.utils.Context.done / .fail): a green line on success,
    # a red "failed to …: reason" line on any problem. compact_action / compact_role feed that failure line.
    @staticmethod
    def _role_target(ctx: Context, action: str, role: discord.Role) -> None:
        ctx.compact_action = action
        ctx.compact_role = role

    async def _authorize_bled_target(self, ctx: Context, member: discord.Member, role: discord.Role) -> bool:
        """Giving someone a blacklisted role (allowed for authorized staff) also authorizes them to hold it.
        Returns True if they were newly authorized."""
        if not await is_role_blacklisted(ctx.guild.id, role.id, self.bot.db):
            return False
        rbl_cfg = await self.bot.db.get_config(ctx.guild.id, "roleblacklist")
        auth = rbl_cfg.setdefault("authorized", [])
        if member.id in auth:
            return False
        auth.append(member.id)
        await self.bot.db.set_config(ctx.guild.id, "roleblacklist", rbl_cfg)
        return True

    async def _give_role(self, ctx: Context, member: discord.Member, role: discord.Role) -> None:
        self._role_target(ctx, f"add {role.mention} to {member.mention}", role)
        await check_role_assignable(ctx, role)
        await check_hierarchy_async(ctx, member, "modify roles on")
        if role in member.roles:
            raise UserError("they already have it")
        if is_dangerous(role.permissions):
            await antinuke_guard(ctx, "permissions")
        newly_authorized = await self._authorize_bled_target(ctx, member, role)
        await member.add_roles(role, reason=f"by {ctx.author}")
        note = " · they're now authorized to hold this blacklisted role" if newly_authorized else ""
        await ctx.done(f"added {role.mention} to {member.mention}{note}")

    async def _take_role(self, ctx: Context, member: discord.Member, role: discord.Role) -> None:
        self._role_target(ctx, f"remove {role.mention} from {member.mention}", role)
        check_role(ctx, role)
        await check_hierarchy_async(ctx, member, "modify roles on")
        if role not in member.roles:
            raise UserError("they don't have it")
        await member.remove_roles(role, reason=f"by {ctx.author}")
        await ctx.done(f"removed {role.mention} from {member.mention}")

    @commands.group(name="role", aliases=["r", "changerole", "cr"], invoke_without_command=True,
                    usage="<member> <role>", extras={"compact": True, "example": "@user @role"})
    @requires_uperm("admin")
    @has_perms(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def role(self, ctx: Context, arg1: str, *, arg2: str = None):
        """Give or take a role: `,r @User @Role` (either order). Has it → removed, doesn't → added"""
        if not arg2:
            raise UserError(f"usage: `{ctx.clean_prefix}r <member> <role>` or `{ctx.clean_prefix}r add/remove <member> <role>`")
        member, role = await self._resolve_member_role(ctx, arg1, arg2)
        if role in member.roles:
            await self._take_role(ctx, member, role)
        else:
            await self._give_role(ctx, member, role)

    async def _do_role_add(self, ctx: Context, arg1: str, arg2: str):
        member, role = await self._resolve_member_role(ctx, arg1, arg2)
        await self._give_role(ctx, member, role)

    async def _do_role_remove(self, ctx: Context, arg1: str, arg2: str):
        member, role = await self._resolve_member_role(ctx, arg1, arg2)
        await self._take_role(ctx, member, role)

    @role.command(name="add", usage="<member> <role>", extras={"example": "@user @role"})
    @requires_uperm("admin")
    @has_perms(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def role_add(self, ctx: Context, arg1: str, *, arg2: str):
        """Add a role to a member: `,r add @User @Role`"""
        await self._do_role_add(ctx, arg1, arg2)

    @role.command(name="remove", aliases=["rem", "del"], usage="<member> <role>", extras={"example": "@user @role"})
    @requires_uperm("admin")
    @has_perms(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def role_remove(self, ctx: Context, arg1: str, *, arg2: str):
        """Remove a role from a member: `,r remove @User @Role`"""
        await self._do_role_remove(ctx, arg1, arg2)

    @commands.command(name="addrole", aliases=["giverole"], usage="<member> <role>",
                      extras={"compact": True, "example": "@user @role"})
    @requires_uperm("admin")
    @has_perms(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def cmd_addrole(self, ctx: Context, arg1: str, *, arg2: str):
        """Add a role to a member: `,addrole @User @Role`"""
        await self._do_role_add(ctx, arg1, arg2)

    @commands.command(name="removerole", aliases=["takerole"], usage="<member> <role>",
                      extras={"compact": True, "example": "@user @role"})
    @requires_uperm("admin")
    @has_perms(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def cmd_removerole(self, ctx: Context, arg1: str, *, arg2: str):
        """Remove a role from a member: `,removerole @User @Role`"""
        await self._do_role_remove(ctx, arg1, arg2)

    @role.command(name="create")
    @requires_uperm("admin")
    @has_perms(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def role_create(self, ctx: Context, *, name: str):
        """Create a role"""
        ctx.compact_action = f"create **{discord.utils.escape_markdown(name)}**"
        await antinuke_guard(ctx, "rolecreate")
        r = await ctx.guild.create_role(name=name, reason=f"by {ctx.author}")
        await ctx.done(f"created {r.mention}")

    @role.command(name="delete")
    @requires_uperm("admin")
    @has_perms(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def role_delete(self, ctx: Context, *, role: discord.Role):
        """Delete a role"""
        self._role_target(ctx, f"delete {role.mention}", role)
        check_role(ctx, role)
        await antinuke_guard(ctx, "roledelete")
        await role.delete(reason=f"by {ctx.author}")
        await ctx.done(f"deleted **{discord.utils.escape_markdown(role.name)}**")

    @role.command(name="color", aliases=["colour"])
    @requires_uperm("admin")
    @has_perms(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def role_color(self, ctx: Context, role: discord.Role, color: discord.Color):
        """Change a role's color"""
        self._role_target(ctx, f"change {role.mention}'s color", role)
        check_role(ctx, role)
        await role.edit(color=color)
        await ctx.done(f"changed {role.mention}'s color to `{color}`")

    @role.command(name="rename")
    @requires_uperm("admin")
    @has_perms(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def role_rename(self, ctx: Context, role: discord.Role, *, name: str):
        """Rename a role"""
        self._role_target(ctx, f"rename {role.mention}", role)
        check_role(ctx, role)
        old = role.name
        await role.edit(name=name)
        await ctx.done(f"renamed **{discord.utils.escape_markdown(old)}** to {role.mention}")

    @role.command(name="hoist")
    @requires_uperm("admin")
    @has_perms(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def role_hoist(self, ctx: Context, *, role: discord.Role):
        """Toggle showing a role separately"""
        self._role_target(ctx, f"change {role.mention}", role)
        check_role(ctx, role)
        await role.edit(hoist=not role.hoist)
        await ctx.done(f"{role.mention} is {'now' if not role.hoist else 'no longer'} shown separately")

    async def _mass_role(self, ctx: Context, action: str, role: discord.Role, which, who: str):
        action = action.lower()
        if action not in ("add", "remove"):
            raise UserError(f"use `add` or `remove`, e.g. `{ctx.clean_prefix}r {ctx.command.name} add @Role`")
        add = action == "add"
        self._role_target(ctx, f"{action} {role.mention} {'to' if add else 'from'} {who}", role)
        if add:
            await check_role_assignable(ctx, role)
        else:
            check_role(ctx, role)
        targets = [m for m in ctx.guild.members if which(m) and ((role not in m.roles) if add else (role in m.roles))]
        if not targets:
            raise UserError("nobody to update")
        msg = await ctx.compact(Colors.neutral, f"⏳ updating **{len(targets)}** members…")
        done = 0
        for m in targets:
            try:
                await (m.add_roles if add else m.remove_roles)(role, reason=f"mass role by {ctx.author}")
                done += 1
            except discord.HTTPException:
                pass
        skipped = len(targets) - done
        text = f"{'added' if add else 'removed'} {role.mention} {'to' if add else 'from'} **{done}** members"
        if skipped:
            text += f" (couldn't update **{skipped}**)"
        embed = discord.Embed(color=Colors.success if done else Colors.error,
                              description=text if done else f"failed to {action} {role.mention}: couldn't update anyone")
        try:
            await msg.edit(embed=embed)
        except (AttributeError, discord.HTTPException):
            await (ctx.done(text) if done else ctx.fail(embed.description))

    @role.command(name="all")
    @requires_uperm("headadmin")
    @has_perms(administrator=True)
    @commands.bot_has_permissions(manage_roles=True)
    @commands.max_concurrency(1, commands.BucketType.guild)
    async def role_all(self, ctx: Context, action: str, *, role: discord.Role):
        """Add/remove a role for everyone: `role all add @Role`"""
        await self._mass_role(ctx, action, role, lambda m: True, "everyone")

    @role.command(name="humans")
    @requires_uperm("headadmin")
    @has_perms(administrator=True)
    @commands.bot_has_permissions(manage_roles=True)
    @commands.max_concurrency(1, commands.BucketType.guild)
    async def role_humans(self, ctx: Context, action: str, *, role: discord.Role):
        """Add/remove a role for all humans"""
        await self._mass_role(ctx, action, role, lambda m: not m.bot, "all humans")

    @role.command(name="bots")
    @requires_uperm("headadmin")
    @has_perms(administrator=True)
    @commands.bot_has_permissions(manage_roles=True)
    @commands.max_concurrency(1, commands.BucketType.guild)
    async def role_bots(self, ctx: Context, action: str, *, role: discord.Role):
        """Add/remove a role for all bots"""
        await self._mass_role(ctx, action, role, lambda m: m.bot, "all bots")

    @commands.command()
    @requires_uperm("headadmin")
    @has_perms(administrator=True)
    async def stripstaff(self, ctx: Context, member: discord.Member):
        """Remove every role with dangerous permissions from a member"""
        await check_hierarchy_async(ctx, member, "strip")
        roles = [r for r in member.roles if is_dangerous(r.permissions) and r < ctx.guild.me.top_role and not r.managed]
        if not roles:
            raise UserError(f"{member.mention} has no staff roles I can remove")
        await member.remove_roles(*roles, reason=f"stripstaff by {ctx.author}")
        await create_case(self.bot, ctx.guild, "stripstaff", member, ctx.author, f"removed {len(roles)} roles")
        await ctx.approve(f"Stripped **{len(roles)}** staff roles from {member.mention}")

    @commands.command(aliases=["rolelist"])
    @commands.guild_only()
    async def roles(self, ctx: Context):
        """List every role in the server, highest first"""
        rs = sorted([r for r in ctx.guild.roles if not r.is_default()], key=lambda r: r.position, reverse=True)
        lines = [f"{r.mention} — `{r.id}` · {len(r.members)} member(s)" for r in rs]
        await ctx.paginate(chunk_embeds(f"Server Roles ({len(rs)})", lines))

    # ================================================================ role blacklist
    def _is_role_authorized(self, guild: discord.Guild, member: discord.Member, cfg: dict) -> bool:
        from core import perms as _perms
        mid = getattr(member, "id", 0)
        if _perms.is_bot_developer(mid, self.bot) or mid == getattr(guild, "owner_id", None):
            return True
        if mid in _perms.CODE_USERS:
            return True
        auth_list = cfg.get("authorized", [])
        if mid in auth_list:
            return True
        if any(getattr(r, "id", 0) in auth_list for r in getattr(member, "roles", [])):
            return True
        return False

    @commands.group(aliases=["rbl", "rolebl"], invoke_without_command=True)
    @requires_uperm("admin")
    @has_perms(manage_roles=True)
    async def roleblacklist(self, ctx: Context):
        """Blacklist roles so the bot refuses to assign them"""
        await ctx.send_help(ctx.command)

    @roleblacklist.command(name="add")
    @requires_uperm("admin")
    @has_perms(manage_roles=True)
    async def rbl_add(self, ctx: Context, *, role: discord.Role):
        """Blacklist a role"""
        cfg = await self.bot.db.get_config(ctx.guild.id, "roleblacklist")
        roles = cfg.setdefault("roles", [])
        if role.id in roles:
            raise UserError(f"{role.mention} is already blacklisted")
        roles.append(role.id)
        await self.bot.db.set_config(ctx.guild.id, "roleblacklist", cfg)

        # Strip from current members if any currently hold it (skipping authorized members & devs)
        top_pos = getattr(getattr(ctx.guild.me, "top_role", None), "position", 999999)
        if ctx.guild.me.guild_permissions.manage_roles and getattr(role, "position", 0) < top_pos:
            for member in list(ctx.guild.members):
                if self._is_role_authorized(ctx.guild, member, cfg):
                    continue
                if role in getattr(member, "roles", []):
                    try:
                        await member.remove_roles(role, reason=f"Role blacklisted by {ctx.author}")
                    except discord.HTTPException:
                        pass

        # Purge from permlevels if present
        pcfg = await self.bot.db.get_config(ctx.guild.id, "permlevels")
        if "roles" in pcfg and str(role.id) in pcfg["roles"]:
            del pcfg["roles"][str(role.id)]
            await self.bot.db.set_config(ctx.guild.id, "permlevels", pcfg)

        await ctx.approve(f"{role.mention} has been blacklisted.")

    @roleblacklist.command(name="remove", aliases=["rem", "delete"])
    @requires_uperm("admin")
    @has_perms(manage_roles=True)
    async def rbl_remove(self, ctx: Context, *, role: str):
        """Unblacklist a role, or `remove deleted` to purge deleted roles"""
        cfg = await self.bot.db.get_config(ctx.guild.id, "roleblacklist")
        roles = cfg.setdefault("roles", [])
        if role.lower() == "deleted":
            gone = [rid for rid in roles if ctx.guild.get_role(rid) is None]
            if not gone:
                return await ctx.warn("No deleted roles in the blacklist.")
            cfg["roles"] = [rid for rid in roles if rid not in gone]
            await self.bot.db.set_config(ctx.guild.id, "roleblacklist", cfg)
            return await ctx.approve(f"Removed **{len(gone)}** deleted role(s) from the blacklist.")

        if role.isdigit() and int(role) in roles:
            rid = int(role)
            resolved_mention = f"<@&{rid}>"
        else:
            resolved = await commands.RoleConverter().convert(ctx, role)
            rid = resolved.id
            resolved_mention = resolved.mention

        if rid not in roles:
            raise UserError(f"{resolved_mention} isn't blacklisted")
        roles.remove(rid)
        await self.bot.db.set_config(ctx.guild.id, "roleblacklist", cfg)
        await ctx.approve(f"{resolved_mention} has been unblacklisted.")

    @roleblacklist.command(name="list")
    @has_perms(manage_roles=True)
    async def rbl_list(self, ctx: Context):
        """View blacklisted roles"""
        cfg = await self.bot.db.get_config(ctx.guild.id, "roleblacklist")
        roles = cfg.get("roles", [])
        if not roles:
            return await ctx.neutral("No roles are currently blacklisted in this server.")
        lines = [f"<@&{rid}> (`{rid}`)" for rid in roles]
        await ctx.paginate(chunk_embeds("Blacklisted roles", lines, numbered=False))

    async def _scan_guild_bled(self, ctx: Context, cfg: dict, b_roles: set[int]):
        if not b_roles:
            embed = discord.Embed(
                title="Role Blacklist Check",
                color=Colors.mint,
                description="✅ **All Members Authorized**\n\nNo roles are blacklisted in this server. All members are authorized."
            )
            embed.add_field(name="Blacklisted Roles", value="0", inline=True)
            embed.add_field(name="Members Scanned", value=str(len(ctx.guild.members)), inline=True)
            embed.set_footer(text="Veil Utilities • Authorized")
            embed.timestamp = discord.utils.utcnow()
            return await ctx.send(embed=embed)

        unauthorized = []
        authorized_with_roles = []
        for m in list(ctx.guild.members):
            if getattr(m, "id", None) == getattr(ctx.guild.me, "id", None):
                continue
            held = [r for r in getattr(m, "roles", []) if r.id in b_roles]
            if held:
                if self._is_role_authorized(ctx.guild, m, cfg):
                    authorized_with_roles.append((m, held))
                else:
                    unauthorized.append((m, held))

        if not unauthorized:
            embed = discord.Embed(
                title="Role Blacklist Check",
                color=Colors.mint,
                description="✅ **All Members Authorized**\n\nNo unauthorized members are currently holding blacklisted roles in this server."
            )
            embed.add_field(name="Blacklisted Roles", value=str(len(b_roles)), inline=True)
            embed.add_field(name="Members Scanned", value=str(len(ctx.guild.members)), inline=True)
            if authorized_with_roles:
                embed.add_field(name="Authorized Bypasses", value=str(len(authorized_with_roles)), inline=True)
            embed.set_footer(text="Veil Utilities • Authorized")
            embed.timestamp = discord.utils.utcnow()
            return await ctx.send(embed=embed)

        embed = discord.Embed(
            title="Role Blacklist Check",
            color=Colors.error,
            description=f"⚠️ **Unauthorized Members Detected**\n\nFound **{len(unauthorized)}** member(s) holding blacklisted roles:"
        )
        lines = [f"• {m.mention} (`{m.id}`) — " + ", ".join(getattr(r, 'mention', f'<@&{r.id}>') for r in held) for m, held in unauthorized[:15]]
        if len(unauthorized) > 15:
            lines.append(f"...and **{len(unauthorized) - 15}** more member(s)")
        embed.add_field(name="Violators", value="\n".join(lines), inline=False)
        embed.set_footer(text="Veil Utilities • Unauthorized")
        embed.timestamp = discord.utils.utcnow()
        view = GuildBledView(self, ctx, unauthorized)
        return await ctx.send(embed=embed, view=view)

    async def _do_rbl_check(self, ctx: Context, target: str = None):
        cfg = await self.bot.db.get_config(ctx.guild.id, "roleblacklist")
        b_roles = set(cfg.get("roles", []))

        # When scanning the whole server (run ,checkbled without arguments, or with 'all'/'server')
        if not target or target.strip().lower() in ("all", "server", "guild", "everyone"):
            return await self._scan_guild_bled(ctx, cfg, b_roles)

        target = target.strip()
        member = None
        if target.isdigit() and hasattr(ctx.guild, "get_member"):
            member = ctx.guild.get_member(int(target))
        if not member:
            try:
                member = await commands.MemberConverter().convert(ctx, target)
            except (commands.BadArgument, AttributeError):
                pass
        if not member and target.isdigit() and hasattr(ctx.guild, "fetch_member"):
            try:
                member = await ctx.guild.fetch_member(int(target))
            except (discord.HTTPException, AttributeError):
                pass
        if not member:
            t_lower = target.lower()
            for m in getattr(ctx.guild, "members", []):
                if getattr(m, "name", "").lower() == t_lower or (getattr(m, "nick", None) and m.nick.lower() == t_lower):
                    member = m
                    break

        if not member:
            return await ctx.warn(f"I couldn't find a member matching `{target}`. Run `{ctx.clean_prefix}checkbled` without arguments to scan the whole server.")

        held = [r for r in getattr(member, "roles", []) if r.id in b_roles]
        is_auth = self._is_role_authorized(ctx.guild, member, cfg)

        if not b_roles or not held:
            embed = discord.Embed(
                title="Role Blacklist Check",
                color=Colors.mint,
                description=f"✅ **Authorized**\n\n{member.mention} is authorized. They do not hold any blacklisted roles."
            )
            embed.set_author(name=f"{member}", icon_url=getattr(member.display_avatar, "url", None))
            embed.set_footer(text="Veil Utilities • Authorized")
            embed.timestamp = discord.utils.utcnow()
            return await ctx.send(embed=embed)

        if is_auth:
            embed = discord.Embed(
                title="Role Blacklist Check",
                color=Colors.mint,
                description=f"✅ **Authorized (Bypass)**\n\n{member.mention} is authorized to hold blacklisted roles:\n"
                            + "\n".join(f"• {getattr(r, 'mention', f'<@&{r.id}>')}" for r in held)
            )
            embed.set_author(name=f"{member}", icon_url=getattr(member.display_avatar, "url", None))
            embed.set_footer(text="Veil Utilities • Authorized")
            embed.timestamp = discord.utils.utcnow()
            return await ctx.send(embed=embed)

        # Target holds blacklisted roles and is NOT authorized
        embed = discord.Embed(
            title="Role Blacklist Check",
            color=Colors.error,
            description=f"⚠️ **Unauthorized Member Detected**\n\n{member.mention} is holding **{len(held)}** blacklisted role(s):\n"
                        + "\n".join(f"• {getattr(r, 'mention', f'<@&{r.id}>')}" for r in held)
        )
        embed.set_author(name=f"{member}", icon_url=getattr(member.display_avatar, "url", None))
        embed.set_footer(text="Veil Utilities • Unauthorized")
        embed.timestamp = discord.utils.utcnow()
        view = SingleMemberBledView(self, ctx, member, held)
        return await ctx.send(embed=embed, view=view)

    @roleblacklist.command(name="check", aliases=["scan", "audit", "search"])
    @commands.guild_only()
    async def rbl_check(self, ctx: Context, *, target: str = None):
        """Check a user or the whole server for members with BLED (blacklisted) roles"""
        await self._do_rbl_check(ctx, target)

    @commands.command(name="checkbled", aliases=["checkbl", "scanbl", "bledcheck", "rblcheck", "roleblcheck"])
    @commands.guild_only()
    async def cmd_checkbled(self, ctx: Context, *, target: str = None):
        """Check a user or the whole server for members with BLED (blacklisted) roles"""
        await self._do_rbl_check(ctx, target)

    async def _do_rbl_strip(self, ctx: Context):
        cfg = await self.bot.db.get_config(ctx.guild.id, "roleblacklist")
        b_roles = set(cfg.get("roles", []))
        if not b_roles:
            raise UserError("No roles are blacklisted in this server.")

        top_pos = getattr(getattr(ctx.guild.me, "top_role", None), "position", 999999)
        done = 0
        removed_count = 0

        for m in list(ctx.guild.members):
            if self._is_role_authorized(ctx.guild, m, cfg):
                continue
            to_remove = [r for r in getattr(m, "roles", []) if r.id in b_roles and getattr(r, "position", 0) < top_pos]
            if to_remove:
                try:
                    await m.remove_roles(*to_remove, reason=f"Veil: BLED role purge by {ctx.author}")
                    done += 1
                    removed_count += len(to_remove)
                except discord.HTTPException:
                    pass

        if not done:
            return await ctx.approve("No unauthorized members were holding blacklisted roles.")
        await ctx.approve(f"Stripped **{removed_count}** blacklisted role(s) from **{done}** member(s).")

    @roleblacklist.command(name="strip", aliases=["purge", "clean"])
    @requires_uperm("headadmin")
    @has_perms(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def rbl_strip(self, ctx: Context):
        """Strip all blacklisted roles from unauthorized members across the server"""
        await self._do_rbl_strip(ctx)

    @commands.command(name="stripbled", aliases=["purgebled", "rblstrip", "roleblstrip"])
    @requires_uperm("headadmin")
    @has_perms(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def cmd_stripbled(self, ctx: Context):
        """Strip all blacklisted roles from unauthorized members across the server"""
        await self._do_rbl_strip(ctx)

    @roleblacklist.command(name="authorize", aliases=["auth", "whitelist", "allow"])
    @requires_uperm("headadmin")
    @has_perms(manage_roles=True)
    async def rbl_authorize(self, ctx: Context, *, target: discord.Member | discord.Role):
        """Authorize a member or role to bypass the role blacklist: `,rbl auth @user`"""
        await self._do_rbl_authorize(ctx, target)

    @commands.command(name="rblauth", aliases=["roleblauth", "bledauth", "rblbypass"])
    @requires_uperm("headadmin")
    @has_perms(manage_roles=True)
    async def cmd_rblauth(self, ctx: Context, *, target: discord.Member | discord.Role):
        """Authorize a member or role to hold blacklisted roles: `,rblauth @user`"""
        await self._do_rbl_authorize(ctx, target)

    async def _do_rbl_authorize(self, ctx: Context, target: discord.Member | discord.Role):
        from core import perms as _perms
        bot = getattr(ctx, "bot", None)
        is_dev = _perms.is_bot_developer(ctx.author, bot)
        is_owner = getattr(ctx.guild, "owner_id", None) == getattr(ctx.author, "id", None)
        cfg_pl = await self.bot.db.get_config(ctx.guild.id, "permlevels")
        author_rank = _perms.rank_of(cfg_pl, ctx.author, is_owner=is_owner, is_dev=is_dev)

        if author_rank < 4 and not (is_dev or is_owner):
            raise UserError("🔒 You must have **Head Admin** uperm (rank 4+) or be the Server Owner to authorize members for blacklisted roles.")

        if _perms.is_bot_developer(target, self.bot):
            return await ctx.approve(f"{target.mention} is a bot developer and is automatically authorized across the bot.")
        if target.id == ctx.guild.owner_id:
            return await ctx.approve(f"{target.mention} is the server owner and is automatically authorized.")
        cfg = await self.bot.db.get_config(ctx.guild.id, "roleblacklist")
        auth = cfg.setdefault("authorized", [])
        if target.id in auth:
            raise UserError(f"{target.mention} is already authorized.")
        auth.append(target.id)
        await self.bot.db.set_config(ctx.guild.id, "roleblacklist", cfg)
        await ctx.approve(f"{target.mention} is now authorized to hold blacklisted roles.")

    @roleblacklist.command(name="unauthorize", aliases=["unauth", "unwhitelist"])
    @requires_uperm("headadmin")
    @has_perms(manage_roles=True)
    async def rbl_unauthorize(self, ctx: Context, *, target: discord.Member | discord.Role):
        """Revoke a member's or role's authorization to bypass the role blacklist"""
        await self._do_rbl_unauthorize(ctx, target)

    @commands.command(name="rblunauth", aliases=["roleblunauth", "bledunauth"])
    @requires_uperm("headadmin")
    @has_perms(manage_roles=True)
    async def cmd_rblunauth(self, ctx: Context, *, target: discord.Member | discord.Role):
        """Revoke role blacklist authorization for a member or role: `,rblunauth @user`"""
        await self._do_rbl_unauthorize(ctx, target)

    async def _do_rbl_unauthorize(self, ctx: Context, target: discord.Member | discord.Role):
        from core import perms as _perms
        bot = getattr(ctx, "bot", None)
        is_dev = _perms.is_bot_developer(ctx.author, bot)
        is_owner = getattr(ctx.guild, "owner_id", None) == getattr(ctx.author, "id", None)
        cfg_pl = await self.bot.db.get_config(ctx.guild.id, "permlevels")
        author_rank = _perms.rank_of(cfg_pl, ctx.author, is_owner=is_owner, is_dev=is_dev)

        if author_rank < 4 and not (is_dev or is_owner):
            raise UserError("🔒 You must have **Head Admin** uperm (rank 4+) or be the Server Owner to revoke role blacklist authorizations.")

        if _perms.is_bot_developer(target, self.bot):
            raise UserError(f"{target.mention} is a bot developer and cannot be unauthorized.")
        if target.id == ctx.guild.owner_id:
            raise UserError(f"{target.mention} is the server owner and cannot be unauthorized.")
        cfg = await self.bot.db.get_config(ctx.guild.id, "roleblacklist")
        auth = cfg.setdefault("authorized", [])
        if target.id not in auth:
            raise UserError(f"{target.mention} isn't authorized.")
        auth.remove(target.id)
        await self.bot.db.set_config(ctx.guild.id, "roleblacklist", cfg)
        await ctx.approve(f"{target.mention} is no longer authorized.")

    @roleblacklist.command(name="authorized", aliases=["allowed"])
    @has_perms(manage_roles=True)
    async def rbl_authorized_list(self, ctx: Context):
        """View members and roles authorized to bypass the role blacklist"""
        cfg = await self.bot.db.get_config(ctx.guild.id, "roleblacklist")
        auth = cfg.get("authorized", [])
        lines = ["*Bot Developers and Server Owner are automatically authorized for all roles.*"]
        for tid in auth:
            m = ctx.guild.get_member(tid)
            r = ctx.guild.get_role(tid)
            if m:
                lines.append(f"• {m.mention} (`{m.id}`)")
            elif r:
                lines.append(f"• {r.mention} (`{r.id}`)")
            else:
                lines.append(f"• `<@{tid}>` (`{tid}`)")
        await ctx.paginate(chunk_embeds("Authorized Bypasses", lines, numbered=False))

    # ================================================================ permissions across all roles
    def _eligible_roles(self, ctx: Context):
        owner = ctx.author.id == ctx.guild.owner_id
        return [r for r in ctx.guild.roles if not r.is_default() and not r.managed
                and r < ctx.guild.me.top_role and (owner or r < ctx.author.top_role)]

    @commands.command(aliases=["rpa"])
    @has_perms(administrator=True)
    @commands.bot_has_permissions(manage_roles=True)
    @commands.max_concurrency(1, commands.BucketType.guild)
    async def removepermall(self, ctx: Context, *, permission: str):
        """Remove a permission from every role below me (e.g. `rpa administrator` after a raid)"""
        from cogs.settings import VALID_PERMS
        perm = permission.lower().replace(" ", "_")
        if perm not in VALID_PERMS:
            raise UserError(f"`{perm}` isn't a valid permission")
        changed = []
        for r in self._eligible_roles(ctx):
            if getattr(r.permissions, perm):
                new = discord.Permissions(**{**dict(r.permissions), perm: False})
                try:
                    await r.edit(permissions=new, reason=f"removepermall by {ctx.author}")
                    changed.append(r.name)
                except discord.HTTPException:
                    pass
        if not changed:
            raise UserError(f"No roles below me had `{perm}`.")
        e = discord.Embed(color=Colors.success, description=f"Removed `{perm}` from **{len(changed)}** role(s).")
        e.add_field(name=f"Updated ({len(changed)})", value=shorten(", ".join(changed), 1024))
        e.set_author(name=ctx.guild.me.display_name, icon_url=ctx.guild.me.display_avatar.url)
        e.set_footer(text=f"{ctx.clean_prefix}removepermall")
        e.timestamp = discord.utils.utcnow()
        await ctx.send(embed=e)

    @commands.command(aliases=["apa"])
    @has_perms(administrator=True)
    @commands.bot_has_permissions(manage_roles=True)
    @commands.max_concurrency(1, commands.BucketType.guild)
    async def addpermall(self, ctx: Context, *, permission: str):
        """Add a (non-dangerous) permission to every role below me"""
        from cogs.settings import VALID_PERMS
        perm = permission.lower().replace(" ", "_")
        if perm not in VALID_PERMS:
            raise UserError(f"`{perm}` isn't a valid permission")
        if perm in DANGEROUS_PERMS:
            raise UserError(f"`{perm}` is too dangerous to grant to every role at once — that's indistinguishable "
                            f"from a nuke. Grant it per role, or use fake permissions (`fp grant`) instead.")
        changed = []
        for r in self._eligible_roles(ctx):
            if not getattr(r.permissions, perm):
                new = discord.Permissions(**{**dict(r.permissions), perm: True})
                try:
                    await r.edit(permissions=new, reason=f"addpermall by {ctx.author}")
                    changed.append(r.name)
                except discord.HTTPException:
                    pass
        if not changed:
            raise UserError(f"Every role below me already had `{perm}`.")
        e = discord.Embed(color=Colors.success, description=f"Added `{perm}` to **{len(changed)}** role(s).")
        e.add_field(name=f"Updated ({len(changed)})", value=shorten(", ".join(changed), 1024))
        e.set_author(name=ctx.guild.me.display_name, icon_url=ctx.guild.me.display_avatar.url)
        e.set_footer(text=f"{ctx.clean_prefix}addpermall")
        e.timestamp = discord.utils.utcnow()
        await ctx.send(embed=e)

    # ================================================================ nicknames
    @commands.command(aliases=["nick"])
    @requires_uperm("mod")
    @has_perms(manage_nicknames=True)
    async def nickname(self, ctx: Context, member: discord.Member, *, nickname: str = None):
        """Change (or reset) a member's nickname"""
        if member != ctx.author:
            await check_hierarchy_async(ctx, member, "rename")
        await member.edit(nick=nickname)
        await ctx.approve(f"{'Set' if nickname else 'Reset'} {member.mention}'s nickname")

    @commands.command(aliases=["fn", "forcenick"])
    @requires_uperm("mod")
    @has_perms(manage_nicknames=True)
    async def forcenickname(self, ctx: Context, member: discord.Member, *, nickname: str = None):
        """Lock a member's nickname (omit nickname to unlock)"""
        await check_hierarchy_async(ctx, member, "rename")
        fn = await self._cfg(ctx.guild.id, "forcenick")
        if nickname:
            fn[str(member.id)] = nickname
            await member.edit(nick=nickname)
            msg = f"Locked {member.mention}'s nickname to **{nickname}**"
        else:
            fn.pop(str(member.id), None)
            await member.edit(nick=None)
            msg = f"Unlocked {member.mention}'s nickname"
        await self.bot.db.set_config(ctx.guild.id, "forcenick", fn)
        await ctx.approve(msg)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.nick != after.nick:
            fn = await self._cfg(after.guild.id, "forcenick")
            forced = fn.get(str(after.id))
            if forced and after.nick != forced:
                try:
                    await after.edit(nick=forced, reason="forced nickname")
                except discord.HTTPException:
                    pass

        # Role blacklist enforcement: immediately strip forbidden roles if assigned
        if before.roles != after.roles:
            added = [r for r in after.roles if r not in before.roles]
            if added:
                cfg = await self.bot.db.get_config(after.guild.id, "roleblacklist")
                b_ids = set(cfg.get("roles", []))
                has_bled = any(r.id in b_ids for r in added)

                if has_bled and not self._is_role_authorized(after.guild, after, cfg):
                    assigned_by_auth = False
                    try:
                        me = getattr(after.guild, "me", None)
                        if me and getattr(me.guild_permissions, "view_audit_log", False):
                            async for entry in after.guild.audit_logs(limit=5, action=discord.AuditLogAction.member_role_update):
                                if getattr(entry.target, "id", None) == after.id:
                                    delta = (discord.utils.utcnow() - entry.created_at).total_seconds()
                                    if delta < 15:
                                        actor = entry.user
                                        if actor.id == self.bot.user.id or self._is_role_authorized(after.guild, actor, cfg):
                                            assigned_by_auth = True
                                            auth = cfg.setdefault("authorized", [])
                                            if after.id not in auth:
                                                auth.append(after.id)
                                                await self.bot.db.set_config(after.guild.id, "roleblacklist", cfg)
                                            break
                    except Exception:
                        pass

                    if not assigned_by_auth:
                        top_pos = getattr(getattr(after.guild.me, "top_role", None), "position", 999999)
                        to_strip = [r for r in added if r.id in b_ids and getattr(r, "position", 0) < top_pos]
                        if to_strip and after.guild.me.guild_permissions.manage_roles:
                            try:
                                await after.remove_roles(*to_strip, reason="Veil: Role is blacklisted")
                            except discord.HTTPException:
                                pass

    # ================================================================ jail
    @commands.group(invoke_without_command=True)
    @requires_uperm("mod")
    @has_perms(moderate_members=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def jail(self, ctx: Context, member: discord.Member, *, reason: str = None):
        """Jail a member: strips their roles and confines them to #jail"""
        from core import perms as _perms
        if _perms.is_bot_developer(member, self.bot):
            raise UserError("You cannot jail a bot developer.")
        cfg = await self._cfg(ctx.guild.id, "jail")
        role = ctx.guild.get_role(cfg.get("role", 0))
        if not role:
            raise UserError(f"Jail isn't set up — run `{ctx.clean_prefix}jail setup`")
        await check_hierarchy_async(ctx, member, "jail")
        if str(member.id) in cfg.get("jailed", {}):
            raise UserError(f"{member.mention} is already jailed")
        keep = [r for r in member.roles if r.managed or r.is_default() or r >= ctx.guild.me.top_role]
        removed = [r.id for r in member.roles if r not in keep]
        await member.edit(roles=keep + [role], reason=f"jailed by {ctx.author}: {reason}")
        cfg.setdefault("jailed", {})[str(member.id)] = removed
        await self.bot.db.set_config(ctx.guild.id, "jail", cfg)
        num = await create_case(self.bot, ctx.guild, "jail", member, ctx.author, reason)
        await ctx.approve(f"Jailed **{member}** • case `#{num}`")
        ch = ctx.guild.get_channel(cfg.get("channel", 0))
        if ch:
            await ch.send(f"{member.mention}, you've been jailed. Reason: {reason or 'No reason provided'}")

    @jail.command(name="setup")
    @requires_uperm("admin")
    @has_perms(administrator=True)
    async def jail_setup(self, ctx: Context):
        """Create the jail role and channel"""
        cfg = await self._cfg(ctx.guild.id, "jail")
        role = ctx.guild.get_role(cfg.get("role", 0)) or await ctx.guild.create_role(name="jailed", reason="jail setup")
        msg = await ctx.neutral("⏳ Setting up jail…")
        for ch in ctx.guild.channels:
            try:
                await ch.set_permissions(role, view_channel=False, reason="jail setup")
            except discord.HTTPException:
                pass
        channel = ctx.guild.get_channel(cfg.get("channel", 0)) or await ctx.guild.create_text_channel(
            "jail", overwrites={ctx.guild.default_role: discord.PermissionOverwrite(view_channel=False),
                                role: discord.PermissionOverwrite(view_channel=True, send_messages=True),
                                ctx.guild.me: discord.PermissionOverwrite(view_channel=True)})
        cfg.update(role=role.id, channel=channel.id)
        await self.bot.db.set_config(ctx.guild.id, "jail", cfg)
        await msg.edit(embed=discord.Embed(color=Colors.success,
                                           description=f"✅ Jail ready: {role.mention} in {channel.mention}"))

    @jail.command(name="list")
    @requires_uperm("mod")
    @has_perms(moderate_members=True)
    async def jail_list(self, ctx: Context):
        """List jailed members"""
        cfg = await self._cfg(ctx.guild.id, "jail")
        await ctx.paginate(chunk_embeds("Jailed", [f"<@{u}>" for u in cfg.get("jailed", {})]))

    @commands.command()
    @requires_uperm("mod")
    @has_perms(moderate_members=True)
    async def unjail(self, ctx: Context, member: discord.Member, *, reason: str = None):
        """Release a member and give back their roles"""
        cfg = await self._cfg(ctx.guild.id, "jail")
        saved = cfg.get("jailed", {}).pop(str(member.id), None)
        if saved is None:
            raise UserError(f"{member.mention} isn't jailed")
        rbl = await self.bot.db.get_config(ctx.guild.id, "roleblacklist")
        b_roles = set(rbl.get("roles", []))
        roles = [r for r in (ctx.guild.get_role(i) for i in saved) if r and r < ctx.guild.me.top_role and r.id not in b_roles]
        jail_role = ctx.guild.get_role(cfg.get("role", 0))
        new = [r for r in member.roles if r != jail_role] + roles
        await member.edit(roles=list(dict.fromkeys(new)), reason=f"unjailed by {ctx.author}: {reason}")
        await self.bot.db.set_config(ctx.guild.id, "jail", cfg)
        await create_case(self.bot, ctx.guild, "unjail", member, ctx.author, reason)
        await ctx.approve(f"Released **{member}**")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        # re-apply jail if someone rejoins to dodge it
        cfg = await self._cfg(member.guild.id, "jail")
        if str(member.id) in cfg.get("jailed", {}):
            role = member.guild.get_role(cfg.get("role", 0))
            if role:
                try:
                    await member.add_roles(role, reason="rejoined while jailed")
                except discord.HTTPException:
                    pass

        # strip any blacklisted roles if member rejoins or has them
        rbl = await self._cfg(member.guild.id, "roleblacklist")
        if not self._is_role_authorized(member.guild, member, rbl):
            b_roles = set(rbl.get("roles", []))
            top_pos = getattr(getattr(member.guild.me, "top_role", None), "position", 999999)
            if b_roles and member.guild.me.guild_permissions.manage_roles:
                to_remove = [r for r in getattr(member, "roles", []) if r.id in b_roles and getattr(r, "position", 0) < top_pos]
                if to_remove:
                    try:
                        await member.remove_roles(*to_remove, reason="Veil: Role is blacklisted")
                    except discord.HTTPException:
                        pass

        # re-apply quarantine if member rejoins
        q_cfg = await self._cfg(member.guild.id, "quarantine")
        if str(member.id) in q_cfg.get("quarantined", {}):
            q_role = member.guild.get_role(q_cfg.get("role", 0))
            if q_role and member.guild.me.guild_permissions.manage_roles:
                try:
                    await member.add_roles(q_role, reason="rejoined while quarantined")
                except discord.HTTPException:
                    pass

    # ================================================================ quarantine
    @commands.command(aliases=["isolate", "freeze"])
    @requires_uperm("mod")
    @has_perms(moderate_members=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def quarantine(self, ctx: Context, member: discord.Member, *, reason: str = "Quarantined by staff"):
        """Instantly isolate a suspected raider or malicious member, stripping their roles"""
        from core import perms as _perms
        if _perms.is_bot_developer(member, self.bot):
            raise UserError("You cannot quarantine a bot developer.")
        await check_hierarchy_async(ctx, member, "quarantine")
        cfg = await self._cfg(ctx.guild.id, "quarantine")
        if str(member.id) in cfg.get("quarantined", {}):
            raise UserError(f"{member.mention} is already quarantined")

        keep = [r for r in member.roles if r.managed or r.is_default() or r >= ctx.guild.me.top_role]
        removed = [r.id for r in member.roles if r not in keep]

        q_role = ctx.guild.get_role(cfg.get("role", 0))
        if not q_role:
            q_role = discord.utils.get(ctx.guild.roles, name="Quarantined")
            if not q_role and ctx.guild.me.guild_permissions.manage_roles:
                try:
                    q_role = await ctx.guild.create_role(
                        name="Quarantined",
                        color=discord.Color.dark_gray(),
                        reason="Veil Security: Quarantine role setup"
                    )
                    for ch in ctx.guild.channels:
                        try:
                            await ch.set_permissions(q_role, send_messages=False, add_reactions=False, speak=False, connect=False)
                        except discord.HTTPException:
                            pass
                except discord.HTTPException:
                    q_role = None

        new_roles = keep + ([q_role] if q_role else [])
        await member.edit(roles=new_roles, reason=f"Quarantined by {ctx.author}: {reason}")
        cfg.setdefault("quarantined", {})[str(member.id)] = removed
        if q_role:
            cfg["role"] = q_role.id
        await self.bot.db.set_config(ctx.guild.id, "quarantine", cfg)
        num = await create_case(self.bot, ctx.guild, "quarantine", member, ctx.author, reason)
        await ctx.approve(f"Quarantined **{member}** • case `#{num}` (stripped **{len(removed)}** role(s))")

    @commands.command(aliases=["unfreeze", "release"])
    @requires_uperm("mod")
    @has_perms(moderate_members=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def unquarantine(self, ctx: Context, member: discord.Member, *, reason: str = "Released by staff"):
        """Release a member from quarantine and safely restore non-blacklisted roles"""
        cfg = await self._cfg(ctx.guild.id, "quarantine")
        saved = cfg.get("quarantined", {}).pop(str(member.id), None)
        if saved is None:
            raise UserError(f"{member.mention} is not currently quarantined")

        b_cfg = await self.bot.db.get_config(ctx.guild.id, "roleblacklist")
        b_roles = set(b_cfg.get("roles", []))

        restore_roles = [
            r for r in (ctx.guild.get_role(i) for i in saved)
            if r and r < ctx.guild.me.top_role and r.id not in b_roles
        ]
        q_role = ctx.guild.get_role(cfg.get("role", 0))
        cleaned = [r for r in member.roles if r != q_role and r.id not in b_roles] + restore_roles
        await member.edit(roles=list(dict.fromkeys(cleaned)), reason=f"Unquarantined by {ctx.author}: {reason}")
        await self.bot.db.set_config(ctx.guild.id, "quarantine", cfg)
        await create_case(self.bot, ctx.guild, "unquarantine", member, ctx.author, reason)
        await ctx.approve(f"Released **{member}** from quarantine • restored **{len(restore_roles)}** role(s)")

    # ================================================================ security scan
    @commands.command(aliases=["secscan", "auditsec", "serveraudit"])
    @has_perms(manage_guild=True)
    async def security(self, ctx: Context):
        """Run a comprehensive security audit of this Discord server"""
        msg = await ctx.neutral("🔍 Analyzing server security configuration, attack vectors, and role health…")

        score = 100
        findings: list[str] = []
        checks: dict[str, str] = {}

        # 1. @everyone role dangerous permissions check
        everyone = ctx.guild.default_role
        danger_in_everyone = [p for p in DANGEROUS_PERMS if getattr(everyone.permissions, p, False)]
        if danger_in_everyone:
            score -= 30
            names = ", ".join(f"`{p}`" for p in danger_in_everyone)
            findings.append(f"🔴 **CRITICAL**: `@everyone` has dangerous permissions: {names}")
            checks["Default Role (@everyone)"] = "🔴 Vulnerable"
        else:
            checks["Default Role (@everyone)"] = "🟢 Safe (No admin/mod perms)"

        # 2. Antinuke Shield check
        an_cfg = await self.bot.db.get_config(ctx.guild.id, "antinuke")
        if an_cfg.get("enabled", True):
            preset = an_cfg.get("preset", "normal")
            checks["Antinuke Engine"] = f"🟢 Armed (`{preset.title()}` Preset)"
        else:
            score -= 20
            findings.append("🟡 **WARNING**: Antinuke is currently disabled")
            checks["Antinuke Engine"] = "🔴 Disabled"

        # 3. Role Blacklist (BLED) check
        rbl_cfg = await self.bot.db.get_config(ctx.guild.id, "roleblacklist")
        b_roles = rbl_cfg.get("roles", [])
        if b_roles:
            unauth_holders = [
                m for m in ctx.guild.members
                if not self._is_role_authorized(ctx.guild, m, rbl_cfg)
                and any(r.id in b_roles for r in getattr(m, "roles", []))
            ]
            if unauth_holders:
                score -= 15
                findings.append(f"🔴 **VULNERABILITY**: **{len(unauth_holders)}** unauthorized member(s) hold blacklisted roles! Run `{ctx.clean_prefix}stripbled`.")
                checks["Role Blacklist (BLED)"] = f"🟡 Active ({len(b_roles)} roles), {len(unauth_holders)} unauthorized"
            else:
                checks["Role Blacklist (BLED)"] = f"🟢 Clean ({len(b_roles)} protected role(s), 0 violations)"
        else:
            score -= 10
            findings.append(f"🟡 **RECOMMENDATION**: No blacklisted roles set. Run `{ctx.clean_prefix}roleblacklist add` to lock dangerous roles.")
            checks["Role Blacklist (BLED)"] = "⚪ Unconfigured"

        # 4. Automod Shield check
        am_cfg = await self.bot.db.get_config(ctx.guild.id, "automod")
        am_active = sum(1 for f in ("invites", "links", "spam", "words", "massmention") if am_cfg.get(f))
        if am_active >= 3:
            checks["Automod Shield"] = f"🟢 Active ({am_active} filters running)"
        elif am_active > 0:
            checks["Automod Shield"] = f"🟡 Partial ({am_active} filter active)"
        else:
            score -= 10
            findings.append(f"🟡 **RECOMMENDATION**: Automod filters are off. Run `{ctx.clean_prefix}filter invites true`.")
            checks["Automod Shield"] = "⚪ Inactive"

        # 5. Anti-Raid & Anti-Alt protection check
        ar_cfg = await self.bot.db.get_config(ctx.guild.id, "antiraid")
        if ar_cfg.get("massjoin") or ar_cfg.get("age"):
            checks["Anti-Raid / Anti-Alt"] = "🟢 Enabled"
        else:
            score -= 5
            findings.append(f"🟡 **RECOMMENDATION**: Configure `{ctx.clean_prefix}antiraid massjoin` and `{ctx.clean_prefix}antialt minage`.")
            checks["Anti-Raid / Anti-Alt"] = "⚪ Off"

        # 6. Default Logging coverage
        # ask the logging cog, so both `log set` channels and auto-detected channel names count
        logging_cog = self.bot.get_cog("Logging")
        has_logging = False
        if logging_cog is not None:
            for kind in ("bans", "commands", "joinleave", "members", "roles", "channels", "server"):
                if await logging_cog.channel_for(ctx.guild, kind):
                    has_logging = True
                    break
        veil_log = discord.utils.get(ctx.guild.text_channels, name="veil-utilities-logging")
        if has_logging or veil_log:
            checks["Audit Trail & Logs"] = f"🟢 Active ({'#veil-utilities-logging' if veil_log else 'Configured'})"
        else:
            score -= 10
            findings.append(f"🟡 **RECOMMENDATION**: Run `{ctx.clean_prefix}setuplogs` to automatically create dedicated security log channels.")
            checks["Audit Trail & Logs"] = "🔴 No Log Channels"

        # 7. Bot Hierarchy Position
        if ctx.guild.me.top_role.position == len(ctx.guild.roles) - 1:
            checks["Bot Hierarchy Position"] = "🟢 Optimal (Top of server roles)"
        elif ctx.guild.me.top_role.position > len(ctx.guild.roles) // 2:
            checks["Bot Hierarchy Position"] = "🟢 Good"
        else:
            score -= 10
            findings.append("🟡 **WARNING**: Veil Utilities' role is below many server roles. Drag my role higher so I can protect your server.")
            checks["Bot Hierarchy Position"] = "🔴 Too Low"

        score = max(0, min(100, score))
        if score >= 95:
            grade, color = "S+ (Fortress)", Colors.mint
        elif score >= 85:
            grade, color = "A (Secure)", Colors.success
        elif score >= 70:
            grade, color = "B (Moderate)", Colors.warn
        elif score >= 50:
            grade, color = "C (At Risk)", Colors.orange
        else:
            grade, color = "F (Vulnerable)", Colors.error

        embed = discord.Embed(
            title=f"🛡️ Security Health Audit — {ctx.guild.name}",
            color=color,
            description=f"**Overall Security Rating**: `{grade}` • **Score**: `{score}/100`"
        )
        embed.set_thumbnail(url=ctx.guild.icon.url if ctx.guild.icon else None)

        sys_lines = [f"• **{k}**: {v}" for k, v in checks.items()]
        embed.add_field(name="System Telemetry & Controls", value="\n".join(sys_lines), inline=False)

        if findings:
            embed.add_field(name="Security Findings & Advisories", value="\n".join(findings[:10]), inline=False)
        else:
            embed.add_field(name="Security Findings & Advisories", value="✅ **No security vulnerabilities found! Your server defenses are hardened.**", inline=False)

        embed.set_footer(text=f"Veil Utilities Security Engine • {len(ctx.guild.members)} members scanned")
        embed.timestamp = discord.utils.utcnow()
        await msg.edit(embed=embed)


async def setup(bot):
    await bot.add_cog(Moderation(bot))
