"""Server logs: one channel per category, detailed cards, set up and managed with `,logs`.

The look of each log is in core/logembeds.py; this file decides which channel gets what, and who may change it.
"""
from __future__ import annotations

import asyncio
import io
import time
from dataclasses import dataclass

import aiohttp
import discord
from discord.ext import commands

from core import logembeds as fmt
from core.utils import Colors, Context, Ignored, OwnedView, UserError, has_perms, respond_or_edit

OFF = -1  # stored instead of a channel id when a category is switched off on purpose
CATEGORY_NAME = "VEIL LOGS"


@dataclass(frozen=True)
class Kind:
    key: str  # the key in the stored config (kept stable so existing servers and the dashboard keep working)
    label: str
    emoji: str
    blurb: str
    channel: str | None  # name given to the channel `,logs setup` creates (None: not created by default)
    names: tuple[str, ...]  # channels with these names are picked up automatically when nothing is set


KINDS: dict[str, Kind] = {k.key: k for k in (
    Kind("messages", "Messages", "💬", "Deleted, edited and bulk-deleted messages", "message-logs",
         ("message-logs", "msg-logs")),
    Kind("members", "Members", "👥", "Joins, leaves, nicknames", "member-logs", ("member-logs", "members")),
    Kind("joinleave", "Joins & leaves", "🚪", "Optional: give joins and leaves their own channel", None,
         ("join-logs", "join-log", "joins-leaves")),
    Kind("roles", "Roles", "🏷️", "Roles created, edited, deleted, given or taken", "role-logs", ("role-logs", "roles")),
    Kind("channels", "Channels", "📁", "Channels and categories created, edited, deleted", "channel-logs",
         ("channel-logs", "channels")),
    Kind("voice", "Voice", "🎙️", "Voice joins, leaves, moves and server mutes", "voice-logs", ("voice-logs", "vc-logs")),
    Kind("bans", "Moderation", "🔨", "Bans, unbans, kicks and timeouts", "mod-logs",
         ("mod-logs", "modlog", "bans", "ban-logs", "ban-log", "punishments")),
    Kind("server", "Server", "⚙️", "Server settings, invites and emojis", "server-logs",
         ("server-logs", "veil-utilities-logging", "bot-logs", "logs")),
    Kind("commands", "Commands", "⌨️", "Every command a member runs", "command-logs",
         ("command-logs", "cmd-logs", "commands", "command-log")),
    Kind("antinuke", "Antinuke", "🚨", "Antinuke alerts and what the bot did about them", "antinuke-logs",
         ("antinuke-logs", "antinuke-log", "security-logs", "veil-security", "veil-utilities-logging")),
)}
TYPES = tuple(KINDS)

ALIASES = {
    "message": "messages", "msg": "messages", "member": "members", "join": "joinleave", "joins": "joinleave",
    "leave": "joinleave", "leaves": "joinleave", "joinleaves": "joinleave", "joinsleaves": "joinleave", "role": "roles", "channel": "channels",
    "vc": "voice", "moderation": "bans", "mod": "bans", "ban": "bans", "punishments": "bans", "guild": "server",
    "command": "commands", "cmd": "commands", "cmds": "commands", "security": "antinuke",
}
NO_SERVER_FALLBACK = ("commands", "bans", "joinleave")  # these never spill into the general server log
SETUP_KINDS = tuple(k.key for k in KINDS.values() if k.channel)
SETUP_NAMES = {k.channel for k in KINDS.values() if k.channel} | {n for k in KINDS.values() for n in k.names}


def canon(name: str) -> str | None:
    """`mod`, `Moderation`, `bans` -> 'bans'; unknown names -> None."""
    flat = name.lower().replace(" ", "").replace("&", "").replace("-", "").replace("_", "")
    flat = ALIASES.get(flat, flat)
    return flat if flat in KINDS else None


def _channel_for(guild, cfg: dict, kind: str) -> discord.TextChannel | None:
    """The channel a category logs to: the one set in the config, else a channel with a recognisable name."""
    chain = [kind] + (["members"] if kind == "joinleave" else [])  # joins and leaves ride with the member log
    for key in chain:
        value = cfg.get(key)
        if value == OFF:
            return None
        channel = guild.get_channel(value or 0)
        if isinstance(channel, discord.TextChannel):
            return channel
    text_channels = list(getattr(guild, "text_channels", []))
    for key in chain:
        for channel in text_channels:
            if channel.name.lower() in KINDS[key].names:
                return channel
    if kind not in NO_SERVER_FALLBACK:
        for channel in text_channels:
            if channel.name.lower() in KINDS["server"].names:
                return channel
    return None


