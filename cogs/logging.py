"""Server logs: one channel per category, detailed cards, set up and managed with `,logs`.

The look of each log is in core/logembeds.py; this file decides which channel gets what, and who may change it.
"""
from __future__ import annotations

import asyncio
import copy
import io
import logging as std_logging
import time
from dataclasses import dataclass, field

import aiohttp
import discord
from discord.ext import commands

from core import autoclean
from core import devchannels
from core import logembeds as fmt
from core.utils import (Colors, Context, Ignored, OwnedView, UserError, has_perms, input_error_text,
                        is_server_owner, owner_or_developer, respond_or_edit, shorten)

OFF = -1  # stored instead of a channel id when a category is switched off on purpose
CATEGORY_NAME = devchannels.LOGS_CATEGORY
BURST_WAIT = 2.0  # seconds to gather role/channel creations or deletions before posting them
BURST_KEEP = 20.0  # a burst's card keeps growing while more of the same arrive within this long
BURST_MAX = 60  # most items on one grouped card
FEED_WAIT = 1.5  # seconds to gather feed lines (commands, edits, voice) before posting them together
FEED_KEEP = 60.0  # a feed message keeps taking new lines for this long
FEED_LIMIT = 1900  # characters in one feed message (Discord allows 2000)
BUTTON_MEMORY = 30.0  # seconds a ban-log button click is remembered, so the ban/unban card names the clicker


class _Feed:
    """Feed lines waiting to be posted in one channel, and the message they're being added to."""

    def __init__(self):
        self.pending: list[str] = []
        self.message: discord.Message | None = None
        self.text = ""
        self.started = 0.0
        self.task: asyncio.Task | None = None


# typing a command without what it needs: the person gets the usage card, and the log doesn't need a line about it
USAGE_SLIPS = (commands.MissingRequiredArgument, commands.MissingRequiredAttachment, commands.TooManyArguments)


class _Burst:
    """Creations or deletions of one kind by one person, gathered onto one card."""

    def __init__(self, actor, reason):
        self.actor, self.reason = actor, reason
        self.items: list = []
        self.message: discord.Message | None = None
        self.last = 0.0
        self.task: asyncio.Task | None = None


_SINGLE = {"role_create": fmt.role_created, "role_delete": fmt.role_deleted,
           "channel_create": fmt.channel_created, "channel_delete": fmt.channel_deleted}


@dataclass(frozen=True)
class Kind:
    key: str  # the key in the stored config (kept stable so existing servers and the dashboard keep working)
    label: str
    emoji: str
    blurb: str
    channel: str | None  # name given to the channel `,logs setup` creates (None: not created by default)
    names: tuple[str, ...]  # channels with these names are picked up automatically when nothing is set


# Channels `,logs setup` makes, in the order a server reads them. Every log category has a home here: the leaner
# set chooses the ones that mean the most, and anything without its own channel shares the general log.
KINDS: dict[str, Kind] = {k.key: k for k in (
    Kind("joinleave", "Joins & leaves", "🚪", "Joins, leaves and kicks", "join-logs",
         ("join-logs", "join-log", "joins-leaves")),
    Kind("commands", "Commands", "⌨️", "Command executions and permission checks", "command-logs",
         ("command-logs", "cmd-logs", "commands", "command-log")),
    Kind("server", "Server", "⚙️", "Server settings, invites and emojis", "veil-utilities-logging",
         ("veil-utilities-logging", "midtown-utilities-logging", "server-logs", "bot-logs", "logs")),
    Kind("antinuke", "Antinuke", "🚨", "Antinuke alerts and automated responses", "antinuke-logs",
         ("antinuke-logs", "antinuke-log", "security-logs", "veil-security", "veil-utilities-logging",
          "midtown-utilities-logging")),
    Kind("messages", "Messages", "💬", "Deleted, edited and bulk-deleted messages", None, ("message-logs", "msg-logs")),
    Kind("members", "Members", "👥", "Nicknames; joins and leaves use the join log when available", None,
         ("member-logs", "members")),
    Kind("roles", "Roles", "🏷️", "Roles created, edited, deleted, given or taken", "role-logs", ("role-logs", "roles")),
    Kind("channels", "Channels", "📁", "Channels and categories created, edited, deleted", "channel-logs",
         ("channel-logs", "channels")),
    Kind("voice", "Voice", "🎙️", "Voice joins, leaves, moves and server mutes", "voice-logs",
         ("voice-logs", "vc-logs")),
    Kind("bans", "Moderation", "🔨", "Bans, unbans and kicks",
         "mod-logs", ("mod-logs", "modlog", "ban-logs", "ban-log", "punishments")),
    Kind("timeouts", "Timeouts", "⏳", "Timeouts and timeout removals", None,
         ("mute-logs", "timeout-logs", "mute-log", "timeout-log", "mutes", "timeouts")),
)}
TYPES = tuple(KINDS)

ALIASES = {
    "message": "messages", "msg": "messages", "member": "members", "join": "joinleave", "joins": "joinleave",
    "leave": "joinleave", "leaves": "joinleave", "joinleaves": "joinleave", "joinsleaves": "joinleave", "role": "roles", "channel": "channels",
    "vc": "voice", "moderation": "bans", "mod": "bans", "ban": "bans", "punishments": "bans", "guild": "server",
    "timeout": "timeouts", "mute": "timeouts", "mutes": "timeouts",
    "command": "commands", "cmd": "commands", "cmds": "commands", "security": "antinuke",
}
NO_SERVER_FALLBACK = ("commands", "bans", "joinleave", "timeouts")  # these never spill into the general server log
DM_KEY = "rbl_dm"  # `,logs` → "Replies: DM": RBL answers use DMs, except the in-channel role list
SETUP_KINDS = tuple(k.key for k in KINDS.values() if k.channel)


def canon(name: str) -> str | None:
    """`mod`, `Moderation`, `bans` -> 'bans'; unknown names -> None."""
    flat = name.lower().replace(" ", "").replace("&", "").replace("-", "").replace("_", "")
    flat = ALIASES.get(flat, flat)
    return flat if flat in KINDS else None


log = std_logging.getLogger("veil.logs")

@dataclass
class SetupReport:
    created: list = field(default_factory=list)
    moved: list = field(default_factory=list)
    unused: list = field(default_factory=list)  # got logs before, not any more (filed under VEIL LOGS, not deleted)
    category: object = None
    raised: bool = False
    failures: list[str] = field(default_factory=list)


