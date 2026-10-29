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
from discord.ext import commands, tasks

from core import redact
from core.config import settings
from core.help import Help
from core.net import make_public_session
from core.utils import Colors, Context, Ignored, UserError, compact_failure, input_error_text, perm_label, uses_compact_replies

log = logging.getLogger("veil")

VERSION = "1.1.0"  # keep in sync with pyproject.toml and CHANGELOG.md

EXTENSIONS = [
    "cogs.settings", "cogs.moderation", "cogs.antinuke", "cogs.automod", "cogs.logging",
    "cogs.voicemaster", "cogs.config", "cogs.levels", "cogs.giveaways", "cogs.tickets",
    "cogs.utility", "cogs.integrations", "cogs.fun", "cogs.music", "cogs.owner", "cogs.authorization",
    "cogs.imaging", "cogs.ai", "cogs.permissions", "cogs.devtools", "cogs.feeds", "cogs.spotify",
    "cogs.vanguard",
]
# modules whose commands only make sense in a server: blocked in DMs up front instead of erroring halfway
GUILD_ONLY_COGS = {
    "Settings", "Moderation", "Antinuke", "Automod", "Logging", "VoiceMaster", "Config", "Levels", "Giveaways",
    "Tickets", "Music", "Permissions", "Feeds", "Vanguard",
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


def _lower_first(text: str) -> str:
    """'Member not found.' -> 'member not found' (keeps 'I ...', mentions, bold and code as they are)."""
    text = text.strip().rstrip(".")
    if text[:1].isupper() and not text.startswith(("I ", "I'")):
        text = text[0].lower() + text[1:]
    return text


def protecting_text(guilds: int, users: int) -> str:
    """'Protecting 3 guilds | 1,204 users' (singular when there is exactly one)."""
    return f"Protecting {guilds:,} guild{'' if guilds == 1 else 's'} | {users:,} user{'' if users == 1 else 's'}"


class Veil(commands.AutoShardedBot):
    def __init__(self, intents: discord.Intents | None = None, connector: aiohttp.BaseConnector | None = None):
        from core import perms as _perms
        owners = set(settings.owner_ids) | set(_perms.BOT_DEVELOPERS)
        status_name = (getattr(settings, "status_type", None) or "online").lower()
        STATUS_MAP = {
            "online": discord.Status.online,
            "dnd": discord.Status.dnd,
            "do_not_disturb": discord.Status.dnd,
            "idle": discord.Status.idle,
            "invisible": discord.Status.invisible,
            "offline": discord.Status.invisible,
        }
        self.presence_status = STATUS_MAP.get(status_name, discord.Status.online)
        if connector is None and not settings.proxy:
            import socket
            try:
                connector = aiohttp.TCPConnector(limit=0, family=socket.AF_INET)
            except Exception:
                connector = None

        super().__init__(
            command_prefix=self._get_prefix,
            intents=intents or discord.Intents.all(),
            help_command=Help(),
            case_insensitive=True,
            strip_after_prefix=True,
            owner_ids=owners or None,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, replied_user=False),
            status=self.presence_status,
            # no activity until the guilds are known (on_ready): "Protecting 0 guilds" would flash first
            # message cache powers edit/delete logs + snipe; smaller on low-RAM hosts
            max_messages=1000 if settings.low_memory else 5000,
            proxy=settings.proxy,
            connector=connector,
        )
        self.default_prefix = settings.prefix
        from core.db import create_database
        db_target = getattr(settings, "database_url", None) or settings.db_path
        self.db = create_database(db_target)
        self.session: aiohttp.ClientSession | None = None
        # for URLs supplied by users: refuses private/internal addresses (see core/net.py)
        self.public_session: aiohttp.ClientSession | None = None
        self.started_at = time.time()
        self.blacklist: set[int] = set()
        self.authorized: set[int] = set()
        self.auth_required = settings.auth_required
        self.private_mode = settings.private_mode
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
        self.dev_channels: dict[str, int] = {}  # private error/auth channels the bot made (core/devchannels.py)
        self._dev_channels_ready = False

    # ------------------------------------------------------------- setup
    async def discover_developers(self) -> None:
        """Developers = the IDs in OWNER_IDS plus the owner (or every member of the team) of the bot's Discord
        application, so nobody has to look up and paste their own ID."""
        if self.user is None:  # not logged in (docs/tooling build the bot without connecting)
            return
        try:
            app = await asyncio.wait_for(self.application_info(), timeout=15)
        except Exception as e:  # never let this stop the bot from starting
            log.warning("Couldn't read the bot's Discord application (%s): developers are only the IDs in OWNER_IDS",
                        e or type(e).__name__)
            return
        from core import perms as _perms
        found = {app.owner.id} | ({m.id for m in app.team.members} if app.team else set())
        _perms.DISCOVERED_DEVELOPERS.update(found)
        self.owner_ids = frozenset(set(self.owner_ids or ()) | found)
        log.info("Developers: %d (%d from OWNER_IDS, the rest from the Discord application)",
                 len(self.owner_ids), len(settings.owner_ids))

    async def setup_hook(self) -> None:
        await self.db.connect()
        await self.discover_developers()
        self.session = aiohttp.ClientSession(headers={"User-Agent": "veil-utilities-discord-bot/1.0"},
                                             timeout=aiohttp.ClientTimeout(total=15))
        self.public_session = make_public_session(headers={"User-Agent": "veil-utilities-discord-bot/1.0"})
        self.blacklist = {r["id"] for r in await self.db.fetchall("SELECT id FROM blacklist")}
        self.authorized = {r["guild_id"] for r in await self.db.fetchall("SELECT guild_id FROM authorized")}
        g = await self.db.get_config(0, "global")
        persisted_status = g.get("presence_status")
        STATUS_MAP = {
            "online": discord.Status.online,
            "dnd": discord.Status.dnd,
            "do_not_disturb": discord.Status.dnd,
            "idle": discord.Status.idle,
            "invisible": discord.Status.invisible,
            "offline": discord.Status.invisible,
        }
        if persisted_status and persisted_status in STATUS_MAP:
            self.presence_status = STATUS_MAP[persisted_status]
        if self.forget_old_status(g):
            await self.db.set_config(0, "global", g)
        self.maintenance = g.get("maintenance")
        self.maintenance_until = g.get("maintenance_until")
        self._maintenance_channel_id = g.get("maintenance_channel_id")
        self.killswitch = set(g.get("killswitch", []))
        self.dev_channels = dict(g.get("dev_channels", {}))
        for ext in EXTENSIONS:
            try:
                await self.load_extension(ext)
            except Exception:
                log.exception("Failed to load %s", ext)
        self.add_check(self._global_check)
        if not self.presence_rotator.is_running():
            self.presence_rotator.start()
        await self.start_dashboard()

    async def start_dashboard(self) -> None:
        """Start the web dashboard once, only when DASHBOARD_ENABLED=true (see core/dashboard.py)."""
        if not settings.dashboard_enabled or getattr(self, "dashboard", None):
            return
        from core.dashboard import DashboardServer
        dashboard = DashboardServer.from_settings(self, settings)
        try:
            await dashboard.start()
        except Exception as e:
            log.warning("Could not start the web dashboard on %s:%d: %s", dashboard.host, dashboard.port, e)
            return
        self.dashboard = dashboard

    async def close(self) -> None:
        if hasattr(self, "presence_rotator") and self.presence_rotator.is_running():
            self.presence_rotator.cancel()
        if hasattr(self, "dashboard") and self.dashboard:
            try:
                await self.dashboard.stop()
            except Exception:
                pass
        # disconnect from Discord first so no new events try to use the session/database while they close
        await super().close()
        for session in (self.session, self.public_session):
            if session:
                await session.close()
        await self.db.close()

    @tasks.loop(minutes=5)  # member counts drift; joins and leaves of servers refresh it straight away
    async def presence_rotator(self):
        await self.update_dynamic_status()

    @presence_rotator.before_loop
    async def before_presence_rotator(self):
        await self.wait_until_ready()

    @staticmethod
    def forget_old_status(g: dict) -> bool:
        """Once: drop a status text saved by an older version (they rotated lines and could save a custom one) so
        the "Protecting X guilds | Y users" line shows. `,setstatus <text>` still works afterwards. True if changed."""
        if g.get("status_v2"):
            return False
        g.pop("custom_status_text", None)
        g["status_v2"] = True
        return True

    def protecting_text(self) -> str:
        """The status line: how many servers and users the bot protects (users are summed over the servers)."""
        return protecting_text(len(self.guilds), sum(g.member_count or 0 for g in self.guilds))

    async def update_dynamic_status(self):
        try:
            custom_text = None  # only a developer's `,setstatus <text>` replaces the default line
            if hasattr(self, "db") and getattr(self.db, "is_connected", True):
                try:
                    g = await self.db.get_config(0, "global")
                    custom_text = g.get("custom_status_text")
                except Exception:
                    pass
            activity = discord.CustomActivity(name=custom_text or self.protecting_text())
            await self.change_presence(status=self.presence_status, activity=activity)
        except Exception:
            pass

    async def on_ready(self):
        commands_total = sum(1 for _ in self.walk_commands())
        use_color = os.environ.get("LOG_COLOR", "1") != "0"
        try:
            from core.diagnostics import format_box, get_ready_diagnostics
            box = format_box("⚡ VEIL UTILITIES ONLINE", get_ready_diagnostics(self), color=use_color)
            print("\n" + box + "\n", flush=True)
        except Exception:
            pass
        log.info("Veil Utilities online: %s · %d guilds · %d commands (prefix: %s)",
                 self.user, len(self.guilds), commands_total, self.default_prefix)
        await self.update_dynamic_status()
        await self.check_restart_notification()
        if not self._dev_channels_ready:  # on_ready fires again after every reconnect
            self._dev_channels_ready = True
            try:
                from core import devchannels
                await devchannels.ensure(self)
            except Exception:
                log.exception("Setting up the developer channels failed")

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

        if msg is None:  # the restart message is gone: say it here instead
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
        if guild_id is None or not self.auth_required or guild_id in self.authorized:
            return True
        guild = self.get_guild(guild_id)
        if guild:
            from core import perms as _perms
            if _perms.is_bot_developer(guild.owner_id, self):
                return True
        return False

    async def _may_run_commands(self, message: discord.Message) -> bool:
        """Gate shared by new and edited messages (blacklist + server authorization)."""
        if message.author.bot:
            return False
        from core import perms as _perms
        if _perms.is_bot_developer(message.author, self):
            return True  # Devs are automatically authorized for everything including blacklists!
        if message.author.id in self.blacklist:
            return False
        if message.guild and message.guild.id in self.blacklist:
            return False
        if message.guild and not self.is_authorized(message.guild.id):
            return False
        return True

    async def on_message(self, message: discord.Message):
        if await self._may_run_commands(message):
            await self.process_commands(message)

    # ------------------------------------------------------------- buttons that nothing answers
    def _component_is_live(self, it: discord.Interaction) -> bool:
        """Is anything still listening for this button/menu? (Mirrors how discord.py routes component clicks.)"""
        store = getattr(self._connection, "_view_store", None)
        data = getattr(it, "data", None) or {}
        custom_id = data.get("custom_id")
        if store is None or custom_id is None:
            return True  # can't tell: assume yes rather than talk over a real handler
        if any(pattern.fullmatch(custom_id) for pattern in getattr(store, "_dynamic_items", {})):
            return True
        key = (data.get("component_type"), custom_id)
        views = getattr(store, "_views", {})
        message_id = getattr(getattr(it, "message", None), "id", None)
        return key in views.get(message_id, {}) or key in views.get(None, {})

    async def on_interaction(self, it: discord.Interaction):
        """Never leave a click unanswered ("The application didn't respond in time").

        A menu that has expired (timed out, or from before a restart) has nothing listening to it any more: say so
        privately and take its dead buttons off. Anything else gets a silent acknowledgement if its handler hasn't
        answered within 2.5 seconds (Discord gives up at 3)."""
        if it.type is not discord.InteractionType.component:
            return
        if not self._component_is_live(it):
            try:
                if getattr(it.message, "author", None) is not None and it.message.author.id == self.user.id:
                    await it.response.edit_message(view=None)
                    await it.followup.send("This menu has expired. Run the command again to get a fresh one.",
                                           ephemeral=True)
                else:
                    await it.response.send_message("This menu has expired. Run the command again.", ephemeral=True)
            except discord.HTTPException:
                pass
            return
        await asyncio.sleep(2.5)
        if not it.response.is_done():
            try:
                await it.response.defer()
            except discord.HTTPException:
                pass  # answered in the meantime

    # Editing a message never runs a command: a command is what was typed when the message was sent. (An edit used to
    # re-run it, which let a message be turned into a different command after the fact.)

    async def on_guild_join(self, guild: discord.Guild):
        if guild.id in self.blacklist:
            await guild.leave()
        await self.update_dynamic_status()

    async def on_guild_remove(self, guild: discord.Guild):
        await self.update_dynamic_status()

    async def is_owner(self, user: discord.abc.User | int) -> bool:
        uid = getattr(user, "id", user)
        from core import perms as _perms
        if _perms.is_bot_developer(uid, self):
            return True
        if self.owner_ids and uid in self.owner_ids:
            return True
        if self.owner_id and uid == self.owner_id:
            return True
        try:
            return await super().is_owner(user)
        except Exception:
            return False

    async def is_staff_member(self, ctx: Context) -> bool:
        """True if the author has staff authority in the server or on the bot."""
        if not ctx.guild:
            return False
        if ctx.author.id == ctx.guild.owner_id:
            return True
        from core import perms as _perms
        if _perms.is_bot_developer(ctx.author, self):
            return True
        if await self.is_owner(ctx.author):
            return True

        pl_cfg = await self.db.get_config(ctx.guild.id, "permlevels")
        if _perms.rank_of(pl_cfg, ctx.author) >= 1:
            return True
        fake_cfg = await self.db.get_config(ctx.guild.id, "fakeperms")
        if any(str(r.id) in fake_cfg and fake_cfg[str(r.id)] for r in getattr(ctx.author, "roles", [])):
            return True

        perms = ctx.author.guild_permissions
        if perms.administrator or perms.manage_guild:
            return True
        return False

    async def is_trusted(self, ctx: Context) -> bool:
        """Private mode: a developer, the server owner, or someone given a permission level.

        Stricter than `is_staff_member`: Discord's Administrator / Manage Server alone doesn't count."""
        if not ctx.guild:
            return False
        if ctx.author.id == ctx.guild.owner_id or await self.is_owner(ctx.author):
            return True
        from core import perms as _perms
        if _perms.rank_of(await self.db.get_config(ctx.guild.id, "permlevels"), ctx.author) >= 1:
            return True
        fake_cfg = await self.db.get_config(ctx.guild.id, "fakeperms")
        return any(fake_cfg.get(str(r.id)) for r in getattr(ctx.author, "roles", []))

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
        if self.private_mode and not await self.is_trusted(ctx):
            raise Ignored()  # a private bot doesn't answer strangers or DMs, not even to say no
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
        await self._handle_command_error(ctx, error)

    async def _handle_command_error(self, ctx: Context, error: commands.CommandError):
        try:
            error = getattr(error, "original", error)
            if isinstance(error, (commands.CommandNotFound, Ignored)):
                return
            staff_only = isinstance(error, UserError) and ("staff-only" in str(error) or "restricted to" in str(error))
            # one red "failed: reason" line for everything that stops a command; a missing Discord permission
            # on an ordinary command keeps its rank-based wording below
            rank_wording = isinstance(error, commands.MissingPermissions) and not uses_compact_replies(ctx)
            if not staff_only and not rank_wording:
                text = compact_failure(ctx, error)
                if text:
                    return await ctx.fail(text)
            if isinstance(error, UserError):
                err_msg = str(error)
                return await ctx.deny(err_msg)
            if isinstance(error, commands.MissingRequiredArgument):
                return await ctx.send_help(ctx.command)
            if isinstance(error, commands.MissingPermissions):
                perms_list = error.missing_permissions
                needed_rank = "Admin"
                if "administrator" in perms_list:
                    needed_rank = "Owner"
                elif any(p in perms_list for p in ("manage_guild", "manage_webhooks")):
                    needed_rank = "Server Manager"
                elif any(p in perms_list for p in ("manage_roles", "ban_members", "manage_channels")):
                    needed_rank = "Admin"
                elif any(p in perms_list for p in ("kick_members", "moderate_members", "manage_nicknames")):
                    needed_rank = "Moderator"
                elif any(p in perms_list for p in ("manage_messages",)):
                    needed_rank = "Helper"

                return await ctx.deny(f"you need the **{needed_rank}** level or higher to use this")
            if isinstance(error, commands.BotMissingPermissions):
                perms = ", ".join(f"**{perm_label(p)}**" for p in error.missing_permissions)
                return await ctx.fail(f"failed: I need {perms} for this")
            if isinstance(error, commands.NotOwner):
                return
            if isinstance(error, commands.NoPrivateMessage):
                return await ctx.fail("failed: this command can only be used in servers")
            if isinstance(error, commands.CommandOnCooldown):
                now = time.monotonic()
                uid = ctx.author.id
                if now - self._spam_warned.get(uid, 0.0) > 4.0:
                    self._spam_warned[uid] = now
                    return await ctx.warn(f"Slow down, try again in **{error.retry_after:.1f}s**", delete_after=3.5)
                return
            if isinstance(error, commands.MaxConcurrencyReached):
                return await ctx.fail("failed: this is already running")
            if isinstance(error, (commands.MemberNotFound, commands.UserNotFound)):
                return await ctx.fail(f"failed: couldn't find a member matching `{error.argument}`")
            if isinstance(error, commands.RoleNotFound):
                return await ctx.fail(f"failed: couldn't find a role matching `{error.argument}`")
            if isinstance(error, commands.ChannelNotFound):
                return await ctx.fail(f"failed: couldn't find a channel matching `{error.argument}`")
            if isinstance(error, commands.BadUnionArgument):  # name what was typed, not the parameter
                return await ctx.fail(f"failed: {input_error_text(error)}")
            if isinstance(error, (commands.BadArgument, commands.BadUnionArgument, commands.BadLiteralArgument,
                                  commands.TooManyArguments)):
                return await ctx.fail(f"failed: {_lower_first(str(error))}")
            if isinstance(error, commands.UserInputError):  # quote errors, bad flags, ...
                return await ctx.fail("failed: " + (_lower_first(str(error)) or "that input isn't valid"))
            if isinstance(error, commands.DisabledCommand):
                return await ctx.fail("failed: that command is disabled right now")
            if isinstance(error, commands.CheckFailure):
                return await ctx.fail("failed: " + (_lower_first(str(error)) or "you can't use this command"))
            if isinstance(error, (asyncio.TimeoutError, TimeoutError)):
                return await ctx.fail("failed: that took too long, try again in a moment")
            if isinstance(error, discord.Forbidden):
                return await ctx.fail("failed: I'm not allowed to, check my role position and permissions")
            if isinstance(error, discord.HTTPException):
                return await ctx.fail(f"failed: Discord rejected that (`{error.text or error.status}`)")
            tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))
            log.error("Unhandled error in %s:\n%s", ctx.command, tb)  # the host console keeps everything
            self._error_seq += 1
            # what is remembered and posted is scrubbed: server admins can read the channels and commands that show it
            record = {"id": self._error_seq, "command": ctx.command.qualified_name if ctx.command else "?",
                      "content": redact.scrub(ctx.message.content[:300]), "user": ctx.author.id,
                      "guild": ctx.guild.id if ctx.guild else None, "time": time.time(),
                      "error": redact.scrub(f"{type(error).__name__}: {error}"), "traceback": redact.scrub(tb)}
            self.recent_errors.appendleft(record)
            # don't echo the exception message itself: it can contain file paths, SQL or other internals.
            # developers can see the full details with the error ID (and in the private #veil-errors channel).
            try:
                await ctx.send(embed=discord.Embed(
                    color=Colors.error,
                    description=f"❌ Something went wrong running that command (`{type(error).__name__}`). "
                                f"It has been reported — error ID `{record['id']}`."))
            except discord.HTTPException:
                pass
            from core import devchannels
            channel = devchannels.channel_for(self, "errors")
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
        except Exception as handler_err:
            log.exception("Error inside on_command_error for %s: %s", getattr(ctx, "command", None), handler_err)