def describe(guild, cfg: dict, kind: str) -> str:
    """How a category's destination reads in the panel."""
    if cfg.get(kind) == OFF:
        return "`off`"
    explicit = guild.get_channel(cfg.get(kind) or 0)
    if isinstance(explicit, discord.TextChannel):
        return explicit.mention
    found = _channel_for(guild, cfg, kind)
    return f"{found.mention} *(auto)*" if found else "`not set`"


def can_post(channel: discord.TextChannel) -> bool:
    perms = channel.permissions_for(channel.guild.me)
    return perms.view_channel and perms.send_messages and perms.embed_links


# ---------------------------------------------------------------- the unban / reban button on ban logs

class BanLogButton(discord.ui.DynamicItem[discord.ui.Button], template=r"banlog:(?P<g>\d+):(?P<u>\d+):(?P<b>[01])"):
    """Unban on a ban log, Reban on an unban log. Works after restarts because the state is in the custom id."""

    def __init__(self, guild_id: int = 0, user_id: int = 0, is_banned: bool = True):
        self.guild_id, self.user_id, self.is_banned = guild_id, user_id, is_banned
        super().__init__(discord.ui.Button(
            label="Unban" if is_banned else "Reban",
            style=discord.ButtonStyle.success if is_banned else discord.ButtonStyle.danger,
            custom_id=f"banlog:{guild_id}:{user_id}:{int(is_banned)}"))

    @classmethod
    async def from_custom_id(cls, it, item, match):
        return cls(int(match["g"]), int(match["u"]), match["b"] == "1")

    @staticmethod
    def build(guild_id: int, user_id: int, is_banned: bool) -> discord.ui.View:
        return discord.ui.View(timeout=None).add_item(BanLogButton(guild_id, user_id, is_banned))

    async def callback(self, it: discord.Interaction):
        guild = it.guild or it.client.get_guild(self.guild_id)
        if not guild:
            return await it.response.send_message("Guild not found", ephemeral=True)
        member = guild.get_member(it.user.id)
        if not member:
            return await it.response.send_message("You are not in this guild", ephemeral=True)

        from core import perms as _perms
        is_dev = _perms.is_bot_developer(it.user.id, it.client)
        is_owner = member.id == guild.owner_id
        cfg = await it.client.db.get_config(guild.id, "permlevels")
        rank = _perms.rank_of(cfg, member, is_owner=is_owner, is_dev=is_dev)

        # Bot developers, the server owner, or uperm Admin+ (rank 3+); Discord's own Administrator isn't enough
        if not (is_dev or is_owner or rank >= 3):
            return await it.response.send_message(
                "🔒 Only Bot Developers, Server Owners, or Admin+ (uperm rank 3+) can use this button.", ephemeral=True)
        if it.user.id == self.user_id:
            return await it.response.send_message("❌ You cannot ban or unban yourself with this button.", ephemeral=True)

        user = it.client.get_user(self.user_id)
        if user is None:
            try:
                user = await it.client.fetch_user(self.user_id)
            except Exception:
                user = discord.Object(id=self.user_id)
        name = getattr(user, "name", str(self.user_id))

        try:
            if self.is_banned:
                await guild.unban(user, reason=f"Unbanned via button by {it.user}")
                verb = "unbanned"
            else:
                await guild.ban(user, reason=f"Rebanned via button by {it.user}")
                verb = "rebanned"
        except discord.NotFound:
            return await it.response.send_message(f"Failed — **{name}** is not currently banned.", ephemeral=True)
        except discord.HTTPException as err:
            return await it.response.send_message(f"Failed to {'unban' if self.is_banned else 'reban'}: {err}",
                                                  ephemeral=True)
        await it.response.edit_message(view=self.build(guild.id, self.user_id, not self.is_banned))
        await it.followup.send(f"{it.user.mention} {verb} **{name}** (`{self.user_id}`).")


# ---------------------------------------------------------------- the ,logs panel

class KindSelect(discord.ui.Select["LogPanel"]):
    def __init__(self, selected: str | None):
        super().__init__(placeholder="Pick a category to change…", row=0, options=[
            discord.SelectOption(label=k.label, value=k.key, emoji=k.emoji, description=k.blurb[:100],
                                 default=(k.key == selected)) for k in KINDS.values()])

    async def callback(self, it: discord.Interaction):
        await self.view.choose(it, self.values[0])


