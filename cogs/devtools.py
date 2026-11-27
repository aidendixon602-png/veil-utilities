"""Developer-only dangerous tools, shown under the red Secret help button.

Everything here is restricted to the bot's developers:
strip/unstrip, massunrole, revert, network hardban/genreban, join-leave logs, and antinuke presets.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from copy import deepcopy
import sqlite3

import aiohttp
import discord
from discord.ext import commands

from core import logembeds as fmt
from core import perms
from core.utils import (
    Colors,
    Context,
    DangerConfirm,
    Duration,
    UserError,
    audit_reason,
    check_target,
    chunk_embeds,
    get_user_role_blacklist_config,
    is_user_role_blacklisted_in_cfg,
    now,
    role_protection_ids,
    shorten,
    ts,
    user_role_blacklist_entry,
)

log = logging.getLogger("veil.devtools")

A = discord.AuditLogAction


def plural(n: int, word: str) -> str:
    """`1 server`, `3 servers`: never `server(s)`."""
    return f"{n} {word}{'' if n == 1 else 's'}"


class _Working:
    """Show typing while the network operation is running, without delaying its result."""

    def __init__(self, typing):
        self.typing = typing

    async def __aenter__(self):
        try:
            await self.typing.__aenter__()
        except Exception:  # typing is only a nicety
            self.typing = contextlib.nullcontext()
        return self

    async def __aexit__(self, *exc):
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
        # Protected people hold the developer rank for ordinary command checks, but these stay operator-only.
        if perms.is_app_operator(ctx.author, self.bot) or (
                await self.bot.is_owner(ctx.author) and not perms.is_whitelisted_person(ctx.author)):
            return True
        raise commands.NotOwner()  # silent — don't confirm the command exists

    # ================================================================ private server inventory
    # Only the cog check above gates these: `@commands.is_owner()` would also admit protected people,
    # who hold the developer rank for ordinary command checks but must never see operator tools.
    @commands.group(name="midtown", hidden=True, invoke_without_command=True, ignore_extra=False)
    async def midtown(self, ctx: Context):
        """Search joined servers privately for Midtown"""
        await ctx.neutral(f"Use `{ctx.clean_prefix}midtown servers` to receive a private server scan.")

    @midtown.command(name="servers", hidden=True, ignore_extra=False)
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def midtown_servers(self, ctx: Context):
        """Privately scan joined server names for Midtown

        Results are sent by DM and can be refreshed. Bot developers only.
        """
        from core.midtown import MidtownView, developer_allowed, result_pages
        if not await developer_allowed(self.bot, ctx.author):
            raise commands.NotOwner()
        try:
            pages = result_pages(self.bot.guilds)
        except Exception as exc:
            log.warning("Midtown scan failed (%s)", type(exc).__name__)
            raise UserError("The server scan is unavailable; try again later") from None
        if not await developer_allowed(self.bot, ctx.author):
            raise commands.NotOwner()
        view = MidtownView(self.bot, ctx.author.id, pages)
        try:
            view.message = await ctx.author.send(embed=pages[0], view=view, allowed_mentions=discord.AllowedMentions.none())
        except (discord.HTTPException, aiohttp.ClientError, asyncio.TimeoutError) as exc:
            log.warning("Midtown scan DM delivery failed (%s)", type(exc).__name__)
            ctx.command_log_outcome = "Private scan delivery not confirmed"
            ctx.command_log_warning = True
            view.stop()
            return await ctx.warn("I couldn't send the private results. Check your DMs and try again.")
        ctx.command_log_outcome = "Private scan sent by DM"
        if ctx.guild is not None:
            await ctx.approve("sent the private scan to your DMs")

    # ================================================================ strip / unstrip
    async def _strip_card(self, ctx: Context, member: discord.Member, removed: list) -> discord.Embed:
        """The strip card: what came off, and who can put it back.

        `,unstrip` is a developer tool, so naming it to everyone else would be a dead end. The card says a bot
        developer can restore the saved roles instead, which is true for everyone who can read the channel.
        """
        saved = f"{plural(len(removed), 'saved role')}"
        return fmt.member_stripped(member, removed, ctx.author,
                                   hint=f"A bot developer can put the {saved} back with {ctx.clean_prefix}unstrip")

    @commands.command(hidden=True)
    @commands.guild_only()
    @commands.bot_has_permissions(manage_roles=True)
    async def strip(self, ctx: Context, member: discord.Member):
        """Remove every role from a member and log the strip

        Saved roles can be restored with `,unstrip` subject to hierarchy and blacklist checks.
        """
        is_self = member.id == ctx.author.id
        if not is_self:
            check_target(ctx, member, "strip")
        else:
            # A developer can opt to strip their own roles, but not if they are the actual guild owner
            # or an explicitly protected person. No other moderation command inherits this exception.
            if not await self.bot.is_owner(ctx.author):
                raise UserError("Only a bot developer can strip their own roles")
            if member.id == ctx.guild.owner_id or perms.is_whitelisted_person(member.id):
                raise UserError(perms.PROTECTED_MODERATION_MESSAGE)
        removable = [r for r in member.roles if not r.is_default() and not r.managed and r < ctx.guild.me.top_role]
        if not removable:
            raise UserError(f"I can't remove any of {member.mention}'s roles")
        cfg = await self.bot.db.get_config(ctx.guild.id, "strips")
        if is_self:
            before = deepcopy(cfg)
            if str(member.id) in before:
                raise UserError("You already have a saved strip; restore or review it before stripping again")
            role_snapshot = [(r.id, r.position) for r in removable]
            if not await ctx.confirm(f"Strip **{len(removable)}** of your own roles? This can remove your access. "
                                     "Some roles may not be restorable due to blacklist or hierarchy checks. Continue?"):
                ctx.command_log_outcome = "Self-strip cancelled · nothing changed"
                return await ctx.neutral("self-strip cancelled; no roles changed")
            current = ctx.guild.get_member(member.id)
            fresh = await self.bot.db.get_config(ctx.guild.id, "strips")
            if (not await self.bot.is_owner(ctx.author) or current is None or ctx.author.id != member.id
                    or ctx.guild.owner_id == member.id or perms.is_whitelisted_person(member.id) or fresh != before):
                raise UserError("Access, membership or saved roles changed during confirmation; nothing was changed")
            now_removable = [r for r in current.roles if not r.is_default() and not r.managed and r < ctx.guild.me.top_role]
            if [(r.id, r.position) for r in now_removable] != role_snapshot:
                raise UserError("Your roles or hierarchy changed during confirmation; nothing was changed")
            member, removable = current, now_removable
            cfg = deepcopy(fresh)
            cfg[str(member.id)] = [r.id for r in removable]
            # Persist first: Discord's role edits may be partial or have an uncertain result.
            await self.bot.db.set_config_committed(ctx.guild.id, "strips", cfg)
        else:
            cfg[str(member.id)] = [r.id for r in removable]
            await self.bot.db.set_config(ctx.guild.id, "strips", cfg)
        try:
            await member.remove_roles(*removable, reason=f"strip by {ctx.author}")
        except (discord.HTTPException, aiohttp.ClientError, asyncio.TimeoutError):
            if not is_self:
                raise
            ctx.command_log_outcome = "Self-strip not confirmed · saved role snapshot retained"
            ctx.command_log_warning = True
            return await ctx.warn("The self-strip could not be confirmed; some roles may have been removed. "
                                  f"Your saved role snapshot was retained. Check your roles before using `{ctx.clean_prefix}unstrip`.")
        await ctx.send(embed=await self._strip_card(ctx, member, removable))

    @commands.command(hidden=True, aliases=["restore"])
    @commands.guild_only()
    @commands.bot_has_permissions(manage_roles=True)
    async def unstrip(self, ctx: Context, member: discord.Member):
        """Give back the roles a strip removed"""
        cfg = await self.bot.db.get_config(ctx.guild.id, "strips")
        saved = list(cfg.get(str(member.id), []))
        if not saved:
            raise UserError(f"{member.mention} wasn't stripped")
        b_cfg = await self.bot.db.get_config(ctx.guild.id, "roleblacklist")
        b_roles = role_protection_ids(b_cfg)
        urbl_cfg = await get_user_role_blacklist_config(ctx.guild.id, self.bot.db)
        roles, skipped = [], []
        for rid in saved:
            role = ctx.guild.get_role(rid)
            if (role and not role.is_default() and role < ctx.guild.me.top_role and not role.managed
                    and role.id not in b_roles
                    and not is_user_role_blacklisted_in_cfg(urbl_cfg, member.id, role.id, allow_kept=True)):
                roles.append(role)
            else:
                skipped.append(rid)
        if not roles:
            ctx.command_log_outcome = "No saved roles eligible · snapshot retained"
            ctx.command_log_warning = True
            return await ctx.warn("No saved roles can be restored: they may be deleted, managed, blacklisted or above my role. "
                                  "The saved snapshot was retained for review.")
        try:
            await member.add_roles(*roles, reason=f"unstrip by {ctx.author}")
        except (discord.HTTPException, aiohttp.ClientError, asyncio.TimeoutError):
            ctx.command_log_outcome = "Role restoration not confirmed · snapshot retained"
            ctx.command_log_warning = True
            return await ctx.warn("Role restoration could not be confirmed; some roles may already have been restored. "
                                  "The saved snapshot was retained. Check the member's roles before retrying.")
        try:
            fresh = await self.bot.db.get_config(ctx.guild.id, "strips")
            snapshot_changed = fresh.get(str(member.id)) != saved
            if not snapshot_changed:
                updated = deepcopy(fresh)
                if skipped:
                    updated[str(member.id)] = skipped
                else:
                    updated.pop(str(member.id), None)
                await self.bot.db.set_config_committed(ctx.guild.id, "strips", updated)
        except Exception:
            log.exception("Failed to update saved strip after restoring roles in guild %s", ctx.guild.id)
            ctx.command_log_outcome = f"Restored {len(roles)} role(s) · saved snapshot update not confirmed"
            ctx.command_log_warning = True
            return await ctx.warn(f"Discord accepted **{len(roles)}** role restoration(s), but updating the saved snapshot "
                                  "failed or could not be confirmed. Check the member's roles and saved strip before retrying.")
        names = [discord.utils.escape_markdown(discord.utils.escape_mentions(role.name)) for role in roles]
        # Stay below Discord's description limit without cutting a role name in half.
        shown = []
        for name in names:
            if len(", ".join([*shown, name])) > 3400:
                break
            shown.append(name)
        role_text = ", ".join(shown) + (f" … (+{len(names) - len(shown)} more)" if len(shown) < len(names) else "")
        description = f"Unstrip complete for {member.mention}.\n\n**Restored ({len(roles)})**\n{role_text}"
        if skipped:
            description += f"\n\n⚠️ **{len(skipped)}** saved role(s) could not be restored."
            if not snapshot_changed:
                description += " They remain saved for review."
        if snapshot_changed:
            description += "\n\n⚠️ The saved snapshot changed during restoration and was not modified. Review it before retrying."
        ctx.command_log_outcome = f"Restored {len(roles)} role(s) · {len(skipped)} skipped"
        ctx.command_log_warning = bool(skipped or snapshot_changed)
        await ctx.send(embed=discord.Embed(description=description, color=Colors.caution if skipped or snapshot_changed else Colors.approve),
                       allowed_mentions=discord.AllowedMentions.none())

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
    def _prompt(self, text: str, *, user=None, color: int = Colors.main) -> discord.Embed:
        e = discord.Embed(description=text, color=color, timestamp=discord.utils.utcnow())
        me = getattr(self.bot, "user", None)
        name = getattr(me, "name", None) or getattr(me, "display_name", None)
        author_label = f"{name} · Security" if name else "Veil Utilities · Security"
        avatar = getattr(getattr(me, "display_avatar", None), "url", None)
        if isinstance(avatar, str) and avatar:
            e.set_author(name=author_label, icon_url=avatar)
        else:
            e.set_author(name=author_label)
        target_avatar = getattr(getattr(user, "display_avatar", None), "url", None)
        if isinstance(target_avatar, str) and target_avatar:
            e.set_thumbnail(url=target_avatar)
        return e

    async def _working(self, ctx: Context, msg, embed: discord.Embed, text: str):
        """After Confirm: show that it's working (no buttons, typing indicator) for at least WORKING_FOR seconds,
        so the step is visible even when every server answers at once."""
        embed.description = text
        await msg.edit(embed=embed, view=None)
        typing = ctx.typing() if hasattr(ctx, "typing") else contextlib.nullcontext()
        return _Working(typing)

    async def _actor_level(self, ctx: Context) -> str:
        db = getattr(self.bot, "db", None)
        cfg = (await db.get_config(ctx.guild.id, "permlevels")) if (ctx.guild and db and hasattr(db, "get_config")) else {}
        if not isinstance(cfg, dict):
            cfg = {}
        is_dev = perms.is_bot_developer(ctx.author, self.bot) or await self.bot.is_owner(ctx.author)
        is_owner = bool(ctx.guild and getattr(ctx.guild, "owner_id", None) == getattr(ctx.author, "id", None))
        return perms.label(cfg, ctx.author, is_owner=is_owner, is_dev=is_dev) or perms.DEV_NAME

    @staticmethod
    def _who(user) -> str:
        return f"**{discord.utils.escape_markdown(getattr(user, 'name', None) or str(user.id))}** (`{user.id}`)"

    async def _already(self, user) -> sqlite3.Row | None:
        return await self.bot.db.fetchone("SELECT * FROM globalbans WHERE user_id=?", user.id)

    @staticmethod
    async def _banned_in(guild, user_id: int) -> bool | None:
        """Whether they're already banned there (by anyone, another bot included); None if it can't be checked."""
        try:
            await guild.fetch_ban(discord.Object(user_id))
            return True
        except discord.NotFound:
            return False
        except (discord.HTTPException, asyncio.TimeoutError):
            return None

    @commands.command(hidden=True, aliases=["reban", "hardban", "globalban"])
    async def genreban(self, ctx: Context, user: discord.User, *, reason: str = None):
        """Attempt a cross-server genreban after confirmation

        A prior server ban is not required. Provide a user mention or ID and confirm before any action. Some failures may be reported; API
        errors can interrupt the operation. Veil attempts to reban on rejoin or unban, but cannot guarantee it.
        """
        # never yourself, the bot, a bot developer, the server owner or a whitelisted person, whoever runs it
        check_target(ctx, user, "genreban")
        existing = await self._already(user)
        if existing:
            ctx.command_log_outcome = "Network ban already recorded · nothing repeated"
            # Already in force: nothing to repeat, and a second confirmation and card would only be noise.
            since = ts(existing["at"], "R") if existing["at"] else "earlier"
            by = f" by <@{existing['mod_id']}>" if existing["mod_id"] else ""
            return await ctx.warn(f"{self._who(user)} is already genrebanned (since {since}{by}). "
                                  "They're banned again automatically if they rejoin or are unbanned.")
        if ctx.guild is None:
            raise UserError("Use this command in a server")
        guilds = list(self.bot.guilds)
        owned = [g for g in guilds if g.owner_id == user.id]  # an owner can't be banned from their own server
        guilds = [g for g in guilds if g.owner_id != user.id]
        if not guilds:
            raise UserError(f"{user.mention} owns every server I'm in, so there's nowhere to ban them from")
        clean_reason = reason.strip() if isinstance(reason, str) and reason.strip() else None
        display_reason = discord.utils.escape_markdown(shorten(clean_reason, 120)) if clean_reason else None
        stored_reason = clean_reason or "Network ban"

        details = [f"Ban {user.mention} in **{plural(len(guilds), 'server')}**?"]
        if owned:
            details.append(f"{plural(len(owned), 'server')} skipped (target owns it).")
        details.append("Re-bans on rejoin or unban · only bot developers can lift it.")
        if display_reason:
            details.append(f"**Reason:** {display_reason}")
        embed = self._prompt("\n".join(details), color=Colors.main)
        embed.title = "Network ban"
        embed.set_footer(text="Only you can confirm · 30s")
        view = DangerConfirm(ctx.author.id, timeout=30)
        msg = await ctx.send(embed=embed, view=view)
        await view.wait()
        embed.clear_fields()
        embed.remove_footer()
        if view.value is not True:
            outcome = "timed out" if view.value is None else "cancelled"
            if view.value is None:
                embed.title = "Confirmation expired"
                embed.description = f"No changes made for {user.mention}. Run the command again to retry."
                embed.timestamp = discord.utils.utcnow()
            else:
                embed.title = "Ban cancelled"
                embed.description = f"No changes made for {user.mention}."
            ctx.command_log_outcome = f"Network ban {outcome} · nothing changed"
            embed.color = Colors.caution if view.value is None else Colors.neutral
            return await msg.edit(embed=embed, view=None)
        if not await self.bot.is_owner(ctx.author):
            embed.title = "Network Ban Not Applied"
            ctx.command_log_outcome = "Developer access changed · nothing changed"
            ctx.command_log_warning = True
            embed.description = "Developer access could not be verified after confirmation. Nothing was changed."
            embed.color = Colors.deny
            return await msg.edit(embed=embed, view=None)
        if await self._already(user):  # someone else did it while this was waiting for Confirm
            embed.title = "Network Ban Already Active"
            ctx.command_log_outcome = "Already recorded · nothing repeated"
            embed.description = f"{self._who(user)} was network-banned by someone else in the meantime. Nothing was repeated."
            return await msg.edit(embed=embed, view=None)

        try:
            check_target(ctx, user, "genreban")
        except UserError:
            embed.title = "Network Ban Not Applied"
            ctx.command_log_outcome = "Target became protected · nothing changed"
            ctx.command_log_warning = True
            embed.description = "The target became protected during confirmation. Nothing was changed."
            embed.color = Colors.deny
            return await msg.edit(embed=embed, view=None)
        # Only use still-joined servers from the original preview; never silently expand scope.
        joined = {g.id: g for g in self.bot.guilds}
        departed = sum(g.id not in joined for g in guilds)
        guilds = [joined[g.id] for g in guilds if g.id in joined]
        newly_owned = [g for g in guilds if g.owner_id == user.id]
        guilds = [g for g in guilds if g.owner_id != user.id]
        owned.extend(newly_owned)
        if not guilds:
            embed.title = "Network Ban Not Applied"
            ctx.command_log_outcome = "No eligible servers remain · nothing changed"
            ctx.command_log_warning = True
            embed.description = "No eligible servers remain after confirmation. Nothing was changed."
            embed.color = Colors.deny
            return await msg.edit(embed=embed, view=None)
        inserted = await self.bot.db.execute(
            "INSERT OR IGNORE INTO globalbans VALUES(?,?,?,?)", user.id, ctx.author.id, stored_reason, now())
        if inserted.rowcount == 0:
            embed.title = "Network Ban Already Active"
            ctx.command_log_outcome = "Already recorded · nothing repeated"
            embed.description = f"{self._who(user)} was network-banned by someone else in the meantime. Nothing was repeated."
            return await msg.edit(embed=embed, view=None)
        banned, already, failed = 0, 0, []
        embed.title = "Network Ban · In Progress"
        embed.color = Colors.caution
        async with await self._working(ctx, msg, embed, f"Applying network ban to {self._who(user)} across "
                                                        f"**{plural(len(guilds), 'server')}**…\n\nProcessing…"):
            for g in guilds:
                if g.owner_id == user.id:
                    owned.append(g)
                    continue
                me = getattr(g, "me", None)
                if me is not None and hasattr(me, "guild_permissions") and not me.guild_permissions.ban_members:
                    failed.append(f"{g.name} (missing permissions)")
                    continue
                status = await self._banned_in(g, user.id)
                if status is True:
                    already += 1  # banned there already (by staff or another bot): no second ban, no second log
                    continue
                if status is None:
                    failed.append(g.name)  # do not repeat a ban when its status is unknown
                    continue
                try:  # the ban logs read who ran it from this reason, so they credit them instead of the bot
                    await g.ban(discord.Object(user.id), reason=audit_reason(ctx.author, clean_reason, "genreban"))
                    banned += 1
                except discord.Forbidden:
                    failed.append(f"{g.name} (missing permissions)")
                except discord.HTTPException:
                    failed.append(g.name)
        embed.description = f"{self._who(user)} has been network-banned."
        done = [f"Banned in {plural(banned, 'server')}"] if banned else []
        if already:
            done.append(f"Already banned in {plural(already, 'server')}")
        embed.add_field(name="Applied", value=" · ".join(done) if done else "Recorded (no new server bans)", inline=True)
        skip_parts = []
        if owned:
            skip_parts.append(f"{plural(len(owned), 'owned server')}")
        if departed:
            skip_parts.append(f"{plural(departed, 'server')} no longer joined")
        if skip_parts:
            embed.add_field(name="Skipped", value=" · ".join(skip_parts), inline=True)
        embed.add_field(name="Reason", value=display_reason, inline=False)
        if failed:
            embed.add_field(
                name="Unconfirmed",
                value=f"Ban not confirmed in {plural(len(failed), 'server')}: {shorten(', '.join(failed), 300)}",
                inline=False,
            )
            embed.add_field(
                name=f"Failed ({len(failed)})",
                value=shorten("\n".join(failed), 300),
                inline=False,
            )
        embed.title = "Network Ban · Partial" if failed else "Network Ban Recorded"
        embed.color = Colors.caution if failed else Colors.approve
        ctx.command_log_outcome = (f"Network ban recorded · {banned} banned · {already} already banned"
                                   f" · {len(failed)} not confirmed · {len(owned)} skipped")
        ctx.command_log_warning = bool(failed)
        embed.timestamp = discord.utils.utcnow()
        await msg.edit(embed=embed, view=None)

    @commands.command(hidden=True, aliases=["unreban", "unhardban", "globalunban"])
    async def ungenreban(self, ctx: Context, user: discord.User):
        """Lift a network genreban in every server"""
        if not await self._already(user):
            return await ctx.warn(f"{self._who(user)} isn't genrebanned, so there's nothing to lift.")
        guilds = list(self.bot.guilds)
        embed = self._prompt(
            f"Removes the network ban on {self._who(user)} and unbans them across joined servers.",
            user=user,
            color=Colors.caution,
        )
        embed.title = "Lift Network Ban"
        embed.add_field(name="Target", value=self._who(user), inline=True)
        embed.add_field(name="Scope", value=plural(len(guilds), "server"), inline=True)
        embed.set_footer(text="Veil  •  Only you can confirm  •  Expires in 30s")
        view = DangerConfirm(ctx.author.id, timeout=30)
        msg = await ctx.send(embed=embed, view=view)
        await view.wait()
        embed.clear_fields()
        embed.set_footer(text=f"Veil  •  Target ID: {user.id}")
        if not view.value:
            embed.title = "Lift Cancelled"
            embed.description = f"Cancelled lifting the network ban on {self._who(user)}. Nothing was changed."
            embed.color = Colors.neutral
            return await msg.edit(embed=embed, view=None)
        if not await self._already(user):
            embed.title = "Network Ban Already Lifted"
            embed.description = f"The network ban on {self._who(user)} was already lifted by someone else."
            embed.color = Colors.neutral
            return await msg.edit(embed=embed, view=None)

        await self.bot.db.execute("DELETE FROM globalbans WHERE user_id=?", user.id)
        lifted, failed = 0, []
        processing_embed = discord.Embed(description="Processing...", color=Colors.caution)
        async with await self._working(ctx, msg, processing_embed, "Processing..."):
            for g in guilds:
                me = getattr(g, "me", None)
                if me is not None and hasattr(me, "guild_permissions") and not me.guild_permissions.ban_members:
                    failed.append(f"{g.name} (missing permissions)")
                    continue
                try:
                    await g.unban(discord.Object(user.id), reason=audit_reason(ctx.author, None, "ungenreban"))
                    lifted += 1
                except discord.NotFound:
                    pass
                except discord.Forbidden:
                    failed.append(f"{g.name} (missing permissions)")
                except discord.HTTPException:
                    failed.append(f"{g.name} (failed)")

        me_user = getattr(self.bot, "user", None)
        bot_name = getattr(me_user, "name", None) or getattr(me_user, "display_name", None) or "Veil Utilities"
        avatar = getattr(getattr(me_user, "display_avatar", None), "url", None)
        result = discord.Embed(
            description=f"Ungenrebanned {getattr(user, 'mention', f'<@{user.id}>')}",
            color=Colors.caution if failed else Colors.approve,
            timestamp=discord.utils.utcnow(),
        )
        if isinstance(avatar, str) and avatar:
            result.set_author(name=bot_name, icon_url=avatar)
        else:
            result.set_author(name=bot_name)
        if failed:
            result.add_field(
                name=f"Failed ({len(failed)})",
                value=shorten("\n".join(failed), 1000),
                inline=False,
            )
        actor_level = await self._actor_level(ctx)
        cmd_name = f"{getattr(ctx, 'clean_prefix', ',')}{getattr(ctx, 'invoked_with', None) or 'ungenreban'}"
        result.set_footer(text=f"{cmd_name} · {actor_level}")
        await msg.edit(embed=result, view=None)

    @commands.command(hidden=True, aliases=["genrebans", "genrebanlist", "globalbans"])
    async def hardbans(self, ctx: Context):
        """List network hardbans"""
        invoked = (getattr(ctx, "invoked_with", None) or "hardbans").lower()
        if invoked.startswith("genre"):
            heading, noun = "Genrebans", "genrebanned"
        elif invoked.startswith("global"):
            heading, noun = "Global Bans", "globally banned"
        else:
            heading, noun = "Hardbans", "hardbanned"
        rows = await self.bot.db.fetchall("SELECT * FROM globalbans ORDER BY at DESC")
        if not rows:
            return await ctx.neutral(f"Nobody is currently {noun}.")
        lines = [f"<@{r['user_id']}> (`{r['user_id']}`) · by <@{r['mod_id']}> {ts(r['at'], 'R')}\n"
                 f"-# {discord.utils.escape_markdown(shorten(r['reason'] or 'No reason given', 200))}" for r in rows]
        pages = chunk_embeds(f"{heading} · {len(rows)}", lines, 8, numbered=True)
        bot_user = getattr(self.bot, "user", None)
        avatar = getattr(getattr(bot_user, "display_avatar", None), "url", None)
        p_prefix = getattr(ctx, "clean_prefix", ",")
        intro = (f"> Active cross-server network bans enforced automatically on join and unban.\n"
                 f"-# Lift a ban with `{p_prefix}ungenreban <user>`")
        for p in pages:
            p.description = f"{intro}\n\n{p.description}"
            if bot_user and getattr(bot_user, "name", None):
                p.set_author(name=f"{bot_user.name} · Security", icon_url=avatar if isinstance(avatar, str) else None)
            p.set_footer(text=f"{plural(len(rows), 'active network ban')} · {p_prefix}ungenreban <user> to lift")
        await ctx.paginate(pages)

    async def _enforce(self, guild: discord.Guild, user_id: int, why: str) -> None:
        """A genrebanned person rejoined or was unbanned: ban them again, once, quietly if it's already done."""
        if perms.is_whitelisted_person(user_id) or guild.owner_id == user_id:
            return
        if not await self.bot.db.fetchone("SELECT 1 FROM globalbans WHERE user_id=?", user_id):
            return
        if await self._banned_in(guild, user_id) is not False:
            return  # already banned or status unavailable; never infer success from an error
        try:
            await guild.ban(discord.Object(user_id), reason=f"Genreban enforced: {why}")
        except discord.HTTPException:
            pass

    async def _enforce_all_servers(self, user_id: int, reason: str = "Automatic enforcement from ban list") -> None:
        """Automatically ban a user in all servers the bot is in if they are in the genreban list (globalbans)."""
        if perms.is_whitelisted_person(user_id):
            return
        # Check if user is in globalbans
        row = await self.bot.db.fetchone("SELECT 1 FROM globalbans WHERE user_id=?", user_id)
        if not row:
            return
        
        for guild in self.bot.guilds:
            if guild.owner_id == user_id:
                continue
            # Check if already banned
            try:
                await guild.fetch_ban(discord.Object(user_id))
                continue  # already banned
            except discord.NotFound:
                pass
            except (discord.HTTPException, asyncio.TimeoutError):
                continue  # skip if unknown
            
            try:
                await guild.ban(discord.Object(user_id), reason=reason)
            except discord.HTTPException:
                pass

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        await self._enforce(member.guild, member.id, "they rejoined")
        # Also check the general ban list (globalbans) for any new enforcement needs
        await self._enforce_all_servers(member.id, "Automatic enforcement on join")

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        await asyncio.sleep(1)  # let any other bot's re-ban land first, so there's only ever one
        await self._enforce(guild, user.id, "they were unbanned")
        await self._enforce_all_servers(user.id, "Automatic enforcement on unban")

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

    # ================================================================ user role blacklist
    @staticmethod
    def _normalize_urbl_record(raw, mod_id: int | None = None) -> dict:
        if isinstance(raw, dict):
            return {
                "all": bool(raw.get("all", False)),
                "roles": [int(r) for r in raw.get("roles", []) if isinstance(r, int) or (isinstance(r, str) and r.isdigit())],
                "kept_roles": [int(r) for r in raw.get("kept_roles", []) if isinstance(r, int) or (isinstance(r, str) and r.isdigit())],
                "mod_id": raw.get("mod_id", mod_id),
                "at": raw.get("at") or now(),
            }
        if isinstance(raw, list):
            return {
                "all": False,
                "roles": [int(r) for r in raw if isinstance(r, int) or (isinstance(r, str) and r.isdigit())],
                "kept_roles": [],
                "mod_id": mod_id,
                "at": now(),
            }
        return {
            "all": bool(raw is True),
            "roles": [],
            "kept_roles": [],
            "mod_id": mod_id,
            "at": now(),
        }

    async def _resolve_urbl_target(self, ctx: Context, target):
        if target is None:
            raise UserError(f"Specify a user: `{ctx.clean_prefix}userrolebl <user> [role] [keep]`")
        if hasattr(target, "id"):
            user = target
        elif isinstance(target, int):
            get_member = getattr(ctx.guild, "get_member", None)
            user = (get_member(target) if callable(get_member) else None) or discord.Object(id=target)
        else:
            text = str(target).strip()
            user = None
            for conv in (commands.MemberConverter(), commands.UserConverter()):
                try:
                    user = await conv.convert(ctx, text)
                    break
                except Exception:
                    continue
            if user is None:
                cleaned = text.strip("<@!>")
                if cleaned.isdigit():
                    uid = int(cleaned)
                    get_member = getattr(ctx.guild, "get_member", None)
                    user = (get_member(uid) if callable(get_member) else None) or discord.Object(id=uid)
                else:
                    raise UserError(f"User `{text}` not found.")
        get_member = getattr(ctx.guild, "get_member", None)
        member = user if hasattr(user, "roles") else (get_member(user.id) if callable(get_member) else None)
        mention = getattr(user, "mention", None) or getattr(member, "mention", None) or f"<@{user.id}>"
        return user, member, mention

    async def _check_urbl_target(self, ctx: Context, user) -> None:
        uid = getattr(user, "id", None)
        if uid == getattr(ctx.author, "id", None):
            raise UserError("You can't role-blacklist yourself")
        bot_uid = getattr(getattr(self.bot, "user", None), "id", None) or getattr(getattr(ctx.guild, "me", None), "id", None)
        if bot_uid is not None and uid == bot_uid:
            raise UserError("I can't role-blacklist myself")
        if uid == getattr(ctx.guild, "owner_id", None):
            raise UserError("You can't role-blacklist the server owner")
        is_owner_fn = getattr(self.bot, "is_owner", None)
        is_dev = perms.is_bot_developer(user, self.bot) or (await is_owner_fn(user) if callable(is_owner_fn) else False)
        if is_dev:
            raise UserError("You can't role-blacklist a bot developer")
        if perms.is_whitelisted_person(user):
            raise UserError(perms.PROTECTED_MODERATION_MESSAGE)

    async def _parse_urbl_options(self, ctx: Context, extra: str | discord.Role | None,
                                  *, default_keep: bool = False) -> tuple[discord.Role | None, bool]:
        if extra is None:
            return None, default_keep
        if isinstance(extra, discord.Role) or (hasattr(extra, "id") and hasattr(extra, "permissions")):
            return extra, default_keep
        text = str(extra).strip()
        if not text:
            return None, default_keep
        lower = text.lower()
        if lower in {"keep", "--keep", "-k"}:
            return None, True
        if lower in {"strip", "--strip", "all", "--all"}:
            return None, False
        try:
            role = await commands.RoleConverter().convert(ctx, text)
            return role, default_keep
        except commands.BadArgument:
            parts = text.rsplit(None, 1)
            if len(parts) == 2 and parts[1].lower() in {"keep", "--keep", "-k", "strip", "--strip"}:
                keep_flag = parts[1].lower() in {"keep", "--keep", "-k"}
                try:
                    role = await commands.RoleConverter().convert(ctx, parts[0].strip())
                    return role, keep_flag
                except commands.BadArgument:
                    raise UserError(f"Role `{parts[0].strip()}` not found.") from None
            raise UserError(f"Role `{text}` not found.") from None

    async def _apply_userrolebl(self, ctx: Context, target, extra: str | discord.Role | None = None,
                                *, default_keep: bool = False):
        is_owner_fn = getattr(self.bot, "is_owner", None)
        if not (perms.is_bot_developer(ctx.author, self.bot) or (await is_owner_fn(ctx.author) if callable(is_owner_fn) else False)):
            raise commands.NotOwner()
        user, member, mention = await self._resolve_urbl_target(ctx, target)
        await self._check_urbl_target(ctx, user)
        role, keep = await self._parse_urbl_options(ctx, extra, default_keep=default_keep)

        cfg = deepcopy(await get_user_role_blacklist_config(ctx.guild.id, self.bot.db))
        entries = cfg.setdefault("entries", {})
        record = self._normalize_urbl_record(entries.get(str(user.id)), mod_id=ctx.author.id)
        record["mod_id"] = ctx.author.id
        record["at"] = now()

        top_role = getattr(getattr(ctx.guild, "me", None), "top_role", None)
        can_manage = getattr(getattr(getattr(ctx.guild, "me", None), "guild_permissions", None), "manage_roles", True)

        if role is not None:
            if (callable(getattr(role, "is_default", None)) and role.is_default()) or getattr(role, "managed", False):
                raise UserError(f"{role.mention} cannot be role-blacklisted")
            if role.id not in record["roles"]:
                record["roles"].append(role.id)
            held = member is not None and role in getattr(member, "roles", [])
            if keep:
                if held and role.id not in record["kept_roles"]:
                    record["kept_roles"].append(role.id)
                entries[str(user.id)] = record
                await self.bot.db.set_config_committed(ctx.guild.id, "userrolebl", cfg)
                note = " (kept current role)" if held else ""
                return await ctx.approve(f"blacklisted {mention} from getting {role.mention} in this server{note}")

            record["kept_roles"] = [rid for rid in record["kept_roles"] if rid != role.id]
            entries[str(user.id)] = record
            await self.bot.db.set_config_committed(ctx.guild.id, "userrolebl", cfg)
            stripped = False
            if held and can_manage and (top_role is None or role < top_role):
                try:
                    await member.remove_roles(role, reason=audit_reason(ctx.author, action="user role blacklist"))
                    stripped = True
                except discord.HTTPException:
                    pass
            note = " and removed it" if stripped else ""
            return await ctx.approve(f"blacklisted {mention} from getting {role.mention} in this server{note}")

        record["all"] = True
        if keep:
            held_ids = [
                r.id for r in getattr(member, "roles", [])
                if not (callable(getattr(r, "is_default", None)) and r.is_default())
                and r.id not in record["roles"]
            ] if member is not None else []
            record["kept_roles"] = list(dict.fromkeys(held_ids))
            entries[str(user.id)] = record
            await self.bot.db.set_config_committed(ctx.guild.id, "userrolebl", cfg)
            return await ctx.approve(
                f"blacklisted {mention} from getting any new roles in this server "
                f"(kept **{plural(len(record['kept_roles']), 'current role')}**)"
            )

        record["kept_roles"] = []
        entries[str(user.id)] = record
        await self.bot.db.set_config_committed(ctx.guild.id, "userrolebl", cfg)
        removable = [
            r for r in getattr(member, "roles", [])
            if not (callable(getattr(r, "is_default", None)) and r.is_default())
            and not getattr(r, "managed", False)
            and (top_role is None or r < top_role)
        ] if member is not None else []
        stripped_count = 0
        if removable and can_manage and hasattr(member, "remove_roles"):
            try:
                await member.remove_roles(*removable, reason=audit_reason(ctx.author, action="user role blacklist"))
                stripped_count = len(removable)
            except discord.HTTPException:
                pass
        if stripped_count:
            return await ctx.approve(
                f"blacklisted {mention} from getting roles in this server and stripped **{plural(stripped_count, 'role')}**"
            )
        return await ctx.approve(f"blacklisted {mention} from getting roles in this server")

    @commands.group(
        name="userrolebl",
        aliases=["urbl", "norole", "userblrole"],
        hidden=True,
        invoke_without_command=True,
        usage="<user> [role] [keep]",
    )
    @commands.guild_only()
    async def userrolebl(self, ctx: Context, target=None, *, extra: str = None):
        """Blacklist a user from getting roles in this server

        Supports all three modes:
        • `,userrolebl <user>` — block all roles in this server and strip current roles
        • `,userrolebl <user> keep` (or `,userrolebl keep <user>`) — block any new roles while keeping current roles
        • `,userrolebl <user> <role> [keep]` — block a specific role for that user
        """
        if target is None:
            return await ctx.send_help(ctx.command)
        await self._apply_userrolebl(ctx, target, extra, default_keep=False)

    @userrolebl.command(name="add", aliases=["block", "set"], usage="<user> [role] [keep]")
    @commands.guild_only()
    async def urbl_add(self, ctx: Context, target, *, extra: str = None):
        """Blacklist a user from all roles or a specific role"""
        await self._apply_userrolebl(ctx, target, extra, default_keep=False)

    @userrolebl.command(name="strip", aliases=["all"], usage="<user>")
    @commands.guild_only()
    async def urbl_strip(self, ctx: Context, target):
        """Block all roles for a user and strip their current roles"""
        await self._apply_userrolebl(ctx, target, extra=None, default_keep=False)

    @userrolebl.command(name="keep", usage="<user> [role]")
    @commands.guild_only()
    async def urbl_keep(self, ctx: Context, target, *, extra: str = None):
        """Block new roles for a user while keeping current roles"""
        await self._apply_userrolebl(ctx, target, extra, default_keep=True)

    @userrolebl.command(name="remove", aliases=["rem", "del", "delete", "unblock", "unblacklist"], usage="<user> [role]")
    @commands.guild_only()
    async def urbl_remove(self, ctx: Context, target, *, role: discord.Role = None):
        """Remove a user or role from the user role blacklist"""
        is_owner_fn = getattr(self.bot, "is_owner", None)
        if not (perms.is_bot_developer(ctx.author, self.bot) or (await is_owner_fn(ctx.author) if callable(is_owner_fn) else False)):
            raise commands.NotOwner()
        user, _, mention = await self._resolve_urbl_target(ctx, target)
        cfg = deepcopy(await get_user_role_blacklist_config(ctx.guild.id, self.bot.db))
        entry = user_role_blacklist_entry(cfg, user.id)
        if entry is None:
            raise UserError(f"{mention} is not role-blacklisted in this server")
        entries = cfg.setdefault("entries", {})
        uid_str = str(user.id)

        if role is None:
            entries.pop(uid_str, None)
            entries.pop(user.id, None)
            await self.bot.db.set_config_committed(ctx.guild.id, "userrolebl", cfg)
            return await ctx.approve(f"removed {mention} from the user role blacklist")

        record = self._normalize_urbl_record(entries.get(uid_str) or entries.get(user.id), mod_id=ctx.author.id)
        if role.id in record["roles"]:
            record["roles"] = [rid for rid in record["roles"] if rid != role.id]
            record["kept_roles"] = [rid for rid in record["kept_roles"] if rid != role.id]
            if not record["all"] and not record["roles"]:
                entries.pop(uid_str, None)
                entries.pop(user.id, None)
            else:
                entries[uid_str] = record
            await self.bot.db.set_config_committed(ctx.guild.id, "userrolebl", cfg)
            return await ctx.approve(f"unblacklisted {role.mention} for {mention}")

        if record["all"]:
            raise UserError(
                f"{mention} is blacklisted from **all** roles; use `{ctx.clean_prefix}userrolebl remove {user.id}` to lift the full blacklist"
            )
        raise UserError(f"{mention} is not blacklisted from {role.mention}")

    @userrolebl.command(name="list", aliases=["show", "view"])
    @commands.guild_only()
    async def urbl_list(self, ctx: Context):
        """List users blacklisted from receiving roles in this server"""
        is_owner_fn = getattr(self.bot, "is_owner", None)
        if not (perms.is_bot_developer(ctx.author, self.bot) or (await is_owner_fn(ctx.author) if callable(is_owner_fn) else False)):
            raise commands.NotOwner()
        cfg = await get_user_role_blacklist_config(ctx.guild.id, self.bot.db)
        entries = cfg.get("entries", {}) if isinstance(cfg, dict) else {}
        lines = []
        for uid in entries:
            entry = user_role_blacklist_entry(cfg, uid)
            if entry is None:
                continue
            role_mentions = [f"<@&{rid}>" for rid in sorted(entry["roles"])]
            if entry["all"] and role_mentions:
                scope = f"all roles (+ {', '.join(role_mentions)})"
            elif entry["all"]:
                if entry["kept_roles"]:
                    scope = f"all new roles (kept {len(entry['kept_roles'])} existing)"
                else:
                    scope = "all roles"
            else:
                scope = ", ".join(role_mentions)
            lines.append(f"<@{uid}> (`{uid}`) — {scope}")
        if not lines:
            return await ctx.neutral("No users are role-blacklisted in this server.")
        await ctx.paginate(chunk_embeds("User Role Blacklist", lines, per_page=10, numbered=True))

    @userrolebl.command(name="clear", aliases=["reset"])
    @commands.guild_only()
    async def urbl_clear(self, ctx: Context):
        """Clear all user role blacklists in this server"""
        is_owner_fn = getattr(self.bot, "is_owner", None)
        if not (perms.is_bot_developer(ctx.author, self.bot) or (await is_owner_fn(ctx.author) if callable(is_owner_fn) else False)):
            raise commands.NotOwner()
        cfg = await get_user_role_blacklist_config(ctx.guild.id, self.bot.db)
        entries = cfg.get("entries", {}) if isinstance(cfg, dict) else {}
        if not entries:
            return await ctx.neutral("No users are role-blacklisted in this server.")
        count = len(entries)
        await self.bot.db.set_config_committed(ctx.guild.id, "userrolebl", {"entries": {}})
        await ctx.approve(f"cleared **{plural(count, 'user')}** from the user role blacklist")


async def setup(bot):
    await bot.add_cog(DevTools(bot))
