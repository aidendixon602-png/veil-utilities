"""Moderation: bans, timeouts, warns, purge, locks, roles, jail, cases."""
from __future__ import annotations

import asyncio
import logging
import re
from copy import deepcopy
from datetime import timedelta
from typing import Optional

import aiohttp
import discord
from discord.ext import commands
from discord.ext.commands.view import StringView

from core import logembeds as fmt
from core.modlog import case_line, create_case, dm_target
import core.utils as _cu
import importlib
if not hasattr(_cu, "check_role_assignable"):
    try:
        importlib.reload(_cu)
    except Exception:
        pass

from core import perms as perms_mod
from core.utils import (DANGEROUS_PERMS, Colors, Context, Duration, UserError, antinuke_guard, audit_reason,
                        check_hierarchy_async, check_target,
                        check_role, check_role_assignable, chunk_embeds, fmt_duration, has_perms, is_dangerous, missing_perms, now, parse_duration, shorten,
                        ts, requires_uperm, is_server_owner, owner_or_developer, has_legacy_role_assignment_auth,
                        has_soft_role_bypass, role_protection_ids)

URL = re.compile(r"https?://\S+", re.I)
INVITE = re.compile(r"(discord\.(gg|io|me|li)|discord(app)?\.com/invite)/\S+", re.I)

# Role permission bits, not members' effective permissions or channel overwrites.
VIEW_ADMIN_PERMISSIONS = (
    ("Administrator", "administrator"),
    ("Manage Server", "manage_guild"),
    ("Manage Channels", "manage_channels"),
    ("Manage Roles", "manage_roles"),
    ("Ban Members", "ban_members"),
    ("Kick Members", "kick_members"),
    ("Manage Messages", "manage_messages"),
    ("Timeout Members", "moderate_members"),
    ("Manage Webhooks", "manage_webhooks"),
    ("Mention Everyone", "mention_everyone"),
)


def viewadmins_pages(roles: list[discord.Role]) -> list[discord.Embed]:
    """Group explicit role permissions into Discord-sized pages without dropping roles."""
    roles = sorted(roles, key=lambda role: role.position, reverse=True)
    pages: list[discord.Embed] = []
    body = ""

    def flush():
        nonlocal body
        if body:
            embed = discord.Embed(title="Roles by permission", description=body, color=Colors.caution)
            embed.set_footer(text="Explicit role permissions only · Administrator grants all permissions")
            pages.append(embed)
            body = ""

    for label, permission in VIEW_ADMIN_PERMISSIONS:
        mentions = [role.mention for role in roles if getattr(role.permissions, permission)] or ["None"]
        heading = f"**{label}**\n"
        for i, mention in enumerate(mentions):
            prefix = ("\n\n" if body else "") + heading if i == 0 else "\n"
            if len(body) + len(prefix) + len(mention) > 3800:
                flush()
                prefix = heading
            body += prefix + mention
    flush()
    return pages


def parse_real_role_permissions(text: str) -> tuple[list[str], list[str]]:
    """Parse Discord permission names and aliases, accepting spaces, underscores and commas."""
    text = re.sub(r"\bmanage[ _-]+server\b", "manage_guild", text, flags=re.I)
    text = re.sub(r"\btimeout[ _-]+members\b", "moderate_members", text, flags=re.I)
    text = re.sub(r"\bview[ _-]+channels\b", "view_channel", text, flags=re.I)
    words = [w.replace("-", "_") for w in re.split(r"[\s,]+", text.lower().strip()) if w]
    flags = discord.Permissions.VALID_FLAGS
    found, unknown, seen = [], [], set()
    i = 0
    while i < len(words):
        for size in range(min(6, len(words) - i), 0, -1):
            name = "_".join(words[i:i + size])
            if name in flags:
                if flags[name] not in seen:
                    found.append(name)
                    seen.add(flags[name])
                i += size
                break
        else:
            unknown.append(words[i])
            i += 1
    return found, unknown


ROLE_PERMISSION_GROUPS = {
    "General": {"administrator", "view_audit_log", "view_guild_insights", "manage_guild", "manage_roles",
                "manage_channels", "kick_members", "ban_members", "create_instant_invite", "change_nickname",
                "manage_nicknames", "manage_webhooks", "manage_expressions", "manage_events", "moderate_members"},
    "Text": {"view_channel", "send_messages", "send_tts_messages", "manage_messages", "embed_links", "attach_files",
             "read_message_history", "mention_everyone", "external_emojis", "external_stickers", "add_reactions",
             "use_application_commands", "manage_threads", "create_public_threads", "create_private_threads",
             "send_messages_in_threads"},
    "Voice": {"connect", "speak", "mute_members", "deafen_members", "move_members", "use_voice_activation",
              "priority_speaker", "stream", "request_to_speak", "start_embedded_activities", "use_soundboard",
              "use_external_sounds", "send_voice_messages"},
}


def role_permissions_help(prefix: str) -> discord.Embed:
    """Show all permission bits supported by the installed Discord library, grouped for readability."""
    valid = {name for name, _ in discord.Permissions.all()}
    aliases = discord.Permissions.VALID_FLAGS
    groups = {label: {name for name in names if name in aliases} for label, names in ROLE_PERMISSION_GROUPS.items()}
    used = {aliases[name] for values in groups.values() for name in values}
    groups["Other"] = {name for name in valid if aliases[name] not in used}
    embed = discord.Embed(title="Discord role permissions", color=Colors.blurple,
                          description=f"**Grant:** `{prefix}rpa @Role <permission>`\n"
                                      f"**Remove from roles:** `{prefix}rpa <permission> [skip role]`\n"
                                      "Bulk removal previews the affected roles and requires confirmation. "
                                      "These are real Discord permissions, not fake permissions.")
    for label, names in groups.items():
        chunks, line = [], ""
        for name in sorted(names):
            token = f"`{name}`"
            if len(line) + len(token) + 1 > 1000:
                chunks.append(line)
                line = ""
            line = f"{line} {token}".strip()
        if line:
            chunks.append(line)
        for i, part in enumerate(chunks):
            embed.add_field(name=label if i == 0 else f"{label} (continued)", value=part, inline=False)
    embed.set_footer(text="Use underscores or quote names with spaces · @everyone and managed roles are excluded")
    return embed


