"""Developer-only tools (shown under the red Secret help category)."""
from __future__ import annotations

import asyncio
import contextlib
import copy
import gzip
import io
import json
import logging
import os
import platform
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback

import discord
from discord.ext import commands

from core import gitinfo, redact, testnuke
from core.config import settings
from core.utils import Colors, Context, UserError, chunk_embeds, fmt_duration, shorten, ts

log = logging.getLogger("veil.owner")
BOT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# code that is imported once at startup and can't be swapped safely while the bot runs
RESTART_PATHS = ("core/", "main.py")


RESET_WORDS = ("clear", "reset", "default", "dynamic", "none", "off")  # words that remove a custom status

class UpdatePlan:
    def __init__(self, restart: bool, restart_reason: str, reload: list[str], deps_changed: bool):
        self.restart = restart
        self.restart_reason = restart_reason
        self.reload = reload
        self.deps_changed = deps_changed


def plan_update(changed: list[str] | None, loaded: set[str]) -> UpdatePlan:
    """Decide how to apply pulled changes: reload the changed cogs, or restart when anything else changed."""
    if changed is None:
        return UpdatePlan(True, "Couldn't tell which files changed.", [], False)
    deps = "requirements.txt" in changed
    core = sorted(f for f in changed if f.startswith(RESTART_PATHS))
    if core:
        shown = ", ".join(f"`{f}`" for f in core[:5]) + (" …" if len(core) > 5 else "")
        return UpdatePlan(True, f"Core files changed ({shown}).", [], deps)
    cogs = sorted({"cogs." + f[len("cogs/"):-3] for f in changed
                   if f.startswith("cogs/") and f.endswith(".py") and f != "cogs/__init__.py"})
    new = [c for c in cogs if c not in loaded]
    if new:
        return UpdatePlan(True, "New cog(s) added (" + ", ".join(f"`{c}`" for c in new) + ").", [], deps)
    return UpdatePlan(False, "", cogs, deps)


def _code_mtimes() -> dict[str, float]:
    found = {}
    for rel in [os.path.join("core", f) for f in os.listdir(os.path.join(BOT_ROOT, "core")) if f.endswith(".py")] \
            + ["main.py"]:
        try:
            found[rel] = os.path.getmtime(os.path.join(BOT_ROOT, rel))
        except OSError:
            pass
    return found