# Where a category goes when it has no channel of its own. The order is the point: a channel named after the
# category wins, then the channel it shares with, and only then the general log (see `_channel_for`).
FALLBACKS: dict[str, tuple[str, ...]] = {
    "joinleave": ("members",),  # joins ride with the member channel when there is no #join-logs
    "members": ("joinleave",),  # one member stream: nicknames, joins, leaves and kicks
    "messages": ("server",), "roles": ("server",), "channels": ("server",), "voice": ("server",),
    "timeouts": ("bans", "members", "joinleave"),
}
# The flip side: a category whose channel carries other categories' logs with it when it is made.
CARRIES: dict[str, tuple[str, ...]] = {
    "joinleave": ("members",),
    "bans": ("timeouts",),
    "server": ("messages", "roles", "channels", "voice"),
}

_CATCHALL_NAMES = frozenset({"veil-utilities-logging", "midtown-utilities-logging", "server-logs", "bot-logs", "logs"})


def _clean_name(name: str | None) -> str:
    """Normalize a channel or category name, stripping leading emoji/separators."""
    if not name:
        return ""
    text = name.strip().lower()
    for idx, ch in enumerate(text):
        if ch.isalnum():
            return "-".join(text[idx:].split())
    return text


def bans_only(channel) -> bool:
    """True when a moderation channel name is dedicated strictly to bans (not timeouts or kicks)."""
    return _clean_name(getattr(channel, "name", None)) in ("bans", "ban-logs", "ban-log")


def other_bots_channel(channel) -> bool:
    """True when a channel already belongs to another bot's logging setup and should be left alone."""
    if channel is None:
        return False
    cname = _clean_name(getattr(channel, "name", None))
    cat = getattr(channel, "category", None)
    cat_name = _clean_name(getattr(cat, "name", None))
    if "vanguard" in cat_name:
        return True
    if cname == "bans" or "vanguard" in cname:
        return True
    return False


def _is_veil_category(cat) -> bool:
    if cat is None:
        return False
    cname = _clean_name(getattr(cat, "name", None))
    return cname == "veil-logs" and "vanguard" not in cname


ALL_LOG_NAMES = frozenset(
    n for k in KINDS.values() for n in k.names
    if "log" in n or "security" in n or n in ("modlog", "punishments")
)


def _is_log_channel(channel) -> bool:
    if channel is None or other_bots_channel(channel):
        return False
    return _clean_name(getattr(channel, "name", None)) in ALL_LOG_NAMES


def _is_text_channel(channel) -> bool:
    return (isinstance(channel, discord.TextChannel)
            or getattr(channel, "__dict__", {}).get("__class__") is discord.TextChannel)


def _is_approved(cfg: dict, key: str, value: int) -> bool:
    approved = cfg.get("approved_routes")
    return isinstance(approved, dict) and approved.get(key) == value


def _set_channel(guild, cfg: dict, key: str) -> discord.TextChannel | None:
    """Resolve an explicitly saved channel ID (skipping another bot's channel unless explicitly approved)."""
    value = cfg.get(key)
    if guild is None or not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return None
    get_channel = getattr(guild, "get_channel", None)
    channel = get_channel(value) if callable(get_channel) else None
    if not _is_text_channel(channel):
        return None
    if other_bots_channel(channel) and not _is_approved(cfg, key, value):
        return None
    return channel


def _auto_channel(guild, kind: str, *, setup_only: bool = False) -> discord.TextChannel | None:
    """Find a matching Veil log channel by name when no explicit ID is set."""
    if guild is None or kind not in KINDS:
        return None
    channels = [c for c in getattr(guild, "text_channels", ())
                if _is_text_channel(c) and not other_bots_channel(c)]
    if not channels:
        return None
    for wanted in KINDS[kind].names:
        if setup_only and (wanted not in ALL_LOG_NAMES or (kind != "server" and wanted in _CATCHALL_NAMES)):
            continue
        matches = [c for c in channels if _clean_name(getattr(c, "name", None)) == wanted]
        if matches:
            in_veil = [c for c in matches if _is_veil_category(getattr(c, "category", None))]
            return in_veil[0] if in_veil else matches[0]
    return None


def _channel_for(guild, cfg: dict, kind: str, _seen: frozenset[str] = frozenset()) -> discord.TextChannel | None:
    if guild is None or kind not in KINDS or kind in _seen:
        return None
    value = cfg.get(kind)
    if value == OFF:
        return None
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        direct = _set_channel(guild, cfg, kind)
        if direct is not None:
            return direct
        # Deleted/missing explicit IDs do not silently spill into another channel;
        # legacy unapproved IDs pointing to another bot's channel fall through to Veil's own channel.
        get_channel = getattr(guild, "get_channel", None)
        raw = get_channel(value) if callable(get_channel) else None
        if not (_is_text_channel(raw) and other_bots_channel(raw)):
            return None
    own = _auto_channel(guild, kind)
    if own is not None:
        return own
    next_seen = _seen | {kind}
    for key in FALLBACKS.get(kind, ()):
        if cfg.get(key) == OFF:
            continue
        channel = _channel_for(guild, cfg, key, next_seen)
        if channel is not None:
            if kind == "timeouts" and key == "bans" and bans_only(channel):
                continue
            return channel
    return None


def _timeout_fallback(guild, cfg: dict) -> discord.TextChannel | None:
    """Secondary fallback for timeout events when direct timeout routing is disabled or unset."""
    if guild is None:
        return None
    for key in ("bans", "members", "joinleave"):
        if cfg.get(key) == OFF:
            continue
        ch = _channel_for(guild, cfg, key)
        if ch is not None:
            if key == "bans" and bans_only(ch):
                continue
            return ch
    return None


def _shared_home(guild, cfg: dict, kind: str) -> discord.TextChannel | None:
    return _channel_for(guild, cfg, kind) if cfg.get(kind) is None else None


def _sharers(home: str) -> tuple[str, ...]:
    return CARRIES.get(home, ())