# ==================================================================== role blacklist views
class SingleMemberBledView(discord.ui.View):
    def __init__(self, cog, ctx: Context, target: discord.Member, roles_to_strip: list[discord.Role]):
        super().__init__(timeout=180)
        self.cog = cog
        self.ctx = ctx
        self.target = target
        self.roles_to_strip = roles_to_strip

    async def interaction_check(self, it: discord.Interaction) -> bool:
        if it.user.id != self.ctx.author.id:
            await it.response.send_message("Only the person who opened this menu can use it. Run the command yourself.", ephemeral=True)
            return False
        guild = getattr(it, "guild", None)
        origin_id = getattr(self.ctx.guild, "id", None)
        if guild is None and origin_id is not None:
            get_guild = getattr(self.cog.bot, "get_guild", None)
            guild = get_guild(origin_id) if callable(get_guild) else None
        if (guild is None or origin_id is None or getattr(guild, "id", None) != origin_id
                or not owner_or_developer(it.user, guild, getattr(it, "client", None))):
            await it.response.send_message("Only the server owner or a bot developer can use this button.", ephemeral=True)
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
        cfg = await self.cog.bot.db.get_config(self.ctx.guild.id, "roleblacklist")
        exceptions = await self.cog._role_exceptions(self.ctx.guild.id)
        denied = self.cog._unauthorized_bled_roles(self.ctx.guild, self.target, cfg, exceptions, self.roles_to_strip)
        valid = [r for r in denied if getattr(r, "position", 0) < top_pos]
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
            await self.target.remove_roles(*valid, reason=audit_reason(interaction.user, action="Veil: BLED role strip"))
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
        cfg = deepcopy(await self.cog.bot.db.get_config(self.ctx.guild.id, "roleblacklist"))
        auth = cfg.setdefault("authorized", [])
        if self.target.id not in auth:
            auth.append(self.target.id)
            await self.cog.bot.db.set_config_committed(self.ctx.guild.id, "roleblacklist", cfg)

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
        if it.user.id != self.ctx.author.id:
            await it.response.send_message("Only the person who opened this menu can use it. Run the command yourself.", ephemeral=True)
            return False
        guild = getattr(it, "guild", None)
        origin_id = getattr(self.ctx.guild, "id", None)
        if guild is None and origin_id is not None:
            get_guild = getattr(self.cog.bot, "get_guild", None)
            guild = get_guild(origin_id) if callable(get_guild) else None
        if (guild is None or origin_id is None or getattr(guild, "id", None) != origin_id
                or not owner_or_developer(it.user, guild, getattr(it, "client", None))):
            await it.response.send_message("Only the server owner or a bot developer can use this button.", ephemeral=True)
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
            cfg = await self.cog.bot.db.get_config(self.ctx.guild.id, "roleblacklist")
            exceptions = await self.cog._role_exceptions(self.ctx.guild.id)
            denied = self.cog._unauthorized_bled_roles(self.ctx.guild, m, cfg, exceptions, roles)
            valid = [r for r in denied if getattr(r, "position", 0) < top_pos]
            if valid:
                try:
                    await m.remove_roles(*valid, reason=audit_reason(interaction.user, action="Veil: BLED role purge"))
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
        cfg = deepcopy(await self.cog.bot.db.get_config(self.ctx.guild.id, "roleblacklist"))
        auth = cfg.setdefault("authorized", [])
        count = 0
        for m, _ in self.unauthorized:
            if m.id not in auth:
                auth.append(m.id)
                count += 1
        await self.cog.bot.db.set_config_committed(self.ctx.guild.id, "roleblacklist", cfg)

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
        self._role_exception_locks: dict[int, asyncio.Lock] = {}
        self._bulk_permission_locks: dict[int, asyncio.Lock] = {}

    def _exception_lock(self, guild_id: int) -> asyncio.Lock:
        return self._role_exception_locks.setdefault(guild_id, asyncio.Lock())

    async def _role_exceptions(self, guild_id: int) -> dict:
        exceptions = await self.bot.db.get_config(guild_id, "rbl_role_exceptions")
        if not isinstance(exceptions, dict) or any(not isinstance(v, list) for v in exceptions.values()):
            raise UserError("Role blacklist exceptions could not be verified")
        return exceptions

    async def _change_role_exceptions(self, guild_id: int, member_id: int, role_ids, *, add: bool) -> bool:
        """Called with the per-guild exception lock held; do not touch legacy trust grants."""
        exceptions = deepcopy(await self._role_exceptions(guild_id))
        key = str(member_id)
        current = exceptions.get(key, [])
        updated = list(current)
        for rid in role_ids:
            if add and rid not in updated:
                updated.append(rid)
            elif not add:
                updated = [existing for existing in updated if existing != rid]
        if updated == current:
            return False
        if updated:
            exceptions[key] = updated
        else:
            exceptions.pop(key, None)
        await self.bot.db.set_config_committed(guild_id, "rbl_role_exceptions", exceptions)
        return True

    async def _owner_audit_role_additions(self, member, roles, observed_at) -> dict[int, int]:
        """Match recent, unambiguous Discord role-add audit entries; never guess on missing data.

        Discord does not attach the actor to gateway updates, so this is a best-effort correlation,
        not a cryptographic guarantee. The receipt prevents reusing an audit entry for later events.
        """
        guild = member.guild
        if not getattr(getattr(guild.me, "guild_permissions", None), "view_audit_log", False):
            return {}
        try:
            receipts = await self.bot.db.get_config(guild.id, "rbl_audit_receipts")
            if not isinstance(receipts, dict) or not isinstance(receipts.get("ids", []), list):
                return {}
            consumed = set(receipts.get("ids", []))
            matches: dict[int, list] = {}
            for _ in range(3):
                await asyncio.sleep(0.6)  # Audit entries may appear after the gateway event.
                matches = {role.id: [] for role in roles}
                count = 0
                async with asyncio.timeout(3):
                    async for entry in guild.audit_logs(limit=100, action=discord.AuditLogAction.member_role_update,
                                                        after=observed_at - timedelta(seconds=3), oldest_first=False):
                        count += 1
                        if count >= 100:
                            return {}  # A truncated result could hide an ambiguous role change.
                        if getattr(getattr(entry, "target", None), "id", None) != member.id:
                            continue
                        created = entry.created_at
                        age = (observed_at - created).total_seconds()
                        if age < -2 or age > 3:
                            continue
                        added = {getattr(r, "id", None) for r in getattr(entry.after, "roles", [])}
                        removed = {getattr(r, "id", None) for r in getattr(entry.before, "roles", [])}
                        for rid in matches:
                            if rid in added or rid in removed:
                                matches[rid].append((entry, rid in added and rid not in removed))
                if all(matches.values()):
                    break
            verified = {}
            for rid, candidates in matches.items():
                if len(candidates) != 1:
                    continue  # multiple changes in the window cannot be safely attributed
                entry, is_add = candidates[0]
                entry_id = getattr(entry, "id", None)
                if (is_add and isinstance(entry_id, int) and entry_id not in consumed
                        and getattr(getattr(entry, "user", None), "id", None) == guild.owner_id):
                    verified[rid] = entry_id
            return verified
        except Exception:
            return {}

    async def _record_owner_audit_exceptions(self, member, verified: dict[int, int]) -> None:
        if not verified:
            return
        guild_id = member.guild.id
        # Persist receipts first: a partial failure can deny access, but cannot replay an owner entry.
        receipts = deepcopy(await self.bot.db.get_config(guild_id, "rbl_audit_receipts"))
        if not isinstance(receipts, dict) or not isinstance(receipts.get("ids", []), list):
            raise UserError("Role audit receipts could not be verified")
        used = receipts.get("ids", [])
        receipts["ids"] = list(dict.fromkeys([*used, *verified.values()]))[-1000:]
        await self.bot.db.set_config_committed(guild_id, "rbl_audit_receipts", receipts)
        await self._change_role_exceptions(guild_id, member.id, verified.keys(), add=True)

    def _unauthorized_bled_roles(self, guild, member, cfg, exceptions, roles):
        if self._is_role_authorized(guild, member, cfg):
            return []
        allowed = exceptions.get(str(member.id), [])
        return [role for role in roles if role.id in cfg.get("roles", [])
                and (role.id not in allowed or role.id in cfg.get("hard_roles", []))]

    async def cog_before_invoke(self, ctx: Context):
        from core.help import is_rbl_command
        if is_rbl_command(ctx.command):
            # Which way a role-blacklist command answers is the server's choice (`,logs` → "Replies: DM"), not a
            # surprise: most owners want it where they ran it, so the channel is the default.
            ctx._private_rbl = await self._rbl_dm_only(ctx)

    async def _rbl_dm_only(self, ctx: Context) -> bool:
        logging_cog = self.bot.get_cog("Logging") if hasattr(self.bot, "get_cog") else None
        if logging_cog is None or getattr(ctx, "guild", None) is None:
            return False
        try:
            return await logging_cog.rbl_private(ctx.guild)
        except Exception:  # a config read must never stop a command from running
            return False

    @staticmethod
    def _role_entry(guild, rid: int) -> str:
        """`@Role` (`id`), or `deleted role` (`id`) when it is gone.

        A deleted role renders as `@unknown-role`, which reads like the bot lost it; the ID is what is stored, so
        naming that state is both truer and the thing to act on (`,rbl remove deleted`).
        """
        get_role = getattr(guild, "get_role", None)
        role = get_role(rid) if callable(get_role) else None
        if role is None:
            return f"deleted role (`{rid}`)"
        return f"{role.mention} (`{rid}`)"

    async def _cfg(self, gid, mod):
        return await self.bot.db.get_config(gid, mod)

    async def _protect_developer_ban(self, ctx, target):
        from core import perms as _perms
        if not _perms.is_bot_developer(target, self.bot):
            return
        get_cog = getattr(self.bot, "get_cog", None)
        protection = get_cog("DeveloperProtection") if get_cog else None
        if protection:
            await protection.handle(ctx.guild, target.id, ctx.author.id, source="Veil command", actual_ban=False)
        raise UserError("🛡️ Developer ban blocked. A security notice was attempted.")

    async def _limited_helper(self, ctx: Context) -> bool:
        """Apply the Helper safety limits even when a Helper also has fake permissions."""
        if ctx.author.id == ctx.guild.owner_id or perms_mod.is_bot_developer(ctx.author, self.bot):
            return False
        cfg = await self.bot.db.get_config(ctx.guild.id, "permlevels")
        levels = perms_mod.all_levels(cfg)
        name, _, rank = perms_mod.member_level_info(cfg, ctx.author)
        if rank == 0:
            from core.utils import direct_command_permissions
            if {"administrator", "moderate_members"} & await direct_command_permissions(ctx):
                return False
        return name == levels["helper"]["name"] or rank < levels["moderator"]["rank"]

    # ================================================================ bans
    @commands.command()
    @requires_uperm("admin")
    @has_perms(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def ban(self, ctx: Context, user: discord.Member | discord.User, *, reason: str = None):
        """Ban a member, or anyone by user ID

        Add `--purge` to also delete their messages from the last 7 days.
        """
        await self._protect_developer_ban(ctx, user)
        delete = 0
        if reason and "--purge" in reason:
            delete, reason = 604800, reason.replace("--purge", "").strip() or None
        if isinstance(user, discord.Member):
            await check_hierarchy_async(ctx, user, "ban")
        else:
            check_target(ctx, user, "ban")  # banning by ID still can't reach yourself, the owner, a developer or me
            # someone not in the server may already be on the ban list: say so instead of banning them again
            try:
                await ctx.guild.fetch_ban(user)
            except discord.NotFound:
                pass
            except discord.HTTPException:
                pass  # couldn't read the ban list; Discord will still refuse a real problem below
            else:
                raise UserError(f"**{getattr(user, 'name', user)}** is already banned")
        await antinuke_guard(ctx, "ban")
        if isinstance(user, discord.Member):
            await dm_target(user, ctx.guild, "banned", ctx.author, reason)
        await ctx.guild.ban(user, reason=audit_reason(ctx.author, reason), delete_message_seconds=delete)
        await create_case(self.bot, ctx.guild, "ban", user, ctx.author, reason)
        await ctx.warn(f"**{getattr(user, 'name', user)}** has been banned")

    @commands.command()
    @requires_uperm("admin")
    @has_perms(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def unban(self, ctx: Context, user: discord.User, *, reason: str = None):
        """Unban a user"""
        gb = await self.bot.db.fetchone("SELECT 1 FROM globalbans WHERE user_id=?", user.id)
        if gb and not await self.bot.is_owner(ctx.author):
            raise UserError(f"{user.mention} is hardbanned across the network, a developer must lift it with `ungenreban`")
        await antinuke_guard(ctx, "unban")
        try:
            await ctx.guild.unban(user, reason=audit_reason(ctx.author, reason))
        except discord.NotFound:
            raise UserError(f"{user.mention} isn't banned")
        await create_case(self.bot, ctx.guild, "unban", user, ctx.author, reason)
        await ctx.warn(f"**{getattr(user, 'name', user)}** has been unbanned")

    @commands.command()
    @requires_uperm("admin")
    @has_perms(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def softban(self, ctx: Context, member: discord.Member, *, reason: str = None):
        """Ban and immediately unban to wipe a member's recent messages"""
        await self._protect_developer_ban(ctx, member)
        await check_hierarchy_async(ctx, member, "softban")
        await antinuke_guard(ctx, "ban")
        await member.ban(reason=audit_reason(ctx.author, reason, "softban"), delete_message_seconds=604800)
        await ctx.guild.unban(member, reason="softban")
        await create_case(self.bot, ctx.guild, "softban", member, ctx.author, reason)
        await ctx.approve(f"softbanned {member.mention}")

    @commands.command(aliases=["banrecent"])
    @requires_uperm("admin")
    @has_perms(administrator=True)
    @commands.bot_has_permissions(ban_members=True)
    @is_server_owner()
    async def massban(self, ctx: Context, minutes: int, *, reason: str = "raid cleanup"):
        """Ban everyone who joined in the last few minutes

        Use it to clean up after a raid.
        """
        if not 1 <= minutes <= 10080:
            raise UserError("minutes must be between 1 and 10080 (one week)")
        cutoff = discord.utils.utcnow() - timedelta(minutes=minutes)
        is_owner = ctx.author.id == ctx.guild.owner_id
        # respect the invoker's hierarchy too — fake-permission admins must not be able to sweep up senior staff
        pl_cfg = await ctx.bot.db.get_config(ctx.guild.id, "permlevels") if (ctx.guild and ctx.bot) else {}
        from core import perms as _perms
        targets = [
            m for m in ctx.guild.members if m.joined_at and m.joined_at >= cutoff
            and not m.bot and m != ctx.author and m.id != ctx.guild.owner_id
            and not _perms.is_bot_developer(m, self.bot) and not _perms.is_whitelisted_person(m)
            and m.top_role < ctx.guild.me.top_role and (is_owner or m.top_role < ctx.author.top_role)
            and (is_owner or _perms.rank_of(pl_cfg, m, is_owner=False, is_dev=False, has_admin=False) == 0)
        ]
        if not targets:
            raise UserError("nobody joined in that window")
        if not await ctx.confirm(f"ban **{len(targets)}** members who joined in the last {fmt_duration(minutes * 60)}?"):
            return await ctx.neutral("cancelled")
        await antinuke_guard(ctx, "ban", len(targets))
        result = await ctx.guild.bulk_ban(targets, reason=audit_reason(ctx.author, reason, "massban"))
        await create_case(self.bot, ctx.guild, "massban", ctx.author, ctx.author, f"{len(result.banned)} users: {reason}")
        await ctx.approve(f"banned **{len(result.banned)}** members" + (f" · {len(result.failed)} failed" if result.failed else ""))

    @commands.command(aliases=["kickrecent"])
    @requires_uperm("admin")
    @has_perms(kick_members=True)
    @commands.bot_has_permissions(kick_members=True)
    @is_server_owner()
    async def masskick(self, ctx: Context, minutes: int, *, reason: str = "raid cleanup"):
        """Kick everyone who joined in the last few minutes

        Use it to clean up after a raid.
        """
        if not 1 <= minutes <= 10080:
            raise UserError("minutes must be between 1 and 10080 (one week)")
        cutoff = discord.utils.utcnow() - timedelta(minutes=minutes)
        is_owner = ctx.author.id == ctx.guild.owner_id
        pl_cfg = await ctx.bot.db.get_config(ctx.guild.id, "permlevels") if (ctx.guild and ctx.bot) else {}
        from core import perms as _perms
        targets = [
            m for m in ctx.guild.members if m.joined_at and m.joined_at >= cutoff
            and not m.bot and m != ctx.author and m.id != ctx.guild.owner_id
            and not _perms.is_bot_developer(m, self.bot) and not _perms.is_whitelisted_person(m)
            and m.top_role < ctx.guild.me.top_role and (is_owner or m.top_role < ctx.author.top_role)
            and (is_owner or _perms.rank_of(pl_cfg, m, is_owner=False, is_dev=False, has_admin=False) == 0)
        ]
        if not targets:
            raise UserError("nobody joined in that window")
        if not await ctx.confirm(f"kick **{len(targets)}** members who joined in the last {fmt_duration(minutes * 60)}?"):
            return await ctx.neutral("cancelled")
        await antinuke_guard(ctx, "kick", len(targets))
        done, failed = 0, 0
        for m in targets:
            try:
                await m.kick(reason=audit_reason(ctx.author, reason, "masskick"))
                done += 1
            except discord.HTTPException:
                failed += 1
        await create_case(self.bot, ctx.guild, "masskick", ctx.author, ctx.author, f"{done} users: {reason}")
        await ctx.approve(f"kicked **{done}** members" + (f" · {failed} failed" if failed else ""))

    # ================================================================ kick / timeout
    @commands.command()
    @requires_uperm("moderator")
    @has_perms(kick_members=True)
    @commands.bot_has_permissions(kick_members=True)
    async def kick(self, ctx: Context, member: discord.Member, *, reason: str = None):
        """Kick a member"""
        await check_hierarchy_async(ctx, member, "kick")
        await antinuke_guard(ctx, "kick")
        await dm_target(member, ctx.guild, "kicked", ctx.author, reason)
        await member.kick(reason=audit_reason(ctx.author, reason))
        num = await create_case(self.bot, ctx.guild, "kick", member, ctx.author, reason)
        await ctx.approve(f"kicked {member.mention} · case #{num}")

    @commands.command(aliases=["mute", "to", "tmute"])
    @requires_uperm("helper")
    @has_perms(veil_timeout=True)
    @commands.bot_has_permissions(moderate_members=True)
    async def timeout(self, ctx: Context, member: discord.Member, duration: Optional[Duration] = None, *, reason: str = None):
        """Timeout a member (default 5m, max 28d; Helper max 1h)"""
        await check_hierarchy_async(ctx, member, "timeout")
        if duration is None:
            duration = timedelta(minutes=5)
        if duration <= timedelta(0):
            raise UserError("timeout duration must be positive")
        if duration > timedelta(days=28):
            raise UserError("timeouts can be at most 28 days")
        if await self._limited_helper(ctx) and duration > timedelta(hours=1):
            raise UserError("Helper timeouts can be at most 1 hour")
        await antinuke_guard(ctx, "timeout")
        await member.timeout(duration, reason=audit_reason(ctx.author, reason))
        human = fmt_duration(duration.total_seconds())
        await dm_target(member, ctx.guild, "timed out", ctx.author, reason, human)
        num = await create_case(self.bot, ctx.guild, f"timeout ({human})", member, ctx.author, reason)
        await ctx.approve(f"timed out {member.mention} for **{human}** · case #{num}")

    @commands.group(aliases=["unmute", "untime", "uto"], invoke_without_command=True)
    @requires_uperm("moderator")
    @has_perms(moderate_members=True)
    async def untimeout(self, ctx: Context, member: discord.Member, *, reason: str = None):
        """Remove a member's timeout

        `,untimeout all` removes everyone's.
        """
        await check_hierarchy_async(ctx, member, "untimeout")
        if not member.is_timed_out():
            raise UserError(f"{member.mention} isn't timed out")
        await member.timeout(None, reason=audit_reason(ctx.author, reason))
        await create_case(self.bot, ctx.guild, "untimeout", member, ctx.author, reason)
        await ctx.approve(f"removed the timeout from {member.mention}")

    @untimeout.command(name="all")
    @requires_uperm("admin")
    @has_perms(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True)
    async def untimeout_all(self, ctx: Context, *, reason: str = None):
        """Remove timeouts from every timed-out member"""
        timed = [m for m in ctx.guild.members if m.is_timed_out()]
        if not timed:
            raise UserError("nobody is timed out")
        done = 0
        for m in timed:
            try:
                await m.timeout(None, reason=audit_reason(ctx.author, reason, "unmute all"))
                done += 1
            except discord.HTTPException:
                pass
        await ctx.approve(f"removed the timeout from **{done}** member{'' if done == 1 else 's'}")

    # ================================================================ warns
    @commands.command()
    @requires_uperm("helper")
    @has_perms(veil_warn=True)
    async def warn(self, ctx: Context, member: discord.Member, *, reason: str = "No reason provided"):
        """Warn a member

        `warnpunish` sets what happens after several warnings.
        """
        await check_hierarchy_async(ctx, member, "warn")
        await self.bot.db.execute("INSERT INTO warns(guild_id,user_id,mod_id,reason,created_at) VALUES(?,?,?,?,?)",
                                  ctx.guild.id, member.id, ctx.author.id, reason, now())
        count = (await self.bot.db.fetchone("SELECT COUNT(*) c FROM warns WHERE guild_id=? AND user_id=?",
                                            ctx.guild.id, member.id))["c"]
        await dm_target(member, ctx.guild, "warned", ctx.author, reason)
        num = await create_case(self.bot, ctx.guild, "warn", member, ctx.author, reason)
        await ctx.approve(f"warned {member.mention} (warning **{count}**) · case #{num}")
        # escalation
        rules = await self._cfg(ctx.guild.id, "warnpunish")
        action = rules.get(str(count))
        if action and await self._limited_helper(ctx):
            # A Helper warning must not indirectly cause a kick, ban, or longer timeout.
            await ctx.warn("The configured automatic warning punishment was not applied because a Helper issued this warning.")
            return
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
                await ctx.warn(f"{member.mention} reached **{count}** warnings, so I applied the auto-punishment: **{action.split(':')[0]}**")
            except discord.HTTPException:
                pass

    @commands.command()
    @requires_uperm("admin")
    @has_perms(administrator=True)
    async def warnpunish(self, ctx: Context, warnings: int, action: str, duration: Duration = None):
        """Set what happens at a number of warnings

        Example: `,warnpunish 3 timeout 1h`
        """
        rules = await self._cfg(ctx.guild.id, "warnpunish")
        action = action.lower()
        if action == "none":
            rules.pop(str(warnings), None)
        elif action in ("kick", "ban"):
            rules[str(warnings)] = action
        elif action in ("timeout", "mute"):
            rules[str(warnings)] = f"timeout:{int((duration or timedelta(hours=1)).total_seconds())}"
        else:
            raise UserError("action must be `timeout`, `kick`, `ban` or `none`")
        await self.bot.db.set_config(ctx.guild.id, "warnpunish", rules)
        await ctx.approve(f"at **{warnings}** warnings: **{action}**" if action != "none" else f"removed the auto-punishment at **{warnings}** warnings")

    @commands.command(aliases=["warns"])
    @has_perms(veil_cases=True)
    async def warnings(self, ctx: Context, member: discord.Member | discord.User):
        """List a member's warnings"""
        rows = await self.bot.db.fetchall("SELECT * FROM warns WHERE guild_id=? AND user_id=? ORDER BY id DESC",
                                          ctx.guild.id, member.id)
        if not rows:
            return await ctx.warn(f"{member.mention} has no warnings")
        lines = [f"**ID {r['id']}** by <@{r['mod_id']}> {ts(r['created_at'])}\n> {r['reason']}" for r in rows]
        await ctx.paginate(chunk_embeds(f"Warnings for {member} ({len(rows)})", lines, 5, numbered=False))

    @commands.command(aliases=["clearwarnings"])
    @requires_uperm("moderator")
    @has_perms(moderate_members=True)
    async def clearwarns(self, ctx: Context, member: discord.Member | discord.User):
        """Clear all of a member's warnings"""
        self._check_warn_target(ctx, member, "clear warnings for")
        cur = await self.bot.db.execute("DELETE FROM warns WHERE guild_id=? AND user_id=?", ctx.guild.id, member.id)
        if cur.rowcount == 0:
            raise UserError(f"{member.mention} has no warnings")
        await create_case(self.bot, ctx.guild, "clearwarns", member, ctx.author, f"Cleared {cur.rowcount} warning(s)")
        await ctx.approve(f"cleared **{cur.rowcount}** warning{'' if cur.rowcount == 1 else 's'} for {member.mention}")

    @commands.command(aliases=["unwarn"])
    @requires_uperm("moderator")
    @has_perms(moderate_members=True)
    async def delwarn(self, ctx: Context, warn_id: int):
        """Delete a single warning by ID"""
        row = await self.bot.db.fetchone("SELECT user_id FROM warns WHERE guild_id=? AND id=?", ctx.guild.id, warn_id)
        if row is None:
            raise UserError("there is no warning with that ID")
        target = ctx.guild.get_member(row["user_id"]) or discord.Object(row["user_id"])
        self._check_warn_target(ctx, target, "remove warnings from")
        await self.bot.db.execute("DELETE FROM warns WHERE guild_id=? AND id=?", ctx.guild.id, warn_id)
        await ctx.approve(f"deleted warning `{warn_id}`")

    @staticmethod
    def _check_warn_target(ctx: Context, target, action: str) -> None:
        """Mods can't wipe their own record or the record of someone at/above their top role."""
        check_target(ctx, target, action)
        if ctx.author.id == ctx.guild.owner_id:
            return
        if isinstance(target, discord.Member) and target.top_role >= ctx.author.top_role:
            raise UserError(f"you can't {action} {target.mention}, their top role is equal or higher than yours")

    # ================================================================ cases
    @commands.command(aliases=["history", "modlogs"])
    @has_perms(veil_cases=True)
    async def cases(self, ctx: Context, user: discord.Member | discord.User = None):
        """View moderation history (for the server or a user)"""
        if user:
            rows = await self.bot.db.fetchall("SELECT * FROM cases WHERE guild_id=? AND user_id=? ORDER BY case_num DESC",
                                              ctx.guild.id, user.id)
        else:
            rows = await self.bot.db.fetchall("SELECT * FROM cases WHERE guild_id=? ORDER BY case_num DESC LIMIT 200",
                                              ctx.guild.id)
        if not rows:
            return await ctx.warn(f"{user.mention} has no cases" if user else "this server has no cases yet")
        await ctx.paginate(chunk_embeds(f"Cases{f' for {user}' if user else ''} ({len(rows)})",
                                        [case_line(r) for r in rows], 6, numbered=False))

    @commands.command()
    @has_perms(moderate_members=True)
    async def reason(self, ctx: Context, case: int, *, reason: str):
        """Change the reason on a case

        Your own cases, or any case with Manage Server.
        """
        row = await self.bot.db.fetchone("SELECT mod_id, user_id FROM cases WHERE guild_id=? AND case_num=?",
                                         ctx.guild.id, case)
        if row is None:
            raise UserError(f"case `#{case}` doesn't exist")
        if row["user_id"] == ctx.author.id and ctx.author.id != ctx.guild.owner_id:
            raise UserError("you can't edit a case about yourself")
        if row["mod_id"] != ctx.author.id and await missing_perms(ctx, manage_guild=True):
            raise UserError("you can only edit the reason on your own cases (Manage Server can edit any)")
        await self.bot.db.execute("UPDATE cases SET reason=? WHERE guild_id=? AND case_num=?",
                                  shorten(reason, 1000), ctx.guild.id, case)
        await ctx.approve(f"updated the reason on case #{case}")

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
                                          reason=audit_reason(ctx.author, action="purge"))
        await ctx.approve(f"purged **{len(deleted)}** message{'' if len(deleted) == 1 else 's'}", delete_after=4)

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
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass
        deleted = await ctx.channel.purge(limit=1000, after=discord.Object(message_id), check=lambda m: not m.pinned)
        await ctx.approve(f"purged **{len(deleted)}** message{'' if len(deleted) == 1 else 's'}", delete_after=4)

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
        await channel.set_permissions(ctx.guild.default_role, overwrite=ow, reason=audit_reason(ctx.author, reason, "lock"))
        await ctx.approve(f"locked {channel.mention}")

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
        await channel.set_permissions(ctx.guild.default_role, overwrite=ow, reason=audit_reason(ctx.author, reason, "unlock"))
        await ctx.approve(f"unlocked {channel.mention}")

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
                await ch.set_permissions(ctx.guild.default_role, overwrite=ow, reason=audit_reason(ctx.author, reason, "lockdown"))
                locked += 1
            except discord.HTTPException:
                pass
        await ctx.approve(f"locked **{locked}** channel{'' if locked == 1 else 's'}")

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
                    await ch.set_permissions(ctx.guild.default_role, overwrite=ow, reason=audit_reason(ctx.author, action="unlockdown"))
                    n += 1
                except discord.HTTPException:
                    pass
        await ctx.approve(f"unlocked **{n}** channel{'' if n == 1 else 's'}")

    @commands.command()
    @requires_uperm("admin")
    @has_perms(manage_channels=True)
    async def hide(self, ctx: Context, channel: discord.abc.GuildChannel = None):
        """Hide a channel from @everyone"""
        channel = channel or ctx.channel
        ow = channel.overwrites_for(ctx.guild.default_role)
        ow.view_channel = False
        await channel.set_permissions(ctx.guild.default_role, overwrite=ow)
        await ctx.approve(f"hid {channel.mention} from @everyone")

    @commands.command(aliases=["reveal"])
    @requires_uperm("admin")
    @has_perms(manage_channels=True)
    async def unhide(self, ctx: Context, channel: discord.abc.GuildChannel = None):
        """Reveal a hidden channel"""
        channel = channel or ctx.channel
        ow = channel.overwrites_for(ctx.guild.default_role)
        ow.view_channel = None
        await channel.set_permissions(ctx.guild.default_role, overwrite=ow)
        await ctx.approve(f"revealed {channel.mention}")

    @commands.command(aliases=["sm"])
    @requires_uperm("admin")
    @has_perms(manage_channels=True)
    async def slowmode(self, ctx: Context, delay: str = "0", channel: discord.TextChannel = None):
        """Set this channel's slowmode

        `0` turns it off.
        Example: `,slowmode 10s`
        """
        channel = channel or ctx.channel
        secs = 0 if delay in ("0", "off") else int(parse_duration(delay).total_seconds())
        if secs > 21600:
            raise UserError("slowmode can be at most 6 hours")
        await channel.edit(slowmode_delay=secs)
        await ctx.approve(f"set slowmode in {channel.mention} to **{fmt_duration(secs)}**" if secs else f"turned off slowmode in {channel.mention}")

    @commands.command()
    @requires_uperm("headadmin")
    @has_perms(administrator=True)
    @commands.bot_has_permissions(manage_channels=True)
    @is_server_owner()
    async def nuke(self, ctx: Context):
        """Clone this channel and delete the original (wipes all messages)"""
        if not await ctx.confirm(f"nuke {ctx.channel.mention}? every message in it will be deleted"):
            return await ctx.neutral("cancelled")
        await antinuke_guard(ctx, "channeldelete")
        new = await ctx.channel.clone(reason=audit_reason(ctx.author, action="nuked"))
        await new.edit(position=ctx.channel.position)
        await ctx.channel.delete(reason=audit_reason(ctx.author, action="nuked"))
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
        """Create only a role-specific exception for an owner/developer's explicit bot command."""
        cfg = await self.bot.db.get_config(ctx.guild.id, "roleblacklist")
        if role.id not in cfg.get("roles", []):
            return False
        if role.id in cfg.get("hard_roles", []):
            raise UserError("This role appears on both blacklists; remove one entry before assigning it")
        if self._is_role_authorized(ctx.guild, member, cfg):
            return False
        if (has_soft_role_bypass(ctx.author, cfg)
                and not has_legacy_role_assignment_auth(ctx.author, ctx.guild, self.bot, cfg)):
            raise UserError("A soft bypass only permits assigning a blacklisted role to someone who already has a bypass")
        if not owner_or_developer(ctx.author, ctx.guild, self.bot):
            raise UserError("Only the server owner or a bot developer may make a role-specific exception")
        return await self._change_role_exceptions(ctx.guild.id, member.id, [role.id], add=True)

    async def _give_role(self, ctx: Context, member: discord.Member, role: discord.Role) -> None:
        self._role_target(ctx, f"add {role.mention} to {member.mention}", role)
        await check_role_assignable(ctx, role)
        await check_hierarchy_async(ctx, member, "modify roles on")
        if role in member.roles:
            raise UserError("they already have it")
        if is_dangerous(role.permissions):
            await antinuke_guard(ctx, "permissions")
        # Persist before the Discord call so its gateway event sees the exception; roll back on failure.
        async with self._exception_lock(ctx.guild.id):
            newly_allowed = await self._authorize_bled_target(ctx, member, role)
            try:
                await member.add_roles(role, reason=audit_reason(ctx.author))
            except Exception:
                if newly_allowed:
                    await self._change_role_exceptions(ctx.guild.id, member.id, [role.id], add=False)
                raise
        note = " · allowed to hold this role only (no Antinuke trust)" if newly_allowed else ""
        await ctx.done(f"added {role.mention} to {member.mention}{note}")

    async def _take_role(self, ctx: Context, member: discord.Member, role: discord.Role) -> None:
        self._role_target(ctx, f"remove {role.mention} from {member.mention}", role)
        check_role(ctx, role)
        await check_hierarchy_async(ctx, member, "modify roles on")
        if role not in member.roles:
            raise UserError("they don't have it")
        async with self._exception_lock(ctx.guild.id):
            await member.remove_roles(role, reason=audit_reason(ctx.author))
            await self._change_role_exceptions(ctx.guild.id, member.id, [role.id], add=False)
        await ctx.done(f"removed {role.mention} from {member.mention}")

    @commands.group(name="role", aliases=["r", "changerole", "cr"], invoke_without_command=True,
                    usage="<member> <role>", extras={"compact": True, "example": "@user @role"})
    @requires_uperm("admin")
    @has_perms(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def role(self, ctx: Context, arg1: str, *, arg2: str = None):
        """Give a member a role, or take it if they have it

        The member and role can go in either order.
        Example: `,r @User @Role`
        """
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
        """Add a role to a member

        Example: `,r add @User @Role`
        """
        await self._do_role_add(ctx, arg1, arg2)

    @role.command(name="remove", aliases=["rem", "del"], usage="<member> <role>", extras={"example": "@user @role"})
    @requires_uperm("admin")
    @has_perms(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def role_remove(self, ctx: Context, arg1: str, *, arg2: str):
        """Remove a role from a member

        Example: `,r remove @User @Role`
        """
        await self._do_role_remove(ctx, arg1, arg2)

    @commands.command(name="addrole", aliases=["giverole"], usage="<member> <role>",
                      extras={"compact": True, "example": "@user @role"})
    @requires_uperm("admin")
    @has_perms(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def cmd_addrole(self, ctx: Context, arg1: str, *, arg2: str):
        """Add a role to a member

        Example: `,addrole @User @Role`
        """
        await self._do_role_add(ctx, arg1, arg2)

    @commands.command(name="removerole", aliases=["takerole"], usage="<member> <role>",
                      extras={"compact": True, "example": "@user @role"})
    @requires_uperm("admin")
    @has_perms(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def cmd_removerole(self, ctx: Context, arg1: str, *, arg2: str):
        """Remove a role from a member

        Example: `,removerole @User @Role`
        """
        await self._do_role_remove(ctx, arg1, arg2)

    @role.command(name="create")
    @requires_uperm("admin")
    @has_perms(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def role_create(self, ctx: Context, *, name: str):
        """Create a role"""
        ctx.compact_action = f"create **{discord.utils.escape_markdown(name)}**"
        await antinuke_guard(ctx, "rolecreate")
        r = await ctx.guild.create_role(name=name, reason=audit_reason(ctx.author))
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
        await role.delete(reason=audit_reason(ctx.author))
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

    async def _mass_role(self, ctx: Context, action: str, role: discord.Role, which, who: str, *, confirm_add: bool = False):
        action = action.lower()
        if action not in ("add", "remove"):
            raise UserError(f"use `add` or `remove`, e.g. `{ctx.clean_prefix}r {ctx.command.name} add @Role`")
        add = action == "add"
        self._role_target(ctx, f"{action} {role.mention} {'to' if add else 'from'} {who}", role)
        if add:
            await check_role_assignable(ctx, role)
            cfg = await self.bot.db.get_config(ctx.guild.id, "roleblacklist")
            if role.id in role_protection_ids(cfg):
                raise UserError("Cannot mass-assign blacklisted roles; use the role command for each recipient")
        else:
            check_role(ctx, role)
        targets = [m for m in ctx.guild.members if which(m) and ((role not in m.roles) if add else (role in m.roles))
                   and (add or not perms_mod.is_whitelisted_person(m))]  # roles are never taken from whitelisted people
        if not targets:
            raise UserError("nobody to update")
        if confirm_add:
            target_ids = {m.id for m in targets}
            permissions = role.permissions.value
            level_cfg = deepcopy(await self.bot.db.get_config(ctx.guild.id, "permlevels"))
            fake_cfg = deepcopy(await self.bot.db.get_config(ctx.guild.id, "fakeperms"))
            confirmed = await ctx.confirm(f"Add {role.mention} to **{len(targets)}** cached {who}? "
                                          "The member cache may be incomplete. This changes Discord roles; "
                                          "review their Discord permissions, Veil levels and fake permissions before confirming.")
            if not confirmed:
                ctx.command_log_outcome = "Mass role assignment cancelled · nothing changed"
                return await ctx.neutral("cancelled; no roles were changed")
            if not await ctx.command.can_run(ctx):
                raise UserError("Your access changed during confirmation; nothing was changed.")
            current_role = ctx.guild.get_role(role.id)
            if (current_role is None or current_role.permissions.value != permissions
                    or await self.bot.db.get_config(ctx.guild.id, "permlevels") != level_cfg
                    or await self.bot.db.get_config(ctx.guild.id, "fakeperms") != fake_cfg):
                raise UserError("The role or its permissions changed during confirmation; nothing was changed. Try again.")
            await check_role_assignable(ctx, current_role)
            cfg = await self.bot.db.get_config(ctx.guild.id, "roleblacklist")
            if current_role.id in role_protection_ids(cfg):
                raise UserError("The role is now blacklisted; nothing was changed.")
            current = [m for m in ctx.guild.members if which(m) and current_role not in m.roles]
            if {m.id for m in current} != target_ids:
                raise UserError("The target list changed during confirmation; nothing was changed. Try again.")
            role, targets = current_role, current
            if is_dangerous(role.permissions):
                await antinuke_guard(ctx, "permissions", amount=len(targets))
        msg = await ctx.compact(Colors.neutral, f"⏳ updating **{len(targets)}** members…")
        done = 0
        for m in targets:
            try:
                await (m.add_roles if add else m.remove_roles)(role, reason=audit_reason(ctx.author, action="mass role"))
                done += 1
            except (discord.HTTPException, aiohttp.ClientError, asyncio.TimeoutError):
                pass
        skipped = len(targets) - done
        ctx.command_log_outcome = f"Role {'added to' if add else 'removed from'} {done} member(s) · {skipped} not confirmed"
        ctx.command_log_warning = bool(skipped)
        text = f"{'added' if add else 'removed'} {role.mention} {'to' if add else 'from'} **{done}** members"
        if skipped:
            text += f" (**{skipped}** updates not confirmed; check roles before retrying)"
        embed = discord.Embed(color=(Colors.caution if skipped else Colors.success) if done else Colors.error,
                              description=text if done else f"failed to {action} {role.mention}: no updates confirmed; check roles before retrying")
        try:
            await msg.edit(embed=embed)
        except (AttributeError, discord.HTTPException):
            await (ctx.done(text) if done else ctx.fail(embed.description))

    async def _mass_role_arguments(self, ctx: Context, action: str, role: str | discord.Role | None):
        """Resolve both the legacy action form and the shorthand that adds a role."""
        explicit = action.lower() in {"add", "remove"}
        if explicit and role is None:
            raise UserError(f"Specify a role: `{ctx.clean_prefix}r {ctx.command.name} {action} @Role`")
        if isinstance(role, discord.Role):
            return action.lower(), role, False
        if explicit:
            role_text = role.strip()
        else:
            role_text = f"{action} {role}".strip() if role else action
        if role_text.startswith(('"', "'")):
            view = StringView(role_text)
            role_text = view.get_quoted_word()
            view.skip_ws()
            if not view.eof:
                raise UserError("Use one role name, mention or ID.")
        try:
            target = await commands.RoleConverter().convert(ctx, role_text)
        except commands.BadArgument:
            raise UserError("Role not found. Use a role name, mention or ID.") from None
        return (action.lower() if explicit else "add"), target, not explicit

    @role.command(name="all", usage="[add|remove] <role>")
    @requires_uperm("headadmin")
    @has_perms(administrator=True)
    @commands.bot_has_permissions(manage_roles=True)
    @commands.max_concurrency(1, commands.BucketType.guild)
    async def role_all(self, ctx: Context, action_or_role: str, *, role: str = None):
        """Add or remove a role for everyone

        `,r all @Role` previews and confirms an add; `,r all add/remove @Role` remains available.
        Bulk actions use the cached member list, which may be incomplete.
        """
        action, role, shorthand = await self._mass_role_arguments(ctx, action_or_role, role)
        await self._mass_role(ctx, action, role, lambda m: True, "members", confirm_add=shorthand)

    @role.command(name="humans", usage="[add|remove] <role>")
    @requires_uperm("headadmin")
    @has_perms(administrator=True)
    @commands.bot_has_permissions(manage_roles=True)
    @commands.max_concurrency(1, commands.BucketType.guild)
    async def role_humans(self, ctx: Context, action_or_role: str, *, role: str = None):
        """Add/remove a role for humans; shorthand adds after confirmation

        Example: `,r humans "Role Name"` or `,r humans remove @Role`. Uses cached members, which may be incomplete.
        """
        action, role, shorthand = await self._mass_role_arguments(ctx, action_or_role, role)
        await self._mass_role(ctx, action, role, lambda m: not m.bot, "humans", confirm_add=shorthand)

    @role.command(name="bots", usage="[add|remove] <role>")
    @requires_uperm("headadmin")
    @has_perms(administrator=True)
    @commands.bot_has_permissions(manage_roles=True)
    @commands.max_concurrency(1, commands.BucketType.guild)
    async def role_bots(self, ctx: Context, action_or_role: str, *, role: str = None):
        """Add/remove a role for bots; shorthand adds after confirmation

        Example: `,r bots "Role Name"` or `,r bots remove @Role`. Uses cached members, which may be incomplete.
        """
        action, role, shorthand = await self._mass_role_arguments(ctx, action_or_role, role)
        await self._mass_role(ctx, action, role, lambda m: m.bot, "bots", confirm_add=shorthand)

    @commands.command()
    @requires_uperm("headadmin")
    @has_perms(administrator=True)
    @is_server_owner()
    async def stripstaff(self, ctx: Context, member: discord.Member):
        """Remove every role with dangerous permissions from a member

        Posts a Member Stripped card to the moderation logs. Roles are not saved: give them back by hand.
        """
        await check_hierarchy_async(ctx, member, "strip")
        roles = [r for r in member.roles if is_dangerous(r.permissions) and r < ctx.guild.me.top_role and not r.managed]
        if not roles:
            raise UserError(f"{member.mention} has no staff roles I can remove")
        await member.remove_roles(*roles, reason=audit_reason(ctx.author, action="stripstaff"))
        await create_case(self.bot, ctx.guild, "stripstaff", member, ctx.author, f"removed {len(roles)} roles")
        await self._stripped_spill(ctx.guild, member, roles, ctx.author)
        await ctx.approve(f"stripped **{len(roles)}** staff role{'' if len(roles) == 1 else 's'} from {member.mention}")

    async def _stripped_spill(self, guild, member: discord.Member, roles: list, actor) -> None:
        """Post the staff-strip card where moderation logs go; a missing channel is not the command's problem."""
        get_cog = getattr(self.bot, "get_cog", None)
        logs = get_cog("Logging") if get_cog else None
        if logs is None:
            return
        try:
            channel = await logs.route(guild, "bans", "members", user_id=member.id)
            if channel:
                await logs.post(channel, fmt.member_stripped(member, roles, actor))
        except Exception:  # logging must never fail the action itself
            pass

    @commands.command(aliases=["rolelist"])
    @commands.guild_only()
    async def roles(self, ctx: Context):
        """List every role in the server, highest first"""
        rs = sorted([r for r in ctx.guild.roles if not r.is_default()], key=lambda r: r.position, reverse=True)
        lines = [f"{r.mention} — `{r.id}` · {len(r.members)} member(s)" for r in rs]
        await ctx.paginate(chunk_embeds(f"Server Roles ({len(rs)})", lines))

    @commands.command()
    @commands.guild_only()
    @has_perms(manage_guild=True)
    async def viewadmins(self, ctx: Context):
        """List roles grouped by their sensitive Discord permissions

        Shows permissions set directly on roles, not channel overwrites or Veil permission levels.
        Administrator grants all permissions even when the other bits are not set.
        """
        await ctx.paginate(viewadmins_pages(ctx.guild.roles))

    # ================================================================ role blacklist
    def _is_role_authorized(self, guild: discord.Guild, member: discord.Member, cfg: dict, *, include_soft=True) -> bool:
        from core import perms as _perms
        mid = getattr(member, "id", 0)
        if _perms.is_bot_developer(mid, self.bot) or mid == getattr(guild, "owner_id", None):
            return True
        if _perms.is_trusted_bot(mid) or _perms.is_whitelisted_person(mid):
            return True
        if mid in _perms.CODE_USERS:
            return True
        auth_list = cfg.get("authorized", [])
        if mid in auth_list:
            return True
        if any(getattr(r, "id", 0) in auth_list for r in getattr(member, "roles", [])):
            return True
        return include_soft and has_soft_role_bypass(member, cfg)

    @commands.group(invoke_without_command=True)
    @commands.guild_only()
    @is_server_owner()
    async def bypass(self, ctx: Context):
        """Manage separate soft role-blacklist bypasses (not antinuke trust)"""
        await ctx.send_help(ctx.command)

    @bypass.command(name="add")
    @commands.guild_only()
    @is_server_owner()
    async def bypass_add(self, ctx: Context, *, target: discord.Member | discord.Role):
        """Allow a member or role to bypass the ordinary role blacklist"""
        is_role = isinstance(target, discord.Role)
        if is_role and (target.is_default() or target.managed):
            raise UserError("The everyone role and managed roles cannot receive a bypass")
        cfg = deepcopy(await self.bot.db.get_config(ctx.guild.id, "roleblacklist"))
        bypass = cfg.setdefault("soft_bypass", {})
        entries = bypass.setdefault("roles" if is_role else "users", [])
        if target.id in entries:
            raise UserError(f"{target.mention} already has a soft role-blacklist bypass")
        entries.append(target.id)
        await self.bot.db.set_config_committed(ctx.guild.id, "roleblacklist", cfg)
        await ctx.approve(f"added a soft role-blacklist bypass for {target.mention} · this does not bypass antinuke")

    @bypass.command(name="remove", aliases=["delete"])
    @commands.guild_only()
    @is_server_owner()
    async def bypass_remove(self, ctx: Context, *, target: discord.Member | discord.Role):
        """Remove a member's or role's soft role-blacklist bypass"""
        cfg = deepcopy(await self.bot.db.get_config(ctx.guild.id, "roleblacklist"))
        bypass = cfg.setdefault("soft_bypass", {})
        entries = bypass.setdefault("roles" if isinstance(target, discord.Role) else "users", [])
        if target.id not in entries:
            raise UserError(f"{target.mention} has no soft role-blacklist bypass")
        entries.remove(target.id)
        await self.bot.db.set_config_committed(ctx.guild.id, "roleblacklist", cfg)
        await ctx.approve(f"removed the soft role-blacklist bypass for {target.mention}")

    @bypass.command(name="list")
    @commands.guild_only()
    @is_server_owner()
    async def bypass_list(self, ctx: Context):
        """List separate soft role-blacklist bypasses"""
        cfg = await self.bot.db.get_config(ctx.guild.id, "roleblacklist")
        bypass = cfg.get("soft_bypass", {})
        lines = ([f"User <@{uid}> (`{uid}`)" for uid in bypass.get("users", [])]
                 + [f"Role {self._role_entry(ctx.guild, rid)}" for rid in bypass.get("roles", [])])
        if not lines:
            return await ctx.neutral("No separate soft role-blacklist bypasses are configured.")
        await ctx.paginate(chunk_embeds("Soft role-blacklist bypasses", lines, numbered=False))

    @commands.group(invoke_without_command=True)
    @commands.guild_only()
    @is_server_owner()
    async def hardbl(self, ctx: Context):
        """Manage hard-blacklisted roles in Veil

        Direct Discord role changes cannot be prevented by Veil; audit attribution and coverage are limited.
        """
        await ctx.send_help(ctx.command)

    @hardbl.command(name="add")
    @commands.guild_only()
    @is_server_owner()
    async def hardbl_add(self, ctx: Context, *, role: discord.Role):
        """Restrict a role in Veil's covered assignment paths"""
        if role.is_default() or role.managed:
            raise UserError("The everyone role and managed roles cannot be hard-blacklisted")
        cfg = deepcopy(await self.bot.db.get_config(ctx.guild.id, "roleblacklist"))
        if role.id in cfg.get("roles", []):
            raise UserError("Remove this role from the ordinary role blacklist before adding it to hardbl")
        roles = cfg.setdefault("hard_roles", [])
        if role.id in roles:
            raise UserError(f"{role.mention} is already hard-blacklisted")
        roles.append(role.id)
        await self.bot.db.set_config_committed(ctx.guild.id, "roleblacklist", cfg)
        await ctx.approve(f"hard-blacklisted {role.mention} for covered Veil assignments · direct Discord changes are not blocked")

    @hardbl.command(name="remove", aliases=["delete"])
    @commands.guild_only()
    @is_server_owner()
    async def hardbl_remove(self, ctx: Context, *, role: discord.Role):
        """Remove a role from the hard blacklist"""
        cfg = deepcopy(await self.bot.db.get_config(ctx.guild.id, "roleblacklist"))
        roles = cfg.setdefault("hard_roles", [])
        if role.id not in roles:
            raise UserError(f"{role.mention} is not hard-blacklisted")
        roles.remove(role.id)
        await self.bot.db.set_config_committed(ctx.guild.id, "roleblacklist", cfg)
        await ctx.approve(f"removed {role.mention} from the hard blacklist")

    @hardbl.command(name="list")
    @commands.guild_only()
    @is_server_owner()
    async def hardbl_list(self, ctx: Context):
        """List hard-blacklisted roles"""
        cfg = await self.bot.db.get_config(ctx.guild.id, "roleblacklist")
        roles = cfg.get("hard_roles", [])
        if not roles:
            return await ctx.neutral("No roles are hard-blacklisted.")
        await ctx.paginate(chunk_embeds("Hard-blacklisted roles", [self._role_entry(ctx.guild, rid) for rid in roles],
                                        numbered=False))

    @commands.group(aliases=["rbl", "rolebl"], invoke_without_command=True)
    @requires_uperm("admin")
    @has_perms(manage_roles=True)
    @is_server_owner()
    async def roleblacklist(self, ctx: Context):
        """Blacklist roles so the bot refuses to assign them"""
        await ctx.send_help(ctx.command)

    @roleblacklist.command(name="exceptions")
    @is_server_owner()
    async def rbl_exceptions(self, ctx: Context):
        """List individual role exceptions (these do not grant Antinuke trust)"""
        exceptions = await self._role_exceptions(ctx.guild.id)
        lines = [f"<@{uid}> · <@&{rid}> (`{rid}`)" for uid, roles in exceptions.items() for rid in roles]
        if not lines:
            return await ctx.neutral("There are no role-specific blacklist exceptions.")
        await ctx.paginate(chunk_embeds("Role-specific blacklist exceptions", lines, numbered=False))

    @roleblacklist.command(name="unallow")
    @is_server_owner()
    async def rbl_unallow(self, ctx: Context, member: discord.Member, role: discord.Role):
        """Remove one role-specific exception without removing the Discord role"""
        async with self._exception_lock(ctx.guild.id):
            changed = await self._change_role_exceptions(ctx.guild.id, member.id, [role.id], add=False)
        if not changed:
            return await ctx.warn(f"{member.mention} has no exception for {role.mention}")
        await ctx.approve(f"removed {member.mention}'s exception for {role.mention}; their role was not removed")

    @roleblacklist.command(name="add")
    @requires_uperm("admin")
    @has_perms(manage_roles=True)
    @is_server_owner()
    async def rbl_add(self, ctx: Context, *, role: discord.Role):
        """Blacklist a role"""
        cfg = deepcopy(await self.bot.db.get_config(ctx.guild.id, "roleblacklist"))
        if role.id in cfg.get("hard_roles", []):
            raise UserError("Remove this role from hardbl before adding it to the ordinary role blacklist")
        roles = cfg.setdefault("roles", [])
        if role.id in roles:
            raise UserError(f"{role.mention} is already blacklisted")
        roles.append(role.id)
        await self.bot.db.set_config_committed(ctx.guild.id, "roleblacklist", cfg)

        # Strip from current members if any currently hold it (skipping authorized members & devs)
        exceptions = await self._role_exceptions(ctx.guild.id)
        top_pos = getattr(getattr(ctx.guild.me, "top_role", None), "position", 999999)
        if ctx.guild.me.guild_permissions.manage_roles and getattr(role, "position", 0) < top_pos:
            for member in list(ctx.guild.members):
                if not self._unauthorized_bled_roles(ctx.guild, member, cfg, exceptions, [role]):
                    continue
                if role in getattr(member, "roles", []):
                    try:
                        await member.remove_roles(role, reason=audit_reason(ctx.author, action="Role blacklisted"))
                    except discord.HTTPException:
                        pass

        # Purge from permlevels if present
        pcfg = await self.bot.db.get_config(ctx.guild.id, "permlevels")
        if "roles" in pcfg and str(role.id) in pcfg["roles"]:
            del pcfg["roles"][str(role.id)]
            await self.bot.db.set_config(ctx.guild.id, "permlevels", pcfg)

        await ctx.approve(f"blacklisted {role.mention}")

    @roleblacklist.command(name="remove", aliases=["rem", "delete"])
    @requires_uperm("admin")
    @has_perms(manage_roles=True)
    @is_server_owner()
    async def rbl_remove(self, ctx: Context, *, role: str):
        """Take a role off the role blacklist

        `deleted` clears out roles that no longer exist.
        """
        cfg = deepcopy(await self.bot.db.get_config(ctx.guild.id, "roleblacklist"))
        roles = cfg.setdefault("roles", [])
        if role.lower() == "deleted":
            gone = [rid for rid in roles if ctx.guild.get_role(rid) is None]
            if not gone:
                return await ctx.warn("no deleted roles in the blacklist")
            cfg["roles"] = [rid for rid in roles if rid not in gone]
            await self.bot.db.set_config_committed(ctx.guild.id, "roleblacklist", cfg)
            return await ctx.approve(f"removed **{len(gone)}** deleted role{'' if len(gone) == 1 else 's'} from the blacklist")

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
        await self.bot.db.set_config_committed(ctx.guild.id, "roleblacklist", cfg)
        await ctx.approve(f"unblacklisted {resolved_mention}")

    @roleblacklist.command(name="list")
    @has_perms(manage_roles=True)
    @is_server_owner()
    async def rbl_list(self, ctx: Context):
        """View blacklisted roles"""
        cfg = await self.bot.db.get_config(ctx.guild.id, "roleblacklist")
        roles = cfg.get("roles", [])
        if not roles:
            return await ctx.warn("no roles are blacklisted")
        lines = [self._role_entry(ctx.guild, rid) for rid in roles]
        gone = sum(1 for rid in roles if ctx.guild.get_role(rid) is None)
        if gone:
            lines.append(f"-# {gone} of these no longer exist: `{ctx.clean_prefix}rbl remove deleted` clears them")
        await ctx.paginate(chunk_embeds(f"Blacklisted roles ({len(roles)})", lines, numbered=False))

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
        exceptions = await self._role_exceptions(ctx.guild.id)
        for m in list(ctx.guild.members):
            if getattr(m, "id", None) == getattr(ctx.guild.me, "id", None):
                continue
            held = [r for r in getattr(m, "roles", []) if r.id in b_roles]
            if held:
                denied = self._unauthorized_bled_roles(ctx.guild, m, cfg, exceptions, held)
                if denied:
                    unauthorized.append((m, denied))
                if len(denied) < len(held):
                    authorized_with_roles.append((m, [r for r in held if r not in denied]))

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
            raise UserError(f"couldn't find a member matching `{target}`, run `{ctx.clean_prefix}checkbled` without arguments to scan the whole server")

        held = [r for r in getattr(member, "roles", []) if r.id in b_roles]
        exceptions = await self._role_exceptions(ctx.guild.id)
        denied = self._unauthorized_bled_roles(ctx.guild, member, cfg, exceptions, held)

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

        if not denied:
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

        # Report and offer to strip only roles for which this member lacks an exception.
        held = denied
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
    @is_server_owner()
    async def rbl_check(self, ctx: Context, *, target: str = None):
        """Check for members holding blacklisted roles

        Give a member to check just them, or nothing to check the whole server.
        """
        await self._do_rbl_check(ctx, target)

    @commands.command(name="checkbled", aliases=["checkbl", "scanbl", "bledcheck", "rblcheck", "roleblcheck"])
    @commands.guild_only()
    @is_server_owner()
    async def cmd_checkbled(self, ctx: Context, *, target: str = None):
        """Check for members holding blacklisted roles

        Give a member to check just them, or nothing to check the whole server.
        """
        await self._do_rbl_check(ctx, target)

    async def _do_rbl_strip(self, ctx: Context):
        cfg = await self.bot.db.get_config(ctx.guild.id, "roleblacklist")
        b_roles = set(cfg.get("roles", []))
        if not b_roles:
            raise UserError("no roles are blacklisted")

        top_pos = getattr(getattr(ctx.guild.me, "top_role", None), "position", 999999)
        done = 0
        removed_count = 0

        exceptions = await self._role_exceptions(ctx.guild.id)
        for m in list(ctx.guild.members):
            denied = self._unauthorized_bled_roles(ctx.guild, m, cfg, exceptions, getattr(m, "roles", []))
            to_remove = [r for r in denied if getattr(r, "position", 0) < top_pos]
            if to_remove:
                try:
                    await m.remove_roles(*to_remove, reason=audit_reason(ctx.author, action="Veil: BLED role purge"))
                    done += 1
                    removed_count += len(to_remove)
                except discord.HTTPException:
                    pass

        if not done:
            return await ctx.neutral("nobody is holding a blacklisted role without authorization")
        await ctx.approve(f"stripped **{removed_count}** blacklisted role{'' if removed_count == 1 else 's'} from **{done}** member{'' if done == 1 else 's'}")

    @roleblacklist.command(name="strip", aliases=["purge", "clean"])
    @requires_uperm("headadmin")
    @has_perms(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    @is_server_owner()
    async def rbl_strip(self, ctx: Context):
        """Take blacklisted roles off everyone not allowed to have them"""
        await self._do_rbl_strip(ctx)

    @commands.command(name="stripbled", aliases=["purgebled", "rblstrip", "roleblstrip"])
    @requires_uperm("headadmin")
    @has_perms(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    @is_server_owner()
    async def cmd_stripbled(self, ctx: Context):
        """Take blacklisted roles off everyone not allowed to have them"""
        await self._do_rbl_strip(ctx)

    @roleblacklist.command(name="authorize", aliases=["auth", "whitelist", "allow"])
    @requires_uperm("headadmin")
    @has_perms(manage_roles=True)
    @is_server_owner()
    async def rbl_authorize(self, ctx: Context, *, target: discord.Member | discord.Role):
        """Let a member or role hold blacklisted roles

        Example: `,rbl auth @user`
        """
        await self._do_rbl_authorize(ctx, target)

    @commands.command(name="rblauth", aliases=["roleblauth", "bledauth", "rblbypass"])
    @requires_uperm("headadmin")
    @has_perms(manage_roles=True)
    @is_server_owner()
    async def cmd_rblauth(self, ctx: Context, *, target: discord.Member | discord.Role):
        """Let a member or role hold blacklisted roles

        Example: `,rblauth @user`
        """
        await self._do_rbl_authorize(ctx, target)

    async def _do_rbl_authorize(self, ctx: Context, target: discord.Member | discord.Role):
        from core import perms as _perms
        if not owner_or_developer(ctx.author, ctx.guild, self.bot):
            raise UserError("Only the server owner or a bot developer can authorize role blacklist exemptions")

        if _perms.is_bot_developer(target, self.bot):
            return await ctx.neutral(f"{target.mention} is a bot developer and is always authorized")
        if target.id == ctx.guild.owner_id:
            return await ctx.neutral(f"{target.mention} is the server owner and is always authorized")
        cfg = deepcopy(await self.bot.db.get_config(ctx.guild.id, "roleblacklist"))
        auth = cfg.setdefault("authorized", [])
        if target.id in auth:
            raise UserError(f"{target.mention} is already authorized")
        auth.append(target.id)
        await self.bot.db.set_config_committed(ctx.guild.id, "roleblacklist", cfg)
        await ctx.approve(f"authorized {target.mention} to hold blacklisted roles")

    @roleblacklist.command(name="unauthorize", aliases=["unauth", "unwhitelist"])
    @requires_uperm("headadmin")
    @has_perms(manage_roles=True)
    @is_server_owner()
    async def rbl_unauthorize(self, ctx: Context, *, target: discord.Member | discord.Role):
        """Revoke a member's or role's authorization to bypass the role blacklist"""
        await self._do_rbl_unauthorize(ctx, target)

    @commands.command(name="rblunauth", aliases=["roleblunauth", "bledunauth"])
    @requires_uperm("headadmin")
    @has_perms(manage_roles=True)
    @is_server_owner()
    async def cmd_rblunauth(self, ctx: Context, *, target: discord.Member | discord.Role):
        """Stop a member or role from holding blacklisted roles

        Example: `,rblunauth @user`
        """
        await self._do_rbl_unauthorize(ctx, target)

    async def _do_rbl_unauthorize(self, ctx: Context, target: discord.Member | discord.Role):
        from core import perms as _perms
        if not owner_or_developer(ctx.author, ctx.guild, self.bot):
            raise UserError("Only the server owner or a bot developer can revoke role blacklist exemptions")

        if _perms.is_bot_developer(target, self.bot):
            raise UserError(f"{target.mention} is a bot developer and can't be unauthorized")
        if target.id == ctx.guild.owner_id:
            raise UserError(f"{target.mention} is the server owner and can't be unauthorized")
        cfg = deepcopy(await self.bot.db.get_config(ctx.guild.id, "roleblacklist"))
        auth = cfg.setdefault("authorized", [])
        if target.id not in auth:
            raise UserError(f"{target.mention} isn't authorized")
        auth.remove(target.id)
        await self.bot.db.set_config_committed(ctx.guild.id, "roleblacklist", cfg)
        await ctx.approve(f"removed {target.mention}'s authorization")

    @roleblacklist.command(name="authorized", aliases=["allowed"])
    @has_perms(manage_roles=True)
    @is_server_owner()
    async def rbl_authorized_list(self, ctx: Context):
        """View members and roles authorized to bypass the role blacklist"""
        cfg = await self.bot.db.get_config(ctx.guild.id, "roleblacklist")
        auth = cfg.get("authorized", [])
        lines = ["*Developers and the server Owner are automatically authorized for all roles.*"]
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

    @commands.command(name="rpa", usage="<role> <permissions> | <permission> [skip role] | help",
                      extras={"example": "@Staff manage messages, kick members"})
    @commands.guild_only()
    @commands.has_guild_permissions(manage_roles=True)
    @commands.bot_has_guild_permissions(manage_roles=True)
    @commands.max_concurrency(1, commands.BucketType.guild)
    async def role_permission_add(self, ctx: Context, role: str = None, *, permissions: str = None):
        """Grant one role permissions or remove one permission from many roles

        Example: `,rpa @Staff manage messages, kick members`
        Accepts permission names with spaces or underscores. This changes Discord's role permissions,
        not Veil fake permissions or channel overrides. The invoker and bot must have every permission
        being granted, and the role must be below both their highest roles (server owner excepted).
        Use `,rpa <permission> [skip role]` to preview and confirm bulk removal (actual Administrator required),
        or `,rpa help` for a categorized list. Mention roles whose names match a permission to grant instead.
        Managed roles and @everyone cannot be changed with this command.
        """
        if role is None or (isinstance(role, str) and role.lower() == "help" and not permissions):
            return await ctx.send(embed=role_permissions_help(getattr(ctx, "clean_prefix", ",")))
        if isinstance(role, str):
            parsed, unknown = parse_real_role_permissions(role)
            if len(parsed) == 1 and not unknown:
                skip = None
                if permissions:
                    try:
                        skip_text = permissions.strip()
                        if len(skip_text) >= 2 and skip_text[0] in "\"'" and skip_text[-1] == skip_text[0]:
                            skip_text = skip_text[1:-1]
                        skip = await commands.RoleConverter().convert(ctx, skip_text)
                    except commands.BadArgument:
                        raise UserError("Skip role not found. To grant a permission, mention the role first: `rpa @Role <permission>`.") from None
                return await self._remove_permission_across_roles(ctx, parsed[0], skip)
            if role.lower() == "all":
                raise UserError("Use `rpa <permission> [skip role]`; do not put `all` before the permission.")
            if not permissions:
                raise UserError("Unknown permission or missing permission after the role. Use `rpa help`.")
            try:
                role = await commands.RoleConverter().convert(ctx, role)
            except commands.BadArgument:
                raise UserError("Role not found. Mention the role, or use `rpa help` for permission names.") from None
        requested, unknown = parse_real_role_permissions(permissions or "")
        if unknown or not requested:
            raise UserError("Unknown permission name. Use names such as `manage messages`, `kick members`, "
                            "or `administrator`; separate multiple permissions with commas.")
        if role.is_default() or role.managed:
            raise UserError("I can't change @everyone or an integration-managed role with this command.")
        if role >= ctx.guild.me.top_role:
            raise UserError(f"{role.mention} must be below my highest role.")
        if ctx.author.id != ctx.guild.owner_id and role >= ctx.author.top_role:
            raise UserError(f"{role.mention} must be below your highest role.")

        actor_perms = ctx.author.guild_permissions
        bot_perms = ctx.guild.me.guild_permissions
        actor_missing = [p for p in requested if not (actor_perms.administrator or getattr(actor_perms, p))]
        bot_missing = [p for p in requested if not (bot_perms.administrator or getattr(bot_perms, p))]
        if actor_missing:
            raise UserError("You need the actual Discord permissions you are granting: "
                            + ", ".join(f"`{p}`" for p in actor_missing))
        if bot_missing:
            raise UserError("I need the actual Discord permissions you are granting: "
                            + ", ".join(f"`{p}`" for p in bot_missing))

        updated = discord.Permissions(role.permissions.value)
        added = [p for p in requested if not getattr(updated, p)]
        if not added:
            return await ctx.neutral(f"{role.mention} already has those Discord permissions.")
        for permission in added:
            setattr(updated, permission, True)
        granted = discord.Permissions(updated.value & ~role.permissions.value)
        await antinuke_guard(ctx, "permissions" if is_dangerous(granted) else "roleupdate")
        try:
            await role.edit(permissions=updated, reason=audit_reason(ctx.author, action="role permission add"))
        except discord.Forbidden:
            raise UserError("Discord rejected the role edit. Check my permissions and role position.")
        await ctx.approve(f"added Discord permissions to {role.mention}: "
                          + ", ".join(f"`{p}`" for p in added))

    # ================================================================ permissions across all roles
    def _eligible_roles(self, ctx: Context):
        owner = ctx.author.id == ctx.guild.owner_id
        return [r for r in ctx.guild.roles if not r.is_default() and not r.managed
                and r < ctx.guild.me.top_role and (owner or r < ctx.author.top_role)]

    async def _remove_permission_across_roles(self, ctx: Context, permission: str, skip=None):
        """Preview and confirm removal; recheck authority, hierarchy and the exact role state before editing."""
        actor = ctx.author.guild_permissions
        bot = ctx.guild.me.guild_permissions
        if not actor.administrator:
            raise UserError("Bulk removal requires your actual Discord Administrator permission.")
        if not (bot.administrator or bot.manage_roles):
            raise UserError("I need the actual Discord Manage Roles permission.")
        lock = self._bulk_permission_locks.setdefault(ctx.guild.id, asyncio.Lock())
        async with lock:
            targets = [r for r in self._eligible_roles(ctx)
                       if getattr(r.permissions, permission) and (skip is None or r.id != skip.id)]
            if not targets:
                ctx.command_log_outcome = "No eligible roles needed changes"
                return await ctx.neutral(f"no eligible roles have `{permission}`; nothing changed")
            snapshot = {r.id: r.permissions.value for r in targets}
            lines = [f"{r.mention} (`{r.id}`)" for r in targets]
            await ctx.paginate(chunk_embeds(f"Roles affected · remove {permission}", lines, per_page=15, numbered=False))
            confirmed = await ctx.confirm(f"Remove `{permission}` from **{len(targets)}** role(s) shown in the preview?\n"
                                          "This changes real Discord permissions and cannot be undone automatically.")
            if not confirmed:
                ctx.command_log_outcome = "Cancelled · no role permissions changed"
                return await ctx.neutral("cancelled; no role permissions were changed")
            if not ctx.author.guild_permissions.administrator or not (ctx.guild.me.guild_permissions.administrator
                                                                     or ctx.guild.me.guild_permissions.manage_roles):
                raise UserError("Permissions changed during confirmation; nothing was changed. Try again.")
            current = {r.id: r for r in self._eligible_roles(ctx)}
            if any(rid not in current or current[rid].permissions.value != value for rid, value in snapshot.items()):
                raise UserError("The role list, hierarchy or permissions changed during confirmation; nothing was changed. Try again.")
            changed, failed = [], []
            for rid, value in snapshot.items():
                # Re-check each role immediately before its edit; earlier API calls may have taken time.
                eligible = {r.id: r for r in self._eligible_roles(ctx)}
                role = eligible.get(rid)
                if (role is None or role.permissions.value != value or not ctx.author.guild_permissions.administrator
                        or not (ctx.guild.me.guild_permissions.administrator or ctx.guild.me.guild_permissions.manage_roles)):
                    failed.append(current[rid].name)
                    continue
                updated = discord.Permissions(role.permissions.value)
                setattr(updated, permission, False)
                try:
                    await role.edit(permissions=updated, reason=audit_reason(ctx.author, action="bulk role permission removal"))
                    changed.append(role.name)
                except (discord.HTTPException, aiohttp.ClientError, asyncio.TimeoutError):
                    failed.append(role.name)
            ctx.command_log_outcome = f"Updated {len(changed)} role(s) · {len(failed)} not confirmed"
            ctx.command_log_warning = bool(failed)
            text = (f"removed `{permission}` from **{len(changed)}** role(s)"
                    + (f"\n-# {shorten(', '.join(changed), 900)}" if changed else ""))
            if failed:
                text += (f"\n⚠️ Updates not confirmed for **{len(failed)}** role(s): "
                         f"{shorten(', '.join(failed), 700)}. Check their current permissions before retrying.")
                return await ctx.warn(text)
            await ctx.approve(text)

    @commands.command()
    @has_perms(administrator=True)
    @commands.bot_has_guild_permissions(manage_roles=True)
    @commands.max_concurrency(1, commands.BucketType.guild)
    async def removepermall(self, ctx: Context, *, permission: str):
        """Remove one Discord permission from eligible roles after confirmation

        Example: `,removepermall administrator`
        """
        parsed, unknown = parse_real_role_permissions(permission)
        if unknown or len(parsed) != 1:
            raise UserError("Specify one valid Discord permission; use `rpa help` for the list.")
        await self._remove_permission_across_roles(ctx, parsed[0])

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
            raise UserError(f"`{perm}` is too dangerous to grant to every role at once, that looks exactly like a nuke. "
                            f"Grant it per role, or use fake permissions (`fp grant`) instead")
        changed = []
        for r in self._eligible_roles(ctx):
            if not getattr(r.permissions, perm):
                new = discord.Permissions(**{**dict(r.permissions), perm: True})
                try:
                    await r.edit(permissions=new, reason=audit_reason(ctx.author, action="addpermall"))
                    changed.append(r.name)
                except discord.HTTPException:
                    pass
        if not changed:
            raise UserError(f"every role below me already had `{perm}`")
        await ctx.approve(f"added `{perm}` to **{len(changed)}** role{'' if len(changed) == 1 else 's'}\n"
                          f"-# {shorten(', '.join(changed), 900)}")

    # ================================================================ nicknames
    @commands.command(aliases=["nick"])
    @requires_uperm("moderator")
    @has_perms(manage_nicknames=True)
    async def nickname(self, ctx: Context, member: discord.Member, *, nickname: str = None):
        """Change (or reset) a member's nickname"""
        if member != ctx.author:
            await check_hierarchy_async(ctx, member, "rename")
        await member.edit(nick=nickname)
        await ctx.approve(f"changed {member.mention}'s nickname to **{discord.utils.escape_markdown(nickname)}**" if nickname
                          else f"reset {member.mention}'s nickname")

    @commands.command(aliases=["fn", "forcenick"])
    @requires_uperm("moderator")
    @has_perms(manage_nicknames=True)
    async def forcenickname(self, ctx: Context, member: discord.Member, *, nickname: str = None):
        """Lock a member's nickname (omit nickname to unlock)"""
        await check_hierarchy_async(ctx, member, "rename")
        fn = await self._cfg(ctx.guild.id, "forcenick")
        if nickname:
            fn[str(member.id)] = nickname
            await member.edit(nick=nickname)
            msg = f"locked {member.mention}'s nickname to **{discord.utils.escape_markdown(nickname)}**"
        else:
            fn.pop(str(member.id), None)
            await member.edit(nick=None)
            msg = f"unlocked {member.mention}'s nickname"
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

        # Exceptions are scoped to one holding; a removed role no longer has an exception.
        if before.roles != after.roles:
            observed_at = discord.utils.utcnow()
            added = [r for r in after.roles if r not in before.roles]
            removed = [r for r in before.roles if r not in after.roles]
            async with self._exception_lock(after.guild.id):
                if removed:
                    await self._change_role_exceptions(after.guild.id, after.id, [r.id for r in removed], add=False)
                if added:
                    cfg = await self.bot.db.get_config(after.guild.id, "roleblacklist")
                    exceptions = await self._role_exceptions(after.guild.id)
                    denied = self._unauthorized_bled_roles(after.guild, after, cfg, exceptions, added)
                    if denied:
                        auditable = [r for r in denied if r.id not in cfg.get("hard_roles", [])]
                        verified = await self._owner_audit_role_additions(after, auditable, observed_at) if auditable else {}
                        if verified:
                            try:
                                await self._record_owner_audit_exceptions(after, verified)
                                exceptions = await self._role_exceptions(after.guild.id)
                                denied = self._unauthorized_bled_roles(after.guild, after, cfg, exceptions, added)
                            except Exception:
                                logging.getLogger("veil").exception("Could not save role-specific owner exception for guild %s", after.guild.id)
                    top_pos = getattr(getattr(after.guild.me, "top_role", None), "position", 999999)
                    to_strip = [r for r in denied if getattr(r, "position", 0) < top_pos]
                    if to_strip and after.guild.me.guild_permissions.manage_roles:
                        try:
                            await after.remove_roles(*to_strip, reason="Veil: Role is blacklisted")
                        except discord.HTTPException:
                            pass

    # ================================================================ jail
    @commands.group(invoke_without_command=True)
    @requires_uperm("moderator")
    @has_perms(moderate_members=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def jail(self, ctx: Context, member: discord.Member, *, reason: str = None):
        """Jail a member: strips their roles and confines them to #jail"""
        from core import perms as _perms
        if _perms.is_bot_developer(member, self.bot):
            raise UserError("you can't jail a bot developer")
        cfg = await self._cfg(ctx.guild.id, "jail")
        role = ctx.guild.get_role(cfg.get("role", 0))
        if not role:
            raise UserError(f"jail isn't set up, run `{ctx.clean_prefix}jail setup`")
        protected = await self.bot.db.get_config(ctx.guild.id, "roleblacklist")
        if role.id in protected.get("hard_roles", []) and role not in member.roles:
            raise UserError("The jail role is hard-blacklisted; change the jail role before using jail")
        await check_hierarchy_async(ctx, member, "jail")
        if str(member.id) in cfg.get("jailed", {}):
            raise UserError(f"{member.mention} is already jailed")
        keep = [r for r in member.roles if r.managed or r.is_default() or r >= ctx.guild.me.top_role]
        removed = [r.id for r in member.roles if r not in keep]
        await member.edit(roles=keep + [role], reason=audit_reason(ctx.author, reason, "jailed"))
        cfg.setdefault("jailed", {})[str(member.id)] = removed
        await self.bot.db.set_config(ctx.guild.id, "jail", cfg)
        num = await create_case(self.bot, ctx.guild, "jail", member, ctx.author, reason)
        await ctx.approve(f"jailed {member.mention} · case #{num}")
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
        msg = await ctx.neutral("setting up jail…")
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
                                           description=f"set up jail: {role.mention} in {channel.mention}"))

    @jail.command(name="list")
    @requires_uperm("moderator")
    @has_perms(moderate_members=True)
    async def jail_list(self, ctx: Context):
        """List jailed members"""
        cfg = await self._cfg(ctx.guild.id, "jail")
        jailed = cfg.get("jailed", {})
        if not jailed:
            return await ctx.warn("nobody is jailed")
        await ctx.paginate(chunk_embeds(f"Jailed ({len(jailed)})", [f"<@{u}>" for u in jailed]))

    @commands.command()
    @requires_uperm("moderator")
    @has_perms(moderate_members=True)
    async def unjail(self, ctx: Context, member: discord.Member, *, reason: str = None):
        """Release a member and give back their roles"""
        check_target(ctx, member, "unjail")
        cfg = await self._cfg(ctx.guild.id, "jail")
        saved = cfg.get("jailed", {}).pop(str(member.id), None)
        if saved is None:
            raise UserError(f"{member.mention} isn't jailed")
        rbl = await self.bot.db.get_config(ctx.guild.id, "roleblacklist")
        b_roles = role_protection_ids(rbl)
        roles = [r for r in (ctx.guild.get_role(i) for i in saved) if r and r < ctx.guild.me.top_role and r.id not in b_roles]
        jail_role = ctx.guild.get_role(cfg.get("role", 0))
        new = [r for r in member.roles if r != jail_role] + roles
        await member.edit(roles=list(dict.fromkeys(new)), reason=audit_reason(ctx.author, reason, "unjailed"))
        await self.bot.db.set_config(ctx.guild.id, "jail", cfg)
        await create_case(self.bot, ctx.guild, "unjail", member, ctx.author, reason)
        await ctx.approve(f"released {member.mention} from jail")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        # re-apply jail if someone rejoins to dodge it
        cfg = await self._cfg(member.guild.id, "jail")
        rbl = await self._cfg(member.guild.id, "roleblacklist")
        if str(member.id) in cfg.get("jailed", {}):
            role = member.guild.get_role(cfg.get("role", 0))
            if role and role.id not in rbl.get("hard_roles", []):
                try:
                    await member.add_roles(role, reason="rejoined while jailed")
                except discord.HTTPException:
                    pass

        # Drop stale exceptions for roles no longer held when a member returns.
        async with self._exception_lock(member.guild.id):
            exceptions = await self._role_exceptions(member.guild.id)
            held_ids = {r.id for r in getattr(member, "roles", [])}
            missing = [rid for rid in exceptions.get(str(member.id), []) if rid not in held_ids]
            if missing:
                await self._change_role_exceptions(member.guild.id, member.id, missing, add=False)

        # strip any blacklisted roles if member rejoins or has them
        rbl = await self._cfg(member.guild.id, "roleblacklist")
        exceptions = await self._role_exceptions(member.guild.id)
        denied = self._unauthorized_bled_roles(member.guild, member, rbl, exceptions, getattr(member, "roles", []))
        top_pos = getattr(getattr(member.guild.me, "top_role", None), "position", 999999)
        if denied and member.guild.me.guild_permissions.manage_roles:
            to_remove = [r for r in denied if getattr(r, "position", 0) < top_pos]
            if to_remove:
                try:
                    await member.remove_roles(*to_remove, reason="Veil: Role is blacklisted")
                except discord.HTTPException:
                    pass

        # re-apply quarantine if member rejoins
        q_cfg = await self._cfg(member.guild.id, "quarantine")
        if str(member.id) in q_cfg.get("quarantined", {}):
            q_role = member.guild.get_role(q_cfg.get("role", 0))
            if q_role and q_role.id not in rbl.get("hard_roles", []) and member.guild.me.guild_permissions.manage_roles:
                try:
                    await member.add_roles(q_role, reason="rejoined while quarantined")
                except discord.HTTPException:
                    pass

    # ================================================================ quarantine
    @commands.command(aliases=["isolate", "freeze"])
    @requires_uperm("moderator")
    @has_perms(moderate_members=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def quarantine(self, ctx: Context, member: discord.Member, *, reason: str = "Quarantined by staff"):
        """Isolate a member and take their roles

        Use it on a suspected raider. `unquarantine` gives the roles back.
        """
        from core import perms as _perms
        if _perms.is_bot_developer(member, self.bot):
            raise UserError("you can't quarantine a bot developer")
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

        rbl = await self.bot.db.get_config(ctx.guild.id, "roleblacklist")
        if q_role and q_role.id in rbl.get("hard_roles", []) and q_role not in member.roles:
            raise UserError("The quarantine role is hard-blacklisted; change the quarantine role first")
        new_roles = keep + ([q_role] if q_role else [])
        await member.edit(roles=new_roles, reason=audit_reason(ctx.author, reason, "Quarantined"))
        cfg.setdefault("quarantined", {})[str(member.id)] = removed
        if q_role:
            cfg["role"] = q_role.id
        await self.bot.db.set_config(ctx.guild.id, "quarantine", cfg)
        num = await create_case(self.bot, ctx.guild, "quarantine", member, ctx.author, reason)
        await ctx.approve(f"quarantined {member.mention} · case #{num} · stripped **{len(removed)}** role{'' if len(removed) == 1 else 's'}")

    @commands.command(aliases=["unfreeze", "release"])
    @requires_uperm("moderator")
    @has_perms(moderate_members=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def unquarantine(self, ctx: Context, member: discord.Member, *, reason: str = "Released by staff"):
        """Release a member from quarantine

        Gives back their roles, except blacklisted ones.
        """
        check_target(ctx, member, "unquarantine")
        cfg = await self._cfg(ctx.guild.id, "quarantine")
        saved = cfg.get("quarantined", {}).pop(str(member.id), None)
        if saved is None:
            raise UserError(f"{member.mention} is not currently quarantined")

        b_cfg = await self.bot.db.get_config(ctx.guild.id, "roleblacklist")
        b_roles = set(b_cfg.get("roles", []))
        protected = role_protection_ids(b_cfg)

        restore_roles = [
            r for r in (ctx.guild.get_role(i) for i in saved)
            if r and r < ctx.guild.me.top_role and r.id not in protected
        ]
        q_role = ctx.guild.get_role(cfg.get("role", 0))
        cleaned = [r for r in member.roles if r != q_role and r.id not in b_roles] + restore_roles
        await member.edit(roles=list(dict.fromkeys(cleaned)), reason=audit_reason(ctx.author, reason, "Unquarantined"))
        await self.bot.db.set_config(ctx.guild.id, "quarantine", cfg)
        await create_case(self.bot, ctx.guild, "unquarantine", member, ctx.author, reason)
        await ctx.approve(f"released {member.mention} from quarantine · restored **{len(restore_roles)}** role{'' if len(restore_roles) == 1 else 's'}")

    # ================================================================ security scan
    @commands.command(aliases=["secscan", "auditsec", "serveraudit"])
    @has_perms(manage_guild=True)
    @is_server_owner()
    async def security(self, ctx: Context):
        """Run a comprehensive security audit of this Discord server"""
        msg = await ctx.neutral("scanning the server…")

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
        from cogs.antinuke import DEFAULT as ANTINUKE_DEFAULT
        an_cfg = await self.bot.db.get_config(ctx.guild.id, "antinuke", ANTINUKE_DEFAULT)
        if an_cfg.get("enabled", False):
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
            exceptions = await self._role_exceptions(ctx.guild.id)
            unauth_holders = [
                m for m in ctx.guild.members
                if self._unauthorized_bled_roles(ctx.guild, m, rbl_cfg, exceptions, getattr(m, "roles", []))
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
        am_cfg = await self.bot.db.get_config(ctx.guild.id, "filters",
                                              {"exempt_roles": [], "exempt_channels": [], "action": "delete", "timeout": 60})
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