def core_changed_on_disk(bot) -> bool:
    """True if core/ or main.py on disk differ from what was there when the bot started."""
    snapshot = getattr(bot, "_code_mtimes", None)
    return snapshot is not None and _code_mtimes() != snapshot


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
        self._last_eval = None

    async def cog_check(self, ctx: Context) -> bool:
        from core import perms as _perms
        if await self.bot.is_owner(ctx.author) or _perms.is_bot_developer(ctx.author, self.bot):
            return True
        raise commands.NotOwner()

    async def save_global(self):
        g = await self.bot.db.get_config(0, "global")
        g["maintenance"] = self.bot.maintenance
        g["maintenance_until"] = getattr(self.bot, "maintenance_until", None)
        g["maintenance_channel_id"] = getattr(self.bot, "_maintenance_channel_id", None)
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
    async def maintenance(self, ctx: Context, state: str = None, duration: str = None, *, reason: str = ""):
        """Turn maintenance mode on or off: `maintenance on 5m DB update`, `maintenance off`"""
        if state is None:
            m = self.bot.maintenance
            until = getattr(self.bot, "maintenance_until", None)
            time_info = ""
            if until and until > time.time():
                rem = max(1, int(until - time.time()))
                time_info = f" (<t:{int(until)}:R> / ~{rem}s remaining)"
            return await ctx.neutral(f"maintenance is **{'on' if m is not None else 'off'}**{time_info}" + (f": {m}" if m else ""))

        if state.lower() in ("on", "enable", "true"):
            parsed_seconds = None
            if duration and any(duration.endswith(unit) for unit in ("s", "m", "h", "d")):
                try:
                    from core.utils import parse_time
                    parsed_seconds = parse_time(duration)
                except Exception:
                    reason = f"{duration} {reason}".strip()
                    parsed_seconds = None
            elif duration:
                reason = f"{duration} {reason}".strip()

            self.bot.maintenance = reason or "Maintenance"
            if parsed_seconds:
                until = int(time.time() + parsed_seconds)
                self.bot.maintenance_until = until
                time_str = f" (<t:{until}:R> / ~{parsed_seconds}s remaining)"
            else:
                self.bot.maintenance_until = None
                time_str = ""

            self.bot._maintenance_channel_id = getattr(ctx.channel, "id", None)
            await self.bot.change_presence(
                status=discord.Status.dnd,
                activity=discord.CustomActivity(name=f"🛠️ Maintenance{f': {reason}' if reason else ''}")
            )
            await self.save_global()
            await ctx.approve(f"turned maintenance mode **on**{time_str} · only developers can use commands")

        elif state.lower() in ("off", "disable", "false"):
            self.bot.maintenance = None
            self.bot.maintenance_until = None
            await self.bot.update_dynamic_status()
            await self.save_global()
            await ctx.approve("turned maintenance mode **off** · Veil Utilities is back online")
            if getattr(self.bot, "_maintenance_channel_id", None) and getattr(ctx.channel, "id", None) != self.bot._maintenance_channel_id:
                try:
                    ch = self.bot.get_channel(self.bot._maintenance_channel_id)
                    if ch:
                        await ch.send("✅ **Veil Utilities is back online!** Maintenance has ended.")
                except Exception:
                    pass
        else:
            raise UserError("Use `maintenance on [duration] [reason]` or `maintenance off`")

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
            msg = f"enabled `{name}` everywhere"
        else:
            self.bot.killswitch.add(name)
            msg = f"disabled `{name}` everywhere · developers can still use it"
        await self.save_global()
        await ctx.approve(msg)

    @killswitch.command(name="list", hidden=True)
    async def killswitch_list(self, ctx: Context):
        """List kill-switched commands"""
        if not self.bot.killswitch:
            return await ctx.warn("nothing is kill-switched")
        await ctx.neutral(", ".join(f"`{c}`" for c in sorted(self.bot.killswitch)))

    # ================================================================ errors & logs
    @commands.command(hidden=True, aliases=["errs"])
    async def errors(self, ctx: Context):
        """Recent command errors (since the last restart)"""
        lines = [f"`#{r['id']}` **{r['command']}** {ts(r['time'])} — {shorten(redact.scrub(r['error']), 90)}"
                 for r in self.bot.recent_errors]
        await ctx.paginate(chunk_embeds("Recent errors", lines, numbered=False) or
                           [discord.Embed(color=Colors.success, description="✅ No errors since the last restart")])

    @commands.command(hidden=True, aliases=["err"])
    async def error(self, ctx: Context, error_id: int = None):
        """Full traceback and context for an error ID (defaults to latest error)"""
        if not self.bot.recent_errors:
            return await ctx.neutral("no errors recorded since the last restart")
        if error_id is None:
            r = self.bot.recent_errors[-1]
        else:
            r = next((x for x in self.bot.recent_errors if x["id"] == error_id), None)
            if not r:
                raise UserError(f"No recent error with ID `#{error_id}`")
        e = discord.Embed(color=Colors.error, title=f"Error #{r['id']} in {r['command']}",
                          description=f"`{redact.scrub(r['error'])}`")
        e.add_field(name="Message", value=r["content"] or "—", inline=False)
        e.add_field(name="User", value=f"<@{r['user']}> (`{r['user']}`)")
        e.add_field(name="Server", value=f"`{r['guild']}`")
        e.add_field(name="When", value=ts(r["time"]))
        await ctx.send(embed=e, file=discord.File(io.BytesIO(redact.scrub(r["traceback"]).encode()), f"error-{r['id']}.txt"))

    @commands.command(name="traceback", hidden=True, aliases=["tb", "lasttraceback", "lastlog"])
    async def cmd_traceback(self, ctx: Context):
        """Show the most recent exception traceback recorded by the bot"""
        if not self.bot.recent_errors:
            return await ctx.neutral("no errors recorded since the last restart")
        r = self.bot.recent_errors[-1]
        tb = redact.scrub(r.get("traceback", "No traceback available"))
        if len(tb) > 3800:
            tb = tb[-3800:]
        e = discord.Embed(
            title=f"⚠️ Latest Error #{r['id']} ({r['command']})",
            description=f"```py\n{tb}\n```",
            color=Colors.error,
        )
        e.add_field(name="Invoker", value=f"<@{r['user']}> (`{r['user']}`)")
        e.add_field(name="Message", value=r["content"] or "—")
        e.set_footer(text=f"Occurred {fmt_duration(time.time() - r['time'])} ago")
        await ctx.send(embed=e)

    @commands.command(name="cogs", hidden=True, aliases=["modules", "extensions"])
    async def cmd_cogs(self, ctx: Context):
        """List all loaded cogs, extensions, and their registered commands"""
        e = discord.Embed(title="🧩 Loaded Extensions & Cogs", color=Colors.main)
        cog_lines = []
        for name, cog in sorted(self.bot.cogs.items()):
            cmds = cog.get_commands()
            cog_lines.append(f"• **`{name}`**: `{len(cmds)}` commands")

        total_cmds = sum(1 for _ in self.bot.walk_commands())
        e.description = (
            f"Active Cogs: **{len(self.bot.cogs)}** • Extensions: **{len(self.bot.extensions)}** • "
            f"Total Commands: **{total_cmds}**\n\n" + "\n".join(cog_lines)
        )
        e.set_footer(text="Veil Utilities • Cog Registry")
        await ctx.send(embed=e)

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
        await ctx.approve("sent the backup to your DMs")

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
        await ctx.approve(f"sent your message to {user.mention}")

    # ================================================================ moderation of the bot itself
    @commands.command(hidden=True)
    async def leaveguild(self, ctx: Context, guild_id: int):
        """Make the bot leave a server"""
        g = self.bot.get_guild(guild_id)
        if not g:
            raise UserError("Not in that server")
        await g.leave()
        await ctx.approve(f"left **{discord.utils.escape_markdown(g.name)}**")

    @commands.command(hidden=True)
    async def blacklist(self, ctx: Context, target_id: int, *, reason: str = None):
        """Toggle blacklisting a user or server ID (blacklisted servers are left immediately)"""
        from core import perms as _perms
        if _perms.is_bot_developer(target_id, self.bot):
            raise UserError("Cannot blacklist a bot developer.")
        if target_id in self.bot.blacklist:
            self.bot.blacklist.discard(target_id)
            await self.bot.db.execute("DELETE FROM blacklist WHERE id=?", target_id)
            return await ctx.approve(f"unblacklisted `{target_id}`")
        self.bot.blacklist.add(target_id)
        await self.bot.db.execute("INSERT OR REPLACE INTO blacklist VALUES(?,?)", target_id, reason)
        g = self.bot.get_guild(target_id)
        if g:
            await g.leave()
        await ctx.approve(f"blacklisted `{target_id}`" + (f" and left **{discord.utils.escape_markdown(g.name)}**" if g else ""))

    @commands.command(hidden=True, aliases=["bl"])
    async def blacklisted(self, ctx: Context):
        """List blacklisted users and servers"""
        rows = await self.bot.db.fetchall("SELECT * FROM blacklist")
        await ctx.paginate(chunk_embeds("Blacklist", [f"`{r['id']}` — {r['reason'] or 'no reason'}" for r in rows],
                                        numbered=False) or [discord.Embed(color=Colors.neutral, description="Nobody is blacklisted")])

    # ================================================================ process
    @commands.command(hidden=True, aliases=["pull", "updatebot", "botupdate", "syncgit"])
    async def gitpull(self, ctx: Context):
        """Pull the latest update; reloads changed cogs, or restarts if core files changed"""
        msg = await ctx.send("🔄 Pulling the latest update...")
        try:
            result = await asyncio.to_thread(gitinfo.pull, BOT_ROOT)
        except Exception as e:
            return await msg.edit(content=f"❌ Failed to pull the update: `{redact.scrub(e)}`")
        # Everything below is posted where server administrators can read it: short commit IDs only, never the
        # account, repository, remote address or commit titles (those go to the host console).
        if not result.ok:
            return await msg.edit(content=f"❌ Couldn't pull the update: {redact.scrub(result.error)}")

        new = result.new
        notes = []
        slug = gitinfo.repo_slug(result.remote)
        if slug and slug.lower() != gitinfo.EXPECTED_REPO.lower():
            log.warning("This folder pulls from %s, not %s", slug, gitinfo.EXPECTED_REPO)
            notes.append("⚠️ This folder pulls from a different repository than the bot expects. "
                         "Fix the Git repo address in the host panel (the console says which).")
        # what the running process has loaded can lag the files (a restart skipped, a host that pulled on its own)
        loaded = getattr(self.bot, "loaded_commit", "")
        base = loaded if isinstance(loaded, str) and loaded else result.old
        changed = result.changed if base == result.old else gitinfo.changed_files(BOT_ROOT, base, new)
        if base == new:
            return await msg.edit(content="\n".join([f"✅ Already up to date (`{new[:7]}`)", *notes]))

        plan = plan_update(changed, set(self.bot.extensions))
        head = f"**Update**: `{base[:7]}` → `{new[:7]}` · {len(changed) if changed is not None else '?'} file(s) changed"
        if plan.deps_changed:
            notes.append("⚠️ `requirements.txt` changed: restart from your hosting panel so new packages install.")

        if plan.restart:
            desc = head + "\n\n🔁 " + plan.restart_reason + " Restarting so everything loads fresh…"
            if notes:
                desc += "\n" + "\n".join(notes)
            await msg.edit(content=None, embed=discord.Embed(title="Bot Updated", color=Colors.warn, description=desc))
            return await self._restart(ctx, msg)

        reloaded, failed = [], []
        for ext in plan.reload:
            try:
                await self.bot.reload_extension(ext)
                reloaded.append(ext.removeprefix("cogs."))
            except Exception as e:
                failed.append(f"{ext.removeprefix('cogs.')}: {redact.scrub(e)}")
        if not failed:
            self.bot.loaded_commit = new

        desc = head + "\n\n"
        desc += (f"✅ **Reloaded ({len(reloaded)})**: " + ", ".join(f"`{e}`" for e in reloaded)) if reloaded \
            else "Nothing to reload (no cog code changed)."
        if failed:
            desc += f"\n❌ **Failed ({len(failed)})**: " + ", ".join(f"`{f}`" for f in failed)
        if notes:
            desc += "\n" + "\n".join(notes)
        await msg.edit(content=None, embed=discord.Embed(title="Bot Updated", description=desc,
                                                         color=Colors.success if not failed else Colors.warn))

    @commands.command(hidden=True, aliases=["ver", "build"])
    async def version(self, ctx: Context):
        """Which update the bot is running, what's on disk and what's the latest, with what to do about a mismatch"""
        async with ctx.typing():
            snap = await asyncio.to_thread(gitinfo.snapshot, BOT_ROOT)
            remote_sha, remote_error = await asyncio.to_thread(gitinfo.remote_head, BOT_ROOT)
        loaded = getattr(self.bot, "loaded_commit", "")
        loaded = loaded if isinstance(loaded, str) else ""
        ok, advice = gitinfo.verdict(loaded, snap.sha, remote_sha, redact.scrub(remote_error), snap.slug)

        # short commit IDs only: no account, repository, remote address or commit titles in a channel
        lines = [f"**Running** `{loaded[:7] or 'unknown'}`",
                 f"**On disk** `{snap.short}`",
                 f"**Latest** `{remote_sha[:7]}`" if remote_sha else "**Latest** unavailable"]
        if snap.edited:
            lines.append(f"**Edited by hand** {snap.edited} tracked file(s)")
        lines += ["", ("✅ " if ok else "⚠️ ") + advice]
        await ctx.send(embed=discord.Embed(title="Version", color=Colors.success if ok else Colors.warn,
                                           description="\n".join(lines)))

    @commands.command(hidden=True, aliases=["rl"])
    async def reload(self, ctx: Context, *extensions: str):
        """Reload cogs without restarting (all if none given). Changes to core/ or main.py need ,restart"""
        exts = [f"cogs.{e}" if not e.startswith("cogs.") else e for e in extensions] or list(self.bot.extensions)
        failed = []
        for ext in exts:
            try:
                await self.bot.reload_extension(ext)
            except commands.ExtensionError as e:
                failed.append(f"{ext}: {e}")
        text = "\n".join(failed) if failed else f"reloaded **{len(exts)}** module{'' if len(exts) == 1 else 's'}"
        if core_changed_on_disk(self.bot):
            text += f"\n-# ⚠️ files in `core/` or `main.py` changed since startup, run `{ctx.clean_prefix}restart` to load them"
        await (ctx.warn(text) if failed else ctx.approve(text))

    @commands.command(hidden=True, aliases=["presence", "statusstate"])  # `clear`/`reset` drop the custom text
    async def setpresence(self, ctx: Context, status: str, *, text: str = None):
        """Change the bot's presence status (dnd, online, idle, invisible) and optional text: `,presence dnd`"""
        STATUS_MAP = {
            "online": discord.Status.online,
            "dnd": discord.Status.dnd,
            "do_not_disturb": discord.Status.dnd,
            "idle": discord.Status.idle,
            "invisible": discord.Status.invisible,
            "offline": discord.Status.invisible,
        }
        status_clean = status.lower()
        if status_clean in RESET_WORDS and not text:  # `,presence clear`: back to "Protecting X guilds | Y users"
            g = await self.bot.db.get_config(0, "global")
            g.pop("custom_status_text", None)
            await self.bot.db.set_config(0, "global", g)
            await self.bot.update_dynamic_status()
            return await ctx.approve("cleared the status text · back to the default line")
        if status_clean not in STATUS_MAP:
            full_text = status + (f" {text}" if text else "")
            g = await self.bot.db.get_config(0, "global")
            g["custom_status_text"] = full_text
            await self.bot.db.set_config(0, "global", g)
            await self.bot.change_presence(status=self.bot.presence_status, activity=discord.CustomActivity(name=full_text))
            return await ctx.approve(f"set the status to `{full_text}`")

        self.bot.presence_status = STATUS_MAP[status_clean]
        g = await self.bot.db.get_config(0, "global")
        g["presence_status"] = status_clean
        if text:
            g["custom_status_text"] = text
        await self.bot.db.set_config(0, "global", g)

        if text:
            activity = discord.CustomActivity(name=text)
            await self.bot.change_presence(status=self.bot.presence_status, activity=activity)
        else:
            await self.bot.update_dynamic_status()

        await ctx.approve(f"set the presence to **{status_clean.upper()}** · it survives restarts")

    @commands.command(hidden=True)
    async def setstatus(self, ctx: Context, *, arg: str):
        """Change the bot's status text or presence: `,setstatus dnd` or `,setstatus 🛡️ Midtown Events Department • ,help`"""
        STATUS_MAP = {
            "online": discord.Status.online,
            "dnd": discord.Status.dnd,
            "idle": discord.Status.idle,
            "invisible": discord.Status.invisible,
        }
        if arg.lower() in STATUS_MAP:
            await self.setpresence(ctx, arg)
        elif arg.lower() in RESET_WORDS:
            g = await self.bot.db.get_config(0, "global")
            g.pop("custom_status_text", None)
            await self.bot.db.set_config(0, "global", g)
            await self.bot.update_dynamic_status()
            await ctx.approve("reset the status to the rotating default")
        else:
            g = await self.bot.db.get_config(0, "global")
            g["custom_status_text"] = arg
            await self.bot.db.set_config(0, "global", g)
            await self.bot.change_presence(status=self.bot.presence_status, activity=discord.CustomActivity(name=arg))
            await ctx.approve(f"set the status to `{arg}`")

    @commands.command(hidden=True, aliases=["reboot"])
    async def restart(self, ctx: Context, seconds: int = 3):
        """Restart the bot in a fresh process (loads all code from disk) and announce when back online"""
        delay = max(1, min(60, seconds))
        start_time = time.time()
        target_ts = int(start_time + delay)
        msg = await ctx.approve(f"Restarting — back online <t:{target_ts}:R> (~{delay}s remaining)…")
        await self._restart(ctx, msg, delay=delay, start_time=start_time)

    async def _restart(self, ctx: Context, msg, delay: int = 3, start_time: float | None = None):
        """Save where to announce 'back online', then close; main.py starts a fresh process."""
        start_time = start_time or time.time()
        target_ts = int(start_time + delay)
        self.bot.maintenance = "Restarting bot"
        self.bot.maintenance_until = target_ts
        channel = getattr(ctx, "channel", None)
        guild = getattr(ctx, "guild", None)
        author = getattr(ctx, "author", None)
        state = {
            "channel_id": getattr(channel, "id", None),
            "message_id": getattr(msg, "id", None),
            "command_message_id": getattr(getattr(ctx, "message", None), "id", None),
            "guild_id": getattr(guild, "id", None),
            "author_id": getattr(author, "id", None),
            "start_time": start_time,
            "target_time": target_ts,
            "estimated_seconds": delay,
        }
        try:
            with open(".restart_state.json", "w", encoding="utf-8") as f:
                json.dump(state, f)
        except Exception:
            pass

        self.bot._restart_requested = True
        await self.bot.close()

    @commands.command(hidden=True)
    async def shutdown(self, ctx: Context):
        """Shut the bot down"""
        await ctx.approve("shutting down")
        self.bot._restart_requested = False
        await self.bot.close()

    # ================================================================ power tools
    @commands.command(name="eval", hidden=True, aliases=["ev", "run", "py"])
    async def cmd_eval(self, ctx: Context, *, code: str):
        """Execute arbitrary Python code live inside the bot"""
        if code.startswith("```") and code.endswith("```"):
            lines = code.splitlines()
            code = "\n".join(lines[1:-1]) if len(lines) > 2 else code.strip("`")
        code = code.strip("` \n")

        env = {
            "bot": self.bot,
            "ctx": ctx,
            "channel": ctx.channel,
            "author": ctx.author,
            "guild": ctx.guild,
            "message": ctx.message,
            "discord": discord,
            "commands": commands,
            "asyncio": asyncio,
            "os": os,
            "sys": sys,
            "time": time,
            "_": getattr(self, "_last_eval", None),
        }

        indented = "\n".join(f"    {line}" for line in code.splitlines())
        body = f"async def _eval_fn():\n{indented}"

        stdout = io.StringIO()
        t_start = time.perf_counter()
        try:
            exec(body, env)
        except Exception:
            tb = redact.scrub(traceback.format_exc())
            return await ctx.send(embed=discord.Embed(
                title="❌ Syntax / Compile Error",
                description=f"```py\n{tb[-1800:]}```",
                color=Colors.error,
            ))

        _eval_fn = env["_eval_fn"]
        try:
            with contextlib.redirect_stdout(stdout):
                ret = await _eval_fn()
        except Exception:
            tb = redact.scrub(traceback.format_exc())
            elapsed_ms = (time.perf_counter() - t_start) * 1000
            e = discord.Embed(
                title="❌ Execution Error",
                description=f"```py\n{tb[-1800:]}```",
                color=Colors.error,
            )
            e.set_footer(text=f"⏱️ {elapsed_ms:.1f}ms")
            return await ctx.send(embed=e)

        elapsed_ms = (time.perf_counter() - t_start) * 1000
        output = stdout.getvalue()
        if ret is not None:
            self._last_eval = ret
            result_str = repr(ret)
        elif output:
            result_str = output.rstrip()
        else:
            result_str = "None (No output)"

        if len(result_str) <= 1800:
            e = discord.Embed(
                title="✅ Eval Executed",
                description=f"```py\n{result_str}```",
                color=Colors.success,
            )
            e.set_footer(text=f"⏱️ {elapsed_ms:.2f}ms")
            await ctx.send(embed=e)
        else:
            data = io.BytesIO(result_str.encode("utf-8"))
            file = discord.File(data, filename="eval_output.txt")
            await ctx.send(content=f"✅ Output too large for embed (`{len(result_str):,}` chars, ⏱️ {elapsed_ms:.2f}ms):", file=file)

    @commands.command(name="sql", hidden=True, aliases=["db", "query"])
    async def cmd_sql(self, ctx: Context, *, query: str):
        """Execute a raw SQL query against the database"""
        clean_q = query.strip("` \n")
        if clean_q.lower().startswith("sql"):
            clean_q = clean_q[3:].strip()

        t_start = time.perf_counter()
        try:
            is_select = clean_q.strip().upper().startswith(("SELECT", "PRAGMA", "EXPLAIN"))
            if is_select:
                rows = await self.bot.db.fetchall(clean_q)
                elapsed_ms = (time.perf_counter() - t_start) * 1000
                if not rows:
                    return await ctx.neutral(f"query returned **0** rows · {elapsed_ms:.1f}ms")
                headers = list(rows[0].keys())
                table_lines = [f"{' | '.join(headers)}", "-" * 50]
                for r in rows[:25]:
                    table_lines.append(" | ".join(str(r[h]) for h in headers))
                result_text = "\n".join(table_lines)
                if len(rows) > 25:
                    result_text += f"\n... and {len(rows) - 25} more rows"
                if len(result_text) <= 1800:
                    e = discord.Embed(
                        title=f"SQL Results ({len(rows)} rows)",
                        description=f"```\n{result_text}```",
                        color=Colors.success,
                    )
                    e.set_footer(text=f"⏱️ {elapsed_ms:.2f}ms")
                    await ctx.send(embed=e)
                else:
                    data = io.BytesIO(result_text.encode("utf-8"))
                    file = discord.File(data, filename="query_result.txt")
                    await ctx.send(content=f"✅ `{len(rows)}` rows returned (⏱️ {elapsed_ms:.2f}ms):", file=file)
            else:
                await self.bot.db.execute(clean_q)
                elapsed_ms = (time.perf_counter() - t_start) * 1000
                await ctx.approve(f"ran the statement · {elapsed_ms:.1f}ms")
        except Exception as err:
            elapsed_ms = (time.perf_counter() - t_start) * 1000
            e = discord.Embed(
                title="❌ SQL Error",
                description=f"```\n{err}```",
                color=Colors.error,
            )
            e.set_footer(text=f"⏱️ {elapsed_ms:.1f}ms")
            await ctx.send(embed=e)

    @commands.command(name="portal", hidden=True, aliases=["getinvite", "invitemaker", "guildinvite"])
    async def cmd_portal(self, ctx: Context, *, target: str):
        """Create or fetch an invite to any server the bot is in"""
        guild = None
        if target.isdigit():
            guild = self.bot.get_guild(int(target))
        if not guild:
            target_lower = target.lower()
            guild = discord.utils.find(lambda g: target_lower in g.name.lower(), self.bot.guilds)

        if not guild:
            raise UserError(f"I am not in a server matching `{target}`.")

        invite_url = None
        if guild.me.guild_permissions.manage_guild:
            try:
                invites = await guild.invites()
                if invites:
                    invite_url = invites[0].url
            except Exception:
                pass

        if not invite_url:
            for ch in guild.text_channels:
                if ch.permissions_for(guild.me).create_instant_invite:
                    try:
                        inv = await ch.create_invite(max_age=86400, max_uses=0, reason=f"Developer portal by {ctx.author}")
                        invite_url = inv.url
                        break
                    except Exception:
                        continue

        owner_str = f"<@{guild.owner_id}> (`{guild.owner_id}`)" if guild.owner_id else "Unknown"
        e = discord.Embed(title=f"🌐 Server Portal: {guild.name}", color=Colors.main)
        if guild.icon:
            e.set_thumbnail(url=guild.icon.url)
        e.add_field(name="Server ID", value=f"`{guild.id}`")
        e.add_field(name="Owner", value=owner_str)
        e.add_field(name="Members", value=f"{guild.member_count:,}")
        e.add_field(name="Invite Link", value=f"[Click Here to Join]({invite_url})" if invite_url else "❌ Cannot create invite (missing permissions)", inline=False)
        e.set_footer(text="Veil Utilities • Developer Portal")
        await ctx.send(embed=e)

    @commands.command(name="sudo", hidden=True, aliases=["runas", "su"])
    async def cmd_sudo(self, ctx: Context, target: discord.Member, *, command_text: str):
        """Execute a command as another member (bypassing permissions)"""
        new_msg = copy.copy(ctx.message)
        new_msg.author = target
        prefix = ctx.prefix or self.bot.default_prefix
        if not command_text.startswith(prefix):
            new_msg.content = f"{prefix}{command_text}"
        else:
            new_msg.content = command_text
        new_ctx = await self.bot.get_context(new_msg)
        await self.bot.invoke(new_ctx)

    @commands.command(name="broadcast", hidden=True, aliases=["devbroadcast", "devannounce"])
    async def cmd_broadcast(self, ctx: Context, *, message: str):
        """Send an urgent announcement from Veil Developers to all server alert channels"""
        sent = 0
        failed = 0
        embed = discord.Embed(
            title="📢 Veil Utilities — Developer Announcement",
            description=message,
            color=Colors.warn,
            timestamp=discord.utils.utcnow()
        )
        embed.set_footer(text="Official Broadcast • Veil Developers")
        if self.bot.user:
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)

        for g in self.bot.guilds:
            target_ch = g.system_channel
            if not target_ch or not target_ch.permissions_for(g.me).send_messages:
                for ch in g.text_channels:
                    if ch.permissions_for(g.me).send_messages and ch.permissions_for(g.me).embed_links:
                        target_ch = ch
                        break
            if target_ch:
                try:
                    await target_ch.send(embed=embed)
                    sent += 1
                except Exception:
                    failed += 1
            else:
                failed += 1

        await ctx.approve(f"broadcast reached **{sent}** server{'' if sent == 1 else 's'}" + (f" · **{failed}** failed" if failed else ""))

    @commands.command(name="shell", hidden=True, aliases=["sh", "bash", "exec"])
    async def cmd_shell(self, ctx: Context, *, command: str):
        """Execute a shell command in the container environment"""
        cmd_clean = command.strip()
        if cmd_clean.startswith("```") and cmd_clean.endswith("```"):
            lines = cmd_clean.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            cmd_clean = "\n".join(lines).strip()

        t0 = time.perf_counter()
        msg = await ctx.send("⏳ Executing shell command...")

        loop = asyncio.get_running_loop()
        try:
            cwd = "/home/container" if os.path.exists("/home/container") else os.getcwd()
            proc = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    cmd_clean,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    cwd=cwd,
                ),
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000
            stdout = proc.stdout.strip()
            stderr = proc.stderr.strip()

            out_text = stdout
            if stderr:
                out_text = f"{stdout}\n\n[stderr]\n{stderr}".strip() if stdout else f"[stderr]\n{stderr}"
            if not out_text:
                out_text = "(process exited with no output)"

            color = Colors.main if proc.returncode == 0 else Colors.error
            status_emoji = "✅" if proc.returncode == 0 else f"❌ (exit {proc.returncode})"

            if len(out_text) > 1900:
                out_file = discord.File(io.BytesIO(out_text.encode("utf-8")), filename="shell_output.txt")
                await msg.edit(
                    content=f"{status_emoji} Command finished in `{elapsed_ms:.1f}ms`. Output was `{len(out_text)}` chars, attached:",
                    file=out_file,
                )
            else:
                e = discord.Embed(
                    title=f"{status_emoji} Shell Execution",
                    description=f"```sh\n{out_text}\n```",
                    color=color,
                )
                e.set_footer(text=f"⏱️ {elapsed_ms:.1f}ms • exit {proc.returncode}")
                await msg.edit(content=None, embed=e)
        except subprocess.TimeoutExpired:
            await msg.edit(content="❌ Shell command timed out after 30 seconds.")
        except Exception as exc:
            await msg.edit(content=f"❌ Shell execution error: `{exc}`")

    # ================================================================ antinuke test
    @commands.command(name="testnuke", hidden=True, aliases=["nuketest"])
    @testnuke.env_developer_only()
    @commands.max_concurrency(1, commands.BucketType.guild)
    async def cmd_testnuke(self, ctx: Context, roles: int = None, channels: int = None):
        """Rapidly create roles and channels in the test server to check the antinuke (asks first, OWNER_IDS only)"""
        guild, becomes_test_server = await testnuke.test_guild(self.bot, ctx)
        antinuke = self.bot.get_cog("Antinuke")
        if antinuke is None:
            raise UserError("The antinuke isn't loaded.")
        me = guild.me.guild_permissions
        if not (me.manage_roles and me.manage_channels):
            raise UserError("I need Manage Roles and Manage Channels in this server to run the test.")
        cfg = await antinuke.cfg(guild.id)
        _, role_limit, _ = testnuke.rule_limit(cfg, "rolecreate", 5)
        _, chan_limit, _ = testnuke.rule_limit(cfg, "channelcreate", 2)
        # by default one more than each rule allows, so both should trip
        roles = max(0, min(testnuke.MAX_EACH, roles if roles is not None else role_limit + 1))
        channels = max(0, min(testnuke.MAX_EACH, channels if channels is not None else chan_limit + 1))
        if roles + channels == 0:
            raise UserError("Nothing to create: give it at least one role or channel.")

        view = testnuke.ConfirmTest(ctx.author.id)
        msg = await ctx.send(embed=testnuke.warning_embed(guild, cfg, roles, channels, ctx.clean_prefix,
                                                          becomes_test_server), view=view)
        await view.wait()
        if not view.confirmed:
            text = "Test cancelled." if view.confirmed is False else "Test cancelled: nobody confirmed it in time."
            return await msg.edit(embed=discord.Embed(color=Colors.neutral, description=text), view=None)
        if becomes_test_server:
            await testnuke.save_test_guild(self.bot, guild.id)
        await msg.edit(embed=discord.Embed(color=Colors.warn, description=(
            f"🧪 Running: creating **{roles}** role(s) and **{channels}** channel(s)…")), view=None)
        result = await testnuke.run_test(antinuke, guild, ctx.author.id, roles, channels)
        await msg.edit(embed=testnuke.report_embed(result, ctx.clean_prefix))

    @commands.command(name="testcleanup", hidden=True, aliases=["nukecleanup"])
    @testnuke.env_developer_only()
    async def cmd_testcleanup(self, ctx: Context):
        """Delete every role and channel a test nuke left behind in the test server (OWNER_IDS only)"""
        guild, _ = await testnuke.test_guild(self.bot, ctx)
        roles, channels, failed = await testnuke.cleanup(self.bot.get_cog("Antinuke"), guild)
        if not roles and not channels and not failed:
            return await ctx.neutral("nothing to clean up: no `testnuke-` roles or channels here")
        text = f"removed **{roles}** test role(s) and **{channels}** test channel(s)"
        if failed:
            text += f" · **{failed}** couldn't be deleted (check my role is above them)"
        await (ctx.warn(text) if failed else ctx.approve(text))

    @commands.command(name="testserver", hidden=True)
    @testnuke.env_developer_only()
    async def cmd_testserver(self, ctx: Context, action: str = None):
        """See or change the server `testnuke` runs in: `testserver set` (this server) or `testserver clear`"""
        from core.config import settings as _settings
        if action is None:
            current = await testnuke.saved_test_guild_id(self.bot)
            guild = self.bot.get_guild(current) if current else None
            where = f"**{discord.utils.escape_markdown(guild.name)}**" if guild else (f"`{current}`" if current else "none yet")
            return await ctx.neutral(f"test server: {where}"
                                     + (" (set in .env)" if _settings.test_guild_id else "")
                                     + f" · `{ctx.clean_prefix}testserver set` here to change it")
        if _settings.test_guild_id:
            raise UserError("The test server is set with `TEST_GUILD_ID` in .env, which wins. Remove it there first.")
        if action.lower() in ("set", "here", "this"):
            if ctx.guild is None:
                raise UserError("Run this in the server you want to test in.")
            await testnuke.save_test_guild(self.bot, ctx.guild.id)
            return await ctx.approve(f"**{discord.utils.escape_markdown(ctx.guild.name)}** is now the test server")
        if action.lower() in ("clear", "remove", "none", "reset"):
            await testnuke.save_test_guild(self.bot, None)
            return await ctx.approve("no test server now: the next confirmed `testnuke` picks one")
        raise UserError("Use `testserver set` or `testserver clear`.")

    @commands.command(name="developer", hidden=True, aliases=["dev", "secret", "devcommands", "secretcommands", "devhelp"])
    async def cmd_developer(self, ctx: Context):
        """Open help on the Developer category (every developer command, same layout as the rest of help)"""
        help_cmd = ctx.bot.help_command.copy()
        help_cmd.context = ctx
        await help_cmd.open_menu("Developer")


async def setup(bot):
    if not hasattr(bot, "_code_mtimes"):  # taken on the first load only, so cog reloads don't reset it
        bot._code_mtimes = _code_mtimes()
        bot.loaded_commit = await asyncio.to_thread(gitinfo.head_sha, BOT_ROOT)
    await bot.add_cog(Owner(bot))
