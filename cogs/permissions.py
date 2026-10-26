"""Per-user permission levels: give trusted members staff powers through the bot without dangerous Discord perms."""
from __future__ import annotations

import discord
from discord.ext import commands

from core import perms
from core.utils import Colors, Context, UserError, chunk_embeds, has_perms, human_join, is_server_owner, requires_uperm


class Permissions(commands.Cog):
    """Assign named permission levels to members"""
    emoji = "🔑"

    def __init__(self, bot):
        self.bot = bot

    async def cfg(self, gid):
        return await self.bot.db.get_config(gid, "permlevels", {"users": {}, "overrides": {}})

    async def save(self, gid, cfg):
        await self.bot.db.set_config(gid, "permlevels", cfg)

    async def actor_rank(self, ctx: Context) -> int:
        cfg = await self.cfg(ctx.guild.id)
        return perms.rank_of(cfg, ctx.author, is_owner=ctx.author.id == ctx.guild.owner_id,
                             is_dev=perms.is_bot_developer(ctx.author, self.bot) or await self.bot.is_owner(ctx.author),
                             has_admin=False)

    # ============================================================ userperm
    @commands.group(aliases=["perm", "perms", "uperm", "ups", "permission", "permissions"], case_insensitive=True, invoke_without_command=True)
    @commands.guild_only()
    async def userperm(self, ctx: Context, *, target: str = None):
        """Manage members' and roles' permission levels. Checks target if given, or shows levels."""
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
            if target.bot:
                raise UserError("Bots can't have permission levels")
            if perms.is_bot_developer(target, self.bot):
                raise UserError(f"{target.mention} is a Bot Developer with built-in supreme permissions.")
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
            rbl = await self.bot.db.get_config(ctx.guild.id, "roleblacklist")
            if target.id in rbl.get("roles", []):
                raise UserError(f"{target.mention} is blacklisted and cannot be assigned a permission level")
            if ctx.author.id != ctx.guild.owner_id and not await self.bot.is_owner(ctx.author):
                if info["rank"] >= actor:
                    raise UserError("You can't assign a level equal to or above your own")
                if target >= ctx.author.top_role:
                    raise UserError(f"You can't assign a level to {target.mention}, it ranks at or above your highest role")
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
            if ctx.author.id != ctx.guild.owner_id and not await self.bot.is_owner(ctx.author) and target >= ctx.author.top_role:
                raise UserError(f"You can't change {target.mention}, it ranks at or above your highest role")
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
    @has_perms(administrator=True)
    async def up_list(self, ctx: Context):
        """List members and roles with a permission level"""
        cfg = await self.cfg(ctx.guild.id)
        levels = perms.all_levels(cfg)
        user_rows = sorted(cfg.get("users", {}).items(),
                           key=lambda kv: levels.get(kv[1], {}).get("rank", 0), reverse=True)
        role_rows = sorted(cfg.get("roles", {}).items(),
                           key=lambda kv: levels.get(kv[1], {}).get("rank", 0), reverse=True)
        lines = []
        if role_rows:
            lines.append("**Roles**")
            for rid, key in role_rows:
                lines.append(f"<@&{rid}> — **{levels.get(key, {}).get('name', key)}**")
            lines.append("")
        if user_rows:
            lines.append("**Members**")
            for uid, key in user_rows:
                lines.append(f"<@{uid}> — **{levels.get(key, {}).get('name', key)}**")
        if not lines:
            lines = ["No members or roles have been assigned a permission level."]
        await ctx.paginate(chunk_embeds("Permission levels", lines, numbered=False))

    @userperm.command(name="check", aliases=["view", "info"])
    @commands.guild_only()
    async def up_check(self, ctx: Context, target: discord.Member | discord.Role = None):
        """Check a member's or role's permission level"""
        target = target or ctx.author
        cfg = await self.cfg(ctx.guild.id)
        levels = perms.all_levels(cfg)

        if isinstance(target, discord.Role):
            rkey = (
                perms.CODE_ROLES.get(target.id)
                or perms.CODE_ROLES.get(str(target.id))
                or perms.CODE_ROLES.get(target.name)
                or perms.CODE_ROLES.get(target.name.lower())
                or (cfg.get("roles") or {}).get(str(target.id))
            )
            rname = levels[rkey]["name"] if (rkey and rkey in levels) else "None"
            return await ctx.neutral(f"{target.mention} has the **{rname}** permission level" if rkey and rkey in levels
                                     else f"{target.mention} has no permission level")

        if target != ctx.author and not ctx.author.guild_permissions.manage_messages:
            actor = await self.actor_rank(ctx)
            if actor < 1:
                raise UserError("You can only check your own permission level")

        name, source, rank = perms.member_level_info(
            cfg,
            target,
            is_owner=target.id == ctx.guild.owner_id,
            is_dev=perms.is_bot_developer(target, self.bot) or await self.bot.is_owner(target),
        )
        display_name = name or "None"
        await ctx.neutral(f"{target.mention} has the **{display_name}** permission level" if name
                          else f"{target.mention} has no permission level")

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
            user_counts[key] = user_counts.get(key, 0) + 1
        for uid, key in perms.CODE_USERS.items():
            if str(uid) not in cfg.get("users", {}):
                user_counts[key] = user_counts.get(key, 0) + 1

        role_counts: dict[str, int] = {}
        for rid, key in list(cfg.get("roles", {}).items()):
            role_counts[key] = role_counts.get(key, 0) + 1
        for r_id_or_name, key in perms.CODE_ROLES.items():
            role_counts[key] = role_counts.get(key, 0) + 1

        lines = [f"Highest to lowest. Assign with `{ctx.clean_prefix}perm add <member|role> <level>`\n"]
        order_map = {name: i for i, name in enumerate(perms.HIERARCHY_ORDER)}
        all_entries = []
        for name, rank in perms.RESERVED.values():
            all_entries.append((order_map.get(name, 0), f"`rank {rank}` **{name}** — automatic (not assignable)"))

        for key, info in levels:
            if "administrator" in info["perms"]:
                grants = "all bot permissions + administrator"
            elif key == "overseer":
                grants = "executive oversight (all staff & voice perms, no administrator)"
            elif key == "botmanager":
                grants = "bot management suite & audit logs"
            else:
                grants = (", ".join(p.replace("_", " ") for p in info["perms"]) or "none")
            ucnt = user_counts.get(key, 0)
            rcnt = role_counts.get(key, 0)
            parts = []
            parts.append(f"**{ucnt}** member{'s' if ucnt != 1 else ''}")
            if rcnt:
                parts.append(f"**{rcnt}** role{'s' if rcnt != 1 else ''}")
            count_str = " • ".join(parts)
            line = f"`rank {info['rank']}` **{info['name']}** (`{key}`) — {grants} • {count_str}"
            all_entries.append((order_map.get(info["name"], 99), line))

        all_entries.sort(key=lambda x: x[0])
        for _, line in all_entries:
            lines.append(line)

        e = discord.Embed(
            title="Permission levels",
            color=Colors.blurple,
            description="\n".join(lines)
        )
        await ctx.send(embed=e)

    @commands.command(name="botperms", aliases=["botpermissions", "botperm"])
    @commands.guild_only()
    async def cmd_botperms(self, ctx: Context):
        """View the bot's Discord permissions in this server and channel"""
        me = ctx.guild.me
        gp = me.guild_permissions
        cp = ctx.channel.permissions_for(me)

        checks = [
            ("Administrator", gp.administrator, "Full bypass across channels"),
            ("Manage Server", gp.manage_guild, "Server settings & antinuke"),
            ("Manage Roles", gp.manage_roles, "Role assignments & antinuke"),
            ("Manage Channels", gp.manage_channels, "Channel creation & logging"),
            ("Kick Members", gp.kick_members, "Moderation kicks"),
            ("Ban Members", gp.ban_members, "Moderation bans & unbans"),
            ("Moderate Members", gp.moderate_members, "Timeouts"),
            ("Manage Messages", cp.manage_messages, "Message deletion & purge"),
            ("Embed Links", cp.embed_links, "Rich embed cards"),
            ("Attach Files", cp.attach_files, "Media, images & transcripts"),
            ("View Audit Log", gp.view_audit_log, "Antinuke & modlog tracking"),
            ("Mention Everyone", cp.mention_everyone, "Announcements & alerts"),
        ]

        lines = []
        for name, granted, desc in checks:
            icon = "✅" if granted else "❌"
            lines.append(f"{icon} **{name}** — {desc}")

        role_hierarchy = f"**Top Role**: {me.top_role.mention} (position {me.top_role.position}/{len(ctx.guild.roles)})"
        embed = discord.Embed(
            title="Bot Permissions",
            color=Colors.approve if gp.administrator else (Colors.main if all(c[1] for c in checks[1:8]) else Colors.warn),
            description=f"{role_hierarchy}\n\n" + "\n".join(lines)
        )
        if self.bot.user:
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.set_footer(text=f"Server: {ctx.guild.name} • {ctx.clean_prefix}permlevels for staff levels")
        await ctx.send(embed=embed)

    @permlevels.command(name="rename")
    @is_server_owner()
    async def pl_rename(self, ctx: Context, key: str, *, name: str):
        """Rename a level (server owner)"""
        cfg = await self.cfg(ctx.guild.id)
        if key.lower() not in perms.all_levels(cfg):
            raise UserError("Unknown level key")
        cfg.setdefault("overrides", {}).setdefault(key.lower(), {})["name"] = name[:40]
        await self.save(ctx.guild.id, cfg)
        await ctx.approve(f"renamed `{key.lower()}` to **{name[:40]}**")

    @permlevels.command(name="rank")
    @is_server_owner()
    async def pl_rank(self, ctx: Context, key: str, rank: int):
        """Change a level's rank (server owner)"""
        cfg = await self.cfg(ctx.guild.id)
        if key.lower() not in perms.all_levels(cfg):
            raise UserError("Unknown level key")
        cfg.setdefault("overrides", {}).setdefault(key.lower(), {})["rank"] = max(1, min(rank, 899))
        await self.save(ctx.guild.id, cfg)
        await ctx.approve(f"set `{key.lower()}` to rank **{max(1, min(rank, 899))}**")

    @permlevels.command(name="grant")
    @is_server_owner()
    async def pl_grant(self, ctx: Context, key: str, permission: str):
        """Toggle a bot-permission a level grants (server owner)"""
        from cogs.settings import VALID_PERMS
        cfg = await self.cfg(ctx.guild.id)
        key = key.lower()
        levels = perms.all_levels(cfg)
        if key not in levels:
            raise UserError("Unknown level key")
        permission = permission.lower().replace(" ", "_")
        if permission not in VALID_PERMS:
            raise UserError(f"`{permission}` isn't a valid permission")
        current = list(levels[key]["perms"])
        if permission in current:
            current.remove(permission)
            msg = f"`{key}` no longer grants `{permission}`"
        else:
            current.append(permission)
            msg = f"`{key}` now grants `{permission}`"
        cfg.setdefault("overrides", {}).setdefault(key, {})["perms"] = current
        await self.save(ctx.guild.id, cfg)
        await ctx.approve(msg)


async def setup(bot):
    await bot.add_cog(Permissions(bot))