class ChannelPick(discord.ui.ChannelSelect["LogPanel"]):
    def __init__(self, label: str):
        super().__init__(placeholder=f"Send {label} logs to…", channel_types=[discord.ChannelType.text],
                         min_values=1, max_values=1, row=1)

    async def callback(self, it: discord.Interaction):
        await self.view.assign(it, self.values[0].id)


class PanelButton(discord.ui.Button["LogPanel"]):
    def __init__(self, label: str, action: str, style=discord.ButtonStyle.secondary, emoji: str | None = None,
                 disabled: bool = False):
        super().__init__(label=label, emoji=emoji, style=style, disabled=disabled, row=2)
        self.action = action

    async def callback(self, it: discord.Interaction):
        await getattr(self.view, f"do_{self.action}")(it)


class LogPanel(OwnedView):
    """`,logs`: see where every category goes and change it without remembering any syntax."""

    def __init__(self, cog: Logging, guild: discord.Guild, author_id: int, cfg: dict):
        super().__init__(author_id, timeout=300)
        self.cog, self.guild, self.cfg = cog, guild, cfg
        self.selected: str | None = None
        self.message: discord.Message | None = None
        self.rebuild()

    def embed(self) -> discord.Embed:
        lines = []
        for kind in KINDS.values():
            mark = "▸ " if kind.key == self.selected else ""
            lines.append(f"{mark}{kind.emoji} **{kind.label}** — {describe(self.guild, self.cfg, kind.key)}")
        e = discord.Embed(title="Logging", color=Colors.main, description="\n".join(lines))
        e.set_footer(text=("Pick a category, then a channel · " if not self.selected else
                           f"Now choose where {KINDS[self.selected].label} logs go · ")
                     + "`auto` finds channels by name")
        return e

    def rebuild(self):
        self.clear_items()
        self.add_item(KindSelect(self.selected))
        if self.selected:
            self.add_item(ChannelPick(KINDS[self.selected].label))
        self.add_item(PanelButton("Create channels", "create", discord.ButtonStyle.primary, "✨"))
        self.add_item(PanelButton("Turn off", "off", emoji="⛔", disabled=not self.selected))
        self.add_item(PanelButton("Auto", "auto", emoji="🔁", disabled=not self.selected))
        self.add_item(PanelButton("Done", "done", emoji="✅"))

    async def refresh(self, it: discord.Interaction):
        self.rebuild()
        await respond_or_edit(it, self.embed(), self)

    async def choose(self, it: discord.Interaction, key: str):
        self.selected = key
        await self.refresh(it)

    async def assign(self, it: discord.Interaction, channel_id: int):
        channel = self.guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return await it.response.send_message("Pick a text channel.", ephemeral=True)
        if not can_post(channel):
            return await it.response.send_message(
                f"I need **View Channel**, **Send Messages** and **Embed Links** in {channel.mention}.", ephemeral=True)
        self.cfg[self.selected] = channel.id
        await self.cog.save(self.guild, self.cfg)
        await self.refresh(it)

    async def do_off(self, it: discord.Interaction):
        self.cfg[self.selected] = OFF
        await self.cog.save(self.guild, self.cfg)
        await self.refresh(it)

    async def do_auto(self, it: discord.Interaction):
        self.cfg[self.selected] = None
        await self.cog.save(self.guild, self.cfg)
        await self.refresh(it)

    async def do_create(self, it: discord.Interaction):
        if not self.guild.me.guild_permissions.manage_channels:
            return await it.response.send_message("I need the **Manage Channels** permission to create them.", ephemeral=True)
        await it.response.defer()
        await self.cog.ensure_default_channels(self.guild)
        self.cfg = await self.cog.config(self.guild)
        self.rebuild()
        await it.edit_original_response(embed=self.embed(), view=self)

    async def do_done(self, it: discord.Interaction):
        self.stop()
        for item in self.children:
            item.disabled = True
        await respond_or_edit(it, self.embed(), self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


# ---------------------------------------------------------------- the cog

class Logging(commands.Cog):
    """Log server events to channels"""
    emoji = "📜"

    def __init__(self, bot):
        self.bot = bot
        self._role_update_tasks: dict[tuple[int, int], asyncio.Task] = {}
        self._role_update_states: dict[tuple[int, int], tuple[discord.Member, discord.Member]] = {}

    async def cog_load(self):
        self.bot.add_dynamic_items(BanLogButton)

    async def cog_unload(self):
        self.bot.remove_dynamic_items(BanLogButton)
        for task in self._role_update_tasks.values():
            task.cancel()

    # ------------------------------------------------------------ config and routing
    async def config(self, guild: discord.Guild) -> dict:
        return await self.bot.db.get_config(guild.id, "logs")

    async def save(self, guild: discord.Guild, cfg: dict) -> None:
        await self.bot.db.set_config(guild.id, "logs", cfg)

    async def channel_for(self, guild: discord.Guild, kind: str) -> discord.TextChannel | None:
        if guild is None:
            return None
        return _channel_for(guild, await self.config(guild), canon(kind) or kind)

    async def route(self, guild: discord.Guild, *kinds: str, channel_id: int | None = None,
                    user_id: int | None = None) -> discord.TextChannel | None:
        """First channel that should get this event, or None (nothing set, switched off, or the source is ignored)."""
        cfg = await self.config(guild)
        ignore = cfg.get("ignore") or {}
        if channel_id and channel_id in ignore.get("channels", []):
            return None
        if user_id and user_id in ignore.get("users", []):
            return None
        for kind in kinds:
            channel = _channel_for(guild, cfg, kind)
            if channel:
                return channel
        return None

    async def post(self, channel: discord.TextChannel, embed: discord.Embed, *, file: discord.File | None = None,
                   view: discord.ui.View | None = None) -> bool:
        kwargs = {"embed": embed}
        if file:
            kwargs["file"] = file
        if view:
            kwargs["view"] = view
        try:
            await channel.send(**kwargs)
            return True
        except (discord.HTTPException, aiohttp.ClientError, asyncio.TimeoutError):
            return False

    async def send(self, guild, kind: str, embed: discord.Embed, file: discord.File | None = None,
                   view: discord.ui.View | None = None) -> bool:
        """Post an embed to a category's channel (used by other cogs too)."""
        channel = await self.route(guild, canon(kind) or kind)
        return bool(channel) and await self.post(channel, embed, file=file, view=view)

    # ------------------------------------------------------------ channel creation
    async def ensure_default_channels(self, guild: discord.Guild, kinds: tuple[str, ...] | None = None
                                      ) -> list[discord.TextChannel]:
        """Create the private log channels that are missing (under a VEIL LOGS category) and point the config at them."""
        me = getattr(guild, "me", None)
        if not me or not getattr(me.guild_permissions, "manage_channels", False):
            return []
        text_channels = list(getattr(guild, "text_channels", []))
        categories = list(getattr(guild, "categories", []))
        if not text_channels and hasattr(guild, "fetch_channels"):
            try:
                fetched = await guild.fetch_channels()
                text_channels = [c for c in fetched if isinstance(c, discord.TextChannel)]
                categories = [c for c in fetched if isinstance(c, discord.CategoryChannel)]
            except (discord.HTTPException, AttributeError):
                pass
        existing = {c.name.lower(): c for c in text_channels}
        cfg = await self.config(guild)

        todo = []
        for key in kinds or SETUP_KINDS:
            kind = KINDS[key]
            if not kind.channel or cfg.get(key) == OFF:
                continue  # not created by default, or switched off on purpose
            if isinstance(guild.get_channel(cfg.get(key) or 0), discord.TextChannel):
                continue  # already pointed somewhere
            todo.append(kind)
        if not todo:
            return []

        private = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False, send_messages=False),
            me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, attach_files=True),
        }
        category = discord.utils.get(categories, name=CATEGORY_NAME)
        if not category and any(k.channel not in existing for k in todo):
            try:
                category = await guild.create_category(CATEGORY_NAME, overwrites=private,
                                                       reason="Veil Utilities: log channels")
            except discord.HTTPException:
                category = None

        created = []
        for kind in todo:
            channel = existing.get(kind.channel)
            if channel is None:
                try:
                    channel = await guild.create_text_channel(
                        kind.channel, category=category, topic=f"Veil Utilities · {kind.blurb}",
                        overwrites=private, reason="Veil Utilities: log channel")
                except discord.HTTPException:
                    continue
                created.append(channel)
                existing[kind.channel] = channel
            cfg[kind.key] = channel.id
        await self.save(guild, cfg)
        return created

    # ------------------------------------------------------------ commands
    @commands.group(aliases=["log"], invoke_without_command=True)
    @commands.guild_only()
    @has_perms(manage_guild=True)
    async def logs(self, ctx: Context):
        """Open the logging panel: see and change where each kind of log goes"""
        panel = LogPanel(self, ctx.guild, ctx.author.id, await self.config(ctx.guild))
        panel.message = await ctx.send(embed=panel.embed(), view=panel)

    async def _kinds(self, text: str) -> tuple[str, ...]:
        if text.lower() == "all":
            return TYPES
        kind = canon(text)
        if kind is None:
            names = ", ".join(f"`{k.label.lower()}`" for k in KINDS.values())
            raise UserError(f"Unknown category `{text}`. Use one of {names}, or `all`.")
        return (kind,)

    @logs.command(name="set")
    @has_perms(manage_guild=True)
    async def logs_set(self, ctx: Context, category: str, channel: str = None):
        """Send a category to a channel: `logs set messages #logs`. Use `off` to stop it or `auto` to find it by name"""
        kinds = await self._kinds(category)
        cfg = await self.config(ctx.guild)
        word = (channel or "auto").lower()
        if word in ("off", "none", "disable"):
            value, shown = OFF, "off"
        elif word == "auto":
            value, shown = None, "auto"
        else:
            target = await commands.TextChannelConverter().convert(ctx, channel)
            if not can_post(target):
                raise UserError(f"I need View Channel, Send Messages and Embed Links in {target.mention}.")
            value, shown = target.id, target.mention
        for key in kinds:
            cfg[key] = value
        await self.save(ctx.guild, cfg)
        label = "all logs" if len(kinds) > 1 else f"**{KINDS[kinds[0]].label}** logs"
        await ctx.approve(f"{label} → {shown}")

    @logs.command(name="off", aliases=["disable"])
    @has_perms(manage_guild=True)
    async def logs_off(self, ctx: Context, category: str):
        """Stop a category (or `all`) from being logged"""
        await self.logs_set(ctx, category, "off")

    @logs.command(name="auto", aliases=["reset"])
    @has_perms(manage_guild=True)
    async def logs_auto(self, ctx: Context, category: str):
        """Go back to finding a category's channel by name"""
        await self.logs_set(ctx, category, "auto")

    @logs.command(name="setup", aliases=["init", "default", "defaults", "create"])
    @has_perms(manage_guild=True)
    async def logs_setup(self, ctx: Context):
        """Create a private channel for every log category under VEIL LOGS (skips ones you already set)"""
        if not ctx.guild.me.guild_permissions.manage_channels:
            raise UserError("I need the `Manage Channels` permission to create the log channels.")
        created = await self.ensure_default_channels(ctx.guild)
        cfg = await self.config(ctx.guild)
        lines = [f"{k.emoji} **{k.label}** — {describe(ctx.guild, cfg, k.key)}" for k in KINDS.values() if k.channel]
        note = f"Created {len(created)} channel(s) under **{CATEGORY_NAME}**." if created else "Everything was already set up."
        await ctx.send(embed=discord.Embed(title="Logging set up", color=Colors.success,
                                           description="\n".join(lines) + f"\n\n{note}"))

    @commands.command(name="setuplogs", aliases=["setupchannels", "autologs"])
    @has_perms(manage_guild=True)
    async def cmd_setuplogs(self, ctx: Context):
        """Create a private channel for every log category (same as `logs setup`)"""
        await self.logs_setup(ctx)

    @logs.command(name="test")
    @has_perms(manage_guild=True)
    async def logs_test(self, ctx: Context):
        """Send a test line to every category that has a channel, so you can see what's missing permissions"""
        cfg = await self.config(ctx.guild)
        ok, failed, unset = [], [], []
        for kind in KINDS.values():
            channel = _channel_for(ctx.guild, cfg, kind.key)
            if channel is None:
                unset.append(kind.label)
                continue
            embed = fmt.card(Colors.blurple, ctx.author, f"**Test** · {kind.emoji} {kind.label} logs are working",
                             ids=[("User ID", ctx.author.id)])
            (ok if await self.post(channel, embed) else failed).append(f"{kind.label} → {channel.mention}")
        lines = []
        if ok:
            lines.append("✅ " + ", ".join(ok))
        if failed:
            lines.append("❌ Couldn't post in: " + ", ".join(failed))
        if unset:
            lines.append("➖ Not set: " + ", ".join(unset))
        await ctx.send(embed=discord.Embed(title="Log test", color=Colors.error if failed else Colors.success,
                                           description="\n".join(lines)))

    @logs.group(name="ignore", invoke_without_command=True)
    @has_perms(manage_guild=True)
    async def logs_ignore(self, ctx: Context):
        """Skip logging messages, voice and commands from a channel or member"""
        ignore = (await self.config(ctx.guild)).get("ignore") or {}
        channels = ", ".join(f"<#{c}>" for c in ignore.get("channels", [])) or "none"
        users = ", ".join(f"<@{u}>" for u in ignore.get("users", [])) or "none"
        await ctx.send(embed=discord.Embed(
            title="Ignored by logs", color=Colors.main,
            description=f"**Channels:** {channels}\n**Members:** {users}\n\n"
                        f"`{ctx.clean_prefix}logs ignore add <#channel|@member>` · `remove` to undo"))

    async def _toggle_ignore(self, ctx: Context, target, add: bool):
        cfg = await self.config(ctx.guild)
        ignore = cfg.setdefault("ignore", {})
        bucket = ignore.setdefault("channels" if isinstance(target, discord.abc.GuildChannel) else "users", [])
        if add and target.id not in bucket:
            bucket.append(target.id)
        elif not add and target.id in bucket:
            bucket.remove(target.id)
        else:
            raise UserError(f"{target.mention} is already {'ignored' if add else 'not ignored'}.")
        await self.save(ctx.guild, cfg)
        await ctx.approve(f"{'Ignoring' if add else 'No longer ignoring'} {target.mention}")

    @logs_ignore.command(name="add")
    @has_perms(manage_guild=True)
    async def logs_ignore_add(self, ctx: Context, target: discord.TextChannel | discord.VoiceChannel | discord.Member):
        """Ignore a channel or member"""
        await self._toggle_ignore(ctx, target, True)

    @logs_ignore.command(name="remove", aliases=["del", "delete"])
    @has_perms(manage_guild=True)
    async def logs_ignore_remove(self, ctx: Context, target: discord.TextChannel | discord.VoiceChannel | discord.Member):
        """Stop ignoring a channel or member"""
        await self._toggle_ignore(ctx, target, False)

    @logs.command(name="cleanup", aliases=["clean", "purgeduplicates", "cleanupchannels"])
    @has_perms(manage_guild=True)
    async def logs_cleanup(self, ctx: Context):
        """Delete duplicate log channels the bot created, and an empty VEIL LOGS category"""
        if not ctx.guild.me.guild_permissions.manage_channels:
            raise UserError("I need the `Manage Channels` permission to delete duplicate channels.")
        seen: set[str] = set()
        deleted = 0
        for channel in list(ctx.guild.text_channels):
            name = channel.name.lower()
            if name not in SETUP_NAMES:
                continue
            if name in seen:
                try:
                    await channel.delete(reason="Veil Utilities: cleanup duplicate log channels")
                    deleted += 1
                except discord.HTTPException:
                    pass
            else:
                seen.add(name)
        empty = 0
        for category in list(ctx.guild.categories):
            if category.name.upper() == CATEGORY_NAME and not category.channels:
                try:
                    await category.delete(reason="Veil Utilities: cleanup empty VEIL LOGS category")
                    empty += 1
                except discord.HTTPException:
                    pass
        text = f"Deleted **{deleted}** duplicate log channel(s)" + (f" and **{empty}** empty categor{'y' if empty == 1 else 'ies'}" if empty else "")
        await ctx.approve(text + ".")

    @commands.command(name="cleanuplogs", aliases=["cleanuplogchannels", "purgeduplicatelogs"])
    @has_perms(manage_guild=True)
    async def cmd_cleanuplogs(self, ctx: Context):
        """Delete duplicate log channels the bot created (same as `logs cleanup`)"""
        await self.logs_cleanup(ctx)

    # ------------------------------------------------------------ commands run
    @commands.Cog.listener()
    async def on_command(self, ctx: Context):
        ctx._log_started = time.perf_counter()  # so the card can say how long the command took

    async def _log_command(self, ctx: Context, error: str | None = None):
        if not ctx.guild or ctx.author.bot or ctx.command is None:
            return
        channel = await self.route(ctx.guild, "commands", channel_id=ctx.channel.id, user_id=ctx.author.id)
        if not channel:
            return
        from core import perms as _perms
        cfg = await self.bot.db.get_config(ctx.guild.id, "permlevels")
        is_dev = _perms.is_bot_developer(ctx.author, self.bot) or await self.bot.is_owner(ctx.author)
        is_owner = ctx.author.id == ctx.guild.owner_id
        level, granted_by, rank = _perms.member_level_info(cfg, ctx.author, is_owner=is_owner, is_dev=is_dev)
        if is_dev:
            permission, granted = "Bot Developer", "Built-in"
        elif level and granted_by:
            permission, granted = level, granted_by
        elif is_owner:
            permission, granted = "Server Owner", "Built-in"
        else:
            permission, granted, rank = "None", "-", None
        started = getattr(ctx, "_log_started", None)
        ms = (time.perf_counter() - started) * 1000 if started is not None else None
        await self.post(channel, fmt.command_used(ctx, permission, granted, rank=rank, ms=ms, error=error))

    @commands.Cog.listener()
    async def on_command_completion(self, ctx: Context):
        await self._log_command(ctx)

    @commands.Cog.listener()
    async def on_command_error(self, ctx: Context, error: commands.CommandError):
        # failures are worth a card too (who tried what, and why it didn't work); noise is not
        if isinstance(error, (Ignored, commands.CommandNotFound, commands.CommandOnCooldown)):
            return
        original = getattr(error, "original", error)
        reason = str(original).strip() or type(original).__name__
        await self._log_command(ctx, f"{type(original).__name__}: {reason}" if not isinstance(original, UserError) else reason)

    # ------------------------------------------------------------ messages
    @commands.Cog.listener()
    async def on_message_delete(self, m: discord.Message):
        if not m.guild or m.author.bot:
            return
        channel = await self.route(m.guild, "messages", channel_id=m.channel.id, user_id=m.author.id)
        if not channel:
            return
        actor, _ = await fmt.audit_actor(
            m.guild, discord.AuditLogAction.message_delete, m.author.id,
            check=lambda e: getattr(getattr(e.extra, "channel", None), "id", m.channel.id) == m.channel.id)
        await self.post(channel, fmt.message_deleted(m, actor))

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages: list[discord.Message]):
        if not messages or not messages[0].guild:
            return
        first = messages[0]
        channel = await self.route(first.guild, "messages", channel_id=first.channel.id)
        if not channel:
            return
        data = io.BytesIO(fmt.transcript(messages).encode())
        await self.post(channel, fmt.bulk_deleted(messages), file=discord.File(data, "messages.txt"))

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if not after.guild or after.author.bot or before.content == after.content:
            return
        channel = await self.route(after.guild, "messages", channel_id=after.channel.id, user_id=after.author.id)
        if channel:
            await self.post(channel, fmt.message_edited(before, after))

    # ------------------------------------------------------------ joins, leaves, bans
    @commands.Cog.listener()
    async def on_member_join(self, m: discord.Member):
        channel = await self.route(m.guild, "joinleave")
        if channel:
            await self.post(channel, fmt.member_joined(m))

    @commands.Cog.listener()
    async def on_member_remove(self, m: discord.Member):
        left = await self.route(m.guild, "joinleave")
        mod = await self.route(m.guild, "bans")
        if not (left or mod):
            return
        kicker, reason = await fmt.audit_actor(m.guild, discord.AuditLogAction.kick, m.id)
        if kicker:  # a kick is moderation; it only lands in the leave log when there is no moderation channel
            await self.post(mod or left, fmt.member_left(m, kicker, reason))
        elif left:
            await self.post(left, fmt.member_left(m))

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User | discord.Member):
        channel = await self.route(guild, "bans")
        if not channel:
            return
        mod, reason = await fmt.audit_actor(guild, discord.AuditLogAction.ban, user.id)
        await self.post(channel, fmt.user_banned(user, mod, reason), view=BanLogButton.build(guild.id, user.id, True))

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        channel = await self.route(guild, "bans")
        if not channel:
            return
        mod, reason = await fmt.audit_actor(guild, discord.AuditLogAction.unban, user.id)
        await self.post(channel, fmt.user_unbanned(user, mod, reason), view=BanLogButton.build(guild.id, user.id, False))

    # ------------------------------------------------------------ member updates and roles
    async def _build_role_update_embed(self, member: discord.Member, added: list, removed: list) -> discord.Embed:
        actor, reason = await fmt.audit_actor(member.guild, discord.AuditLogAction.member_role_update, member.id)
        return fmt.roles_changed(member, added, removed, actor, reason)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.nick != after.nick:
            channel = await self.route(after.guild, "members", user_id=after.id)
            if channel:
                actor, _ = await fmt.audit_actor(after.guild, discord.AuditLogAction.member_update, after.id)
                await self.post(channel, fmt.nickname_changed(after, before.nick, after.nick, actor))

        if before.roles != after.roles:
            self._queue_role_update(before, after)

        if before.timed_out_until != after.timed_out_until:
            now = discord.utils.utcnow()
            channel = await self.route(after.guild, "bans", "members")
            if not channel:
                return
            actor, reason = await fmt.audit_actor(after.guild, discord.AuditLogAction.member_update, after.id)
            if after.timed_out_until and after.timed_out_until > now:
                await self.post(channel, fmt.timed_out(after, after.timed_out_until, actor, reason))
            elif before.timed_out_until and (not after.timed_out_until or after.timed_out_until <= now):
                await self.post(channel, fmt.timeout_removed(after, actor))

    def _queue_role_update(self, before: discord.Member, after: discord.Member):
        """Roles often change several at a time; wait a moment and log the net result once."""
        key = (after.guild.id, after.id)
        first = self._role_update_states[key][0] if key in self._role_update_states else before
        self._role_update_states[key] = (first, after)
        task = self._role_update_tasks.get(key)
        if task and not task.done():
            task.cancel()
        self._role_update_tasks[key] = asyncio.create_task(self._dispatch_role_update(key))

    async def _dispatch_role_update(self, key: tuple[int, int]):
        try:
            await asyncio.sleep(1.2)
        except asyncio.CancelledError:
            return
        state = self._role_update_states.pop(key, None)
        self._role_update_tasks.pop(key, None)
        if not state:
            return
        first, last = state
        added = [r for r in last.roles if r not in first.roles]
        removed = [r for r in first.roles if r not in last.roles]
        if not added and not removed:
            return
        channel = await self.route(last.guild, "roles", user_id=last.id)
        if channel:
            await self.post(channel, await self._build_role_update_embed(last, added, removed))

    # ------------------------------------------------------------ roles, channels, server
    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        channel = await self.route(role.guild, "roles")
        if channel:
            actor, _ = await fmt.audit_actor(role.guild, discord.AuditLogAction.role_create, role.id)
            await self.post(channel, fmt.role_created(role, actor))

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        channel = await self.route(role.guild, "roles")
        if channel:
            actor, _ = await fmt.audit_actor(role.guild, discord.AuditLogAction.role_delete, role.id)
            await self.post(channel, fmt.role_deleted(role, actor))

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role):
        changes = fmt.role_changes(before, after)
        channel = await self.route(after.guild, "roles") if changes else None
        if channel:
            actor, _ = await fmt.audit_actor(after.guild, discord.AuditLogAction.role_update, after.id)
            await self.post(channel, fmt.role_updated(after, changes, actor))

    @commands.Cog.listener()
    async def on_guild_channel_create(self, c: discord.abc.GuildChannel):
        channel = await self.route(c.guild, "channels")
        if channel:
            actor, _ = await fmt.audit_actor(c.guild, discord.AuditLogAction.channel_create, c.id)
            await self.post(channel, fmt.channel_created(c, actor))

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, c: discord.abc.GuildChannel):
        channel = await self.route(c.guild, "channels")
        if channel:
            actor, _ = await fmt.audit_actor(c.guild, discord.AuditLogAction.channel_delete, c.id)
            await self.post(channel, fmt.channel_deleted(c, actor))

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
        changes = fmt.channel_changes(before, after)
        channel = await self.route(after.guild, "channels") if changes else None
        if channel:
            actor, _ = await fmt.audit_actor(after.guild, discord.AuditLogAction.channel_update, after.id)
            await self.post(channel, fmt.channel_updated(after, changes, actor))

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild):
        changes = fmt.server_changes(before, after)
        channel = await self.route(after, "server") if changes else None
        if channel:
            actor, _ = await fmt.audit_actor(after, discord.AuditLogAction.guild_update, after.id)
            await self.post(channel, fmt.server_updated(after, changes, actor))

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        if invite.guild is None:
            return
        channel = await self.route(invite.guild, "server")
        if channel:
            await self.post(channel, fmt.invite_created(invite))

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        if invite.guild is None:
            return
        channel = await self.route(invite.guild, "server")
        if channel:
            await self.post(channel, fmt.invite_deleted(invite))

    @commands.Cog.listener()
    async def on_guild_emojis_update(self, guild: discord.Guild, before, after):
        old, new = {e.id for e in before}, {e.id for e in after}
        added = [e for e in after if e.id not in old]
        removed = [e for e in before if e.id not in new]
        channel = await self.route(guild, "server") if (added or removed) else None
        if channel:
            await self.post(channel, fmt.emojis_changed(guild, added, removed))

    # ------------------------------------------------------------ voice
    @commands.Cog.listener()
    async def on_voice_state_update(self, m: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        embed = fmt.voice_changed(m, before, after)
        if embed is None:
            return
        where = after.channel or before.channel
        channel = await self.route(m.guild, "voice", channel_id=getattr(where, "id", None), user_id=m.id)
        if channel:
            await self.post(channel, embed)


async def setup(bot):
    await bot.add_cog(Logging(bot))
