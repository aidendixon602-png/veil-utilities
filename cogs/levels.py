"""Leveling: XP per message & voice minute, rank, leaderboard, level roles, multipliers."""
from __future__ import annotations

import logging
import random
import time

import discord
from discord.ext import commands, tasks

from core.utils import (Colors, Context, antinuke_guard, build_vars, check_role, chunk_embeds, has_perms, is_dangerous,
                        send_script)


def xp_for(level: int) -> int:
    """XP needed to go from `level` to `level + 1`."""
    return 5 * level ** 2 + 50 * level + 100


def level_from_xp(xp: int) -> tuple[int, int, int]:
    level = 0
    while xp >= xp_for(level):
        xp -= xp_for(level)
        level += 1
    return level, xp, xp_for(level)


def bar(cur: int, total: int, width: int = 14) -> str:
    filled = int(width * cur / total) if total else 0
    return "▰" * filled + "▱" * (width - filled)


DEFAULT = {"enabled": False, "message": "GG {user.mention}, you reached **level {level}**!", "channel": None,
           "roles": {}, "stack": True, "ignored": [], "multipliers": {}, "voice": True}


log = logging.getLogger("veil.levels")


class Levels(commands.Cog):
    """Reward activity with XP and roles"""
    emoji = "📈"

    def __init__(self, bot):
        self.bot = bot
        self.cooldown: dict[tuple[int, int], float] = {}

    async def cog_load(self):
        self.voice_xp.start()

    async def cog_unload(self):
        self.voice_xp.cancel()

    async def cfg(self, gid):
        return await self.bot.db.get_config(gid, "levels", DEFAULT)

    async def add_xp(self, member: discord.Member, amount: int, channel=None):
        cfg = await self.cfg(member.guild.id)
        mult = max([1.0] + [float(v) for k, v in cfg.get("multipliers", {}).items()
                            if int(k) in {r.id for r in member.roles}])
        amount = int(amount * mult)
        row = await self.bot.db.fetchone("SELECT xp, level FROM levels WHERE guild_id=? AND user_id=?",
                                         member.guild.id, member.id)
        xp = (row["xp"] if row else 0) + amount
        old = row["level"] if row else 0
        new, _, _ = level_from_xp(xp)
        await self.bot.db.execute(
            "INSERT INTO levels VALUES(?,?,?,?) ON CONFLICT(guild_id,user_id) DO UPDATE SET xp=excluded.xp, level=excluded.level",
            member.guild.id, member.id, xp, new)
        if new > old:
            await self.level_up(member, new, cfg, channel)

    async def level_up(self, member, level, cfg, channel):
        dest = member.guild.get_channel(cfg.get("channel") or 0) or channel
        if cfg.get("message") and dest:
            try:
                await send_script(dest, cfg["message"], build_vars(member, member.guild, dest, {"level": str(level)}))
            except discord.HTTPException:
                pass
        rewards = {int(k): v for k, v in cfg.get("roles", {}).items()}
        earned = [member.guild.get_role(r) for lvl, r in rewards.items() if lvl <= level]
        earned = [r for r in earned if r and r < member.guild.me.top_role]
        if not earned:
            return
        try:
            if cfg.get("stack", True):
                await member.add_roles(*[r for r in earned if r not in member.roles], reason=f"level {level}")
            else:
                top_lvl = max(l for l in rewards if l <= level)
                keep = member.guild.get_role(rewards[top_lvl])
                drop = [r for r in earned if r != keep and r in member.roles]
                if drop:
                    await member.remove_roles(*drop, reason="level role replace")
                if keep and keep not in member.roles:
                    await member.add_roles(keep, reason=f"level {level}")
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_message(self, m: discord.Message):
        if not m.guild or m.author.bot:
            return
        cfg = await self.cfg(m.guild.id)
        if not cfg.get("enabled") or m.channel.id in cfg.get("ignored", []):
            return
        key = (m.guild.id, m.author.id)
        t = time.monotonic()
        if t - self.cooldown.get(key, 0) < 60:
            return
        self.cooldown[key] = t
        await self.add_xp(m.author, random.randint(15, 25), m.channel)

    @tasks.loop(minutes=1)
    async def voice_xp(self):
        for guild in self.bot.guilds:
            vcs = [vc for vc in guild.voice_channels if len([m for m in vc.members if not m.bot]) >= 2]
            if not vcs:
                continue
            cfg = await self.cfg(guild.id)
            if not cfg.get("enabled") or not cfg.get("voice", True):
                continue
            for vc in vcs:
                if vc == guild.afk_channel:
                    continue
                for m in vc.members:
                    if not m.bot and m.voice and not (m.voice.self_mute or m.voice.self_deaf):
                        try:  # one bad member/config must not kill the task loop for every server
                            await self.add_xp(m, 5)
                        except Exception:
                            log.exception("voice xp failed for %s in %s", m.id, guild.id)

    @voice_xp.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------ user commands
    @commands.command(aliases=["level", "lvl", "xp"])
    @commands.guild_only()
    async def rank(self, ctx: Context, member: discord.Member = None):
        """View your level and progress"""
        member = member or ctx.author
        row = await self.bot.db.fetchone("SELECT xp FROM levels WHERE guild_id=? AND user_id=?", ctx.guild.id, member.id)
        xp = row["xp"] if row else 0
        level, cur, need = level_from_xp(xp)
        pos = (await self.bot.db.fetchone("SELECT COUNT(*)+1 n FROM levels WHERE guild_id=? AND xp>?", ctx.guild.id, xp))["n"]
        e = discord.Embed(color=member.color if member.color.value else Colors.main)
        e.set_author(name=member.display_name, icon_url=member.display_avatar.url)
        e.add_field(name="Level", value=f"**{level}**")
        e.add_field(name="Rank", value=f"**#{pos}**")
        e.add_field(name="Total XP", value=f"{xp:,}")
        e.add_field(name="Progress", value=f"{bar(cur, need)} `{cur:,}/{need:,}`", inline=False)
        await ctx.send(embed=e)

    @commands.command(aliases=["lb", "top"])
    @commands.guild_only()
    async def leaderboard(self, ctx: Context):
        """Server XP leaderboard"""
        rows = await self.bot.db.fetchall("SELECT user_id, xp FROM levels WHERE guild_id=? ORDER BY xp DESC LIMIT 100",
                                          ctx.guild.id)
        lines = [f"<@{r['user_id']}> — level **{level_from_xp(r['xp'])[0]}** ({r['xp']:,} xp)" for r in rows]
        await ctx.paginate(chunk_embeds(f"{ctx.guild.name} leaderboard", lines))

    # ------------------------------------------------------------ config
    @commands.group(aliases=["levels"], invoke_without_command=True)
    @has_perms(manage_guild=True)
    async def leveling(self, ctx: Context):
        """Configure leveling"""
        cfg = await self.cfg(ctx.guild.id)
        e = discord.Embed(title="Leveling", color=Colors.main)
        e.add_field(name="Status", value="on" if cfg["enabled"] else "off")
        e.add_field(name="Channel", value=f"<#{cfg['channel']}>" if cfg.get("channel") else "same channel")
        e.add_field(name="Stack roles", value=str(cfg.get("stack", True)))
        e.add_field(name="Voice XP", value=str(cfg.get("voice", True)))
        e.add_field(name="Rewards", value="\n".join(f"lvl {k} → <@&{v}>" for k, v in sorted(cfg["roles"].items(), key=lambda x: int(x[0]))) or "none", inline=False)
        e.add_field(name="Multipliers", value="\n".join(f"<@&{k}> ×{v}" for k, v in cfg.get("multipliers", {}).items()) or "none", inline=False)
        e.add_field(name="Message", value=f"```{cfg.get('message')}```", inline=False)
        await ctx.send(embed=e)

    async def _set(self, ctx, **kw):
        cfg = await self.cfg(ctx.guild.id)
        cfg.update(kw)
        await self.bot.db.set_config(ctx.guild.id, "levels", cfg)
        return cfg

    @leveling.command(name="on", aliases=["enable"])
    @has_perms(manage_guild=True)
    async def lv_on(self, ctx):
        """Enable leveling"""
        await self._set(ctx, enabled=True)
        await ctx.approve("Leveling enabled")

    @leveling.command(name="off", aliases=["disable"])
    @has_perms(manage_guild=True)
    async def lv_off(self, ctx):
        """Disable leveling"""
        await self._set(ctx, enabled=False)
        await ctx.approve("Leveling disabled")

    @leveling.command(name="message")
    @has_perms(manage_guild=True)
    async def lv_message(self, ctx, *, message: str = None):
        """Level-up message (`{level}` + all variables; `none` to silence)"""
        await self._set(ctx, message=None if message in (None, "none") else message)
        await ctx.approve("Updated level-up message")

    @leveling.command(name="channel")
    @has_perms(manage_guild=True)
    async def lv_channel(self, ctx, channel: discord.TextChannel = None):
        """Send level-ups to a channel (omit for same channel)"""
        await self._set(ctx, channel=channel.id if channel else None)
        await ctx.approve(f"Level-ups → {channel.mention if channel else 'same channel'}")

    @leveling.command(name="reward", aliases=["role"])
    @has_perms(manage_roles=True)
    async def lv_reward(self, ctx, level: int, role: discord.Role = None):
        """Give a role at a level (omit role to remove)"""
        cfg = await self.cfg(ctx.guild.id)
        if role:
            check_role(ctx, role)
            if is_dangerous(role.permissions):  # anyone could earn staff powers by chatting
                await antinuke_guard(ctx, "permissions")
            cfg["roles"][str(level)] = role.id
        else:
            cfg["roles"].pop(str(level), None)
        await self.bot.db.set_config(ctx.guild.id, "levels", cfg)
        await ctx.approve(f"Level **{level}** → {role.mention if role else 'no reward'}")

    @leveling.command(name="stack")
    @has_perms(manage_guild=True)
    async def lv_stack(self, ctx, state: bool):
        """Keep old reward roles (true) or only the highest (false)"""
        await self._set(ctx, stack=state)
        await ctx.approve(f"Stacking rewards: **{state}**")

    @leveling.command(name="voice")
    @has_perms(manage_guild=True)
    async def lv_voice(self, ctx, state: bool):
        """Toggle XP for time in voice"""
        await self._set(ctx, voice=state)
        await ctx.approve(f"Voice XP: **{state}**")

    @leveling.command(name="ignore")
    @has_perms(manage_guild=True)
    async def lv_ignore(self, ctx, channel: discord.TextChannel):
        """Toggle a channel not giving XP"""
        cfg = await self.cfg(ctx.guild.id)
        lst = cfg.setdefault("ignored", [])
        lst.remove(channel.id) if channel.id in lst else lst.append(channel.id)
        await self.bot.db.set_config(ctx.guild.id, "levels", cfg)
        await ctx.approve(f"Toggled XP in {channel.mention}")

    @leveling.command(name="multiplier")
    @has_perms(manage_guild=True)
    async def lv_mult(self, ctx, role: discord.Role, multiplier: float = 1.0):
        """XP multiplier for a role, e.g. boosters ×2 (1 to remove)"""
        cfg = await self.cfg(ctx.guild.id)
        if multiplier == 1:
            cfg["multipliers"].pop(str(role.id), None)
        else:
            cfg["multipliers"][str(role.id)] = max(0.1, min(multiplier, 10))
        await self.bot.db.set_config(ctx.guild.id, "levels", cfg)
        await ctx.approve(f"{role.mention} XP ×{multiplier}")

    @leveling.command(name="setxp")
    @has_perms(administrator=True)
    async def lv_setxp(self, ctx, member: discord.Member, xp: int):
        """Set a member's total XP"""
        level = level_from_xp(max(0, xp))[0]
        await self.bot.db.execute("INSERT INTO levels VALUES(?,?,?,?) ON CONFLICT(guild_id,user_id) DO UPDATE SET xp=excluded.xp, level=excluded.level",
                                  ctx.guild.id, member.id, max(0, xp), level)
        await ctx.approve(f"{member.mention} now has **{xp:,}** XP (level {level})")

    @leveling.command(name="setlevel")
    @has_perms(administrator=True)
    async def lv_setlevel(self, ctx, member: discord.Member, level: int):
        """Set a member's level"""
        xp = sum(xp_for(i) for i in range(max(0, level)))
        await self.bot.db.execute("INSERT INTO levels VALUES(?,?,?,?) ON CONFLICT(guild_id,user_id) DO UPDATE SET xp=excluded.xp, level=excluded.level",
                                  ctx.guild.id, member.id, xp, level)
        await ctx.approve(f"{member.mention} is now level **{level}**")

    @leveling.command(name="reset")
    @has_perms(administrator=True)
    async def lv_reset(self, ctx, member: discord.Member = None):
        """Reset one member's XP, or everyone's"""
        if member:
            await self.bot.db.execute("DELETE FROM levels WHERE guild_id=? AND user_id=?", ctx.guild.id, member.id)
            return await ctx.approve(f"Reset {member.mention}'s XP")
        if await ctx.confirm("Reset **everyone's** XP?"):
            await self.bot.db.execute("DELETE FROM levels WHERE guild_id=?", ctx.guild.id)
            await ctx.approve("Reset all XP")


async def setup(bot):
    await bot.add_cog(Levels(bot))
