"""Developer-only dangerous tools, shown under the red Secret help button.

Everything here is restricted to the bot's developers:
strip/unstrip, massunrole, revert, network hardban/genreban, join-leave logs, and antinuke presets.
"""
from __future__ import annotations

import asyncio
import contextlib
import time

import discord
from discord.ext import commands

from core import perms
from core.utils import Colors, Context, DangerConfirm, audit_reason, Duration, UserError, check_target, chunk_embeds, now, shorten, ts

A = discord.AuditLogAction


def plural(n: int, word: str) -> str:
    """`1 server`, `3 servers`: never `server(s)`."""
    return f"{n} {word}{'' if n == 1 else 's'}"


WORKING_FOR = 2.0  # seconds a network ban shows "Genrebanning..." at the least


class _Working:
    """Typing while the bans go out, and keeps the working step up for at least WORKING_FOR seconds."""

    def __init__(self, typing, started: float):
        self.typing, self.started = typing, started

    async def __aenter__(self):
        try:
            await self.typing.__aenter__()
        except Exception:  # typing is only a nicety
            self.typing = contextlib.nullcontext()
        return self

    async def __aexit__(self, *exc):
        left = WORKING_FOR - (time.monotonic() - self.started)
        if left > 0 and exc[0] is None:
            await asyncio.sleep(left)
        try:
            await self.typing.__aexit__(*exc)
        except Exception:
            pass
        return False


