"""Developer-only tools (shown under the red Secret help category)."""
from __future__ import annotations

import asyncio
import gzip
import io
import os
import platform
import sqlite3
import sys
import tempfile
import time

import discord
from discord.ext import commands

from core.config import settings
from core.utils import Colors, Context, UserError, chunk_embeds, fmt_duration, shorten, ts


def memory_mb() -> str:
    try:
        import psutil  # optional
        return f"{psutil.Process().memory_info().rss / 1_048_576:.0f} MB"
    except ImportError:
        pass
    try:
        import resource
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return f"{rss / (1_048_576 if sys.platform == 'darwin' else 1024):.0f} MB (peak)"
    except (ImportError, AttributeError):
        return "n/a"


class Owner(commands.Cog):
    """Bot developer tools"""
    emoji = "👑"

    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx: Context) -> bool:
        if not await self.bot.is_owner(ctx.author):
            raise commands.NotOwner()
        return True

    async def save_global(self):
        g = await self.bot.db.get_config(0, "global")
        g["maintenance"] = self.bot.maintenance
        g["killswitch"] = sorted(self.bot.killswitch)
        await self.bot.db.set_config(0, "global", g)

    # ================================================================ status
    @commands.command(hidden=True, aliases=["sys", "botstatus", "health"])
    async def system(self, ctx: Context):
        """Bot health: uptime, latency, shards, memory, database, maintenance state"""
        b = self.bot
        e = discord.Embed(title="System", color=Colors.main, timestamp=discord.utils.utcnow())
        e.add_field(name="Uptime", value=fmt_duration(time.time() - b.started_at))
        e.add_field(name="Latency", value=f"{b.latency * 1000:.0f}ms")
        e.add_field(name="Memory", value=memory_mb())
        e.add_field(name="Servers", value=f"{len(b.guilds):,} ({len(b.authorized):,} authorized)")
        e.add_field(name="Users", value=f"{sum(g.member_count or 0 for g in b.guilds):,}")
        e.add_field(name="Channels", value=f"{sum(len(g.channels) for g in b.guilds):,}")
        shards = ", ".join(f"#{sid}: {lat * 1000:.0f}ms" for sid, lat in b.latencies) or "—"
        e.add_field(name=f"Shards ({b.shard_count or 1})", value=shorten(shards, 1024), inline=False)
        db_path = b.db.path
        size = os.path.getsize(db_path) / 1_048_576 if db_path != ":memory:" and os.path.exists(db_path) else 0
        e.add_field(name="Database", value=f"{size:.2f} MB")
        e.add_field(name="Errors (this run)", value=str(b._error_seq))
        e.add_field(name="Blacklisted", value=str(len(b.blacklist)))
        try:
            load = ", ".join(f"{x:.2f}" for x in os.getloadavg())
        except (AttributeError, OSError):
            load = "n/a"
        e.add_field(name="CPU load (1/5/15m)", value=load)
        e.add_field(name="Maintenance", value=f"🛠️ on — {b.maintenance or 'no reason'}" if b.maintenance is not None else "off")
        e.add_field(name="Kill-switched", value=", ".join(f"`{c}`" for c in sorted(b.killswitch)) or "none")
        e.set_footer(text=f"Python {platform.python_version()} • discord.py {discord.__version__} • "
                          f"{len(b.extensions)} modules • {sum(1 for _ in b.walk_commands())} commands")
        await ctx.send(embed=e)

    # ================================================================ maintenance / kill switch
    @commands.command(hidden=True, aliases=["maint"])
    async def maintenance(self, ctx: Context, state: str = None, *, reason: str = ""):
        """`maintenance on [reason]` — everyone but developers gets a maintenance notice; `maintenance off`"""
        if state is None:
            m = self.bot.maintenance
            return await ctx.neutral(f"Maintenance is **{'on' if m is not None else 'off'}**" + (f": {m}" if m else ""))
        if state.lower() in ("on", "enable", "true"):
            self.bot.maintenance = reason
            await self.bot.change_presence(status=discord.Status.dnd,
                                           activity=discord.CustomActivity(name=f"🛠️ Maintenance{f': {reason}' if reason else ''}"))
            msg = "Maintenance mode **on** — only developers can use commands"
        elif state.lower() in ("off", "disable", "false"):
            self.bot.maintenance = None
            await self.bot.change_presence(status=discord.Status.online, activity=discord.Activity(
                type=discord.ActivityType.watching, name=settings.status))
            msg = "Maintenance mode **off**"
        else:
            raise UserError("Use `maintenance on [reason]` or `maintenance off`")
        await self.save_global()
        await ctx.approve(msg)

    @commands.group(hidden=True, aliases=["ks", "globaldisable"], invoke_without_command=True)
    async def killswitch(self, ctx: Context, *, command: str):
        """Instantly disable (or re-enable) a command everywhere — e.g. if it's broken or being abused"""
        cmd = self.bot.get_command(command)
        if not cmd:
            raise UserError(f"No command called `{command}`")
        if cmd.cog_name in ("Owner", "Authorization"):
            raise UserError("Developer commands can't be kill-switched")
        name = cmd.qualified_name
        if name in self.bot.killswitch:
            self.bot.killswitch.discard(name)
            msg = f"Re-enabled `{name}` everywhere"
        else:
            self.bot.killswitch.add(name)
            msg = f"Disabled `{name}` everywhere (developers can still use it)"
        await self.save_global()
        await ctx.approve(msg)

    @killswitch.command(name="list", hidden=True)
    async def killswitch_list(self, ctx: Context):
        """List kill-switched commands"""
        await ctx.neutral(", ".join(f"`{c}`" for c in sorted(self.bot.killswitch)) or "Nothing is kill-switched")

    # ================================================================ errors & logs
    @commands.command(hidden=True, aliases=["errs"])
    async def errors(self, ctx: Context):
        """Recent command errors (since the last restart)"""
        lines = [f"`#{r['id']}` **{r['command']}** {ts(r['time'])} — {shorten(r['error'], 90)}"
                 for r in self.bot.recent_errors]
        await ctx.paginate(chunk_embeds("Recent errors", lines, numbered=False) or
                           [discord.Embed(color=Colors.success, description="✅ No errors since the last restart")])

    @commands.command(hidden=True, aliases=["err", "traceback"])
    async def error(self, ctx: Context, error_id: int):
        """Full traceback and context for an error ID"""
        r = next((x for x in self.bot.recent_errors if x["id"] == error_id), None)
        if not r:
            raise UserError("No recent error with that ID")
        e = discord.Embed(color=Colors.error, title=f"Error #{r['id']} in {r['command']}", description=f"`{r['error']}`")
        e.add_field(name="Message", value=r["content"] or "—", inline=False)
        e.add_field(name="User", value=f"<@{r['user']}> (`{r['user']}`)")
        e.add_field(name="Server", value=f"`{r['guild']}`")
        e.add_field(name="When", value=ts(r["time"]))
        await ctx.send(embed=e, file=discord.File(io.BytesIO(r["traceback"].encode()), f"error-{r['id']}.txt"))

    @commands.command(name="botlogs", hidden=True, aliases=["logfile", "console"])
    async def botlogs(self, ctx: Context, lines: int = 60):
        """The last N lines of the bot's log file"""
        path = settings.log_file
        if not os.path.exists(path):
            raise UserError(f"No log file at `{path}`")
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 400_000))
            tail = f.read().decode("utf-8", "replace").splitlines()[-max(1, min(lines, 2000)):]
        await ctx.send(file=discord.File(io.BytesIO("\n".join(tail).encode()), "veil-log.txt"))

    # ================================================================ backup
    @commands.command(hidden=True)
    @commands.max_concurrency(1)
    async def backup(self, ctx: Context):
        """DM yourself a full database backup"""
        path = self.bot.db.path
        if path == ":memory:" or not os.path.exists(path):
            raise UserError("There's no database file to back up")

        def make() -> bytes:
            with tempfile.TemporaryDirectory() as tmp:
                out = os.path.join(tmp, "backup.db")
                src, dst = sqlite3.connect(path), sqlite3.connect(out)
                try:
                    src.backup(dst)
                finally:
                    dst.close(); src.close()
                with open(out, "rb") as f:
                    return gzip.compress(f.read())

        async with ctx.typing():
            data = await asyncio.to_thread(make)
        if len(data) > 9_500_000:
            raise UserError(f"The compressed backup is {len(data) / 1_048_576:.1f} MB — too big for Discord. "
                            f"Copy `{path}` from the server directly.")
        name = f"veil-backup-{time.strftime('%Y%m%d-%H%M%S')}.db.gz"
        try:
            await ctx.author.send(f"🗄️ Database backup ({len(data) / 1024:.0f} KB, gzip). Keep it private.",
                                  file=discord.File(io.BytesIO(data), name))
        except discord.Forbidden:
            raise UserError("I can't DM you — open your DMs and try again")
        await ctx.approve("Backup sent to your DMs")

    # ================================================================ lookups
    @commands.command(hidden=True)
    async def guilds(self, ctx: Context):
        """List servers the bot is in"""
        lines = [f"{'✅' if g.id in self.bot.authorized else '⚠️'} **{g.name}** (`{g.id}`) — {g.member_count:,} members"
                 for g in sorted(self.bot.guilds, key=lambda g: g.member_count or 0, reverse=True)]
        await ctx.paginate(chunk_embeds(f"{len(lines)} servers", lines))

    @commands.command(name="lookupserver", hidden=True, aliases=["ginfo", "serverlookup"])
    async def lookupserver(self, ctx: Context, guild_id: int):
        """Everything about a server the bot is in"""
        g = self.bot.get_guild(guild_id)
        if not g:
            raise UserError("I'm not in that server")
        auth = await self.bot.db.fetchone("SELECT * FROM authorized WHERE guild_id=?", g.id)
        an = await self.bot.db.get_config(g.id, "antinuke")
        settings = await self.bot.db.get_config(g.id, "settings")
        e = discord.Embed(color=Colors.main, title=g.name, description=g.description)
        if g.icon:
            e.set_thumbnail(url=g.icon.url)
        e.add_field(name="ID", value=f"`{g.id}`")
        e.add_field(name="Owner", value=f"{g.owner} (`{g.owner_id}`)" if g.owner else f"`{g.owner_id}`")
        e.add_field(name="Members", value=f"{g.member_count:,} ({sum(m.bot for m in g.members)} bots)")
        e.add_field(name="Created", value=ts(g.created_at))
        e.add_field(name="Bot joined", value=ts(g.me.joined_at) if g.me.joined_at else "?")
        e.add_field(name="Boosts", value=f"{g.premium_subscription_count} (tier {g.premium_tier})")
        e.add_field(name="Authorized", value=(f"✅ by <@{auth['authorized_by']}> {ts(auth['at'])}" if auth and auth["authorized_by"]
                                             else "✅ (grandfathered/system)" if auth else "❌ no"))
        e.add_field(name="Antinuke", value="on" if an.get("enabled") else "off")
        e.add_field(name="Prefix", value=f"`{settings.get('prefix', self.bot.default_prefix)}`")
        e.add_field(name="Bot permissions", value="Administrator" if g.me.guild_permissions.administrator else "limited")
        e.add_field(name="Vanity", value=f"/{g.vanity_url_code}" if g.vanity_url_code else "none")
        e.add_field(name="Blacklisted", value="yes" if g.id in self.bot.blacklist else "no")
        await ctx.send(embed=e)

    @commands.command(hidden=True, aliases=["shared", "sharedservers"])
    async def mutuals(self, ctx: Context, user: discord.User):
        """Servers the bot shares with a user (for abuse investigations)"""
        shared = [g for g in self.bot.guilds if g.get_member(user.id)]
        lines = [f"**{g.name}** (`{g.id}`){' 👑 owner' if g.owner_id == user.id else ''}" for g in shared]
        await ctx.paginate(chunk_embeds(f"{user} — {len(shared)} shared servers", lines, numbered=False) or
                           [discord.Embed(color=Colors.neutral, description=f"No shared servers with **{user}**")])

    @commands.command(hidden=True, aliases=["dmuser"])
    async def dm(self, ctx: Context, user: discord.User, *, message: str):
        """DM a user from the bot (marked as coming from the developers)"""
        e = discord.Embed(color=Colors.main, description=message, timestamp=discord.utils.utcnow())
        e.set_author(name=f"Message from the {self.bot.user.name} developers", icon_url=self.bot.user.display_avatar.url)
        try:
            await user.send(embed=e)
        except discord.HTTPException:
            raise UserError(f"I can't DM **{user}**")
        await ctx.approve(f"Sent to **{user}**")

    # ================================================================ moderation of the bot itself
    @commands.command(hidden=True)
    async def leaveguild(self, ctx: Context, guild_id: int):
        """Make the bot leave a server"""
        g = self.bot.get_guild(guild_id)
        if not g:
            raise UserError("Not in that server")
        await g.leave()
        await ctx.approve(f"Left **{g.name}**")

    @commands.command(hidden=True)
    async def blacklist(self, ctx: Context, target_id: int, *, reason: str = None):
        """Toggle blacklisting a user or server ID (blacklisted servers are left immediately)"""
        if target_id in self.bot.blacklist:
            self.bot.blacklist.discard(target_id)
            await self.bot.db.execute("DELETE FROM blacklist WHERE id=?", target_id)
            return await ctx.approve(f"Unblacklisted `{target_id}`")
        self.bot.blacklist.add(target_id)
        await self.bot.db.execute("INSERT OR REPLACE INTO blacklist VALUES(?,?)", target_id, reason)
        g = self.bot.get_guild(target_id)
        if g:
            await g.leave()
        await ctx.approve(f"Blacklisted `{target_id}`" + (f" and left **{g.name}**" if g else ""))

    @commands.command(hidden=True, aliases=["bl"])
    async def blacklisted(self, ctx: Context):
        """List blacklisted users and servers"""
        rows = await self.bot.db.fetchall("SELECT * FROM blacklist")
        await ctx.paginate(chunk_embeds("Blacklist", [f"`{r['id']}` — {r['reason'] or 'no reason'}" for r in rows],
                                        numbered=False) or [discord.Embed(color=Colors.neutral, description="Nobody is blacklisted")])

    # ================================================================ process
    @commands.command(hidden=True, aliases=["rl"])
    async def reload(self, ctx: Context, *extensions: str):
        """Reload modules without restarting (all if none given)"""
        exts = [f"cogs.{e}" if not e.startswith("cogs.") else e for e in extensions] or list(self.bot.extensions)
        failed = []
        for ext in exts:
            try:
                await self.bot.reload_extension(ext)
            except commands.ExtensionError as e:
                failed.append(f"{ext}: {e}")
        await (ctx.warn("\n".join(failed)) if failed else ctx.approve(f"Reloaded {len(exts)} module(s)"))

    @commands.command(hidden=True)
    async def setstatus(self, ctx: Context, *, text: str):
        """Change the bot's status text"""
        await self.bot.change_presence(activity=discord.CustomActivity(name=text))
        await ctx.approve("Status updated")

    @commands.command(hidden=True, aliases=["reboot"])
    async def restart(self, ctx: Context):
        """Restart the bot cleanly in-process without killing the container"""
        await ctx.approve("Restarting bot — back in ~2 seconds…")
        self.bot._restart_requested = True
        await self.bot.close()

    @commands.command(hidden=True)
    async def shutdown(self, ctx: Context):
        """Shut the bot down"""
        await ctx.approve("Shutting down")
        self.bot._restart_requested = False
        await self.bot.close()


async def setup(bot):
    await bot.add_cog(Owner(bot))