def describe(guild, cfg: dict, kind: str) -> str:
    value = cfg.get(kind)
    if value == OFF:
        return "`off`"
    direct = _set_channel(guild, cfg, kind)
    if direct is not None:
        return direct.mention
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        get_channel = getattr(guild, "get_channel", None)
        raw = get_channel(value) if callable(get_channel) else None
        if not (isinstance(raw, discord.TextChannel) and other_bots_channel(raw)):
            return f"`{value}` *(unavailable)*"
    own = _auto_channel(guild, kind)
    if own is not None:
        return f"{own.mention} *(auto)*"
    shared = _channel_for(guild, cfg, kind)
    if shared is not None:
        return f"{shared.mention} *(shared)*"
    return "`not set`"


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
        if member is None and getattr(getattr(it.user, "guild", None), "id", None) == guild.id:
            member = it.user
        if member is None and hasattr(guild, "fetch_member"):
            try:
                member = await guild.fetch_member(it.user.id)
            except discord.HTTPException:
                member = None
        if not member:
            return await it.response.send_message("You are not in this guild", ephemeral=True)

        from core import perms as _perms
        if not self.is_banned and it.user.id != self.user_id and _perms.is_bot_developer(self.user_id, it.client):
            protection = it.client.get_cog("DeveloperProtection")
            if protection:
                await it.response.defer(ephemeral=True)
                await protection.handle(guild, self.user_id, it.user.id, source="Veil ban-log button", actual_ban=False)
                return await it.followup.send("❌ Developer ban blocked — this attempt has been logged.",
                                              ephemeral=True)
            return await it.response.send_message("❌ Developer ban blocked.", ephemeral=True)
        is_dev = _perms.is_bot_developer(it.user.id, it.client)
        is_owner = member.id == guild.owner_id
        is_button_user = _perms.is_ban_button_user(it.user.id)
        cfg = await it.client.db.get_config(guild.id, "permlevels")
        rank = _perms.rank_of(cfg, member, is_owner=is_owner, is_dev=is_dev)

        # Bot developers, the server owner, designated ban-button users, or uperm Admin+ (rank 3+)
        if not (is_dev or is_owner or is_button_user or rank >= 3):
            return await it.response.send_message(
                "🔒 Only the server owner, the bot's developers or the **Admin** permission level or higher can use this button.", ephemeral=True)
        if it.user.id == self.user_id:
            return await it.response.send_message("❌ You cannot ban or unban yourself with this button.", ephemeral=True)
        if not self.is_banned and _perms.is_whitelisted_person(self.user_id):
            return await it.response.send_message("❌ That user is whitelisted and cannot be banned.", ephemeral=True)
        if not self.is_banned and self.user_id == guild.owner_id:
            return await it.response.send_message("❌ You cannot ban the server owner.", ephemeral=True)

        user = it.client.get_user(self.user_id)
        if user is None:
            try:
                user = await it.client.fetch_user(self.user_id)
            except Exception:
                user = discord.Object(id=self.user_id)
        name = getattr(user, "name", str(self.user_id))

        logs = it.client.get_cog("Logging") if hasattr(it.client, "get_cog") else None
        action = "unban" if self.is_banned else "ban"
        if logs is not None:  # so the unban/ban card names who clicked, not the bot
            logs.remember_click(guild.id, self.user_id, action, it.user)
        try:
            if self.is_banned:
                await guild.unban(user, reason=f"Unbanned with the ban-log button by {it.user}")
            else:
                await guild.ban(user, reason=f"Banned again with the ban-log button by {it.user}")
        except discord.NotFound:
            if logs is not None:
                logs.forget_click(guild.id, self.user_id, action)
            return await it.response.send_message(f"**{name}** isn't banned any more.", ephemeral=True)
        except discord.HTTPException as err:
            if logs is not None:
                logs.forget_click(guild.id, self.user_id, action)
            return await it.response.send_message(f"Couldn't {action} **{name}** (Discord said {err.status}).",
                                                  ephemeral=True)
        # the card itself says what happened: no extra message in the channel
        card = getattr(it, "message", None)
        embed = card.embeds[0].copy() if card is not None and getattr(card, "embeds", None) else None
        if embed is not None:
            done = "Unbanned" if self.is_banned else "Rebanned"
            note = f"{done} by {it.user.mention} {discord.utils.format_dt(discord.utils.utcnow(), 'R')}"
            status_idx = next((idx for idx, f in enumerate(embed.fields) if f.name == "Status"), None)
            if status_idx is not None:
                embed.set_field_at(status_idx, name="Status", value=note, inline=False)
            elif len(embed.fields) < 25:
                embed.add_field(name="Status", value=note, inline=False)
            else:
                embed.description = shorten(((embed.description or "") + "\n" + note).strip(), 4000)
            await it.response.edit_message(embed=embed, view=self.build(guild.id, self.user_id, not self.is_banned))
        else:
            await it.response.edit_message(view=self.build(guild.id, self.user_id, not self.is_banned))


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
                 disabled: bool = False, row: int = 1):
        super().__init__(label=label, emoji=emoji, style=style, disabled=disabled, row=row)
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

    async def interaction_check(self, it: discord.Interaction) -> bool:
        guild = getattr(it, "guild", None)
        origin_id = getattr(self.guild, "id", None)
        if (guild is None or origin_id is None or getattr(guild, "id", None) != origin_id
                or not owner_or_developer(it.user, guild, getattr(it, "client", None))):
            await it.response.send_message("Only the server owner or a bot developer can use this panel.", ephemeral=True)
            return False
        return await super().interaction_check(it)

    def embed(self) -> discord.Embed:
        lines = []
        for kind in KINDS.values():
            mark = "▸ " if kind.key == self.selected else ""
            lines.append(f"{mark}{kind.emoji} **{kind.label}** — {describe(self.guild, self.cfg, kind.key)}")
        where = "DM'd" if self.cfg.get(DM_KEY) else "in the channel"
        lines += ["", f"🔐 **Role blacklist replies** — {where}",
                  "-# `rbl list` is always visible in the invoking channel."]
        e = discord.Embed(title="Logging", color=Colors.main, description="\n".join(lines))
        e.set_footer(text=(f"Now choose where {KINDS[self.selected].label} logs go · " if self.selected else
                           "Pick a category, then a channel · ") + "Unset routes may share explicitly approved destinations")
        return e

    def rebuild(self):
        self.clear_items()
        self.add_item(KindSelect(self.selected))
        if self.selected:
            self.add_item(ChannelPick(KINDS[self.selected].label))
        # A select takes a whole row of its own: categories (row 0), channel (row 1), the actions (row 2),
        # and the one setting that is not a category (row 3).
        self.add_item(PanelButton("Create channels", "create", discord.ButtonStyle.primary, "✨", row=2))
        self.add_item(PanelButton("Turn off", "off", emoji="⛔", row=2))
        self.add_item(PanelButton("Auto", "auto", emoji="🔁", row=2))
        self.add_item(PanelButton("Done", "done", emoji="✅", row=2))
        self.add_item(PanelButton("Role blacklist: DM" if self.cfg.get(DM_KEY) else "Role blacklist: here",
                                  "toggle_dm", emoji="🔐", row=3))

    async def refresh(self, it: discord.Interaction):
        self.rebuild()
        await respond_or_edit(it, self.embed(), self)

    async def choose(self, it: discord.Interaction, key: str):
        self.selected = key
        await self.refresh(it)

    async def assign(self, it: discord.Interaction, channel_id: int):
        if await self._need_kind(it):
            return
        channel = self.guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return await it.response.send_message("Pick a text channel in this server.", ephemeral=True)
        if other_bots_channel(channel):
            return await it.response.send_message(
                "Another bot already logs to that channel; pick a Veil channel instead.",
                ephemeral=True,
            )
        if not can_post(channel):
            return await it.response.send_message(
                f"I need View Channel, Send Messages and Embed Links in {channel.mention}.", ephemeral=True)
        self.cfg = await self.cog.config(self.guild)
        self.cfg[self.selected] = channel_id
        if not isinstance(self.cfg.get("approved_routes"), dict):
            self.cfg["approved_routes"] = {}
        self.cfg["approved_routes"][self.selected] = channel_id
        await self.cog.save(self.guild, self.cfg)
        await self.refresh(it)

    async def _need_kind(self, it: discord.Interaction) -> bool:
        """A greyed-out button says nothing; say what is missing instead."""
        if self.selected:
            return False
        await it.response.send_message("Pick a category in the menu first — then **Turn off** and **Auto** work.",
                                       ephemeral=True)
        return True

    async def do_off(self, it: discord.Interaction):
        if await self._need_kind(it):
            return
        self.cfg = await self.cog.config(self.guild)
        self.cfg[self.selected] = OFF
        if isinstance(self.cfg.get("approved_routes"), dict):
            self.cfg["approved_routes"].pop(self.selected, None)
        await self.cog.save(self.guild, self.cfg)
        await self.refresh(it)

    async def do_auto(self, it: discord.Interaction):
        if await self._need_kind(it):
            return
        self.cfg = await self.cog.config(self.guild)
        self.cfg[self.selected] = None
        if isinstance(self.cfg.get("approved_routes"), dict):
            self.cfg["approved_routes"].pop(self.selected, None)
        await self.cog.save(self.guild, self.cfg)
        await self.refresh(it)

    async def do_toggle_dm(self, it: discord.Interaction):
        """Choose channel or DM for RBL replies other than the always-in-channel list."""
        self.cfg = await self.cog.config(self.guild)
        self.cfg[DM_KEY] = not self.cfg.get(DM_KEY)
        await self.cog.save(self.guild, self.cfg)
        await self.refresh(it)

    async def do_create(self, it: discord.Interaction):
        me = getattr(self.guild, "me", None)
        if not me or not getattr(me.guild_permissions, "manage_channels", False):
            return await it.response.send_message("I need **Manage Channels** to create log channels.", ephemeral=True)
        await it.response.defer()
        await self.cog.organize(self.guild)
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
        self._bursts: dict[tuple, _Burst] = {}
        self._feeds: dict[int, _Feed] = {}
        self._clicks: dict[tuple[int, int, str], tuple[object, float]] = {}
        self._setup_locks: dict[int, asyncio.Lock] = {}
        self._startup_done: bool = False

    async def cog_load(self):
        self.bot.add_dynamic_items(BanLogButton)

    async def cog_unload(self):
        self.bot.remove_dynamic_items(BanLogButton)
        for task in self._role_update_tasks.values():
            task.cancel()
        for b in [*self._bursts.values(), *self._feeds.values()]:
            if b.task is not None:
                b.task.cancel()

    # ------------------------------------------------------------ config and routing
    async def config(self, guild: discord.Guild) -> dict:
        return copy.deepcopy(await self.bot.db.get_config(guild.id, "logs"))

    async def save(self, guild: discord.Guild, cfg: dict) -> None:
        writer = getattr(self.bot.db, "set_config_committed", self.bot.db.set_config)
        try:
            await writer(guild.id, "logs", cfg)
        except Exception as exc:
            log.warning("Logging configuration save not confirmed in guild %s (%s)", guild.id, type(exc).__name__)
            raise UserError("Save not confirmed. Refresh and review the current settings before retrying.") from exc

    async def channel_for(self, guild: discord.Guild, kind: str) -> discord.TextChannel | None:
        if guild is None:
            return None
        return _channel_for(guild, await self.config(guild), canon(kind) or kind)

    async def route(self, guild: discord.Guild, *kinds: str, channel_id: int | None = None,
                    user_id: int | None = None) -> discord.TextChannel | None:
        """First channel that should get this event, or None (nothing set, switched off, or the source is ignored)."""
        if guild is None:
            return None
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

    async def rbl_private(self, guild: discord.Guild) -> bool:
        """Whether role-blacklist replies go to DMs: only when the server's owner (or a developer) asked for that."""
        return bool((await self.config(guild)).get(DM_KEY))

    async def _send(self, channel: discord.TextChannel, embed: discord.Embed, *, file: discord.File | None = None,
                    view: discord.ui.View | None = None) -> discord.Message | None:
        kwargs = {"embed": embed, "allowed_mentions": discord.AllowedMentions.none()}
        if file:
            kwargs["file"] = file
        if view:
            kwargs["view"] = view
        try:
            return await channel.send(**kwargs) or True
        except (discord.HTTPException, aiohttp.ClientError, asyncio.TimeoutError) as exc:
            log.warning("Log delivery failed for channel %s (%s)", channel.id, type(exc).__name__)
            return None

    async def post(self, channel: discord.TextChannel, embed: discord.Embed, *, file: discord.File | None = None,
                   view: discord.ui.View | None = None) -> bool:
        return await self._send(channel, embed, file=file, view=view) is not None

    # ------------------------------------------------------------ the feed: one line per small event
    def post_line(self, channel: discord.TextChannel, line: str) -> asyncio.Task:
        """Add a feed line (a command or a voice move). Lines that arrive close together go out as one
        message, and that message keeps growing for a minute while it has room, like an audit log."""
        feed = self._feeds.setdefault(channel.id, _Feed())
        feed.pending.append(line)
        if feed.task is None or feed.task.done():
            feed.task = asyncio.create_task(self._flush_feed(channel, feed))
        return feed.task

    async def _flush_feed(self, channel: discord.TextChannel, feed: _Feed) -> None:
        quiet = discord.AllowedMentions.none()  # a log line names people; it must never ping them
        while True:
            await asyncio.sleep(FEED_WAIT)
            if not feed.pending:
                return
            lines, feed.pending = feed.pending, []
            for line in lines:
                fits = feed.message is not None and len(feed.text) + len(line) + 1 <= FEED_LIMIT \
                    and time.monotonic() - feed.started <= FEED_KEEP
                if fits:
                    try:
                        await feed.message.edit(content=f"{feed.text}\n{line}", allowed_mentions=quiet)
                        feed.text = f"{feed.text}\n{line}"
                        continue
                    except Exception:  # deleted, or not ours to edit any more: start a new message
                        pass
                try:
                    feed.message = await channel.send(line, allowed_mentions=quiet)
                except (discord.HTTPException, aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    log.warning("Log feed delivery failed for channel %s (%s)", channel.id, type(exc).__name__)
                    feed.message = None
                    continue
                feed.text, feed.started = line, time.monotonic()

    # ------------------------------------------------------------ bursts: many of the same thing, one card
    def burst(self, channel: discord.TextChannel, kind: str, obj, actor=None, reason: str | None = None
              ) -> asyncio.Task:
        """Gather a role/channel creation or deletion. One on its own gets its normal card; several by the same
        person within a few seconds (a nuke, its clean-up, a test) share one card that grows as they come in."""
        now = time.monotonic()
        for key in [k for k, b in self._bursts.items()
                    if now - b.last > BURST_KEEP and (b.task is None or b.task.done())]:
            del self._bursts[key]
        key = (channel.id, kind, getattr(actor, "id", 0))
        b = self._bursts.get(key)
        if b is None or now - b.last > BURST_KEEP or len(b.items) >= BURST_MAX:
            b = self._bursts[key] = _Burst(actor, reason)
        b.items.append(obj)
        b.last = now
        if b.task is None or b.task.done():
            b.task = asyncio.create_task(self._flush(channel, kind, b))
        return b.task

    async def _flush(self, channel: discord.TextChannel, kind: str, b: _Burst) -> None:
        shown = 0
        while True:
            await asyncio.sleep(BURST_WAIT)
            count = len(b.items)
            if count == shown:
                return
            shown = count
            items = list(b.items)
            embed = (_SINGLE[kind](items[0], b.actor, b.reason) if count == 1
                     else fmt.grouped(kind, items, b.actor, b.reason))
            if b.message is not None and hasattr(b.message, "edit"):
                try:
                    await b.message.edit(embed=embed)
                    continue
                except discord.HTTPException:
                    pass  # deleted or not ours any more: post a fresh card
            b.message = await self._send(channel, embed)
            if b.message is None:
                return

    # ------------------------------------------------------------ ban-log button clicks
    def remember_click(self, guild_id: int, user_id: int, action: str, clicker) -> None:
        self._clicks[(guild_id, user_id, action)] = (clicker, time.monotonic())

    def forget_click(self, guild_id: int, user_id: int, action: str) -> None:
        self._clicks.pop((guild_id, user_id, action), None)

    def take_click(self, guild_id: int, user_id: int, action: str):
        """Who pressed the ban-log button for this ban/unban just now, if anyone."""
        found = self._clicks.pop((guild_id, user_id, action), None)
        if found and time.monotonic() - found[1] <= BUTTON_MEMORY:
            return found[0]
        return None

    async def send(self, guild, kind: str, embed: discord.Embed, file: discord.File | None = None,
                   view: discord.ui.View | None = None) -> bool:
        """Post an embed to a category's channel (used by other cogs too)."""
        channel = await self.route(guild, canon(kind) or kind)
        return bool(channel) and await self.post(channel, embed, file=file, view=view)

    # ------------------------------------------------------------ channel creation
    async def ensure_default_channels(self, guild: discord.Guild, kinds: tuple[str, ...] | None = None
                                      ) -> list[discord.TextChannel]:
        """Create the private log channels that are missing (under a VEIL LOGS category) and point the config at them."""
        return (await self.organize(guild, kinds)).created

    async def organize(self, guild: discord.Guild, kinds: tuple[str, ...] | None = None) -> SetupReport:
        """Set up private Veil log channels under VEIL LOGS, reusing existing Veil channels and splitting old catch-alls."""
        async with self._setup_locks.setdefault(guild.id, asyncio.Lock()):
            report = SetupReport()
            me = getattr(guild, "me", None)
            if not me or not getattr(me.guild_permissions, "manage_channels", False):
                report.failures.append("Missing Manage Channels permission.")
                return report

            cfg = await self.config(guild)
            if not isinstance(cfg.get("approved_routes"), dict):
                cfg["approved_routes"] = {}
            approved = cfg["approved_routes"]

            existing_cat = next((c for c in getattr(guild, "categories", ()) if _is_veil_category(c)), None)
            if existing_cat is not None:
                report.category = existing_cat
                if getattr(existing_cat, "position", 0) > 0 and callable(getattr(existing_cat, "edit", None)):
                    try:
                        await existing_cat.edit(position=0)
                        report.raised = True
                    except discord.HTTPException:
                        pass

            server_ch = _set_channel(guild, cfg, "server") or _auto_channel(guild, "server", setup_only=True)
            target_kinds = SETUP_KINDS if kinds is None else kinds
            todo: list[str] = []
            changed_existing = False

            for key in target_kinds:
                if not KINDS[key].channel or cfg.get(key) == OFF:
                    continue
                current = _set_channel(guild, cfg, key)
                if (key != "server" and current is not None and server_ch is not None
                        and current.id == server_ch.id and _clean_name(current.name) in _CATCHALL_NAMES):
                    current = None
                if current is not None:
                    continue
                raw_val = cfg.get(key)
                if isinstance(raw_val, int) and not isinstance(raw_val, bool) and raw_val > 0 and _is_approved(cfg, key, raw_val):
                    get_channel = getattr(guild, "get_channel", None)
                    if callable(get_channel) and get_channel(raw_val) is None:
                        continue
                reused = _auto_channel(guild, key, setup_only=True)
                if reused is not None:
                    if cfg.get(key) != reused.id or approved.get(key) != reused.id:
                        cfg[key] = reused.id
                        approved[key] = reused.id
                        changed_existing = True
                    if key == "server":
                        server_ch = reused
                    continue
                todo.append(key)

            # Reset catch-all carried categories other than messages/server when splitting
            if server_ch is not None:
                for carried_key in ("members",):
                    if cfg.get(carried_key) == server_ch.id:
                        cfg.pop(carried_key, None)
                        approved.pop(carried_key, None)
                        changed_existing = True
                if cfg.get("messages") is None and _auto_channel(guild, "messages", setup_only=True) is None:
                    cfg["messages"] = server_ch.id
                    approved["messages"] = server_ch.id
                    changed_existing = True

            if not todo:
                if changed_existing:
                    try:
                        await self.save(guild, cfg)
                    except Exception as exc:
                        log.warning("Log setup persistence could not be confirmed in guild %s (%s)", guild.id, type(exc).__name__)
                        report.failures.append("Save not confirmed; review before retrying.")
                return report

            private = {guild.default_role: discord.PermissionOverwrite(view_channel=False, send_messages=False),
                       me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True,
                                                       attach_files=True)}
            if report.category is None:
                try:
                    report.category = await guild.create_category(CATEGORY_NAME, overwrites=private,
                                                                  reason="Veil Utilities: log setup")
                except discord.HTTPException as exc:
                    report.failures.append(f"Category creation failed ({type(exc).__name__}).")
                    return report
                if getattr(report.category, "position", 0) > 0 and callable(getattr(report.category, "edit", None)):
                    try:
                        await report.category.edit(position=0)
                        report.raised = True
                    except discord.HTTPException:
                        pass

            for key in todo:
                try:
                    channel = await guild.create_text_channel(KINDS[key].channel, category=report.category,
                        topic=f"Veil Utilities · {KINDS[key].blurb}", overwrites=private,
                        reason="Veil Utilities: log setup")
                except discord.HTTPException as exc:
                    report.failures.append(f"{KINDS[key].label}: creation failed ({type(exc).__name__}).")
                    continue
                report.created.append(channel)
                cfg[key] = channel.id
                approved[key] = channel.id
                if key == "server" and cfg.get("messages") is None and _auto_channel(guild, "messages", setup_only=True) is None:
                    cfg["messages"] = channel.id
                    approved["messages"] = channel.id
                try:
                    await self.save(guild, cfg)
                except Exception as exc:
                    log.warning("Log setup persistence could not be confirmed in guild %s (%s)", guild.id, type(exc).__name__)
                    report.failures.append(f"Save not confirmed for {channel.mention}; review before retrying.")
                    break
            return report

    async def auto_setup_enabled(self) -> bool:
        g = await self.bot.db.get_config(0, "global")
        return bool(g.get("auto_log_setup", True))

    async def set_auto_setup(self, enabled: bool) -> None:
        g = dict(await self.bot.db.get_config(0, "global") or {})
        g["auto_log_setup"] = bool(enabled)
        await self.bot.db.set_config(0, "global", g)

    async def _set_auto(self, ctx: Context, mode: str) -> None:
        from core import perms as _perms
        if not _perms.is_bot_developer(ctx.author, self.bot):
            raise UserError("Only a bot developer can change automatic log setup.")
        enabled = mode.lower() in ("on", "enable", "true", "auto")
        await self.set_auto_setup(enabled)
        if enabled:
            totals = await self.organize_everywhere()
            await ctx.approve(f"Automatic log setup **enabled** across **{totals['servers']}** server(s) "
                              f"(created **{totals['created']}** channel(s)).")
        else:
            await ctx.approve("Automatic log setup **disabled**.")

    async def organize_everywhere(self, pause: float = 2.0) -> dict[str, int]:
        try:
            await devchannels.ensure(self.bot)
        except Exception:
            pass
        totals = {"servers": 0, "created": 0, "moved": 0, "skipped": 0}
        for guild in list(getattr(self.bot, "guilds", ())):
            is_auth = getattr(self.bot, "is_authorized", None)
            me = getattr(guild, "me", None)
            if (callable(is_auth) and not is_auth(guild.id)) or not me or not getattr(me.guild_permissions, "manage_channels", False):
                totals["skipped"] += 1
                continue
            report = await self.organize(guild)
            totals["servers"] += 1
            totals["created"] += len(report.created)
            totals["moved"] += len(report.moved)
            if pause:
                await asyncio.sleep(pause)
        return totals

    @commands.Cog.listener()
    async def on_ready(self):
        if self._startup_done:
            return
        self._startup_done = True
        if await self.auto_setup_enabled():
            totals = await self.organize_everywhere()
            log.info("Discord logging ready · %d server(s) checked · %d channel(s) created",
                     totals["servers"], totals["created"])
        else:
            log.info("Discord logging ready · %d server(s) attached (auto-setup off)",
                     len(getattr(self.bot, "guilds", ())))

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        if not await self.auto_setup_enabled():
            return
        await asyncio.sleep(2)
        is_auth = getattr(self.bot, "is_authorized", None)
        if callable(is_auth) and not is_auth(guild.id):
            get_cog = getattr(self.bot, "get_cog", None)
            auth = get_cog("Authorization") if callable(get_cog) else None
            waiter = getattr(auth, "wait_for_pending_approval", None)
            if not callable(waiter) or not await waiter(guild.id):
                return
        await self.organize(guild)

    # ------------------------------------------------------------ commands
    @commands.group(aliases=["log"], invoke_without_command=True)
    @commands.guild_only()
    @has_perms(manage_guild=True)
    @is_server_owner()
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
    @is_server_owner()
    async def logs_set(self, ctx: Context, category: str, channel: str = None):
        """Send a log category to a channel

        `off` stops it, `auto` uses an explicitly approved shared route if available.
        Example: `,logs set messages #logs`
        """
        kinds = await self._kinds(category)
        cfg = await self.config(ctx.guild)
        word = (channel or "auto").lower()
        if word in ("off", "none", "disable"):
            value, shown = OFF, "off"
        elif word == "auto":
            value, shown = None, "auto"
        else:
            target = await commands.TextChannelConverter().convert(ctx, channel)
            if other_bots_channel(target):
                raise UserError("Another bot already logs to that channel; choose a Veil channel instead.")
            if not can_post(target):
                raise UserError(f"I need View Channel, Send Messages and Embed Links in {target.mention}.")
            value, shown = target.id, target.mention
        if not isinstance(cfg.get("approved_routes"), dict):
            cfg["approved_routes"] = {}
        approved = cfg["approved_routes"]
        for key in kinds:
            cfg[key] = value
            if isinstance(value, int) and value > 0:
                approved[key] = value
            else:
                approved.pop(key, None)
        await self.save(ctx.guild, cfg)
        label = "all logs" if len(kinds) > 1 else f"**{KINDS[kinds[0]].label}** logs"
        await ctx.approve(f"{label} → {shown}")

    @logs.command(name="off", aliases=["disable"])
    @has_perms(manage_guild=True)
    @is_server_owner()
    async def logs_off(self, ctx: Context, category: str):
        """Stop logging a category

        `all` stops every category.
        """
        await self.logs_set(ctx, category, "off")

    @logs.command(name="auto", aliases=["reset"])
    @has_perms(manage_guild=True)
    @is_server_owner()
    async def logs_auto(self, ctx: Context, category: str):
        """Use an approved shared route if available"""
        await self.logs_set(ctx, category, "auto")

    @logs.command(name="setup", aliases=["init", "default", "defaults", "create"])
    @has_perms(manage_guild=True)
    @is_server_owner()
    async def logs_setup(self, ctx: Context, mode: str = None):
        """Preview and confirm creation of channels for unset log categories"""
        if mode is not None:
            mode_clean = mode.strip().lower()
            if mode_clean in ("on", "off", "enable", "disable", "auto"):
                return await self._set_auto(ctx, mode_clean)
            raise UserError("Use `logs setup` to create missing log channels, or `logs setup on|off` (developers only).")
        if not ctx.guild.me.guild_permissions.manage_channels:
            raise UserError("I need Manage Channels to create log channels.")
        async with ctx.typing():
            report = await self.organize(ctx.guild)
        cfg = await self.config(ctx.guild)
        lines = [f"{k.emoji} **{k.label}** — {describe(ctx.guild, cfg, k.key)}" for k in KINDS.values()]
        if report.created:
            lines.append("")
            lines.append(f"Created **{len(report.created)}** channel(s).")
        lines.extend(report.failures)
        await ctx.send(embed=discord.Embed(title="Logging setup results", description="\n".join(lines),
                                           color=Colors.error if report.failures else Colors.success))

    @commands.command(name="setuplogs", aliases=["setupchannels", "autologs"])
    @has_perms(manage_guild=True)
    @is_server_owner()
    async def cmd_setuplogs(self, ctx: Context):
        """Preview and confirm creation of missing log destinations

        Same as `logs setup`.
        """
        await self.logs_setup(ctx)

    @logs.command(name="list", aliases=["show", "status"])
    @has_perms(manage_guild=True)
    @is_server_owner()
    async def logs_list(self, ctx: Context):
        """Show where every log category goes, without opening the panel"""
        cfg = await self.config(ctx.guild)
        lines = [f"{k.emoji} **{k.label}** — {describe(ctx.guild, cfg, k.key)}" for k in KINDS.values()]
        lines.append("")
        lines.append("-# `auto` matches by channel name · `shared` rides in a related log channel")
        await ctx.send(embed=discord.Embed(title="Where logs go", color=Colors.main, description="\n".join(lines)))

    @logs.command(name="coexist")
    @is_server_owner()
    async def logs_coexist(self, ctx: Context):
        """Review routing when multiple logging tools are in use"""
        await ctx.send(embed=discord.Embed(
            title="Logging review", color=Colors.main,
            description="Veil skips log channels that already belong to another bot. It can only read and "
                        "change its own settings, so review `logs list` and turn overlapping categories off in "
                        "one of the bots to avoid duplicate events."))

    @logs.command(name="test")
    @has_perms(manage_guild=True)
    @is_server_owner()
    async def logs_test(self, ctx: Context):
        """Send a test line to every log channel

        Shows which ones are missing permissions.
        """
        cfg = await self.config(ctx.guild)
        ok, failed, unset = [], [], []
        for kind in KINDS.values():
            channel = _channel_for(ctx.guild, cfg, kind.key)
            if channel is None:
                unset.append(kind.label)
                continue
            embed = fmt.field_card(fmt.SLATE, f"Log Test — {kind.label}", subject=ctx.author, fields=[
                ("Category", kind.label, True),
                ("Triggered By", ctx.author.mention, True),
            ], ids=[("User ID", ctx.author.id)])
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
    @is_server_owner()
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
    @is_server_owner()
    async def logs_ignore_add(self, ctx: Context, target: discord.TextChannel | discord.VoiceChannel | discord.Member):
        """Ignore a channel or member"""
        await self._toggle_ignore(ctx, target, True)

    @logs_ignore.command(name="remove", aliases=["del", "delete"])
    @has_perms(manage_guild=True)
    @is_server_owner()
    async def logs_ignore_remove(self, ctx: Context, target: discord.TextChannel | discord.VoiceChannel | discord.Member):
        """Stop ignoring a channel or member"""
        await self._toggle_ignore(ctx, target, False)

    @logs.command(name="cleanup", aliases=["clean", "purgeduplicates", "cleanupchannels"])
    @has_perms(manage_guild=True)
    @is_server_owner()
    async def logs_cleanup(self, ctx: Context, delete: str = None):
        """Explain safe manual cleanup without deleting channels"""
        cfg = await self.config(ctx.guild)
        active_ids = {ch.id for k in KINDS if (ch := _channel_for(ctx.guild, cfg, k)) is not None}
        veil_logs = [c for c in getattr(ctx.guild, "text_channels", ()) if _is_log_channel(c)]
        name_counts: dict[str, list] = {}
        for c in veil_logs:
            name_counts.setdefault(_clean_name(c.name), []).append(c)
        duplicates = [c for group in name_counts.values() if len(group) > 1 for c in group]
        leftovers = [c for c in veil_logs if c.id not in active_ids and c not in duplicates]

        if delete and delete.strip().lower() == "delete":
            removable = [c for c in veil_logs if c.id not in active_ids and _clean_name(c.name) in _CATCHALL_NAMES]
            if not removable:
                return await ctx.neutral("No leftover catch-all log channels to remove. Nothing was deleted.")
            removed = 0
            for c in removable:
                try:
                    await c.delete(reason=f"Veil Utilities: logs cleanup delete by {ctx.author}")
                    removed += 1
                except discord.HTTPException:
                    pass
            return await ctx.approve(f"deleted **{removed}** leftover log channel(s)")

        flagged = list(dict.fromkeys([*leftovers, *duplicates]))
        if not flagged:
            return await ctx.neutral("No duplicate or leftover log channels found. Nothing was deleted.")
        lines = [f"• {c.mention} (`#{c.name}`)" for c in flagged]
        lines.append("")
        lines.append(f"Nothing was deleted. Run `{getattr(ctx, 'clean_prefix', ',')}logs cleanup delete` to remove "
                     "unused leftover catch-all channels, or remove duplicates manually.")
        embed = discord.Embed(title="Log channel cleanup", color=Colors.main, description="\n".join(lines))
        await ctx.paginate([embed])

    @commands.command(name="cleanuplogs", aliases=["cleanuplogchannels", "purgeduplicatelogs"])
    @has_perms(manage_guild=True)
    @is_server_owner()
    async def cmd_cleanuplogs(self, ctx: Context):
        """List possible duplicate log channels without deleting anything

        Same as `logs cleanup`.
        """
        await self.logs_cleanup(ctx)

    # ------------------------------------------------------------ commands run
    @commands.Cog.listener()
    async def on_command(self, ctx: Context):
        ctx._log_started = time.perf_counter()  # so the card can say how long the command took

    async def _log_command(self, ctx: Context, error: str | None = None):
        if not ctx.guild or ctx.author.bot or ctx.command is None:
            return
        started = getattr(ctx, "_log_started", None)
        ms = (time.perf_counter() - started) * 1000 if started is not None else None
        cmd_name = getattr(ctx.command, "qualified_name", "command")
        status = "error" if error else ("warn" if getattr(ctx, "command_log_warning", False) else "ok")
        took = f" · {ms:.0f}ms" if ms is not None else ""
        log.info("Command %s%s · user=%s · guild=%s [%s%s]",
                 getattr(ctx, "clean_prefix", ","), cmd_name, ctx.author.id, ctx.guild.id, status, took)
        channel = await self.route(ctx.guild, "commands", channel_id=getattr(ctx.channel, "id", None),
                                   user_id=ctx.author.id)
        if not channel:
            return
        from core import perms as _perms
        cfg = await self.bot.db.get_config(ctx.guild.id, "permlevels")
        is_dev = _perms.is_bot_developer(ctx.author, self.bot) or await self.bot.is_owner(ctx.author)
        is_owner = ctx.author.id == ctx.guild.owner_id
        level, granted_by, rank = _perms.member_level_info(cfg, ctx.author, is_owner=is_owner, is_dev=is_dev)
        if is_dev:
            permission, granted = _perms.DEV_NAME, "Built-in"
        elif level and granted_by:
            permission, granted = level, granted_by
        elif is_owner:
            permission, granted = _perms.OWNER_NAME, "Built-in"
        else:
            permission, granted, rank = "None", "-", None
        await self.post(channel, fmt.command_used(ctx, permission, granted, rank=rank, ms=ms, error=error))

    @commands.Cog.listener()
    async def on_command_completion(self, ctx: Context):
        await self._log_command(ctx)

    @commands.Cog.listener()
    async def on_command_error(self, ctx: Context, error: commands.CommandError):
        # failures are worth a card too (who tried what, and why it didn't work); noise is not
        if isinstance(error, (Ignored, commands.CommandNotFound, commands.CommandOnCooldown)):
            return
        if isinstance(getattr(error, "original", error), USAGE_SLIPS):
            return  # left something out: they were shown how to use it, nothing went wrong
        original = getattr(error, "original", error)
        if isinstance(original, commands.UserInputError):  # typed something wrong: say what, in plain words
            reason = input_error_text(original)
            reason = reason[:1].upper() + reason[1:]
        elif isinstance(original, (UserError, commands.CheckFailure)):
            reason = fmt.redact.scrub(str(original).strip()) or "Not allowed"
        else:
            reason = f"{type(original).__name__}: details withheld"
        await self._log_command(ctx, reason)

    # ------------------------------------------------------------ messages
    @commands.Cog.listener()
    async def on_message_delete(self, m: discord.Message):
        if not m.guild or m.author.bot or autoclean.was_cleaned(m.id):  # the bot tidying a command away
            return
        channel = await self.route(m.guild, "messages", channel_id=m.channel.id, user_id=m.author.id)
        if not channel:
            return
        # Discord audit entries aggregate deletions and do not identify a particular message.
        await self.post(channel, fmt.message_deleted(m))

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages: list[discord.Message]):
        if not messages or not messages[0].guild:
            return
        first = messages[0]
        channel = await self.route(first.guild, "messages", channel_id=first.channel.id)
        if not channel:
            return
        ignore = (await self.config(first.guild)).get("ignore") or {}
        messages = [m for m in messages if m.guild == first.guild and m.channel.id == first.channel.id
                    and m.author.id not in ignore.get("users", []) and not m.author.bot
                    and not autoclean.was_cleaned(m.id)]
        if not messages:
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
        if m.guild is None:
            return  # a DM: nothing to log
        channel = await self.member_stream_channel(m.guild, m.id)
        if channel:
            await self.post(channel, fmt.member_joined(m))

    @commands.Cog.listener()
    async def on_member_remove(self, m: discord.Member):
        if m.guild is None:
            return  # a DM: nothing to log
        left = await self.member_stream_channel(m.guild, m.id)
        mod = await self.route(m.guild, "bans", user_id=m.id)
        if not (left or mod):
            return
        kicker, reason = await fmt.audit_actor(m.guild, discord.AuditLogAction.kick, m.id)
        if kicker:  # a kick is moderation; it only lands in the leave log when there is no moderation channel
            await self.post(mod or left, fmt.member_left(m, kicker, reason))
        elif left:
            await self.post(left, fmt.member_left(m))

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User | discord.Member):
        channel = await self.route(guild, "bans", user_id=user.id)
        if not channel:
            return
        mod, reason = await fmt.audit_actor(guild, discord.AuditLogAction.ban, user.id)
        clicker = self.take_click(guild.id, user.id, "ban")
        if clicker is not None:
            mod, reason = clicker, None
        await self.post(channel, fmt.user_banned(user, mod, reason, clicked=clicker is not None),
                        view=BanLogButton.build(guild.id, user.id, True))

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        channel = await self.route(guild, "bans", user_id=user.id)
        if not channel:
            return
        mod, reason = await fmt.audit_actor(guild, discord.AuditLogAction.unban, user.id)
        clicker = self.take_click(guild.id, user.id, "unban")
        if clicker is not None:
            mod, reason = clicker, None
        await self.post(channel, fmt.user_unbanned(user, mod, reason, clicked=clicker is not None),
                        view=BanLogButton.build(guild.id, user.id, False))

    async def member_stream_channel(self, guild: discord.Guild, user_id: int | None = None) -> discord.TextChannel | None:
        return await self.route(guild, "joinleave", user_id=user_id)

    async def timeout_channel(self, guild: discord.Guild, user_id: int | None = None) -> discord.TextChannel | None:
        if guild is None:
            return None
        cfg = await self.config(guild)
        ignore = cfg.get("ignore") or {}
        if user_id and user_id in ignore.get("users", []):
            return None
        return _channel_for(guild, cfg, "timeouts") or _timeout_fallback(guild, cfg)

    # ------------------------------------------------------------ member updates and roles
    async def _build_role_update_embed(self, member: discord.Member, added: list, removed: list,
                                       had: int | None = None) -> discord.Embed:
        """The roles-changed card. `had` is how many roles the member had before the burst started, which is what
        tells an ordinary edit apart from a strip."""
        actor, reason = await fmt.audit_actor(member.guild, discord.AuditLogAction.member_role_update, member.id)
        return fmt.roles_updated(member, added, removed, actor, reason, total=had)

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
            channel = await self.timeout_channel(after.guild, after.id)
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
            await self.post(channel, await self._build_role_update_embed(last, added, removed, had=len(first.roles)))

    # ------------------------------------------------------------ roles, channels, server
    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        channel = await self.route(role.guild, "roles")
        if channel:
            actor, reason = await fmt.audit_actor(role.guild, discord.AuditLogAction.role_create, role.id)
            await self.burst(channel, "role_create", role, actor, reason)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        channel = await self.route(role.guild, "roles")
        if channel:
            actor, reason = await fmt.audit_actor(role.guild, discord.AuditLogAction.role_delete, role.id)
            await self.burst(channel, "role_delete", role, actor, reason)

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
            actor, reason = await fmt.audit_actor(c.guild, discord.AuditLogAction.channel_create, c.id)
            await self.burst(channel, "channel_create", c, actor, reason)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, c: discord.abc.GuildChannel):
        channel = await self.route(c.guild, "channels")
        if channel:
            actor, reason = await fmt.audit_actor(c.guild, discord.AuditLogAction.channel_delete, c.id)
            await self.burst(channel, "channel_delete", c, actor, reason)

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
        embed = fmt.voice_embed(m, before, after)
        if embed is None:
            return
        where = after.channel or before.channel
        channel = await self.route(m.guild, "voice", channel_id=getattr(where, "id", None), user_id=m.id)
        if channel:
            await self.post(channel, embed)


async def setup(bot):
    await bot.add_cog(Logging(bot))
