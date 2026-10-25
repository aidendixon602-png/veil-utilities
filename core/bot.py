"""The bot class: prefixes, aliases, global checks, error handling."""
from __future__ import annotations

import asyncio
import copy
from collections import defaultdict, deque
import json
import logging
import os
import time
import traceback

import aiohttp
import discord
from discord.ext import commands

from core.config import settings
from core.db import Database
from core.help import Help
from core.net import make_public_session
from core.utils import Colors, Context, Ignored, UserError

log = logging.getLogger("veil")

VERSION = "1.1.0"  # keep in sync with pyproject.toml and CHANGELOG.md

EXTENSIONS = [
    "cogs.settings", "cogs.moderation", "cogs.antinuke", "cogs.automod", "cogs.logging",
    "cogs.voicemaster", "cogs.config", "cogs.levels", "cogs.giveaways", "cogs.tickets",
    "cogs.utility", "cogs.integrations", "cogs.fun", "cogs.music", "cogs.owner", "cogs.authorization",
    "cogs.imaging", "cogs.ai", "cogs.permissions", "cogs.devtools", "cogs.feeds", "cogs.spotify",
]
# modules whose commands only make sense in a server: blocked in DMs up front instead of erroring halfway
GUILD_ONLY_COGS = {
    "Settings", "Moderation", "Antinuke", "Automod", "Logging", "VoiceMaster", "Config", "Levels", "Giveaways",
    "Tickets", "Music", "Permissions", "Feeds",
}


INTENT_HELP = ("Turn them on at https://discord.com/developers/applications -> your bot -> Bot -> "
               "Privileged Gateway Intents (Presence, Server Members, Message Content), then restart.")


def intent_plans():
    """Try every intent first; if Discord refuses privileged ones that aren't switched on, drop them one at a
    time so the bot still starts (with fewer features) instead of crashing in a restart loop."""
    full = discord.Intents.all()
    no_presence = discord.Intents.all()
    no_presence.presences = False
    minimal = discord.Intents.all()
    minimal.presences = minimal.members = False
    bare = discord.Intents.all()
    bare.presences = bare.members = bare.message_content = False
    return [
        (full, None),
        (no_presence, "Presence Intent is off: Spotify 'listening to' and status-based features are disabled."),
        (minimal, "Server Members Intent is off: welcome messages, autoroles, antiraid and member counters "
                  "won't work, and member lookups are limited."),
        (bare, "Message Content Intent is off: prefix commands won't work — only commands that @mention the "
               "bot (e.g. '@veil help') will. Automod and snipe are disabled."),
    ]


