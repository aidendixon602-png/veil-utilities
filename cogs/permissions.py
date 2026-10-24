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
                             is_dev=await self.bot.is_owner(ctx.author),
                             has_admin=ctx.author.guild_permissions.administrator)

    # ============================================================ userperm
    @commands.group(aliases=["uperm", "ups"], invoke_without_command=True)
    @commands.guild_only()
    async def userperm(self, ctx: Context):
        """Manage members' permission levels"""
        await ctx.send_help(ctx.command)

    @userperm.command(name="add", aliases=["set", "give"])
    @has_perms(administrator=True)
    async def up_add(self, ctx: Context, member: discord.Member, *, level: str):
        """Assign a permission level to a member"""
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
        target_current = perms.rank_of(cfg, member, is_owner=member.id == ctx.guild.owner_id,
                                       has_admin=member.guild_permissions.administrator)
        if ctx.author.id != ctx.guild.owner_id and not await self.bot.is_owner(ctx.author):
            if info["rank"] >= actor:
                raise UserError("You can't assign a level equal to or above your own")
            if target_current >= actor:
                raise UserError(f"You can't change {member.mention}, they rank at or above you")
        cfg.setdefault("users", {})[str(member.id)] = key
        await self.save(ctx.guild.id, cfg)
        await ctx.approve(f"Set {member.mention}'s permission level to **{info['name']}**.")

    @userperm.group(name="remove", aliases=["rem", "delete", "unset"], invoke_without_command=True)
    @has_perms(administrator=True)
    async def up_remove(self, ctx: Context, member: discord.Member):
        """Remove a member's permission level"""
        cfg = await self.cfg(ctx.guild.id)
        if str(member.id) not in cfg.get("users", {}):
            raise UserError(f"{member.mention} has no permission level")
        actor = await self.actor_rank(ctx)
        target = perms.rank_of(cfg, member, is_owner=member.id == ctx.guild.owner_id)
        if ctx.author.id != ctx.guild.owner_id and not await self.bot.is_owner(ctx.author) and target >= actor:
            raise UserError(f"You can't change {member.mention}, they rank at or above you")
        cfg["users"].pop(str(member.id))
        await self.save(ctx.guild.id, cfg)
        await ctx.approve(f"Removed {member.mention}'s permission level.")

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
        """List members with a permission level"""
        cfg = await self.cfg(ctx.guild.id)
        levels = perms.all_levels(cfg)
        rows = sorted(cfg.get("users", {}).items(),
                      key=lambda kv: levels.get(kv[1], {}).get("rank", 0), reverse=True)
        lines = [f"<@{uid}> — **{levels.get(key, {}).get('name', key)}**" for uid, key in rows]
        await ctx.paginate(chunk_embeds("Permission levels", lines, numbered=False))

    @userperm.command(name="check", aliases=["view", "info"])
    @commands.guild_only()
    async def up_check(self, ctx: Context, member: discord.Member = None):
        """Check a member's permission level and what it grants"""
        member = member or ctx.author
        cfg = await self.cfg(ctx.guild.id)
        key = perms.user_key(cfg, member.id)
        e = discord.Embed(color=Colors.main)
        e.set_author(name=f"{member}", icon_url=member.display_avatar.url)
        if not key:
            e.description = f"{member.mention} has no permission level."
        else:
            info = perms.all_levels(cfg)[key]
            e.add_field(name="Level", value=f"**{info['name']}**")
            e.add_field(name="Rank", value=str(info["rank"]))
            grants = "Everything (administrator)" if "administrator" in info["perms"] \
                else ", ".join(f"`{p.replace('_', ' ')}`" for p in info["perms"]) or "nothing"
            e.add_field(name="Grants", value=grants, inline=False)
        await ctx.send(embed=e)

    # ============================================================ levels
    @commands.group(aliases=["plevels", "plvl"], invoke_without_command=True)
    @commands.guild_only()
    async def permlevels(self, ctx: Context):
        """View the permission levels available in this server"""
        cfg = await self.cfg(ctx.guild.id)
        levels = sorted(perms.all_levels(cfg).items(), key=lambda kv: kv[1]["rank"], reverse=True)
        counts: dict[str, int] = {}
        for key in cfg.get("users", {}).values():
            counts[key] = counts.get(key, 0) + 1
        e = discord.Embed(title="Permission levels", color=Colors.main,
                          description="Highest to lowest. Assign with `" + ctx.clean_prefix + "uperm add <member> <level>`")
        for name, rank in sorted(((n, r) for n, r in perms.RESERVED.values()), key=lambda x: x[1], reverse=True):
            e.add_field(name=f"{name} · rank {rank}", value="automatic — not assignable", inline=False)
        for key, info in levels:
            grants = "all permissions" if "administrator" in info["perms"] else \
                (", ".join(p.replace("_", " ") for p in info["perms"]) or "none")
            e.add_field(name=f"{info['name']} · rank {info['rank']}",
                        value=f"`{key}` — {grants}\n{counts.get(key, 0)} member(s)", inline=False)
        await ctx.send(embed=e)

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
