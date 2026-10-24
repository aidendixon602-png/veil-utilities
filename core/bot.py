"""The bot class: prefixes, aliases, global checks, error handling."""
from __future__ import annotations

import copy
from collections import deque
import logging
import os
import time
import traceback

import aiohttp
import discord
from discord.ext import commands

from core.db import Database
from core.help import Help
from core.utils import Colors, Context, Ignored, UserError

log = logging.getLogger("veil")

EXTENSIONS = [
    "cogs.settings", "cogs.moderation", "cogs.antinuke", "cogs.automod", "cogs.logging",
    "cogs.voicemaster", "cogs.config", "cogs.levels", "cogs.giveaways", "cogs.tickets",
    "cogs.utility", "cogs.integrations", "cogs.fun", "cogs.music", "cogs.owner", "cogs.authorization",
    "cogs.imaging", "cogs.ai", "cogs.permissions", "cogs.devtools",
]
# turned off for now — add back to EXTENSIONS to re-enable: "cogs.feeds" (social feeds), "cogs.spotify"


class Veil(commands.AutoShardedBot):
    def __init__(self):
        owners = {int(x) for x in os.getenv("OWNER_IDS", "").replace(" ", "").split(",") if x}
        super().__init__(
            command_prefix=self._get_prefix,
            intents=discord.Intents.all(),
            help_command=Help(),
            case_insensitive=True,
            strip_after_prefix=True,
            owner_ids=owners or None,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, replied_user=False),
            activity=discord.Activity(type=discord.ActivityType.watching, name=os.getenv("STATUS", "veil utilities | ,help")),
            # message cache powers edit/delete logs + snipe; smaller on low-RAM hosts
            max_messages=1000 if os.getenv("LOW_MEMORY", "false").lower() == "true" else 5000,
        )
        self.default_prefix = os.getenv("PREFIX", ",")
        self.db = Database(os.getenv("DB_PATH", "veil.db"))
        self.session: aiohttp.ClientSession | None = None
        self.started_at = time.time()
        self.blacklist: set[int] = set()
        self.authorized: set[int] = set()
        self.auth_required = os.getenv("AUTH_REQUIRED", "true").lower() != "false"
        # developer controls (persisted in config under guild 0, module "global")
        self.maintenance: str | None = None
        self.killswitch: set[str] = set()
        self.recent_errors: deque = deque(maxlen=50)
        self._error_seq = 0

    # ------------------------------------------------------------- setup
    async def setup_hook(self) -> None:
        await self.db.connect()
        self.session = aiohttp.ClientSession(headers={"User-Agent": "veil-utilities-discord-bot/1.0"},
                                             timeout=aiohttp.ClientTimeout(total=15))
        self.blacklist = {r["id"] for r in await self.db.fetchall("SELECT id FROM blacklist")}
        self.authorized = {r["guild_id"] for r in await self.db.fetchall("SELECT guild_id FROM authorized")}
        g = await self.db.get_config(0, "global")
        self.maintenance = g.get("maintenance")
        self.killswitch = set(g.get("killswitch", []))
        for ext in EXTENSIONS:
            try:
                await self.load_extension(ext)
            except Exception:
                log.exception("Failed to load %s", ext)
        self.add_check(self._global_check)

    async def close(self) -> None:
        if self.session:
            await self.session.close()
        await self.db.close()
        await super().close()

    async def on_ready(self):
        log.info("Logged in as %s (%s) in %d guilds", self.user, self.user.id, len(self.guilds))

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

    async def on_message(self, message: discord.Message):
        if message.author.bot or message.author.id in self.blacklist:
            return
        if message.guild and message.guild.id in self.blacklist:
            return
        if message.guild and not self.is_authorized(message.guild.id) and not await self.is_owner(message.author):
            return
        await self.process_commands(message)

    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        # re-run commands that were fixed within 60s
        if before.content != after.content and not after.author.bot and \
                (discord.utils.utcnow() - after.created_at).total_seconds() < 60:
            await self.process_commands(after)

    async def on_guild_join(self, guild: discord.Guild):
        if guild.id in self.blacklist:
            await guild.leave()

    # ------------------------------------------------------------- checks
    async def _global_check(self, ctx: Context) -> bool:
        if await self.is_owner(ctx.author):
            return True
        if self.maintenance is not None:
            raise UserError(f"🛠️ The bot is under maintenance right now"
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
        disabled = settings.get("disabled", [])
        root = ctx.command.root_parent.name if ctx.command.root_parent else ctx.command.name
        if ctx.command.qualified_name in disabled or root in disabled:
            if not admin:
                raise UserError(f"`{ctx.command.qualified_name}` is disabled in this server")
        return True

    # ------------------------------------------------------------- errors
    async def on_command_error(self, ctx: Context, error: commands.CommandError):
        error =getattr(error, "original", error)
        if isinstance(error, (commands.CommandNotFound, Ignored)):
            return
        if isinstance(error, UserError):
            return await ctx.warn(str(error))
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
            return await ctx.warn(f"Slow down, try again in **{error.retry_after:.1f}s**", delete_after=5)
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
        if isinstance(error, commands.CheckFailure):
            return await ctx.warn(str(error) or "You can't use this command")
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
        await ctx.send(embed=discord.Embed(color=Colors.error,
                                           description=f"❌ Something went wrong: `{type(error).__name__}: {error}`"[:3900]
                                           + f"\nError ID: `{record['id']}`"))
        channel = self.get_channel(int(os.getenv("ERROR_LOG_CHANNEL") or 0))
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
