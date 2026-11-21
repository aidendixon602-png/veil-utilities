"""Per-user permission levels: give trusted members staff powers through the bot without dangerous Discord perms."""
from __future__ import annotations

import copy
import logging

import discord
from discord.ext import commands

from core import perms
from core.help import NOTE, chip, section, script
from core.utils import (Colors, Context, MissingRank, UserError, chunk_embeds, has_perms, human_join,
                        is_server_owner, missing_perms, requires_uperm, role_protection_ids)

log = logging.getLogger("veil.permissions")

_CLEAN_PERMS: dict[str, str] = {
    "manage_guild": "manage server",
    "moderate_members": "timeout members",
    "manage_emojis": "manage expressions",
    "manage_emojis_and_stickers": "manage expressions",
    "external_emojis": "use external emojis",
    "veil_warn": "warn",
    "veil_timeout": "timeout",
    "veil_cases": "view cases",
}

_ACTION_ALIASES: dict[str, str] = {
    "warn": "veil_warn",
    "veil_warn": "veil_warn",
    "timeout": "veil_timeout",
    "veil_timeout": "veil_timeout",
    "cases": "veil_cases",
    "case": "veil_cases",
    "view_cases": "veil_cases",
    "view cases": "veil_cases",
    "veil_cases": "veil_cases",
}


def clean_perm(name: str) -> str:
    """Clean, underscore-free permission name for display chips."""
    return _CLEAN_PERMS.get(name.lower(), name.lower().replace("_", " "))


def clean_key(key: str) -> str:
    """Clean, single-word level key for display chips (`bot_manager` -> `botmanager`)."""
    return key.replace("_", "")


def can_view_assignments():
    """Admin-level staff can read assignments; preserve existing bot Administrator grants."""
    async def predicate(ctx: Context) -> bool:
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        if not await missing_perms(ctx, administrator=True):
            return True
        cfg = await ctx.bot.db.get_config(ctx.guild.id, "permlevels")
        rank = perms.rank_of(cfg, ctx.author, has_admin=False)
        if rank <= 0 or rank < perms.all_levels(cfg)["admin"]["rank"]:
            raise MissingRank("Admin")
        return True

    def decorator(func):
        func = commands.check(predicate)(func)
        func.__veil_perms__ = [*getattr(func, "__veil_perms__", []), "uperm: admin"]
        return func
    return decorator


def assignment_pages(title: str, members: list[tuple[str, str]], roles: list[tuple[str, str]], levels: dict) -> list[discord.Embed]:
    """Compact, paginated direct assignments in the Veil house card style; no claims about effective access."""
    entries = [("Role", f"<@&{rid}>", key) for rid, key in roles]
    entries += [("Member", f"<@{uid}>", key) for uid, key in members]
    summary = f"{len(members)} {'member' if len(members) == 1 else 'members'} · {len(roles)} {'role' if len(roles) == 1 else 'roles'}"
    note = ("> Direct Veil assignments in this server.\n"
            f"{NOTE} Direct Veil assignments only. Automatic access and fake permissions are not shown.")
    if not entries:
        page = discord.Embed(title=title, color=Colors.main,
                             description=f"{note}\n\nNo direct assignments yet.\n{NOTE} Assign a level with `,perm add <member|role> <level>`")
        page.set_footer(text=summary)
        return [page]
    pages = []
    for start in range(0, len(entries), 8):
        lines = [f"`{i:02}` {mention} — **{levels.get(key, {}).get('name', key)}** · {kind.lower()} (`{clean_key(key)}`)"
                 for i, (kind, mention, key) in enumerate(entries[start:start + 8], start + 1)]
        page = discord.Embed(title=title, color=Colors.main, description=f"{note}\n\n" + "\n".join(lines))
        page.set_footer(text=summary)
        pages.append(page)
    return pages


