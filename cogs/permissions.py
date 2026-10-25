"""Per-user permission levels: give trusted members staff powers through the bot without dangerous Discord perms."""
from __future__ import annotations

import discord
from discord.ext import commands

from core import perms
from core.utils import Colors, Context, UserError, chunk_embeds, has_perms, human_join, is_server_owner


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
                             has_admin=ctx.author.guild_permissions.administrator)

    # ============================================================ userperm
    @commands.group(aliases=["perm", "perms", "uperm", "ups"], invoke_without_command=True)
    @commands.guild_only()
    async def userperm(self, ctx: Context):
        """Manage members' and roles' permission levels. Shows levels if no subcommand given."""
        await self.permlevels(ctx)

    @userperm.command(name="add", aliases=["set", "give"])
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
                                           has_admin=target.guild_permissions.administrator)
            if ctx.author.id != ctx.guild.owner_id and not (perms.is_bot_developer(ctx.author, self.bot) or await self.bot.is_owner(ctx.author)):
                if info["rank"] >= actor:
                    raise UserError("You can't assign a level equal to or above your own")
                if target_current >= actor:
                    raise UserError(f"You can't change {target.mention}, they rank at or above you")
            cfg.setdefault("users", {})[str(target.id)] = key
            await self.save(ctx.guild.id, cfg)
            await ctx.approve(f"Set {target.mention}'s permission level to **{info['name']}**.")
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
            await ctx.approve(f"Set {target.mention}'s permission level to **{info['name']}**.")

    @userperm.group(name="remove", aliases=["rem", "delete", "unset"], invoke_without_command=True)
    @has_perms(administrator=True)
    async def up_remove(self, ctx: Context, target: discord.Member | discord.Role):
        """Remove a member's or role's permission level"""
        cfg = await self.cfg(ctx.guild.id)
        actor = await self.actor_rank(ctx)
        if isinstance(target, discord.Member):
            users = cfg.get("users", {})
            if str(target.id) not in users:
                raise UserError(f"{target.mention} has no direct permission level")
            target_rank = perms.rank_of(cfg, target, is_owner=target.id == ctx.guild.owner_id,
                                        is_dev=perms.is_bot_developer(target, self.bot) or await self.bot.is_owner(target),
                                        has_admin=target.guild_permissions.administrator)
            if ctx.author.id != ctx.guild.owner_id and not (perms.is_bot_developer(ctx.author, self.bot) or await self.bot.is_owner(ctx.author)) and target_rank >= actor:
                raise UserError(f"You can't change {target.mention}, they rank at or above you")
            users.pop(str(target.id))
            await self.save(ctx.guild.id, cfg)
            await ctx.approve(f"Removed {target.mention}'s permission level.")
        else:
            roles = cfg.get("roles", {})
            if str(target.id) not in roles:
                raise UserError(f"{target.mention} has no permission level")
            if ctx.author.id != ctx.guild.owner_id and not await self.bot.is_owner(ctx.author) and target >= ctx.author.top_role:
                raise UserError(f"You can't change {target.mention}, it ranks at or above your highest role")
            roles.pop(str(target.id))
            await self.save(ctx.guild.id, cfg)
            await ctx.approve(f"Removed {target.mention}'s permission level.")

    @up_remove.command(name="deleted")
    @has_perms(administrator=True)
    async def up_remove_deleted(self, ctx: Context):
        """Purge permission levels for members who left the server"""
        cfg = await self.cfg(ctx.guild.id)
        users = cfg.get("users", {})
        gone = [uid for uid in users if not ctx.guild.get_member(int(uid))]
        if not gone:
            return await ctx.warn("No permission levels belong to members who left.")
        for uid in gone:
            users.pop(uid)
        await self.save(ctx.guild.id, cfg)
        await ctx.approve(f"Removed **{len(gone)}** permission level(s) from members who left.")

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
        """Check a member's or role's permission level and what it grants"""
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
            e = discord.Embed(color=target.color if target.color.value else Colors.blurple)
            e.title = f"Role: {target.name}"
            if not rkey or rkey not in levels:
                e.description = f"{target.mention} has no assigned permission level."
            else:
                info = levels[rkey]
                e.add_field(name="Level", value=f"**{info['name']}**")
                e.add_field(name="Rank", value=str(info["rank"]))
                grants = "Everything (administrator)" if "administrator" in info["perms"] \
                    else ", ".join(f"`{p.replace('_', ' ')}`" for p in info["perms"]) or "nothing"
                e.add_field(name="Grants", value=grants, inline=False)
            return await ctx.send(embed=e)

        if target != ctx.author and not ctx.author.guild_permissions.manage_messages:
            actor = await self.actor_rank(ctx)
            if actor < 1:
                raise UserError("You can only check your own permission level")

        e = discord.Embed(color=Colors.blurple)
        e.set_author(name=f"{target}", icon_url=target.display_avatar.url)
        name, source, rank = perms.member_level_info(cfg, target,
                                                      is_owner=target.id == ctx.guild.owner_id,
                                                      is_dev=perms.is_bot_developer(target, self.bot) or await self.bot.is_owner(target))
        if not name:
            e.description = f"{target.mention} has no permission level."
        else:
            e.add_field(name="Level", value=f"**{name}**")
            e.add_field(name="Rank", value=str(rank))
            e.add_field(name="Granted By", value=source or "Permission system")
            ukey = perms.user_key(cfg, target.id)
            info = levels.get(ukey)
            if not info and name in [lv["name"] for lv in levels.values()]:
                info = next(lv for lv in levels.values() if lv["name"] == name)
            if info:
                grants = "Everything (administrator)" if "administrator" in info["perms"] \
                    else ", ".join(f"`{p.replace('_', ' ')}`" for p in info["perms"]) or "nothing"
                e.add_field(name="Grants", value=grants, inline=False)
        await ctx.send(embed=e)

    # ============================================================ levels
    @commands.group(aliases=["plevels", "plvl"], invoke_without_command=True)
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
            grants = "all permissions" if "administrator" in info["perms"] else \
                (", ".join(p.replace("_", " ") for p in info["perms"]) or "none")
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
        await ctx.approve(f"Renamed `{key.lower()}` to **{name[:40]}**.")

    @permlevels.command(name="rank")
    @is_server_owner()
    async def pl_rank(self, ctx: Context, key: str, rank: int):
        """Change a level's rank (server owner)"""
        cfg = await self.cfg(ctx.guild.id)
        if key.lower() not in perms.all_levels(cfg):
            raise UserError("Unknown level key")
        cfg.setdefault("overrides", {}).setdefault(key.lower(), {})["rank"] = max(1, min(rank, 899))
        await self.save(ctx.guild.id, cfg)
        await ctx.approve(f"Set `{key.lower()}` to rank **{max(1, min(rank, 899))}**.")

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
        await ctx.approve(msg + ".")


async def setup(bot):
    await bot.add_cog(Permissions(bot))