class DevTools(commands.Cog):
    """Developer-only tools"""
    emoji = "⚡"

    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx: Context) -> bool:
        if await self.bot.is_owner(ctx.author):
            return True
        raise commands.NotOwner()  # silent — don't confirm the command exists

    # ================================================================ strip / unstrip
    @commands.command(hidden=True)
    @commands.guild_only()
    @commands.bot_has_permissions(manage_roles=True)
    async def strip(self, ctx: Context, member: discord.Member):
        """Remove every role from a member (restore with unstrip)"""
        check_target(ctx, member, "strip")
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
        await ctx.approve(f"restored **{len(roles)}** role{'' if len(roles) == 1 else 's'} to {member.mention}")

    # ================================================================ massunrole
    @commands.command(hidden=True)
    @commands.guild_only()
    @commands.bot_has_permissions(manage_roles=True)
    @commands.max_concurrency(1, commands.BucketType.guild)
    async def massunrole(self, ctx: Context, role: discord.Role = None, flag: str = None):
        """Remove a role from everyone

        Add `samerole` to also clear the role's permissions.
        """
        if role is None:
            raise UserError(f"`{ctx.clean_prefix}massunrole <role mention or ID> [samerole]`")
        if role >= ctx.guild.me.top_role:
            raise UserError(f"{role.mention} is above my top role")
        members = list(role.members)
        if not members and flag != "samerole":
            raise UserError(f"Nobody has {role.mention}")
        msg = await ctx.neutral(f"removing {role.mention} from **{len(members)}** member{'' if len(members) == 1 else 's'}…")
        done = 0
        for m in members:
            if perms.is_whitelisted_person(m):
                continue  # roles are never taken from whitelisted people
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
        """Undo someone's recent bans and deletions

        Best effort: bans, deleted roles and deleted channels.
        """
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

    # ================================================================ network ban (genreban)
    def _prompt(self, text: str) -> discord.Embed:
        e = discord.Embed(description=text, color=Colors.warn, timestamp=discord.utils.utcnow())
        me = getattr(self.bot, "user", None)
        name = getattr(me, "display_name", None)
        if name:
            e.set_author(name=name, icon_url=getattr(getattr(me, "display_avatar", None), "url", None))
        return e

    async def _working(self, ctx: Context, msg, embed: discord.Embed, text: str):
        """After Confirm: show that it's working (no buttons, typing indicator) for at least WORKING_FOR seconds,
        so the step is visible even when every server answers at once."""
        embed.description = f"⏳ {text}"
        await msg.edit(embed=embed, view=None)
        typing = ctx.typing() if hasattr(ctx, "typing") else contextlib.nullcontext()
        return _Working(typing, time.monotonic())

    @staticmethod
    def _who(user) -> str:
        return f"**{discord.utils.escape_markdown(getattr(user, 'name', None) or str(user.id))}** (`{user.id}`)"

    async def _already(self, user) -> dict | None:
        return await self.bot.db.fetchone("SELECT * FROM globalbans WHERE user_id=?", user.id)

    @staticmethod
    async def _banned_in(guild, user_id: int) -> bool | None:
        """Whether they're already banned there (by anyone, another bot included); None if it can't be checked."""
        try:
            await guild.fetch_ban(discord.Object(user_id))
            return True
        except discord.NotFound:
            return False
        except discord.HTTPException:
            return None

    @commands.command(hidden=True, aliases=["reban", "hardban", "globalban", "hb"])
    async def genreban(self, ctx: Context, user: discord.User, *, reason: str = "Genre ban across authorized servers"):
        """Ban someone from every server the bot is in

        They're banned again if they rejoin or get unbanned.
        """
        # never yourself, the bot, a bot developer, the server owner or a whitelisted person, whoever runs it
        check_target(ctx, user, "genreban")
        existing = await self._already(user)
        if existing:
            # Already in force: nothing to repeat, and a second confirmation and card would only be noise.
            since = ts(existing["at"], "R") if existing.get("at") else "earlier"
            by = f" by <@{existing['mod_id']}>" if existing.get("mod_id") else ""
            return await ctx.warn(f"{self._who(user)} is already genrebanned (since {since}{by}). "
                                  "They're banned again automatically if they rejoin or are unbanned.")
        guilds = list(self.bot.guilds)
        owned = [g for g in guilds if g.owner_id == user.id]  # an owner can't be banned from their own server
        guilds = [g for g in guilds if g.owner_id != user.id]
        if not guilds:
            raise UserError(f"{user.mention} owns every server I'm in, so there's nowhere to ban them from")
        msg = await ctx.warn("Checking...")
        lines = [f"Genreban {self._who(user)} from {plural(len(guilds), 'server')}?",
                 f"**Reason:** {discord.utils.escape_markdown(shorten(reason, 300))}"]
        if owned:
            lines.append(f"-# They own {plural(len(owned), 'server')}, which will be skipped.")
        embed = self._prompt("\n".join(lines))
        view = DangerConfirm(ctx.author.id, timeout=30)
        await msg.edit(content=None, embed=embed, view=view)
        await view.wait()
        if not view.value:
            embed.description = f"Genreban of {self._who(user)} cancelled. Nothing was changed."
            embed.color = Colors.neutral
            return await msg.edit(embed=embed, view=None)
        if await self._already(user):  # someone else did it while this was waiting for Confirm
            embed.description = f"{self._who(user)} was genrebanned by someone else in the meantime. Nothing was repeated."
            return await msg.edit(embed=embed, view=None)

        await self.bot.db.execute(
            "INSERT OR REPLACE INTO globalbans VALUES(?,?,?,?)", user.id, ctx.author.id, reason, now())
        banned, already, failed = 0, 0, []
        async with await self._working(ctx, msg, embed, f"Genrebanning {self._who(user)} in "
                                                        f"{plural(len(guilds), 'server')}..."):
            for g in guilds:
                if await self._banned_in(g, user.id):
                    already += 1  # banned there already (by staff or another bot): no second ban, no second log
                    continue
                try:  # the ban logs read who ran it from this reason, so they credit them instead of the bot
                    await g.ban(discord.Object(user.id), reason=audit_reason(ctx.author, reason, "genreban"))
                    banned += 1
                except discord.HTTPException:
                    failed.append(g.name)
        lines = [f"{self._who(user)} is genrebanned."]
        done = [f"banned in {plural(banned, 'server')}"] if banned else []
        if already:
            done.append(f"already banned in {plural(already, 'server')}")
        if done:
            lines.append("-# " + (", ".join(done)).capitalize())
        if owned:
            lines.append(f"-# Skipped {plural(len(owned), 'server')} they own")
        if failed:
            lines.append(f"-# Couldn't ban in {plural(len(failed), 'server')}: {shorten(', '.join(failed), 300)}")
        embed.description = "\n".join(lines)
        embed.color = Colors.deny
        embed.timestamp = discord.utils.utcnow()
        await msg.edit(embed=embed, view=None)

    @commands.command(hidden=True, aliases=["unreban", "unhardban", "globalunban", "unhb"])
    async def ungenreban(self, ctx: Context, user: discord.User):
        """Lift a network genreban in every server"""
        if not await self._already(user):
            return await ctx.warn(f"{self._who(user)} isn't genrebanned, so there's nothing to lift.")
        guilds = list(self.bot.guilds)
        msg = await ctx.warn("Checking...")
        embed = self._prompt(f"Lift the genreban on {self._who(user)} in {plural(len(guilds), 'server')}?")
        view = DangerConfirm(ctx.author.id, timeout=30)
        await msg.edit(content=None, embed=embed, view=view)
        await view.wait()
        if not view.value:
            embed.description = f"Lifting the genreban on {self._who(user)} was cancelled. Nothing was changed."
            embed.color = Colors.neutral
            return await msg.edit(embed=embed, view=None)
        if not await self._already(user):
            embed.description = f"The genreban on {self._who(user)} was already lifted by someone else."
            return await msg.edit(embed=embed, view=None)

        await self.bot.db.execute("DELETE FROM globalbans WHERE user_id=?", user.id)
        lifted, failed = 0, []
        async with await self._working(ctx, msg, embed, f"Lifting the genreban on {self._who(user)} in "
                                                        f"{plural(len(guilds), 'server')}..."):
            for g in guilds:
                try:
                    await g.unban(discord.Object(user.id), reason=audit_reason(ctx.author, None, "ungenreban"))
                    lifted += 1
                except discord.NotFound:
                    pass
                except discord.HTTPException:
                    failed.append(g.name)
        lines = [f"The genreban on {self._who(user)} is lifted."]
        lines.append(f"-# Unbanned in {plural(lifted, 'server')}" if lifted else "-# They weren't banned in any server")
        if failed:
            lines.append(f"-# Couldn't unban in {plural(len(failed), 'server')}: {shorten(', '.join(failed), 300)}")
        embed.description = "\n".join(lines)
        embed.color = Colors.approve
        embed.timestamp = discord.utils.utcnow()
        await msg.edit(embed=embed, view=None)

    @commands.command(hidden=True, aliases=["genrebans", "genrebanlist", "globalbans"])
    async def hardbans(self, ctx: Context):
        """List network genrebans"""
        rows = await self.bot.db.fetchall("SELECT * FROM globalbans ORDER BY at DESC")
        if not rows:
            return await ctx.neutral("Nobody is genrebanned.")
        lines = [f"<@{r['user_id']}> (`{r['user_id']}`) · by <@{r['mod_id']}> {ts(r['at'], 'R')}\n"
                 f"-# {discord.utils.escape_markdown(shorten(r['reason'] or 'No reason given', 200))}" for r in rows]
        await ctx.paginate(chunk_embeds(f"Genrebans · {len(rows)}", lines, 8, numbered=False))

    async def _enforce(self, guild: discord.Guild, user_id: int, why: str) -> None:
        """A genrebanned person rejoined or was unbanned: ban them again, once, quietly if it's already done."""
        if perms.is_whitelisted_person(user_id) or guild.owner_id == user_id:
            return
        if not await self.bot.db.fetchone("SELECT 1 FROM globalbans WHERE user_id=?", user_id):
            return
        if await self._banned_in(guild, user_id):
            return  # another bot (or staff) already banned them again
        try:
            await guild.ban(discord.Object(user_id), reason=f"Genreban enforced: {why}")
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        await self._enforce(member.guild, member.id, "they rejoined")

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        await asyncio.sleep(1)  # let any other bot's re-ban land first, so there's only ever one
        await self._enforce(guild, user.id, "they were unbanned")

    # ================================================================ join/leave logs
    @commands.command(hidden=True, aliases=["jllogs"])
    @commands.guild_only()
    async def joinleavelogs(self, ctx: Context, channel: discord.TextChannel = None):
        """Log joins, leaves and kicks in their own channel"""
        cfg = await self.bot.db.get_config(ctx.guild.id, "logs")
        cfg["joinleave"] = channel.id if channel else None
        await self.bot.db.set_config(ctx.guild.id, "logs", cfg)
        if channel:
            await ctx.approve(f"joins, leaves and kicks will go to {channel.mention}")
        else:
            await ctx.approve("joins, leaves and kicks go to the member log again")

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
        await ctx.approve(f"saved antinuke preset **{name.lower()}**")

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
        await ctx.approve(f"applied antinuke preset **{name.lower()}** to this server")

    @antinukepreset.command(name="list")
    async def anp_list(self, ctx: Context):
        """List antinuke presets"""
        presets = await self.bot.db.get_config(0, "anpresets")
        if not presets:
            return await ctx.warn("there are no antinuke presets")
        await ctx.neutral("antinuke presets: " + ", ".join(f"`{n}`" for n in presets))

    @antinukepreset.command(name="delete", aliases=["remove"])
    async def anp_delete(self, ctx: Context, name: str):
        """Delete a preset"""
        presets = await self.bot.db.get_config(0, "anpresets")
        if presets.pop(name.lower(), None) is None:
            raise UserError(f"No preset named `{name.lower()}`")
        await self.bot.db.set_config(0, "anpresets", presets)
        await ctx.approve(f"deleted preset **{name.lower()}**")


async def setup(bot):
    await bot.add_cog(DevTools(bot))