class Permissions(commands.Cog):
    """Assign named permission levels to members"""
    emoji = "🔑"

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        """Move every server's saved levels to the current names (Mod -> Moderator, ...)."""
        db = getattr(self.bot, "db", None)
        guilds_with = getattr(db, "guilds_with", None)
        if guilds_with is None:
            return
        try:
            for gid, data in await guilds_with("permlevels"):
                updated = copy.deepcopy(await db.get_config(gid, "permlevels"))
                if perms.migrate(updated):
                    await db.set_config_committed(gid, "permlevels", updated)
        except Exception:  # keep startup available; reads retry the migration
            log.exception("Permission level migration failed; it will be retried on access")

    async def cfg(self, gid):
        cfg = copy.deepcopy(await self.bot.db.get_config(gid, "permlevels", {"users": {}, "overrides": {}}))
        if perms.migrate(cfg):
            await self.save(gid, cfg)
        return cfg

    async def save(self, gid, cfg):
        await self.bot.db.set_config_committed(gid, "permlevels", cfg)

    async def actor_rank(self, ctx: Context) -> int:
        cfg = await self.cfg(ctx.guild.id)
        return perms.rank_of(cfg, ctx.author, is_owner=ctx.author.id == ctx.guild.owner_id,
                             is_dev=perms.is_bot_developer(ctx.author, self.bot) or await self.bot.is_owner(ctx.author),
                             has_admin=False)

    # ============================================================ userperm
    @commands.group(aliases=["perm", "perms", "uperm", "ups", "permission", "permissions"], case_insensitive=True, invoke_without_command=True)
    @commands.guild_only()
    async def userperm(self, ctx: Context, *, target: str = None):
        """Manage members' and roles' permission levels

        Give a member or role to see their level, or nothing to list the levels.
        """
        if target:
            clean = target.strip()
            words = clean.split(maxsplit=1)
            if words[0].lower() in ("check", "view", "info"):
                clean = words[1] if len(words) > 1 else None
            if clean:
                obj = None
                try:
                    obj = await commands.MemberConverter().convert(ctx, clean)
                except Exception:
                    try:
                        obj = await commands.RoleConverter().convert(ctx, clean)
                    except Exception:
                        pass
                if obj:
                    return await self.up_check(ctx, obj)
                raise UserError(f"Could not find member or role `{clean}`.")
            return await self.up_check(ctx, ctx.author)
        await self.permlevels(ctx)

    @userperm.command(name="add", aliases=["set", "give"])
    @requires_uperm("headadmin")
    @has_perms(administrator=True)
    async def up_add(self, ctx: Context, target: discord.Member | discord.Role, *, level: str):
        """Assign a permission level to a member or a role"""
        cfg = await self.cfg(ctx.guild.id)
        key = perms.resolve_key(cfg, level)
        if not key:
            reserved = perms.is_reserved(level)
            if reserved:
                raise UserError(f"**{reserved}** is automatic — it can't be assigned with this command")
            opts = human_join(["`" + d["name"] + "`" for d in perms.all_levels(cfg).values()], "or")
            raise UserError(f"Unknown level. Options: {opts}")
        info = perms.all_levels(cfg)[key]
        actor = await self.actor_rank(ctx)

        if isinstance(target, discord.Member):
            if target.id == ctx.author.id:
                raise UserError("You can't assign a permission level to yourself")
            if target.id == ctx.guild.owner_id:
                raise UserError("The server owner's permissions cannot be changed")
            if target.bot:
                raise UserError("Bots can't have permission levels")
            if perms.is_bot_developer(target, self.bot):
                raise UserError(f"{target.mention} is a **{perms.DEV_NAME}**: that level is automatic and above every other.")
            target_current = perms.rank_of(cfg, target, is_owner=target.id == ctx.guild.owner_id,
                                           is_dev=perms.is_bot_developer(target, self.bot) or await self.bot.is_owner(target),
                                           has_admin=False)
            if ctx.author.id != ctx.guild.owner_id and not (perms.is_bot_developer(ctx.author, self.bot) or await self.bot.is_owner(ctx.author)):
                if info["rank"] >= actor:
                    raise UserError("You can't assign a level equal to or above your own")
                if target_current >= actor:
                    raise UserError(f"You can't change {target.mention}, they rank at or above you")
            cfg.setdefault("users", {})[str(target.id)] = key
            await self.save(ctx.guild.id, cfg)
            await ctx.approve(f"set {target.mention}'s permission level to **{info['name']}**")
        else:
            if target.is_default():
                raise UserError("You can't assign a permission level to @everyone")
            if target.managed:
                raise UserError("You can't assign a permission level to an integrated bot role")
            is_dev = perms.is_bot_developer(ctx.author, self.bot) or await self.bot.is_owner(ctx.author)
            privileged = ctx.author.id == ctx.guild.owner_id or is_dev
            if not privileged and any(role.id == target.id for role in getattr(ctx.author, "roles", [])):
                raise UserError("You can't assign a permission level to a role you hold")
            rbl = copy.deepcopy(await self.bot.db.get_config(ctx.guild.id, "roleblacklist"))
            if target.id in role_protection_ids(rbl):
                if not privileged or (target.id in rbl.get("hard_roles", []) and not is_dev):
                    raise UserError(f"{target.mention} is blacklisted and cannot be assigned a permission level by you")
                confirmed = await ctx.confirm(
                    f"{target.mention} is blacklisted. Assigning **{info['name']}** can give that Veil level "
                    "to current and future role holders, subject to assignment precedence. "
                    "The role blacklist and its enforcement remain unchanged. "
                    "Continue?")
                if not confirmed:
                    ctx.command_log_outcome = "Permission assignment cancelled · nothing changed"
                    return await ctx.neutral("cancelled; no permission level was changed")
                # Confirmation grants access to everyone holding the role, so reject stale policy or role changes.
                current_role = ctx.guild.get_role(target.id)
                current_rbl = await self.bot.db.get_config(ctx.guild.id, "roleblacklist")
                current_cfg = await self.cfg(ctx.guild.id)
                still_dev = perms.is_bot_developer(ctx.author, self.bot) or await self.bot.is_owner(ctx.author)
                if (current_role is None or current_role.is_default() or current_role.managed
                        or (ctx.author.id != ctx.guild.owner_id and not still_dev)
                        or (target.id in current_rbl.get("hard_roles", []) and not still_dev)
                        or current_rbl != rbl or current_cfg != cfg):
                    raise UserError("The role, permissions or blacklist changed during confirmation; nothing was changed. Try again.")
            if not privileged:
                if info["rank"] >= actor:
                    raise UserError("You can't assign a level equal to or above your own")
                if target >= ctx.author.top_role:
                    raise UserError(f"You can't assign a level to {target.mention}, it ranks at or above your highest role")
                old_key = perms.role_key(cfg, target.id)
                old_rank = perms.all_levels(cfg).get(old_key, {}).get("rank", 0)
                if old_rank >= actor:
                    raise UserError("You can't change a role with a permission level equal to or above your own")
            cfg.setdefault("roles", {})[str(target.id)] = key
            await self.save(ctx.guild.id, cfg)
            await ctx.approve(f"set {target.mention}'s permission level to **{info['name']}**")

    @userperm.group(name="remove", aliases=["rem", "delete", "unset", "clear"], case_insensitive=True, invoke_without_command=True)
    @requires_uperm("headadmin")
    @has_perms(administrator=True)
    async def up_remove(self, ctx: Context, target: discord.Member | discord.Role):
        """Remove a member's or role's permission level"""
        cfg = await self.cfg(ctx.guild.id)
        actor = await self.actor_rank(ctx)
        if isinstance(target, discord.Member):
            if target.id == ctx.guild.owner_id:
                raise UserError("The server owner's permissions cannot be changed")
            users = cfg.get("users", {})
            if str(target.id) not in users:
                roles_cfg = cfg.get("roles", {})
                member_perm_roles = [r for r in getattr(target, "roles", []) if str(r.id) in roles_cfg]
                if member_perm_roles:
                    role_names = ", ".join(r.name for r in member_perm_roles)
                    raise UserError(f"{target.mention} has no direct user assignment, but has role(s) with permissions: `{role_names}`. Run `{ctx.clean_prefix}perm remove @Role` to remove the role's permission level.")
                raise UserError(f"{target.mention} has no direct permission level")
            target_rank = perms.rank_of(cfg, target, is_owner=target.id == ctx.guild.owner_id,
                                        is_dev=perms.is_bot_developer(target, self.bot) or await self.bot.is_owner(target),
                                        has_admin=False)
            if ctx.author.id != ctx.guild.owner_id and not (perms.is_bot_developer(ctx.author, self.bot) or await self.bot.is_owner(ctx.author)) and target_rank >= actor:
                raise UserError(f"You can't change {target.mention}, they rank at or above you")
            users.pop(str(target.id))
            await self.save(ctx.guild.id, cfg)
            await ctx.approve(f"removed {target.mention}'s permission level")
        else:
            roles = cfg.get("roles", {})
            if str(target.id) not in roles:
                raise UserError(f"{target.mention} has no permission level")
            if ctx.author.id != ctx.guild.owner_id and not await self.bot.is_owner(ctx.author):
                if target >= ctx.author.top_role:
                    raise UserError(f"You can't change {target.mention}, it ranks at or above your highest role")
                old_rank = perms.all_levels(cfg).get(perms.role_key(cfg, target.id), {}).get("rank", 0)
                if old_rank >= actor:
                    raise UserError("You can't change a role with a permission level equal to or above your own")
            roles.pop(str(target.id))
            await self.save(ctx.guild.id, cfg)
            await ctx.approve(f"removed {target.mention}'s permission level")

    @up_remove.command(name="deleted")
    @requires_uperm("headadmin")
    @has_perms(administrator=True)
    async def up_remove_deleted(self, ctx: Context):
        """Purge permission levels for members who left the server"""
        cfg = await self.cfg(ctx.guild.id)
        users = cfg.get("users", {})
        gone = [uid for uid in users if not ctx.guild.get_member(int(uid))]
        if not gone:
            return await ctx.warn("no permission levels belong to members who left")
        for uid in gone:
            users.pop(uid)
        await self.save(ctx.guild.id, cfg)
        await ctx.approve(f"removed **{len(gone)}** permission level{'' if len(gone) == 1 else 's'} from members who left")

    @userperm.command(name="list", aliases=["all"])
    @can_view_assignments()
    async def up_list(self, ctx: Context):
        """List members and roles with a permission level"""
        cfg = await self.cfg(ctx.guild.id)
        levels = perms.all_levels(cfg)
        user_rows = sorted(cfg.get("users", {}).items(),
                           key=lambda kv: levels.get(kv[1], {}).get("rank", 0), reverse=True)
        role_rows = sorted(cfg.get("roles", {}).items(),
                           key=lambda kv: levels.get(kv[1], {}).get("rank", 0), reverse=True)
        await ctx.paginate(assignment_pages("Permission assignments", user_rows, role_rows, levels))

    @userperm.command(name="roles")
    @can_view_assignments()
    async def up_roles(self, ctx: Context):
        """List roles assigned a Veil permission level"""
        cfg = await self.cfg(ctx.guild.id)
        levels = perms.all_levels(cfg)
        role_rows = sorted(cfg.get("roles", {}).items(),
                           key=lambda kv: levels.get(kv[1], {}).get("rank", 0), reverse=True)
        await ctx.paginate(assignment_pages("Role permissions", [], role_rows, levels))

    @userperm.command(name="check", aliases=["view", "info"])
    @commands.guild_only()
    async def up_check(self, ctx: Context, target: discord.Member | discord.Role = None):
        """Check a member's or role's permission level"""
        target = target or ctx.author
        cfg = await self.cfg(ctx.guild.id)
        levels = perms.all_levels(cfg)
        db = getattr(self.bot, "db", None)
        fp = (await db.get_config(ctx.guild.id, "fakeperms")) if db and hasattr(db, "get_config") else {}
        if not isinstance(fp, dict):
            fp = {}

        if isinstance(target, discord.Role):
            rkey = perms.canonical(
                perms.CODE_ROLES.get(target.id)
                or perms.CODE_ROLES.get(str(target.id))
                or perms.CODE_ROLES.get(target.name)
                or perms.CODE_ROLES.get(target.name.lower())
                or (cfg.get("roles") or {}).get(str(target.id))
            )
            rname = levels[rkey]["name"] if (rkey and rkey in levels) else None
            role_fp = [p for p in (fp.get(str(target.id)) or []) if p]
            fp_line = f"\n{NOTE} Fake permissions: {' · '.join(chip(clean_perm(p)) for p in role_fp)}" if role_fp else ""
            if rname:
                return await ctx.neutral(f"{target.mention} has the **{rname}** (`{clean_key(rkey)}`) permission level{fp_line}")
            return await ctx.neutral(f"{target.mention} has no permission level{fp_line}")

        if target != ctx.author:
            actor = await self.actor_rank(ctx)
            if actor < 1:
                raise UserError("You can only check your own permission level")

        name, source, rank = perms.member_level_info(
            cfg,
            target,
            is_owner=target.id == ctx.guild.owner_id,
            is_dev=perms.is_bot_developer(target, self.bot) or await self.bot.is_owner(target),
        )
        member_fp = sorted({p for r in getattr(target, "roles", []) for p in (fp.get(str(r.id)) or []) if p})
        fp_line = f"\n{NOTE} Fake permissions: {' · '.join(chip(clean_perm(p)) for p in member_fp)}" if member_fp else ""
        if name:
            lkey = next((k for k, v in levels.items() if v["name"] == name), None)
            label = f"**{name}** (`{clean_key(lkey)}`)" if lkey else f"**{name}**"
            src_line = f"\n{NOTE} Source: {source}" if source else ""
            return await ctx.neutral(f"{target.mention} has the {label} permission level{src_line}{fp_line}")
        await ctx.neutral(f"{target.mention} has no permission level{fp_line}")

    # ============================================================ levels
    @commands.group(aliases=["plevels", "plvl"], case_insensitive=True, invoke_without_command=True)
    @has_perms(manage_messages=True)
    @commands.guild_only()
    async def permlevels(self, ctx: Context):
        """View the permission levels available in this server"""
        cfg = await self.cfg(ctx.guild.id)
        levels = sorted(perms.all_levels(cfg).items(), key=lambda kv: kv[1]["rank"], reverse=True)
        user_counts: dict[str, int] = {}
        for uid, key in list(cfg.get("users", {}).items()):
            key = perms.canonical(key)
            user_counts[key] = user_counts.get(key, 0) + 1
        for uid, key in perms.CODE_USERS.items():
            if str(uid) not in cfg.get("users", {}):
                key = perms.canonical(key)
                user_counts[key] = user_counts.get(key, 0) + 1

        role_counts: dict[str, int] = {}
        for rid, key in list(cfg.get("roles", {}).items()):
            key = perms.canonical(key)
            role_counts[key] = role_counts.get(key, 0) + 1
        for r_id_or_name, key in perms.CODE_ROLES.items():
            key = perms.canonical(key)
            role_counts[key] = role_counts.get(key, 0) + 1

        preset_incremental: dict[str, tuple[list[str], str]] = {
            "headadmin": (["manage_guild", "manage_webhooks", "manage_expressions", "mention_everyone"], "Admin"),
            "admin": (["ban_members", "manage_roles", "manage_channels"], "Moderator"),
            "moderator": (["manage_messages", "kick_members", "moderate_members", "manage_nicknames"], "Helper"),
        }
        action_labels = {
            "veil_warn": "`warn`",
            "veil_timeout": "`timeout` (1h max)",
            "veil_cases": "`view cases`",
        }

        def grants(key: str, info: dict) -> str:
            if "administrator" in info["perms"]:
                return "`administrator` · all standard bot permissions (not Antinuke or developer access)"
            if set(info["perms"]) >= {p for p in perms.ALL_BOT_PERMS if p != "administrator"}:
                if key == "bot_manager":
                    return "senior staff · everything except Administrator"
                return "permissions & audit log · everything except Administrator"
            if key in preset_incremental and info["perms"] == list(perms.PRESETS[key][2]):
                added, base_name = preset_incremental[key]
                return f"{' · '.join(chip(clean_perm(p)) for p in added)} + {base_name}"
            items = [chip(clean_perm(p)) for p in info["perms"]]
            if not info["perms"] or "moderate_members" not in info["perms"]:
                items.extend(action_labels.get(a, chip(clean_perm(a))) for a in info.get("actions", []))
            return " · ".join(items) or "no permissions"

        def holders(key: str) -> str:
            ucnt, rcnt = user_counts.get(key, 0), role_counts.get(key, 0)
            parts = [f"{ucnt} member{'s' if ucnt != 1 else ''}"] if ucnt else []
            if rcnt:
                parts.append(f"{rcnt} role{'s' if rcnt != 1 else ''}")
            return f"\n{NOTE} {' · '.join(parts)}" if parts else ""

        rows = [(perms.DEV_NAME, "automatic · the bot's developers"),
                (perms.OWNER_NAME, "automatic · whoever owns the server")]
        rows += [(f"{info['name']} (`{clean_key(key)}`)", grants(key, info) + holders(key)) for key, info in levels]
        p = ctx.clean_prefix
        use = script([f"{p}perm add <member|role> <level>", f"{p}fp add <role> <permission>"])
        intro = "\n".join([
            "> Assign staff levels to members or roles, or grant individual permissions with fakeperms.",
            f"{NOTE} Highest to lowest · staff levels do not grant Antinuke or developer access.",
            "",
            section("Use", quiet=True),
            *use,
        ])
        lines = [f"`{i:02}` **{name}**\n{text}" for i, (name, text) in enumerate(rows, 1)]
        pages = chunk_embeds("Permission levels", lines, per_page=9, numbered=False)
        for page in pages:
            page.description = f"{intro}\n\n{page.description}"
            page.set_footer(text=f"Automatic access and saved assignments are separate · {p}fp for role grants")
        await ctx.paginate(pages)

    @commands.command(name="botperms", aliases=["botpermissions", "botperm"])
    @commands.guild_only()
    async def cmd_botperms(self, ctx: Context):
        """View the bot's Discord permissions in this server and channel"""
        me = ctx.guild.me
        gp = me.guild_permissions
        cp = ctx.channel.permissions_for(me)

        groups = [
            ("Server & security", [
                ("administrator", gp.administrator, "Full bypass across channels"),
                ("manage_guild", gp.manage_guild, "Server settings & Antinuke"),
                ("manage_roles", gp.manage_roles, "Role assignments & Antinuke"),
                ("manage_channels", gp.manage_channels, "Channel creation & logging"),
                ("view_audit_log", gp.view_audit_log, "Antinuke & modlog attribution"),
            ]),
            ("Moderation", [
                ("ban_members", gp.ban_members, "Bans & unbans"),
                ("kick_members", gp.kick_members, "Member kicks"),
                ("moderate_members", gp.moderate_members, "Timeouts"),
            ]),
            ("This channel", [
                ("manage_messages", cp.manage_messages, "Message deletion & purge"),
                ("embed_links", cp.embed_links, "Rich embed cards"),
                ("attach_files", cp.attach_files, "Media, images & transcripts"),
                ("mention_everyone", cp.mention_everyone, "Announcements & alerts"),
            ]),
        ]

        all_checks = [item for _, items in groups for item in items]
        missing = [name for name, granted, _ in all_checks if not granted]

        body = [
            "> Veil's effective Discord permissions in this server and channel.",
            f"{NOTE} Top role {me.top_role.mention} · position {me.top_role.position}/{len(ctx.guild.roles)}",
        ]
        if missing and not gp.administrator:
            body.append(f"{NOTE} ! Missing: {' · '.join(chip(clean_perm(p)) for p in missing)}")

        for heading, items in groups:
            body.extend(["", section(heading)])
            for key, granted, desc in items:
                mark = "✓" if granted else "✗"
                body.append(f"`{mark}` {chip(clean_perm(key))} · {desc}")

        embed = discord.Embed(
            title="Bot permissions",
            color=Colors.approve if gp.administrator else (Colors.main if all(c[1] for c in all_checks[1:8]) else Colors.warn),
            description="\n".join(body),
        )
        bot_user = getattr(self.bot, "user", None)
        if bot_user:
            avatar = getattr(getattr(bot_user, "display_avatar", None), "url", None)
            if isinstance(avatar, str) and avatar:
                embed.set_author(name=f"{ getattr(bot_user, 'name', 'Veil Utilities') } · Permissions", icon_url=avatar)
                embed.set_thumbnail(url=avatar)
        embed.set_footer(text=f"Server: {ctx.guild.name} · {ctx.clean_prefix}permlevels for staff levels")
        await ctx.send(embed=embed)

    @staticmethod
    def _level(cfg: dict, text: str) -> str:
        """An assignable level from its key or name (old names too), or a clear error."""
        key = perms.resolve_key(cfg, text)
        if key:
            return key
        reserved = perms.is_reserved(text)
        if reserved:
            raise UserError(f"**{reserved}** is automatic and can't be changed")
        opts = human_join(["`" + d["name"] + "`" for d in perms.all_levels(cfg).values()], "or")
        raise UserError(f"Unknown level. Options: {opts}")

    @permlevels.command(name="rename")
    @is_server_owner()
    async def pl_rename(self, ctx: Context, key: str, *, name: str):
        """Rename a level (server owner)"""
        cfg = await self.cfg(ctx.guild.id)
        key = self._level(cfg, key)
        cfg.setdefault("overrides", {}).setdefault(key, {})["name"] = name[:40]
        await self.save(ctx.guild.id, cfg)
        await ctx.approve(f"renamed `{key}` to **{name[:40]}**")

    @permlevels.command(name="rank")
    @is_server_owner()
    async def pl_rank(self, ctx: Context, key: str, rank: int):
        """Change a level's rank (server owner)"""
        cfg = await self.cfg(ctx.guild.id)
        key = self._level(cfg, key)
        cfg.setdefault("overrides", {}).setdefault(key, {})["rank"] = max(1, min(rank, 899))
        await self.save(ctx.guild.id, cfg)
        await ctx.approve(f"set `{key}` to rank **{max(1, min(rank, 899))}**")

    @permlevels.command(name="grant")
    @is_server_owner()
    async def pl_grant(self, ctx: Context, key: str, permission: str):
        """Toggle a bot-permission a level grants (server owner)"""
        from cogs.settings import VALID_PERMS, parse_perms
        cfg = await self.cfg(ctx.guild.id)
        key = self._level(cfg, key)
        levels = perms.all_levels(cfg)
        parsed, unknown = parse_perms(permission)
        raw = permission.lower().replace(" ", "_")
        target_perm = parsed[0] if (parsed and not unknown and len(parsed) == 1) else raw
        if target_perm not in VALID_PERMS:
            raise UserError(f"`{raw}` isn't a valid permission")
        current = list(levels[key]["perms"])
        if target_perm in current:
            current.remove(target_perm)
            msg = f"`{clean_key(key)}` no longer grants `{target_perm}`"
        else:
            current.append(target_perm)
            msg = f"`{clean_key(key)}` now grants `{target_perm}`"
        cfg.setdefault("overrides", {}).setdefault(key, {})["perms"] = current
        await self.save(ctx.guild.id, cfg)
        await ctx.approve(msg)

    @permlevels.command(name="action")
    @is_server_owner()
    async def pl_action(self, ctx: Context, key: str, action: str):
        """Toggle a narrow moderation action for a level (server owner)

        Actions: warn, timeout, cases. Helper timeouts are capped at one hour.
        """
        cfg = await self.cfg(ctx.guild.id)
        key = self._level(cfg, key)
        canonical_action = _ACTION_ALIASES.get(action.strip().lower())
        if canonical_action not in perms.MODERATION_ACTIONS:
            raise UserError("action must be `warn`, `timeout` or `cases`")
        level = perms.all_levels(cfg)[key]
        if "administrator" in level["perms"]:
            raise UserError("this level has Administrator and bypasses action checks; change that grant first")
        current = list(level["actions"])
        if canonical_action in current:
            current.remove(canonical_action)
            enabled = False
        else:
            current.append(canonical_action)
            enabled = True
        cfg.setdefault("overrides", {}).setdefault(key, {})["actions"] = current
        await self.save(ctx.guild.id, cfg)
        await ctx.approve(f"`{clean_key(key)}` {'now grants' if enabled else 'no longer grants'} `{clean_perm(canonical_action)}`")


async def setup(bot):
    await bot.add_cog(Permissions(bot))
