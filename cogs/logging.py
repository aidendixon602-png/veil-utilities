"""Server event logging."""
from __future__ import annotations

import io

import discord
from discord.ext import commands

from core.utils import Colors, Context, UserError, has_perms, shorten, ts

TYPES = ("messages", "members", "roles", "channels", "voice", "server")


class Logging(commands.Cog):
    """Log server events to channels"""
    emoji = "📜"

    def __init__(self, bot):
        self.bot = bot

    async def channel_for(self, guild: discord.Guild, kind: str) -> discord.TextChannel | None:
        if guild is None:
            return None
        cfg = await self.bot.db.get_config(guild.id, "logs")
        # joins/leaves/kicks use their own channel if set, else the main member log
        cid = cfg.get(kind) or (cfg.get("members") if kind == "joinleave" else 0)
        ch = guild.get_channel(cid or 0)
        return ch if isinstance(ch, discord.TextChannel) else None

    async def send(self, guild, kind, embed: discord.Embed, file: discord.File | None = None):
        ch = await self.channel_for(guild, kind)
        if ch:
            embed.timestamp = embed.timestamp or discord.utils.utcnow()
            try:
                await ch.send(embed=embed, file=file) if file else await ch.send(embed=embed)
            except discord.HTTPException:
                pass

    # ------------------------------------------------------------ commands
    @commands.group(aliases=["logs"], invoke_without_command=True)
    @has_perms(manage_guild=True)
    async def log(self, ctx: Context):
        """View logging channels"""
        cfg = await self.bot.db.get_config(ctx.guild.id, "logs")
        embed = discord.Embed(title="Logging", color=Colors.main,
                              description="\n".join(f"**{t}**: {f'<#{cfg[t]}>' if cfg.get(t) else 'off'}" for t in TYPES))
        embed.set_footer(text=f"{ctx.clean_prefix}log set <type|all> #channel")
        await ctx.send(embed=embed)

    @log.command(name="set")
    @has_perms(manage_guild=True)
    async def log_set(self, ctx: Context, kind: str, channel: discord.TextChannel = None):
        """Set a log channel: `log set messages #logs` or `log set all #logs` (no channel = off)"""
        kind = kind.lower()
        kinds = TYPES if kind == "all" else (kind,)
        if any(k not in TYPES for k in kinds):
            raise UserError(f"Types: {', '.join(f'`{t}`' for t in TYPES)}, `all`")
        cfg = await self.bot.db.get_config(ctx.guild.id, "logs")
        for k in kinds:
            cfg[k] = channel.id if channel else None
        await self.bot.db.set_config(ctx.guild.id, "logs", cfg)
        await ctx.approve(f"**{kind}** logs → {channel.mention}" if channel else f"Disabled **{kind}** logs")

    # ------------------------------------------------------------ messages
    @commands.Cog.listener()
    async def on_message_delete(self, m: discord.Message):
        if not m.guild or m.author.bot:
            return
        e = discord.Embed(color=Colors.error, description=f"**Message deleted in {m.channel.mention}**\n{shorten(m.content or '*no text*', 3900)}")
        e.set_author(name=str(m.author), icon_url=m.author.display_avatar.url)
        if m.attachments:
            e.add_field(name="Attachments", value="\n".join(a.filename for a in m.attachments)[:1024])
        e.set_footer(text=f"User {m.author.id} • Message {m.id}")
        await self.send(m.guild, "messages", e)

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages: list[discord.Message]):
        if not messages or not messages[0].guild:
            return
        body = "\n".join(f"[{m.created_at:%Y-%m-%d %H:%M}] {m.author}: {m.content}" for m in sorted(messages, key=lambda x: x.id))
        e = discord.Embed(color=Colors.error, description=f"**{len(messages)} messages bulk deleted in {messages[0].channel.mention}**")
        await self.send(messages[0].guild, "messages", e, discord.File(io.BytesIO(body.encode()), "messages.txt"))

    @commands.Cog.listener()
    async def on_message_edit(self, b: discord.Message, a: discord.Message):
        if not a.guild or a.author.bot or b.content == a.content:
            return
        e = discord.Embed(color=Colors.warn, description=f"**Message edited in {a.channel.mention}** [jump]({a.jump_url})")
        e.add_field(name="Before", value=shorten(b.content or "*empty*"), inline=False)
        e.add_field(name="After", value=shorten(a.content or "*empty*"), inline=False)
        e.set_author(name=str(a.author), icon_url=a.author.display_avatar.url)
        e.set_footer(text=f"User {a.author.id}")
        await self.send(a.guild, "messages", e)

    # ------------------------------------------------------------ members
    @commands.Cog.listener()
    async def on_member_join(self, m: discord.Member):
        e = discord.Embed(color=Colors.success, description=f"{m.mention} joined\nCreated {ts(m.created_at)}")
        e.set_author(name=str(m), icon_url=m.display_avatar.url)
        e.set_footer(text=f"ID {m.id} • member #{m.guild.member_count}")
        await self.send(m.guild, "joinleave", e)

    @commands.Cog.listener()
    async def on_member_remove(self, m: discord.Member):
        roles = ", ".join(r.mention for r in m.roles[1:][::-1]) or "none"
        e = discord.Embed(color=Colors.error, description=f"{m.mention} left\nJoined {ts(m.joined_at) if m.joined_at else '?'}")
        e.add_field(name="Roles", value=shorten(roles))
        e.set_author(name=str(m), icon_url=m.display_avatar.url)
        e.set_footer(text=f"ID {m.id}")
        await self.send(m.guild, "joinleave", e)

    @commands.Cog.listener()
    async def on_member_update(self, b: discord.Member, a: discord.Member):
        if b.nick != a.nick:
            e = discord.Embed(color=Colors.neutral, description=f"{a.mention} nickname: `{b.nick}` → `{a.nick}`")
            await self.send(a.guild, "members", e)
        if b.roles != a.roles:
            added = [r.mention for r in a.roles if r not in b.roles]
            removed = [r.mention for r in b.roles if r not in a.roles]
            e = discord.Embed(color=Colors.neutral, description=f"**Roles updated for {a.mention}**")
            if added:
                e.add_field(name="Added", value=", ".join(added)[:1024])
            if removed:
                e.add_field(name="Removed", value=", ".join(removed)[:1024])
            await self.send(a.guild, "roles", e)
        if b.timed_out_until != a.timed_out_until and a.timed_out_until:
            await self.send(a.guild, "members", discord.Embed(color=Colors.warn,
                            description=f"{a.mention} timed out until {ts(a.timed_out_until, 'f')}"))

    @commands.Cog.listener()
    async def on_member_ban(self, g, u):
        await self.send(g, "members", discord.Embed(color=Colors.error, description=f"🔨 **{u}** (`{u.id}`) was banned"))

    @commands.Cog.listener()
    async def on_member_unban(self, g, u):
        await self.send(g, "members", discord.Embed(color=Colors.success, description=f"**{u}** (`{u.id}`) was unbanned"))

    # ------------------------------------------------------------ roles / channels / server
    @commands.Cog.listener()
    async def on_guild_role_create(self, r):
        await self.send(r.guild, "roles", discord.Embed(color=Colors.success, description=f"Role created: {r.mention}"))

    @commands.Cog.listener()
    async def on_guild_role_delete(self, r):
        await self.send(r.guild, "roles", discord.Embed(color=Colors.error, description=f"Role deleted: **{r.name}**"))

    @commands.Cog.listener()
    async def on_guild_role_update(self, b, a):
        changes = []
        if b.name != a.name:
            changes.append(f"name `{b.name}` → `{a.name}`")
        if b.permissions != a.permissions:
            gained = [p for p, v in a.permissions if v and not getattr(b.permissions, p)]
            lost = [p for p, v in b.permissions if v and not getattr(a.permissions, p)]
            if gained:
                changes.append("+ " + ", ".join(gained))
            if lost:
                changes.append("- " + ", ".join(lost))
        if b.color != a.color:
            changes.append(f"color `{b.color}` → `{a.color}`")
        if changes:
            await self.send(a.guild, "roles", discord.Embed(color=Colors.neutral,
                            description=f"**Role updated: {a.mention}**\n" + "\n".join(changes)))

    @commands.Cog.listener()
    async def on_guild_channel_create(self, c):
        await self.send(c.guild, "channels", discord.Embed(color=Colors.success, description=f"Channel created: {c.mention}"))

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, c):
        await self.send(c.guild, "channels", discord.Embed(color=Colors.error, description=f"Channel deleted: **#{c.name}**"))

    @commands.Cog.listener()
    async def on_guild_update(self, b: discord.Guild, a: discord.Guild):
        changes = []
        if b.name != a.name:
            changes.append(f"name `{b.name}` → `{a.name}`")
        if b.vanity_url_code != a.vanity_url_code:
            changes.append(f"vanity `{b.vanity_url_code}` → `{a.vanity_url_code}`")
        if b.icon != a.icon:
            changes.append("icon changed")
        if changes:
            await self.send(a, "server", discord.Embed(color=Colors.neutral, description="**Server updated**\n" + "\n".join(changes)))

    # ------------------------------------------------------------ voice
    @commands.Cog.listener()
    async def on_voice_state_update(self, m: discord.Member, b: discord.VoiceState, a: discord.VoiceState):
        if b.channel == a.channel:
            return
        if not b.channel:
            d = f"{m.mention} joined {a.channel.mention}"
        elif not a.channel:
            d = f"{m.mention} left {b.channel.mention}"
        else:
            d = f"{m.mention} moved {b.channel.mention} → {a.channel.mention}"
        await self.send(m.guild, "voice", discord.Embed(color=Colors.neutral, description=d))


async def setup(bot):
    await bot.add_cog(Logging(bot))