class Veil(commands.AutoShardedBot):
    def __init__(self, intents: discord.Intents | None = None):
        owners = set(settings.owner_ids)
        super().__init__(
            command_prefix=self._get_prefix,
            intents=intents or discord.Intents.all(),
            help_command=Help(),
            case_insensitive=True,
            strip_after_prefix=True,
            owner_ids=owners or None,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, replied_user=False),
            activity=discord.Activity(type=discord.ActivityType.watching, name=settings.status),
            # message cache powers edit/delete logs + snipe; smaller on low-RAM hosts
            max_messages=1000 if settings.low_memory else 5000,
        )
        self.default_prefix = settings.prefix
        self.db = Database(settings.db_path)
        self.session: aiohttp.ClientSession | None = None
        # for URLs supplied by users: refuses private/internal addresses (see core/net.py)
        self.public_session: aiohttp.ClientSession | None = None
        self.started_at = time.time()
        self.blacklist: set[int] = set()
        self.authorized: set[int] = set()
        self.auth_required = settings.auth_required
        # developer controls (persisted in config under guild 0, module "global")
        self.maintenance: str | None = None
        self.maintenance_until: float | None = None
        self._maintenance_channel_id: int | None = None
        self.killswitch: set[str] = set()
        self.recent_errors: deque = deque(maxlen=50)
        self._error_seq = 0
        self._cmd_cooldowns: dict[int, list[float]] = defaultdict(list)
        self._spam_warned: dict[int, float] = {}
        self._restart_requested: bool = False

    # ------------------------------------------------------------- setup
    async def setup_hook(self) -> None:
        await self.db.connect()
        self.session = aiohttp.ClientSession(headers={"User-Agent": "veil-utilities-discord-bot/1.0"},
                                             timeout=aiohttp.ClientTimeout(total=15))
        self.public_session = make_public_session(headers={"User-Agent": "veil-utilities-discord-bot/1.0"})
        self.blacklist = {r["id"] for r in await self.db.fetchall("SELECT id FROM blacklist")}
        self.authorized = {r["guild_id"] for r in await self.db.fetchall("SELECT guild_id FROM authorized")}
        g = await self.db.get_config(0, "global")
        self.maintenance = g.get("maintenance")
        self.maintenance_until = g.get("maintenance_until")
        self._maintenance_channel_id = g.get("maintenance_channel_id")
        self.killswitch = set(g.get("killswitch", []))
        for ext in EXTENSIONS:
            try:
                await self.load_extension(ext)
            except Exception:
                log.exception("Failed to load %s", ext)
        self.add_check(self._global_check)

    async def close(self) -> None:
        # disconnect from Discord first so no new events try to use the session/database while they close
        await super().close()
        for session in (self.session, self.public_session):
            if session:
                await session.close()
        await self.db.close()

    async def update_dynamic_status(self):
        try:
            users = sum(g.member_count or 0 for g in self.guilds)
            guilds = len(self.guilds)
            status_template = settings.status
            if not status_template or status_template == "veil utilities | ,help":
                text = f"Guarding {users:,} users across {guilds} guilds • Watching over the community"
            else:
                try:
                    text = status_template.format(users=users, guilds=guilds)
                except Exception:
                    text = status_template
            activity = discord.CustomActivity(name=text)
            await self.change_presence(activity=activity)
        except Exception:
            pass

    async def on_ready(self):
        commands_total = sum(1 for _ in self.walk_commands())
        log.info("──────────────────────────────────────────")
        log.info(" veil utilities v%s", VERSION)
        log.info(" %s · %d guilds · %d commands", self.user, len(self.guilds), commands_total)
        log.info(" prefix %s · %shelp for the menu", self.default_prefix, self.default_prefix)
        log.info("──────────────────────────────────────────")
        await self.update_dynamic_status()
        await self.check_restart_notification()

    async def check_restart_notification(self):
        state_file = ".restart_state.json"
        if not os.path.exists(state_file):
            return
        state = None
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            state = None
        finally:
            try:
                os.remove(state_file)
            except OSError:
                pass

        if not state:
            return

        # Clear temporary restart maintenance
        if self.maintenance == "Restarting bot":
            self.maintenance = None
            self.maintenance_until = None

        channel_id = state.get("channel_id")
        message_id = state.get("message_id")
        start_time = state.get("start_time", time.time())
        duration = max(0.1, time.time() - start_time)

        channel = self.get_channel(channel_id) if channel_id else None
        if not channel and channel_id:
            try:
                channel = await self.fetch_channel(channel_id)
            except Exception:
                channel = None

        if not channel:
            return

        msg = None
        if message_id:
            try:
                msg = await channel.fetch_message(message_id)
            except Exception:
                msg = None

        online_desc = f"✅ **Back online!** Restart took `{duration:.1f}s`."
        if msg:
            try:
                embed = msg.embeds[0] if msg.embeds else discord.Embed(color=Colors.success)
                embed.description = online_desc
                embed.color = Colors.success
                if embed.footer and embed.footer.text:
                    p = embed.footer.text.split("·")[0].strip() if "·" in embed.footer.text else ",restart"
                    embed.set_footer(text=f"{p} · Bot Dev · Back online in {duration:.1f}s")
                await msg.edit(embed=embed)
            except Exception:
                pass

        try:
            await channel.send(f"✅ **Veil Utilities is back online!** (Restart completed in `{duration:.1f}s`)")
        except Exception:
            pass

    # ------------------------------------------------------------- prefixes
    async def _get_prefix(self, bot, message: discord.Message):
        prefixes = []
        if message.guild:
            settings = await self.db.get_config(message.guild.id, "settings")
            prefixes.append(settings.get("prefix", self.default_prefix))
        else:
            prefixes.append(self.default_prefix)
        own = await self.db.get_selfprefix(message.author.id)
        if own:
            prefixes.insert(0, own)
        return commands.when_mentioned_or(*prefixes)(bot, message)

    async def get_context(self, origin, /, *, cls=Context):
        ctx = await super().get_context(origin, cls=cls)
        # custom aliases: ,alias add b ban  ->  ,b @user  runs  ,ban @user
        if ctx.command is None and ctx.invoked_with and ctx.guild and isinstance(origin, discord.Message):
            aliases = await self.db.get_config(ctx.guild.id, "aliases")
            target = aliases.get(ctx.invoked_with.lower())
            if target:
                body = origin.content[len(ctx.prefix):].lstrip()
                rest = body[len(ctx.invoked_with):]
                msg = copy.copy(origin)
                msg.content = f"{ctx.prefix}{target}{rest}"
                ctx = await super().get_context(msg, cls=cls)
        return ctx

    def is_authorized(self, guild_id: int | None) -> bool:
        return guild_id is None or not self.auth_required or guild_id in self.authorized

    async def _may_run_commands(self, message: discord.Message) -> bool:
        """Gate shared by new and edited messages (blacklist + server authorization)."""
        if message.author.bot or message.author.id in self.blacklist:
            return False
        if message.guild and message.guild.id in self.blacklist:
            return False
        if message.guild and not self.is_authorized(message.guild.id) and not await self.is_owner(message.author):
            return False
        if message.guild and hasattr(message.author, "roles"):
            from core import perms as _perms
            if not (message.author.id == getattr(message.guild, "owner_id", None) or message.author.id in _perms.BOT_DEVELOPERS):
                rbl = await self.db.get_config(message.guild.id, "roleblacklist")
                b_ids = set(rbl.get("roles", []))
                if b_ids and any(getattr(r, "id", None) in b_ids for r in message.author.roles):
                    return False
        return True

    async def on_message(self, message: discord.Message):
        if await self._may_run_commands(message):
            await self.process_commands(message)

    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        # re-run commands that were fixed within 60s (same gate as on_message, or edits would bypass the blacklist)
        if before.content == after.content or (discord.utils.utcnow() - after.created_at).total_seconds() >= 60:
            return
        if await self._may_run_commands(after):
            await self.process_commands(after)

    async def on_guild_join(self, guild: discord.Guild):
        if guild.id in self.blacklist:
            await guild.leave()
        await self.update_dynamic_status()

    async def on_guild_remove(self, guild: discord.Guild):
        await self.update_dynamic_status()

    async def is_staff_member(self, ctx: Context) -> bool:
        """True if the author has staff authority in the server or on the bot."""
        if not ctx.guild:
            return False
        if ctx.author.id == ctx.guild.owner_id:
            return True
        if await self.is_owner(ctx.author):
            return True
        perms = ctx.author.guild_permissions
        if perms.administrator or perms.manage_guild or perms.manage_messages or perms.moderate_members:
            return True
        from core import perms as _perms
        pl_cfg = await self.db.get_config(ctx.guild.id, "permlevels")
        if _perms.rank_of(pl_cfg, ctx.author) >= 1:
            return True
        fake_cfg = await self.db.get_config(ctx.guild.id, "fakeperms")
        if any(str(r.id) in fake_cfg and fake_cfg[str(r.id)] for r in ctx.author.roles):
            return True
        return False

    # ------------------------------------------------------------- checks
    async def _global_check(self, ctx: Context) -> bool:
        if ctx.guild is None and ctx.cog is not None and ctx.cog.qualified_name in GUILD_ONLY_COGS:
            raise commands.NoPrivateMessage()
        # hidden commands are developer-only, no exceptions: pretend they don't exist so
        # guessing a command's name reveals nothing. Runs before every other check.
        cmd = ctx.command
        if cmd is not None and (cmd.hidden or any(p.hidden for p in cmd.parents)) \
                and not await self.is_owner(ctx.author):
            raise commands.CommandNotFound()
        if await self.is_owner(ctx.author):
            return True
        if self.maintenance is not None:
            now = time.time()
            until = getattr(self, "maintenance_until", None)
            if until and until > now:
                remaining = max(1, int(until - now))
                time_str = f" (<t:{int(until)}:R> / ~{remaining}s remaining)"
                raise UserError(f"🛠️ The bot is under maintenance right now{time_str}"
                                + (f": **{self.maintenance}**" if self.maintenance else "") + ". Try again soon.")
            elif until and now >= until:
                self.maintenance = None
                self.maintenance_until = None
            else:
                raise UserError("🛠️ The bot is under maintenance right now"
                                + (f": **{self.maintenance}**" if self.maintenance else "") + ". Try again soon.")
        root_name = ctx.command.root_parent.name if ctx.command.root_parent else ctx.command.name
        if ctx.command.qualified_name in self.killswitch or root_name in self.killswitch:
            raise UserError(f"`{ctx.command.qualified_name}` has been temporarily disabled by the developers")
        if ctx.guild is None:
            return True
        if not self.is_authorized(ctx.guild.id):
            raise Ignored()
        settings = await self.db.get_config(ctx.guild.id, "settings")
        admin = ctx.author.guild_permissions.administrator
        if not admin and (ctx.channel.id in settings.get("ignored_channels", [])
                          or ctx.author.id in settings.get("ignored_users", [])):
            raise Ignored()

        # staff-only mode & bot-channels restrictions
        staff_only = settings.get("staff_only")
        allowed_channels = settings.get("bot_channels", [])
        channel_restricted = bool(allowed_channels and ctx.channel.id not in allowed_channels)

        if staff_only or channel_restricted:
            is_staff = await self.is_staff_member(ctx)
            if staff_only and not is_staff:
                raise UserError("🔒 The bot is currently in **staff-only** mode in this server.")
            if channel_restricted and not is_staff:
                channel_list = ", ".join(f"<#{c}>" for c in allowed_channels)
                raise UserError(f"Commands in this server are restricted to {channel_list}")

        # anti-spam command cooldown for regular users to prevent chat floods
        if ctx.guild and not await self.is_staff_member(ctx):
            now = time.monotonic()
            uid = ctx.author.id
            history = [t for t in self._cmd_cooldowns[uid] if now - t < 5.0]
            self._cmd_cooldowns[uid] = history
            if len(history) >= 4:
                last_warn = self._spam_warned.get(uid, 0.0)
                if now - last_warn > 8.0:
                    self._spam_warned[uid] = now
                    try:
                        await ctx.send("⏳ **Slow down!** You are sending commands too quickly.", delete_after=3.5)
                    except discord.HTTPException:
                        pass
                raise Ignored()
            history.append(now)

        disabled = settings.get("disabled", [])
        root = ctx.command.root_parent.name if ctx.command.root_parent else ctx.command.name
        if ctx.command.qualified_name in disabled or root in disabled:
            if not admin:
                raise UserError(f"`{ctx.command.qualified_name}` is disabled in this server")
        return True

    # ------------------------------------------------------------- errors
    async def on_command_error(self, ctx: Context, error: commands.CommandError):
        error = getattr(error, "original", error)
        if isinstance(error, (commands.CommandNotFound, Ignored)):
            return
        if isinstance(error, UserError):
            err_msg = str(error)
            del_after = 5.0 if ("staff-only" in err_msg or "restricted to" in err_msg) else None
            return await ctx.warn(err_msg, delete_after=del_after)
        if isinstance(error, commands.MissingRequiredArgument):
            return await ctx.send_help(ctx.command)
        if isinstance(error, commands.MissingPermissions):
            perms = ", ".join(f"`{p.replace('_', ' ')}`" for p in error.missing_permissions)
            return await ctx.warn(f"You're missing {perms} permission")
        if isinstance(error, commands.BotMissingPermissions):
            perms = ", ".join(f"`{p.replace('_', ' ')}`" for p in error.missing_permissions)
            return await ctx.warn(f"I'm missing {perms} permission")
        if isinstance(error, commands.NotOwner):
            return
        if isinstance(error, commands.NoPrivateMessage):
            return await ctx.warn("This command can only be used in servers")
        if isinstance(error, commands.CommandOnCooldown):
            now = time.monotonic()
            uid = ctx.author.id
            if now - self._spam_warned.get(uid, 0.0) > 4.0:
                self._spam_warned[uid] = now
                return await ctx.warn(f"Slow down, try again in **{error.retry_after:.1f}s**", delete_after=3.5)
            return
        if isinstance(error, commands.MaxConcurrencyReached):
            return await ctx.warn("This command is already running")
        if isinstance(error, (commands.MemberNotFound, commands.UserNotFound)):
            return await ctx.warn(f"I couldn't find a user matching `{error.argument}`")
        if isinstance(error, commands.RoleNotFound):
            return await ctx.warn(f"I couldn't find a role matching `{error.argument}`")
        if isinstance(error, commands.ChannelNotFound):
            return await ctx.warn(f"I couldn't find a channel matching `{error.argument}`")
        if isinstance(error, (commands.BadArgument, commands.BadUnionArgument, commands.BadLiteralArgument,
                              commands.TooManyArguments)):
            return await ctx.warn(str(error))
        if isinstance(error, commands.UserInputError):  # quote errors, bad flags, ...
            return await ctx.warn(str(error) or "That input isn't valid")
        if isinstance(error, commands.DisabledCommand):
            return await ctx.warn("That command is disabled right now")
        if isinstance(error, commands.CheckFailure):
            return await ctx.warn(str(error) or "You can't use this command")
        if isinstance(error, (asyncio.TimeoutError, TimeoutError)):
            return await ctx.warn("That took too long, try again in a moment")
        if isinstance(error, discord.Forbidden):
            return await ctx.warn("I don't have permission to do that (check my role position & permissions)")
        if isinstance(error, discord.HTTPException):
            return await ctx.warn(f"Discord rejected that: `{error.text or error.status}`")
        tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        log.error("Unhandled error in %s:\n%s", ctx.command, tb)
        self._error_seq += 1
        record = {"id": self._error_seq, "command": ctx.command.qualified_name if ctx.command else "?",
                  "content": ctx.message.content[:300], "user": ctx.author.id,
                  "guild": ctx.guild.id if ctx.guild else None, "time": time.time(),
                  "error": f"{type(error).__name__}: {error}", "traceback": tb}
        self.recent_errors.appendleft(record)
        # don't echo the exception message itself: it can contain file paths, SQL or other internals.
        # developers can see the full details with the error ID (and in ERROR_LOG_CHANNEL).
        try:
            await ctx.send(embed=discord.Embed(
                color=Colors.error,
                description=f"❌ Something went wrong running that command (`{type(error).__name__}`). "
                            f"It has been reported — error ID `{record['id']}`."))
        except discord.HTTPException:
            pass
        channel = self.get_channel(settings.error_log_channel or 0)
        if channel:
            e = discord.Embed(color=Colors.error, title=f"Error #{record['id']} in {record['command']}",
                              description=f"```py\n{tb[-3900:]}```", timestamp=discord.utils.utcnow())
            e.add_field(name="Message", value=record["content"] or "—", inline=False)
            e.add_field(name="User", value=f"<@{record['user']}>")
            e.add_field(name="Server", value=f"`{record['guild']}`")
            try:
                await channel.send(embed=e)
            except discord.HTTPException:
                pass
