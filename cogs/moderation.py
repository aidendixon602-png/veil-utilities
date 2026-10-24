"""Moderation: bans, timeouts, warns, purge, locks, roles, jail, cases."""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Optional

import discord
from discord.ext import commands

from core.modlog import case_line, create_case, dm_target
from core.utils import (DANGEROUS_PERMS, Colors, Context, Duration, UserError, antinuke_guard, check_hierarchy,
                        check_role, chunk_embeds, fmt_duration, has_perms, is_dangerous, missing_perms, now, parse_duration, shorten,
                        ts)

URL = re.compile(r"https?://\S+", re.I)
INVITE = re.compile(r"(discord\.(gg|io|me|li)|discord(app)?\.com/invite)/\S+", re.I)


class Moderation(commands.Cog):
    """Keep your server in line"""
    emoji = "🔨"

    def __init__(self, bot):
        self.bot = bot

    async def _cfg(self, gid, mod):
        return await self.bot.db.get_config(gid, mod)

    # ================================================================ bans
    @commands.command()
    @has_perms(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def ban(self, ctx: Context, user: discord.Member | discord.User, *, reason: str = None):
        """Ban a member (or any user by ID). Add `--purge` to delete their last 7 days of messages"""
        delete = 0
        if reason and "--purge" in reason:
            delete, reason = 604800, reason.replace("--purge", "").strip() or None
        if isinstance(user, discord.Member):
            check_hierarchy(ctx, user, "ban")
        await antinuke_guard(ctx, "ban")
        if isinstance(user, discord.Member):
            await dm_target(user, ctx.guild, "banned", ctx.author, reason)
        await ctx.guild.ban(user, reason=f"{ctx.author} ({ctx.author.id}): {reason}", delete_message_seconds=delete)
        num = await create_case(self.bot, ctx.guild, "ban", user, ctx.author, reason)
        await ctx.approve(f"Banned **{user}** • case `#{num}`")

    @commands.command()
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
    @has_perms(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def softban(self, ctx: Context, member: discord.Member, *, reason: str = None):
        """Ban and immediately unban to wipe a member's recent messages"""
        check_hierarchy(ctx, member, "softban")
        await antinuke_guard(ctx, "ban")
        await member.ban(reason=f"softban by {ctx.author}: {reason}", delete_message_seconds=604800)
        await ctx.guild.unban(member, reason="softban")
        num = await create_case(self.bot, ctx.guild, "softban", member, ctx.author, reason)
        await ctx.approve(f"Softbanned **{member}** • case `#{num}`")

    @commands.command(aliases=["banrecent"])
    @has_perms(administrator=True)
    @commands.bot_has_permissions(ban_members=True)
    async def massban(self, ctx: Context, minutes: int, *, reason: str = "raid cleanup"):
        """Ban everyone who joined in the last N minutes (raid cleanup)"""
        if not 1 <= minutes <= 10080:
            raise UserError("Minutes must be between 1 and 10080 (one week)")
        cutoff = discord.utils.utcnow() - timedelta(minutes=minutes)
        is_owner = ctx.author.id == ctx.guild.owner_id
        # respect the invoker's hierarchy too — fake-permission admins must not be able to sweep up senior staff
        targets = [m for m in ctx.guild.members if m.joined_at and m.joined_at >= cutoff
                   and not m.bot and m != ctx.author and m.id != ctx.guild.owner_id
                   and m.top_role < ctx.guild.me.top_role and (is_owner or m.top_role < ctx.author.top_role)]
        if not targets:
            raise UserError("Nobody joined in that window")
        if not await ctx.confirm(f"Ban **{len(targets)}** members who joined in the last {minutes} minutes?"):
            return await ctx.neutral("Cancelled")
        await antinuke_guard(ctx, "ban", len(targets))
        result = await ctx.guild.bulk_ban(targets, reason=f"massban by {ctx.author}: {reason}")
        await create_case(self.bot, ctx.guild, "massban", ctx.author, ctx.author, f"{len(result.banned)} users: {reason}")
        await ctx.approve(f"Banned **{len(result.banned)}** members ({len(result.failed)} failed)")

    # ================================================================ kick / timeout
    @commands.command()
    @has_perms(kick_members=True)
    @commands.bot_has_permissions(kick_members=True)
    async def kick(self, ctx: Context, member: discord.Member, *, reason: str = None):
        """Kick a member"""
        check_hierarchy(ctx, member, "kick")
        await antinuke_guard(ctx, "kick")
        await dm_target(member, ctx.guild, "kicked", ctx.author, reason)
        await member.kick(reason=f"{ctx.author}: {reason}")
        num = await create_case(self.bot, ctx.guild, "kick", member, ctx.author, reason)
        await ctx.approve(f"Kicked **{member}** • case `#{num}`")

    @commands.command(aliases=["mute", "to", "tmute"])
    @has_perms(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True)
    async def timeout(self, ctx: Context, member: discord.Member, duration: Optional[Duration] = None, *, reason: str = None):
        """Timeout a member (default 5m, max 28d)"""
        check_hierarchy(ctx, member, "timeout")
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
    @has_perms(moderate_members=True)
    async def untimeout(self, ctx: Context, member: discord.Member, *, reason: str = None):
        """Remove a timeout (use `untimeout all` for everyone)"""
        check_hierarchy(ctx, member, "untimeout")
        if not member.is_timed_out():
            raise UserError(f"{member.mention} isn't timed out")
        await member.timeout(None, reason=f"{ctx.author}: {reason}")
        await create_case(self.bot, ctx.guild, "untimeout", member, ctx.author, reason)
        await ctx.approve(f"{member.mention}'s timeout has been removed.")

    @untimeout.command(name="all")
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
    @has_perms(moderate_members=True)
    async def warn(self, ctx: Context, member: discord.Member, *, reason: str = "No reason provided"):
        """Warn a member. Auto-punishments can be set with `warnpunish`"""
        check_hierarchy(ctx, member, "warn")
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
    @has_perms(manage_messages=True)
    async def botclear(self, ctx: Context, amount: int = 100):
        """Delete bot messages and command invocations"""
        prefix = ctx.prefix
        await self._purge(ctx, amount, lambda m: m.author.bot or m.content.startswith(prefix))

    # ================================================================ channels
    @commands.command()
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
    @has_perms(manage_channels=True)
    async def hide(self, ctx: Context, channel: discord.abc.GuildChannel = None):
        """Hide a channel from @everyone"""
        channel = channel or ctx.channel
        ow = channel.overwrites_for(ctx.guild.default_role)
        ow.view_channel = False
        await channel.set_permissions(ctx.guild.default_role, overwrite=ow)
        await ctx.approve(f"Hid {channel.mention}")

    @commands.command(aliases=["reveal"])
    @has_perms(manage_channels=True)
    async def unhide(self, ctx: Context, channel: discord.abc.GuildChannel = None):
        """Reveal a hidden channel"""
        channel = channel or ctx.channel
        ow = channel.overwrites_for(ctx.guild.default_role)
        ow.view_channel = None
        await channel.set_permissions(ctx.guild.default_role, overwrite=ow)
        await ctx.approve(f"Revealed {channel.mention}")

    @commands.command(aliases=["sm"])
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
    @commands.group(aliases=["r"], invoke_without_command=True)
    @has_perms(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def role(self, ctx: Context, member: discord.Member, *, role: discord.Role):
        """Toggle a role on a member"""
        check_role(ctx, role)
        if role in member.roles:
            await member.remove_roles(role, reason=f"by {ctx.author}")
            await ctx.approve(f"Removed {role.mention} from {member.mention}")
        else:
            if await self._blacklisted(ctx.guild.id, role.id):
                raise UserError(f"{role.mention} is blacklisted and can't be assigned")
            if is_dangerous(role.permissions):
                await antinuke_guard(ctx, "permissions")
            await member.add_roles(role, reason=f"by {ctx.author}")
            await ctx.approve(f"Added {role.mention} to {member.mention}")

    async def _blacklisted(self, guild_id: int, role_id: int) -> bool:
        cfg = await self.bot.db.get_config(guild_id, "roleblacklist")
        return role_id in cfg.get("roles", [])

    @role.command(name="create")
    @has_perms(manage_roles=True)
    async def role_create(self, ctx: Context, *, name: str):
        """Create a role"""
        await antinuke_guard(ctx, "rolecreate")
        r = await ctx.guild.create_role(name=name, reason=f"by {ctx.author}")
        await ctx.approve(f"Created {r.mention}")

    @role.command(name="delete")
    @has_perms(manage_roles=True)
    async def role_delete(self, ctx: Context, *, role: discord.Role):
        """Delete a role"""
        check_role(ctx, role)
        await antinuke_guard(ctx, "roledelete")
        await role.delete(reason=f"by {ctx.author}")
        await ctx.approve(f"Deleted **{role.name}**")

    @role.command(name="color", aliases=["colour"])
    @has_perms(manage_roles=True)
    async def role_color(self, ctx: Context, role: discord.Role, color: discord.Color):
        """Change a role's color"""
        check_role(ctx, role)
        await role.edit(color=color)
        await ctx.approve(f"Changed {role.mention}'s color to `{color}`")

    @role.command(name="rename")
    @has_perms(manage_roles=True)
    async def role_rename(self, ctx: Context, role: discord.Role, *, name: str):
        """Rename a role"""
        check_role(ctx, role)
        await role.edit(name=name)
        await ctx.approve(f"Renamed role to {role.mention}")

    @role.command(name="hoist")
    @has_perms(manage_roles=True)
    async def role_hoist(self, ctx: Context, *, role: discord.Role):
        """Toggle showing a role separately"""
        check_role(ctx, role)
        await role.edit(hoist=not role.hoist)
        await ctx.approve(f"{role.mention} is {'now' if not role.hoist else 'no longer'} hoisted")

    async def _mass_role(self, ctx: Context, role: discord.Role, add: bool, which):
        check_role(ctx, role)
        if add and await self._blacklisted(ctx.guild.id, role.id):
            raise UserError(f"{role.mention} is blacklisted and can't be assigned")
        targets = [m for m in ctx.guild.members if which(m) and ((role not in m.roles) if add else (role in m.roles))]
        if not targets:
            raise UserError("Nobody to update")
        msg = await ctx.neutral(f"⏳ Updating **{len(targets)}** members…")
        done = 0
        for m in targets:
            try:
                await (m.add_roles if add else m.remove_roles)(role, reason=f"mass role by {ctx.author}")
                done += 1
            except discord.HTTPException:
                pass
        await msg.edit(embed=discord.Embed(color=Colors.success,
                                           description=f"✅ {'Added' if add else 'Removed'} {role.mention} "
                                                       f"{'to' if add else 'from'} **{done}** members"))

    @role.command(name="all")
    @has_perms(administrator=True)
    @commands.max_concurrency(1, commands.BucketType.guild)
    async def role_all(self, ctx: Context, action: str, *, role: discord.Role):
        """Add/remove a role for everyone: `role all add @Role`"""
        await self._mass_role(ctx, role, action.lower() == "add", lambda m: True)

    @role.command(name="humans")
    @has_perms(administrator=True)
    @commands.max_concurrency(1, commands.BucketType.guild)
    async def role_humans(self, ctx: Context, action: str, *, role: discord.Role):
        """Add/remove a role for all humans"""
        await self._mass_role(ctx, role, action.lower() == "add", lambda m: not m.bot)

    @role.command(name="bots")
    @has_perms(administrator=True)
    @commands.max_concurrency(1, commands.BucketType.guild)
    async def role_bots(self, ctx: Context, action: str, *, role: discord.Role):
        """Add/remove a role for all bots"""
        await self._mass_role(ctx, role, action.lower() == "add", lambda m: m.bot)

    @commands.command()
    @has_perms(administrator=True)
    async def stripstaff(self, ctx: Context, member: discord.Member):
        """Remove every role with dangerous permissions from a member"""
        check_hierarchy(ctx, member, "strip")
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
    @commands.group(aliases=["rbl", "rolebl"], invoke_without_command=True)
    @has_perms(manage_roles=True)
    async def roleblacklist(self, ctx: Context):
        """Blacklist roles so the bot refuses to assign them"""
        await ctx.send_help(ctx.command)

    @roleblacklist.command(name="add")
    @has_perms(manage_roles=True)
    async def rbl_add(self, ctx: Context, *, role: discord.Role):
        """Blacklist a role"""
        cfg = await self.bot.db.get_config(ctx.guild.id, "roleblacklist")
        roles = cfg.setdefault("roles", [])
        if role.id in roles:
            raise UserError(f"{role.mention} is already blacklisted")
        roles.append(role.id)
        await self.bot.db.set_config(ctx.guild.id, "roleblacklist", cfg)
        await ctx.approve(f"{role.mention} has been blacklisted.")

    @roleblacklist.command(name="remove", aliases=["rem", "delete"])
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
        resolved = await commands.RoleConverter().convert(ctx, role)
        if resolved.id not in roles:
            raise UserError(f"{resolved.mention} isn't blacklisted")
        roles.remove(resolved.id)
        await self.bot.db.set_config(ctx.guild.id, "roleblacklist", cfg)
        await ctx.approve(f"{resolved.mention} has been unblacklisted.")

    @roleblacklist.command(name="list")
    @has_perms(manage_roles=True)
    async def rbl_list(self, ctx: Context):
        """View blacklisted roles"""
        cfg = await self.bot.db.get_config(ctx.guild.id, "roleblacklist")
        lines = [f"<@&{rid}> (`{rid}`)" for rid in cfg.get("roles", [])]
        await ctx.paginate(chunk_embeds("Blacklisted roles", lines, numbered=False))

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
    @has_perms(manage_nicknames=True)
    async def nickname(self, ctx: Context, member: discord.Member, *, nickname: str = None):
        """Change (or reset) a member's nickname"""
        if member != ctx.author:
            check_hierarchy(ctx, member, "rename")
        await member.edit(nick=nickname)
        await ctx.approve(f"{'Set' if nickname else 'Reset'} {member.mention}'s nickname")

    @commands.command(aliases=["fn", "forcenick"])
    @has_perms(manage_nicknames=True)
    async def forcenickname(self, ctx: Context, member: discord.Member, *, nickname: str = None):
        """Lock a member's nickname (omit nickname to unlock)"""
        check_hierarchy(ctx, member, "rename")
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
        if before.nick == after.nick:
            return
        fn = await self._cfg(after.guild.id, "forcenick")
        forced = fn.get(str(after.id))
        if forced and after.nick != forced:
            try:
                await after.edit(nick=forced, reason="forced nickname")
            except discord.HTTPException:
                pass

    # ================================================================ jail
    @commands.group(invoke_without_command=True)
    @has_perms(moderate_members=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def jail(self, ctx: Context, member: discord.Member, *, reason: str = None):
        """Jail a member: strips their roles and confines them to #jail"""
        cfg = await self._cfg(ctx.guild.id, "jail")
        role = ctx.guild.get_role(cfg.get("role", 0))
        if not role:
            raise UserError(f"Jail isn't set up — run `{ctx.clean_prefix}jail setup`")
        check_hierarchy(ctx, member, "jail")
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
    @has_perms(moderate_members=True)
    async def jail_list(self, ctx: Context):
        """List jailed members"""
        cfg = await self._cfg(ctx.guild.id, "jail")
        await ctx.paginate(chunk_embeds("Jailed", [f"<@{u}>" for u in cfg.get("jailed", {})]))

    @commands.command()
    @has_perms(moderate_members=True)
    async def unjail(self, ctx: Context, member: discord.Member, *, reason: str = None):
        """Release a member and give back their roles"""
        cfg = await self._cfg(ctx.guild.id, "jail")
        saved = cfg.get("jailed", {}).pop(str(member.id), None)
        if saved is None:
            raise UserError(f"{member.mention} isn't jailed")
        roles = [r for r in (ctx.guild.get_role(i) for i in saved) if r and r < ctx.guild.me.top_role]
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


async def setup(bot):
    await bot.add_cog(Moderation(bot))
