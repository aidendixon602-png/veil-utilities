"""Antinuke (audit-log driven, threshold based) and Antiraid (join filters)."""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from datetime import timedelta

import discord
from discord.ext import commands

from core.antinukepanel import AntinukePanel, home_embed
from core.config import settings
from core.nukerestore import Snapshots, collapse, display, restore_channels, restore_roles
from core.utils import Colors, Context, UserError, antinuke_admin, has_perms, is_dangerous

# verbs for the "that would … at once" message
VERBS = {"ban": "ban", "kick": "kick", "channeldelete": "delete channels for", "roledelete": "delete roles for",
         "rolecreate": "create roles for", "permissions": "grant staff permissions to", "timeout": "time out",
         "unban": "unban"}

A = discord.AuditLogAction
MODULES = {
    "ban": "Mass banning members", "kick": "Mass kicking members",
    "channeldelete": "Deleting channels", "channelcreate": "Creating channels",
    "roledelete": "Deleting roles", "rolecreate": "Creating roles",
    "webhook": "Creating webhooks", "emoji": "Deleting emojis/stickers",
    "botadd": "Adding bots", "vanity": "Changing the vanity URL",
    "permissions": "Granting dangerous permissions to roles/members",
    "prune": "Pruning members",
    "timeout": "Mass timing out members", "unban": "Mass unbanning users",
}
# modules that default to a higher threshold than 3 when turned on with `antinuke enable`
DEFAULT_THRESHOLDS = {"timeout": 5}

MIDTOWN_RULES = [
    {"key": "ban", "name": "Ban", "threshold": 3, "window": 60, "punishment": "ban", "tags": "[revert]"},
    {"key": "botadd", "name": "Bot Add", "threshold": 1, "window": 60, "punishment": "ban", "tags": "[revert]"},
    {"key": "channelcreate", "name": "Channel Create", "threshold": 2, "window": 120, "punishment": "kick", "tags": "[revert]"},
    {"key": "channeldelete", "name": "Channel Delete", "threshold": 2, "window": 120, "punishment": "ban", "tags": None},
    {"key": "kick", "name": "Kick", "threshold": 3, "window": 120, "punishment": "ban", "tags": None},
    {"key": "permissions", "name": "Permission Grant", "extra": " (administrator, ban_members, kick_members, manage_channels, manage_guild, manage_webhooks)", "threshold": 1, "window": 60, "punishment": "strip", "tags": "[revert, user]"},
    {"key": "rolecreate", "name": "Role Create", "threshold": 5, "window": 120, "punishment": "strip", "tags": "[revert]"},
    {"key": "roledelete", "name": "Role Delete", "threshold": 5, "window": 120, "punishment": "strip", "tags": None},
    {"key": "roleinvite", "name": "Invite Spam", "threshold": 10, "window": 60, "punishment": "strip", "tags": "[revert]"},
    {"key": "serverupdate", "name": "Server Update", "threshold": 1, "window": 60, "punishment": "strip", "tags": None},
    {"key": "timeout", "name": "Timeout", "threshold": 5, "window": 120, "punishment": "strip", "tags": "[revert]"},
    {"key": "webhook", "name": "Webhook", "threshold": 1, "window": 60, "punishment": "kick", "tags": "[revert]"},
]
RULE_LOOKUP = {r["key"]: r for r in MIDTOWN_RULES}

PRESETS = {
    "default": {
        "ban": {"threshold": 3, "window": 60, "punishment": "ban"},
        "botadd": {"threshold": 1, "window": 60, "punishment": "ban"},
        "channelcreate": {"threshold": 2, "window": 120, "punishment": "kick"},
        "channeldelete": {"threshold": 2, "window": 120, "punishment": "ban"},
        "kick": {"threshold": 3, "window": 120, "punishment": "ban"},
        "permissions": {"threshold": 1, "window": 60, "punishment": "strip"},
        "rolecreate": {"threshold": 5, "window": 120, "punishment": "strip"},
        "roledelete": {"threshold": 5, "window": 120, "punishment": "strip"},
        "roleinvite": {"threshold": 10, "window": 60, "punishment": "strip"},
        "serverupdate": {"threshold": 1, "window": 60, "punishment": "strip"},
        "timeout": {"threshold": 5, "window": 120, "punishment": "strip"},
        "webhook": {"threshold": 1, "window": 60, "punishment": "kick"},
    },
    "strict": {
        "ban": {"threshold": 1, "window": 60, "punishment": "ban"},
        "botadd": {"threshold": 1, "window": 60, "punishment": "ban"},
        "channelcreate": {"threshold": 1, "window": 60, "punishment": "ban"},
        "channeldelete": {"threshold": 1, "window": 60, "punishment": "ban"},
        "kick": {"threshold": 1, "window": 60, "punishment": "ban"},
        "permissions": {"threshold": 1, "window": 60, "punishment": "strip"},
        "rolecreate": {"threshold": 1, "window": 60, "punishment": "strip"},
        "roledelete": {"threshold": 1, "window": 60, "punishment": "strip"},
        "roleinvite": {"threshold": 5, "window": 60, "punishment": "strip"},
        "serverupdate": {"threshold": 1, "window": 60, "punishment": "strip"},
        "timeout": {"threshold": 2, "window": 60, "punishment": "strip"},
        "webhook": {"threshold": 1, "window": 60, "punishment": "ban"},
    },
    "relaxed": {
        "ban": {"threshold": 5, "window": 120, "punishment": "ban"},
        "botadd": {"threshold": 2, "window": 60, "punishment": "ban"},
        "channelcreate": {"threshold": 5, "window": 180, "punishment": "kick"},
        "channeldelete": {"threshold": 3, "window": 180, "punishment": "ban"},
        "kick": {"threshold": 5, "window": 180, "punishment": "ban"},
        "permissions": {"threshold": 2, "window": 60, "punishment": "strip"},
        "rolecreate": {"threshold": 10, "window": 180, "punishment": "strip"},
        "roledelete": {"threshold": 10, "window": 180, "punishment": "strip"},
        "roleinvite": {"threshold": 20, "window": 60, "punishment": "strip"},
        "serverupdate": {"threshold": 2, "window": 60, "punishment": "strip"},
        "timeout": {"threshold": 10, "window": 180, "punishment": "strip"},
        "webhook": {"threshold": 2, "window": 60, "punishment": "kick"},
    },
}


def parse_timeframe(tf: str | int) -> int:
    if isinstance(tf, int):
        return max(5, min(tf, 86400))
    s = str(tf).strip().lower()
    if s.endswith("s"):
        s = s[:-1]
    elif s.endswith("m"):
        return max(5, min(int(float(s[:-1]) * 60), 86400))
    elif s.endswith("h"):
        return max(5, min(int(float(s[:-1]) * 3600), 86400))
    try:
        val = int(float(s))
        return max(5, min(val, 86400))
    except (ValueError, TypeError):
        raise UserError(f"Invalid timeframe `{tf}`. Example: `60s`, `120s`, `2m`")


# channel-overwrite permissions that are staff powers inside a channel (administrator can't be set there)
OVERWRITE_DANGEROUS = discord.Permissions(manage_roles=True, manage_channels=True, manage_webhooks=True,
                                          mention_everyone=True, manage_messages=True)
ACTION_MAP = {
    A.ban: "ban", A.kick: "kick", A.member_prune: "prune",
    A.channel_delete: "channeldelete", A.channel_create: "channelcreate",
    A.role_delete: "roledelete", A.role_create: "rolecreate",
    A.webhook_create: "webhook", A.emoji_delete: "emoji", A.sticker_delete: "emoji",
    A.bot_add: "botadd", A.guild_update: "serverupdate",
    A.role_update: "permissions",
    A.overwrite_create: "permissions", A.overwrite_update: "permissions",
    A.member_update: "timeout", A.unban: "unban",
    A.invite_create: "roleinvite",
}
INSTANT = {"botadd", "vanity", "permissions", "prune", "serverupdate"}  # threshold of 1
# what the antinuke puts right for each kind of action once it catches someone (shown as [revert] in `,an`)
UNDOES = {
    "ban": "unbans the people they banned", "unban": "bans the people they unbanned again",
    "channeldelete": "rebuilds the channels they deleted", "channelcreate": "deletes the channels they made",
    "roledelete": "rebuilds the roles they deleted and gives them back", "rolecreate": "deletes the roles they made",
    "webhook": "deletes the webhooks they made", "roleinvite": "deletes the invites they made",
    "botadd": "bans the bot they added", "permissions": "takes the permissions back",
    "timeout": "removes the timeouts they gave", "serverupdate": "restores the server name and vanity URL",
}
# things other bots create as part of their normal job; destructive actions by bots still count
BOT_JOBS = {"channelcreate", "rolecreate", "webhook", "roleinvite"}
RAID_QUIET = 300  # seconds without a new join wave before raid mode ends by itself
RAID_PAUSE_FOR = 3600  # invites/DMs are paused this long at most (Discord resumes them itself if I'm offline)
RAID_LOCK_DEFAULTS = {"invites": True, "channels": False}  # what raid mode does unless a server changes it
LOOKBACK = 600  # seconds: when someone is caught, everything they did in this long is undone
CAUGHT_FOR = 120  # seconds after being caught during which anything else they do is undone straight away
LABELS = {
    "ban": "Mass Ban", "kick": "Mass Kick", "prune": "Member Prune",
    "channeldelete": "Channel Delete", "channelcreate": "Channel Create",
    "roledelete": "Role Delete", "rolecreate": "Role Create", "webhook": "Webhook Create",
    "emoji": "Emoji Delete", "botadd": "Bot Add", "vanity": "Vanity Change",
    "serverupdate": "Server Update", "permissions": "Permission Grant",
    "timeout": "Mass Timeout", "unban": "Mass Unban", "roleinvite": "Invite Spam",
}


def gained_dangerous(before: discord.Permissions | None, after: discord.Permissions | None) -> bool:
    """True if `after` has a dangerous permission that `before` didn't (bit-wise, not by total value:
    swapping Manage Server for Administrator lowers the number but is a big escalation)."""
    if after is None:
        return False
    added = discord.Permissions(after.value & ~(before.value if before is not None else 0))
    return is_dangerous(added)


def overwrite_gained(before, after) -> bool:
    """For channel overwrite audit entries: did the allow set gain a staff power?"""
    allow_after = getattr(after, "allow", None)
    if allow_after is None:
        return False
    allow_before = getattr(before, "allow", None)
    added = allow_after.value & ~(allow_before.value if allow_before is not None else 0)
    return bool(added & OVERWRITE_DANGEROUS.value)


ALERT_MERGE_WINDOW = 60  # seconds: repeat triggers by the same user + module update one alert instead of new ones


class Incident:
    """One antinuke incident: what happened, and the alert messages sent about it (edited when it repeats)."""

    def __init__(self, *, guild, user, actor_id, module, threshold, window, via, roles, result, undone, problems):
        self.guild, self.user, self.actor_id, self.module = guild, user, actor_id, module
        self.threshold, self.window, self.via, self.roles = threshold, window, via, roles
        self.result, self.undone, self.problems = result, list(undone), list(problems)
        self.count = 1
        self.first = self.last = time.monotonic()
        self.messages: list[discord.Message] = []

    def merge(self, *, result, undone, problems, roles) -> None:
        self.count += 1
        self.last = time.monotonic()
        self.result = result
        self.roles = roles or self.roles
        for line in undone:
            if line not in self.undone:
                self.undone.append(line)
        for line in problems:
            if line not in self.problems:
                self.problems.append(line)


def _bullets(lines: list[str]) -> str:
    text = "\n".join(f"• {line}" for line in lines)
    return text if len(text) <= 1024 else text[:1020] + "…"


def alert_embed(inc: Incident, footer: str) -> discord.Embed:
    """The alert card sent to the log channel, server owner and developers."""
    label = LABELS.get(inc.module, inc.module)
    e = discord.Embed(title=f"Antinuke triggered: {label}", color=Colors.error, timestamp=discord.utils.utcnow())
    name = getattr(inc.user, "name", None) or "Unknown user"
    is_bot = bool(getattr(inc.user, "bot", False))
    avatar = getattr(getattr(inc.user, "display_avatar", None), "url", None)
    e.set_author(name=name, icon_url=avatar)
    e.add_field(name="User", value=f"{discord.utils.escape_markdown(name)}{' (bot)' if is_bot else ''}\n`{inc.actor_id}`")
    e.add_field(name="Server", value=f"{discord.utils.escape_markdown(inc.guild.name)}\n`{inc.guild.id}`")
    e.add_field(name="Limit", value=f"{inc.threshold} in {inc.window}s"
                                    + (f"\nvia bot command `{inc.via}`" if inc.via else ""))
    e.add_field(name="Result", value=inc.result, inline=False)
    if inc.undone:
        e.add_field(name="Undone", value=_bullets(inc.undone), inline=False)
    if inc.problems:
        e.add_field(name="Problems", value=_bullets(inc.problems), inline=False)
    roles = ", ".join(inc.roles)
    e.add_field(name="Their roles (highest first)", inline=False,
                value=(roles[:1020] + "…") if len(roles) > 1024 else (roles or "None"))
    if inc.count > 1:
        secs = max(1, int(inc.last - inc.first))
        e.add_field(name="Repeated", value=f"{inc.count} times in {secs}s", inline=False)
    e.set_footer(text=footer)
    return e


DEFAULT = {"enabled": False, "punishment": "ban", "window": 60, "whitelist": [], "admins": [],
           "modules": {}, "presets": {}, "log": None}


class Antinuke(commands.Cog):
    """Stop rogue admins and raids before they wreck your server"""
    emoji = "🛡️"

    def __init__(self, bot):
        self.bot = bot
        self.hits: dict[tuple[int, int, str], deque] = defaultdict(deque)
        self.joins: dict[int, deque] = defaultdict(deque)
        self.raid_until: dict[int, float] = {}
        self.raid_tasks: dict[int, asyncio.Task] = {}  # guild -> waits for the raid to go quiet, then ends it
        self.incidents: dict[tuple[int, int, str], Incident] = {}
        self.snapshots = Snapshots()  # recently deleted channels/roles, so they can be rebuilt
        self.recent: dict[tuple[int, int], deque] = defaultdict(deque)  # (guild, user) -> (time, module, entry)
        self.caught: dict[tuple[int, int], float] = {}  # (guild, user) -> until when follow-ups are undone at once

    async def cfg(self, gid):
        cfg = await self.bot.db.get_config(gid, "antinuke", DEFAULT)
        if self.migrate(cfg):
            await self.save(gid, cfg)
        return cfg

    @staticmethod
    def migrate(cfg: dict) -> bool:
        """Saved settings from older versions: the invite rule used to fire on every single invite (1 in 60s),
        so ordinary members making an invite set off an alert. Move those to the new default. True if changed."""
        rule = (cfg.get("modules") or {}).get("roleinvite")
        if cfg.get("invite_rule_v2") or not isinstance(rule, dict):
            return False
        cfg["invite_rule_v2"] = True
        if int(rule.get("threshold", 1) or 1) <= 2:
            rule["threshold"] = RULE_LOOKUP["roleinvite"]["threshold"]
        return True

    async def save(self, gid, data):
        await self.bot.db.set_config(gid, "antinuke", data)

    # ================================================================ detection
    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        self.snapshots.channel_deleted(channel)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role):
        self.snapshots.role_deleted(role)

    @staticmethod
    def _matters(module: str, entry) -> bool:
        """Filter the noisy audit events down to the ones that can hurt."""
        if module in ("vanity", "serverupdate"):  # renaming the server or changing its icon isn't an attack
            return getattr(entry.before, "vanity_url_code", None) != getattr(entry.after, "vanity_url_code", None)
        if module in BOT_JOBS and getattr(getattr(entry, "user", None), "bot", False):
            return False  # ticket, logging and role bots make channels, webhooks and invites all day
        if module == "timeout":
            # member_update covers nick changes etc. — only count a timeout being applied
            return getattr(entry.after, "timed_out_until", None) is not None
        if module == "permissions":
            if entry.action == A.role_update:
                return gained_dangerous(getattr(entry.before, "permissions", None),
                                        getattr(entry.after, "permissions", None))
            if entry.action in (A.overwrite_create, A.overwrite_update):
                return overwrite_gained(entry.before, entry.after)
            added = getattr(entry.after, "roles", []) or []
            return any(is_dangerous(r.permissions) for r in added if isinstance(r, discord.Role))
        return True

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry: discord.AuditLogEntry):
        module = ACTION_MAP.get(entry.action)
        if not module:
            return
        guild = entry.guild
        cfg = await self.cfg(guild.id)
        if not cfg.get("enabled"):
            return
        actor_id = entry.user_id
        if not actor_id or await self._trusted(guild, cfg, actor_id):
            return
        if not self._matters(module, entry):
            return
        who = (guild.id, actor_id)
        t = time.monotonic()
        if self.caught.get(who, 0) > t:  # already caught: undo whatever else lands straight away
            return await self._follow_up(guild, actor_id, module, entry)
        if module in UNDOES:  # remembered even when its own rule is off, so it can be undone if they trip another
            recent = self.recent[who]
            recent.append((t, module, entry))
            while recent and t - recent[0][0] > LOOKBACK:
                recent.popleft()
        mod = cfg["modules"].get(module)
        if not mod or not mod.get("on"):
            return

        rule_def = RULE_LOOKUP.get(module)
        def_th = rule_def["threshold"] if rule_def else 3
        def_win = rule_def["window"] if rule_def else 60
        threshold = 1 if module in INSTANT else max(1, int(mod.get("threshold", def_th)))
        window = int(mod.get("window", cfg.get("window", def_win)))
        key = (guild.id, actor_id, module)
        q = self.hits[key]
        q.append(t)
        while q and t - q[0] > window:
            q.popleft()
        if len(q) < threshold:
            return
        del self.hits[key]  # don't keep an empty deque per (guild, user, module) forever
        entries = [(m, e) for _, m, e in self.recent.pop(who, ())]
        if all(e is not entry for _, e in entries):
            entries.append((module, entry))
        self._mark_caught(who, t)
        await self.respond(guild, cfg, actor_id, module, entry, threshold, entries=entries)

    def _mark_caught(self, who: tuple[int, int], now: float) -> None:
        for key in [k for k, until in self.caught.items() if until <= now]:
            del self.caught[key]
        self.caught[who] = now + CAUGHT_FOR

    async def _follow_up(self, guild: discord.Guild, actor_id: int, module: str, entry) -> None:
        """Something else done by a user who was just caught (requests already in flight): undo it and add it to
        their alert."""
        reason = f"Antinuke: {MODULES.get(module, module)}"
        lines = await self._revert_all(guild, [(module, entry)], reason, {})
        inc = next((i for k, i in self.incidents.items() if k[0] == guild.id and k[1] == actor_id), None)
        if inc is None or not lines:
            return
        inc.undone = collapse(inc.undone + [x for x in lines if not x.startswith("⚠️")])
        inc.problems += [x.removeprefix("⚠️ ") for x in lines if x.startswith("⚠️") and x.removeprefix("⚠️ ")
                         not in inc.problems]
        for msg, footer in list(inc.messages):
            try:
                await msg.edit(embed=alert_embed(inc, footer))
            except discord.HTTPException:
                pass

    # ================================================================ bot-command guard
    async def _trusted(self, guild: discord.Guild, cfg: dict, user_id: int) -> bool:
        from core import perms as _perms
        if user_id in (guild.owner_id, self.bot.user.id) or user_id in cfg.get("whitelist", []) \
                or user_id in cfg.get("admins", []) or _perms.is_bot_developer(user_id, self.bot):
            return True
        try:
            rbl_cfg = await self.bot.db.get_config(guild.id, "roleblacklist")
            if user_id in rbl_cfg.get("authorized", []):
                return True
        except Exception:
            pass
        member = guild.get_member(user_id)
        if member:
            try:
                perm_cfg = await self.bot.db.get_config(guild.id, "permlevels")
                if _perms.rank_of(perm_cfg, member) >= 3:
                    return True
            except Exception:
                pass
        return False

    async def guard(self, ctx: Context, module: str, amount: int = 1) -> None:
        """Count a destructive action done *through the bot* against the member who ran the command.

        Discord's audit log credits those actions to the bot, which the audit-log listener ignores, so without
        this anyone with bot permissions (a permission level or fake perms) could ban/delete freely while
        antinuke watched nothing. Uses the same modules, thresholds, window and counter as direct actions,
        but *blocks* the action that would hit the threshold instead of reacting after it happened.
        """
        guild = ctx.guild
        cfg = await self.cfg(guild.id)
        mod = cfg["modules"].get(module)
        if not cfg.get("enabled") or not mod or not mod.get("on"):
            return
        if await self._trusted(guild, cfg, ctx.author.id):
            return
        rule_def = RULE_LOOKUP.get(module)
        def_th = rule_def["threshold"] if rule_def else 3
        def_win = rule_def["window"] if rule_def else 60
        threshold = 1 if module in INSTANT else max(1, int(mod.get("threshold", def_th)))
        window = int(mod.get("window", cfg.get("window", def_win)))
        key = (guild.id, ctx.author.id, module)
        q = self.hits[key]
        t = time.monotonic()
        while q and t - q[0] > window:
            q.popleft()
        if amount > 1 and amount >= threshold:
            # one bulk request (e.g. massban) over the limit: refuse, but don't treat it as an attack
            if not q:
                self.hits.pop(key, None)
            raise UserError(f"🛡️ That would {VERBS.get(module, module)} **{amount}** at once — over this server's "
                            f"antinuke limit ({threshold} per {window}s). The server owner or an antinuke admin "
                            f"has to do this.")
        if len(q) + amount >= threshold:
            del self.hits[key]
            who = (guild.id, ctx.author.id)
            earlier = [(m, e) for _, m, e in self.recent.pop(who, ())]  # what they did directly in Discord
            self._mark_caught(who, t)
            await self.respond(guild, cfg, ctx.author.id, module, None, threshold, via=ctx.command.qualified_name,
                               entries=earlier)
            raise UserError(f"🛡️ Antinuke blocked this: **{LABELS.get(module, module)}** limit reached "
                            f"({threshold} per {window}s). The server owner has been alerted.")
        q.extend([t] * amount)

    async def _revoke_bot_perms(self, guild: discord.Guild, user_id: int) -> list[str]:
        """Take away powers granted *through the bot*: a permission level and roles carrying fake perms."""
        done = []
        levels = await self.bot.db.get_config(guild.id, "permlevels")
        if (levels.get("users") or {}).pop(str(user_id), None) is not None:
            await self.bot.db.set_config(guild.id, "permlevels", levels)
            done.append("revoked their bot permission level")
        member = guild.get_member(user_id)
        if member:
            fake = await self.bot.db.get_config(guild.id, "fakeperms")
            roles = [r for r in member.roles if fake.get(str(r.id)) and not r.managed and r < guild.me.top_role]
            if roles:
                try:
                    await member.remove_roles(*roles, reason="Antinuke: revoke fake permissions")
                    done.append(f"removed {len(roles)} fake-permission role(s)")
                except discord.HTTPException:
                    done.append("⚠️ couldn't remove their fake-permission roles")
        return done

    async def respond(self, guild: discord.Guild, cfg: dict, actor_id: int, module: str, entry, threshold: int = 1,
                      via: str | None = None, entries: list[tuple[str, object]] | None = None):
        actor = guild.get_member(actor_id)
        user = actor or self.bot.get_user(actor_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(actor_id)
            except discord.HTTPException:
                user = None
        # snapshot before punishment strips anything; highest role first
        roles_held = [r.name for r in reversed(actor.roles[1:])] if actor else []
        mod = cfg.get("modules", {}).get(module, {})
        rule_def = RULE_LOOKUP.get(module)
        if module == "permissions":
            punishment = "strip"  # granting dangerous permissions is answered by taking them back, never a ban
        else:
            punishment = mod.get("punishment") or cfg.get("punishment", rule_def["punishment"] if rule_def else "ban")
        window = mod.get("window") or cfg.get("window", rule_def["window"] if rule_def else 60)
        reason = f"Antinuke: {MODULES.get(module, module)}"

        # member id -> (member, removed role names, "target" | "actor"), for the notice DMs
        stripped: dict[int, tuple] = {}
        undone = [f"blocked the `{via}` command"] if via else []
        # strip bot-granted powers first, so a kick (or a failed punishment) doesn't leave them armed
        undone += await self._revoke_bot_perms(guild, actor_id)
        if entries is None:
            entries = [(module, entry)] if entry is not None else []
        # permission grants are taken back before the punishment (it decides what is left to strip); everything
        # slow (rebuilding channels and roles, unbanning) comes after it, so the attacker is stopped first
        undone += await self._revert_all(guild, [x for x in entries if x[0] == "permissions"], reason, stripped)
        result = await self._punish(guild, actor, actor_id, module, punishment, reason, stripped)
        undone += await self._revert_all(guild, [x for x in entries if x[0] != "permissions"], reason, stripped)
        problems = [line.removeprefix("⚠️ ") for line in undone if line.startswith("⚠️")]
        undone = collapse([line for line in undone if not line.startswith("⚠️")])

        await self._notify_stripped(guild, stripped)
        await self._send_alert(guild, cfg, actor_id, module, dict(
            guild=guild, user=user, actor_id=actor_id, module=module, threshold=threshold, window=window,
            via=via, roles=roles_held, result=result, undone=undone, problems=problems))

    @staticmethod
    def _strippable(guild: discord.Guild, role) -> bool:
        if not is_dangerous(getattr(role, "permissions", None)) or getattr(role, "managed", False):
            return False
        try:
            return role < guild.me.top_role
        except Exception:
            return getattr(role, "position", 0) < getattr(guild.me.top_role, "position", 999999)

    @staticmethod
    def _below_me(guild: discord.Guild, member) -> bool:
        try:
            return member.top_role < guild.me.top_role
        except Exception:
            return getattr(member.top_role, "position", 0) < getattr(guild.me.top_role, "position", 0)

    async def _revert_all(self, guild: discord.Guild, entries: list[tuple[str, object]], reason: str,
                          stripped: dict) -> list[str]:
        """Undo everything in `entries`. Deleted roles are rebuilt before deleted channels, so channel overwrites
        that pointed at a deleted role point at its replacement."""
        lines: list[str] = []
        wanted: dict[str, list[int]] = {"role": [], "channel": []}
        for module, entry in entries:
            if entry is None:
                continue
            target_id = getattr(getattr(entry, "target", None), "id", None)
            if module == "roledelete" and target_id:
                wanted["role"].append(target_id)
            elif module == "channeldelete" and target_id:
                wanted["channel"].append(target_id)
            else:
                try:
                    lines += await self._revert(guild, module, entry, reason, stripped)
                except Exception as e:  # never let one failed undo stop the rest or the alert
                    lines.append(f"⚠️ couldn't undo {LABELS.get(module, module).lower()} ({type(e).__name__})")
        snaps: dict[str, list[dict]] = {"role": [], "channel": []}
        for kind, ids in wanted.items():
            for i, object_id in enumerate(ids):
                snap = await self.snapshots.wait_for(kind, object_id, timeout=3.0 if i == 0 else 0.5)
                if snap:
                    snaps[kind].append(snap)
                else:
                    lines.append(f"⚠️ couldn't rebuild a deleted {kind}: I had no copy of it")
        role_map: dict[int, object] = {}
        if snaps["role"]:
            role_map, made = await restore_roles(guild, snaps["role"], reason)
            lines += made
        if snaps["channel"]:
            lines += await restore_channels(guild, snaps["channel"], role_map, reason)
        return lines

    async def _revert(self, guild: discord.Guild, module: str, entry, reason: str, stripped: dict) -> list[str]:
        """Undo one thing the attacker did where that's safe. Returns lines for the alert ("⚠️ …" for failures)."""
        if entry is None:  # the bot-command guard blocked the action before it happened
            return []
        target = getattr(entry, "target", None)
        target_id = getattr(target, "id", None)
        try:
            if module == "botadd" and isinstance(target, (discord.Member, discord.User, discord.Object)):
                await guild.ban(target, reason="Antinuke: unauthorized bot")
                return [f"banned the bot {display(target)}"]
            if module == "ban" and target_id:
                await guild.unban(discord.Object(target_id), reason=reason)
                return [f"unbanned {display(target)}"]
            if module == "unban" and target_id:
                await guild.ban(discord.Object(target_id), reason=reason)
                return [f"banned again {display(target)}"]
            if module == "channelcreate" and target_id:
                channel = guild.get_channel(target_id)
                if channel is not None:
                    await channel.delete(reason=reason)
                    return [f"deleted #{display(channel)}"]
                return []
            if module == "rolecreate" and target_id:
                role = guild.get_role(target_id)
                if role is not None and not getattr(role, "managed", False):
                    await role.delete(reason=reason)
                    return [f"deleted @{display(role)}"]
                return []
            if module == "webhook" and target_id:
                hook = await self.bot.fetch_webhook(target_id)
                await hook.delete(reason=reason)
                return [f"deleted the webhook {display(hook)}"]
            if module == "roleinvite":
                code = getattr(target, "code", None) or getattr(entry.after, "code", None)
                if code:
                    await self.bot.delete_invite(code, reason=reason)
                    return [f"deleted the invite {code}"]
                return []
            if module == "permissions" and entry.action == A.role_update and isinstance(target, discord.Role):
                before = getattr(entry.before, "permissions", None)
                if before is not None:
                    await target.edit(permissions=before, reason=reason)
                    return [f"reverted the permissions on {target.name}"]
            elif module == "permissions" and entry.action in (A.overwrite_create, A.overwrite_update) \
                    and isinstance(target, discord.abc.GuildChannel):
                ow_target = getattr(entry.extra, "id", None) and (
                    guild.get_role(entry.extra.id) or guild.get_member(entry.extra.id))
                if ow_target is not None:
                    ow = target.overwrites_for(ow_target)
                    for perm, value in OVERWRITE_DANGEROUS:
                        if value and getattr(ow, perm) is True:
                            setattr(ow, perm, None)
                    await target.set_permissions(ow_target, overwrite=ow, reason=reason)
                    return [f"removed staff overwrites in #{target.name}"]
            elif module == "permissions" and isinstance(target, discord.Member):
                added = [r for r in getattr(entry.after, "roles", []) or [] if self._strippable(guild, r)]
                if not added:
                    added = [r for r in target.roles if self._strippable(guild, r)]
                if added:
                    await target.remove_roles(*added, reason=reason)
                    stripped[target.id] = (target, [r.name for r in added], "target")
                    return [f"removed {', '.join(r.name for r in added)} from {target}"]
            elif module == "timeout" and isinstance(target, discord.Member):
                await target.timeout(None, reason=reason)
                return [f"removed the timeout from {target}"]
            elif module in ("vanity", "serverupdate"):
                changes, done = {}, []
                before_name = getattr(entry.before, "name", None)
                if before_name and before_name != getattr(entry.after, "name", None):
                    changes["name"], done = before_name, done + ["restored the server name"]
                before_vanity = getattr(entry.before, "vanity_url_code", None)
                if before_vanity and before_vanity != getattr(entry.after, "vanity_url_code", None):
                    changes["vanity_code"], done = before_vanity, done + ["restored the vanity URL"]
                if changes:
                    await guild.edit(reason=reason, **changes)
                return done
        except discord.NotFound:
            return []  # already gone
        except discord.HTTPException as e:
            return [f"⚠️ couldn't undo {LABELS.get(module, module).lower()} (Discord said {e.status})"]
        return []

    async def _punish(self, guild: discord.Guild, actor, actor_id: int, module: str, punishment: str, reason: str,
                      stripped: dict) -> str:
        """Punish the attacker and describe what actually happened."""
        word = {"ban": "Ban", "kick": "Kick", "strip": "Strip"}.get(punishment, punishment.title())
        try:
            if actor is None:
                if module == "permissions":
                    return "Nothing to strip: they're no longer in the server"
                await guild.ban(discord.Object(actor_id), reason=reason)
                return "Banned (they had already left the server)"
            if not self._below_me(guild, actor):
                return "⚠️ Not punished: their top role is above mine. Move my role higher."
            if punishment == "kick":
                await actor.kick(reason=reason)
                return "Kicked"
            if punishment == "strip" or module == "permissions":
                roles = [r for r in actor.roles if self._strippable(guild, r)]
                if roles:
                    await actor.remove_roles(*roles, reason=reason)
                    prev = stripped.get(actor.id)
                    names = list(prev[1]) if prev else []
                    names += [r.name for r in roles if r.name not in names]
                    stripped[actor.id] = (actor, names, "actor")
                    return f"Stripped {len(roles)} staff role(s)"
                if actor.id in stripped:  # gave the roles to themselves: already taken back above
                    member, names, _ = stripped[actor.id]
                    stripped[actor.id] = (member, names, "actor")
                    return f"Stripped {len(names)} staff role(s)"
                return "Nothing to strip: they have no staff roles"
            await actor.ban(reason=reason)
            return "Banned"
        except discord.HTTPException as e:
            return f"⚠️ {word} failed (Discord said {e.status})"

    async def _notify_stripped(self, guild: discord.Guild, stripped: dict) -> None:
        """Tell people whose roles the antinuke removed what happened, so it isn't a mystery."""
        for member, names, kind in stripped.values():
            if getattr(member, "bot", False) or not names:
                continue
            roles = ", ".join(f"**{discord.utils.escape_markdown(n)}**" for n in names)
            if kind == "actor":
                text = (f"Veil removed your staff roles ({roles}) in **{discord.utils.escape_markdown(guild.name)}** "
                        f"because you gave out dangerous permissions without being trusted by the server's antinuke.")
            else:
                text = (f"Veil removed {roles} from you in **{discord.utils.escape_markdown(guild.name)}**. "
                        f"They were given to you by someone the server's antinuke doesn't trust.")
            embed = discord.Embed(title="🛡️ Veil Antinuke Security Notice", color=Colors.error,
                                  description=text + "\n\nIf this was a mistake, ask the server owner to restore them.")
            try:
                await member.send(embed=embed)
            except (discord.HTTPException, AttributeError):
                pass

    async def _log_channel(self, guild: discord.Guild, cfg: dict) -> discord.TextChannel | None:
        """Where alerts are posted: this module's own `log` channel, else the Antinuke channel from `,logs`."""
        channel = guild.get_channel(cfg.get("log") or 0)
        if channel:
            return channel
        get_cog = getattr(self.bot, "get_cog", None)
        logs = get_cog("Logging") if get_cog else None
        return await logs.channel_for(guild, "antinuke") if logs else None

    async def _send_alert(self, guild: discord.Guild, cfg: dict, actor_id: int, module: str, details: dict) -> None:
        """Send the alert card, or update the existing one if the same user tripped the same module just now."""
        now = time.monotonic()
        for k in [k for k, inc in self.incidents.items() if now - inc.last > ALERT_MERGE_WINDOW]:
            del self.incidents[k]
        key = (guild.id, actor_id, module)
        inc = self.incidents.get(key)
        if inc is not None:
            inc.merge(result=details["result"], undone=details["undone"], problems=details["problems"],
                      roles=details["roles"])
            for msg, footer in list(inc.messages):
                try:
                    await msg.edit(embed=alert_embed(inc, footer))
                except discord.HTTPException:
                    pass
            return
        inc = self.incidents[key] = Incident(**details)

        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Jump to Server", emoji="↗️",
                                        url=f"https://discord.com/channels/{guild.id}"))
        log_channel = await self._log_channel(guild, cfg)

        targets: list[tuple] = [(d, "Antinuke alert") for d in (log_channel, guild.owner) if d]
        if settings.antinuke_dev_alerts:
            from cogs.authorization import developers
            for uid in await developers(self.bot):
                if uid == guild.owner_id:
                    continue  # already got the owner copy
                try:
                    dev = self.bot.get_user(uid) or await self.bot.fetch_user(uid)
                except discord.HTTPException:
                    dev = None
                if dev:
                    targets.append((dev, "Global antinuke alert • Veil Developers"))

        for dest, footer in targets:
            try:
                msg = await dest.send(embed=alert_embed(inc, footer), view=view)
            except (discord.HTTPException, AttributeError):
                continue
            if msg is not None and hasattr(msg, "edit"):
                inc.messages.append((msg, footer))

    # ================================================================ status & rules
    async def build_status_embed(self, guild_id: int, prefix: str = ",", guild=None) -> discord.Embed:
        return home_embed(await self.cfg(guild_id), prefix, guild)

    @staticmethod
    def enable_rules(cfg: dict) -> None:
        """Turn the antinuke on, filling in any rule the server hasn't set with its default."""
        cfg["enabled"] = True
        modules = cfg.setdefault("modules", {})
        for r in MIDTOWN_RULES:
            modules.setdefault(r["key"], {"on": True, "threshold": r["threshold"], "window": r["window"],
                                          "punishment": r["punishment"]})
        for m in MODULES:
            modules.setdefault(m, {"on": True, "threshold": DEFAULT_THRESHOLDS.get(m, 3)})

    @staticmethod
    def apply_preset(cfg: dict, target: dict) -> None:
        for k, v in target.items():
            rule_def = RULE_LOOKUP.get(k)
            cfg.setdefault("modules", {})[k] = {
                "on": True,
                "threshold": v.get("threshold", rule_def["threshold"] if rule_def else 3),
                "window": v.get("window", rule_def["window"] if rule_def else 60),
                "punishment": v.get("punishment", rule_def["punishment"] if rule_def else "ban"),
            }

    async def send_panel(self, ctx: Context) -> None:
        cfg = await self.cfg(ctx.guild.id)
        panel = AntinukePanel(self, ctx.guild, ctx.author.id, cfg, ctx.clean_prefix)
        panel.message = await ctx.send(embed=panel.embed(), view=panel)

    def resolve_action(self, name: str) -> str | None:
        s = name.lower().replace("_", "").replace(" ", "").replace("-", "")
        alias_map = {
            "ban": "ban", "bans": "ban",
            "botadd": "botadd", "bot": "botadd", "bots": "botadd",
            "channelcreate": "channelcreate", "createchannel": "channelcreate",
            "channeldelete": "channeldelete", "deletechannel": "channeldelete",
            "kick": "kick", "kicks": "kick",
            "permissions": "permissions", "permission": "permissions", "perm": "permissions", "perms": "permissions", "grant": "permissions",
            "rolecreate": "rolecreate", "createrole": "rolecreate",
            "roledelete": "roledelete", "deleterole": "roledelete",
            "roleinvite": "roleinvite", "invite": "roleinvite", "invites": "roleinvite",
            "serverupdate": "serverupdate", "guild": "serverupdate", "vanity": "serverupdate", "server": "serverupdate",
            "timeout": "timeout", "mute": "timeout", "timeouts": "timeout",
            "webhook": "webhook", "webhooks": "webhook",
            "emoji": "emoji", "prune": "prune", "unban": "unban",
        }
        return alias_map.get(s)

    async def _update_rule(self, ctx: Context, action: str, count: int, punishment: str, timeframe: str | int):
        await antinuke_admin(ctx)
        action_key = self.resolve_action(action)
        if not action_key:
            valid = ", ".join(f"`{r['key']}`" for r in MIDTOWN_RULES)
            raise UserError(f"Unknown action `{action}`. Valid actions:\n{valid}")
        punishment = punishment.lower()
        if punishment not in ("ban", "kick", "strip"):
            raise UserError("Punishment must be `ban`, `kick`, or `strip`")
        seconds = parse_timeframe(timeframe)
        if count < 1 or count > 100:
            raise UserError("Count must be between 1 and 100")

        cfg = await self.cfg(ctx.guild.id)
        mod = cfg["modules"].setdefault(action_key, {"on": True})
        mod["on"] = True
        mod["threshold"] = count
        mod["punishment"] = punishment
        mod["window"] = seconds
        await self.save(ctx.guild.id, cfg)

        rule_def = RULE_LOOKUP.get(action_key)
        name = rule_def["name"] if rule_def else action_key
        await ctx.approve(f"set the **{name}** rule to **{count} in {seconds}s** → {punishment}")

    async def _show_rule(self, ctx: Context, action: str):
        cfg = await self.cfg(ctx.guild.id)
        mod = cfg.get("modules", {}).get(action, {})
        rule = RULE_LOOKUP.get(action)
        if not rule:
            raise UserError(f"Unknown action `{action}`")
        name = rule["name"]
        th = mod.get("threshold", rule["threshold"])
        win = mod.get("window", rule["window"])
        pun = mod.get("punishment", rule["punishment"])
        tag_str = f" · {UNDOES[action]}" if action in UNDOES else ""
        await ctx.neutral(f"**{name}** rule: **{th} in {win}s** → {pun}{tag_str}")

    # ================================================================ commands
    @commands.group(aliases=["an"], invoke_without_command=True)
    @commands.guild_only()
    async def antinuke(self, ctx: Context):
        """Antinuke status and rules, with buttons to turn it on or off, load a preset, or change a rule"""
        await self.send_panel(ctx)

    @antinuke.command(name="status", aliases=["config", "settings", "panel"])
    async def an_status(self, ctx: Context):
        """Antinuke status and rules, with buttons to change them"""
        await self.send_panel(ctx)

    @antinuke.command(name="enable", aliases=["on"])
    async def an_enable(self, ctx: Context):
        """Turn antinuke on"""
        await antinuke_admin(ctx)
        cfg = await self.cfg(ctx.guild.id)
        self.enable_rules(cfg)
        await self.save(ctx.guild.id, cfg)
        await ctx.approve("turned antinuke **on**")

    @antinuke.command(name="disable", aliases=["off"])
    async def an_disable(self, ctx: Context):
        """Turn antinuke off"""
        await antinuke_admin(ctx)
        cfg = await self.cfg(ctx.guild.id)
        cfg["enabled"] = False
        await self.save(ctx.guild.id, cfg)
        await ctx.approve("turned antinuke **off**")

    @antinuke.command(name="toggle", aliases=["on/off"])
    async def an_toggle(self, ctx: Context):
        """Toggle antinuke on or off"""
        await antinuke_admin(ctx)
        cfg = await self.cfg(ctx.guild.id)
        if cfg.get("enabled"):
            cfg["enabled"] = False
        else:
            self.enable_rules(cfg)
        await self.save(ctx.guild.id, cfg)
        await ctx.approve(f"turned antinuke **{'on' if cfg['enabled'] else 'off'}**")

    @antinuke.command(name="set", aliases=["rule", "configure"])
    async def an_set(self, ctx: Context, action: str, arg2: str, arg3: str = None, arg4: str = None):
        """Configure an antinuke rule: `,an set ban 3 ban 60s` or `,an set ban on 3`"""
        await antinuke_admin(ctx)
        if arg2.lower() in ("on", "enable", "true", "yes", "off", "disable", "false", "no"):
            state = arg2.lower()
            threshold = int(arg3) if arg3 and arg3.isdigit() else 3
            k = self.resolve_action(action) or action.lower()
            cfg = await self.cfg(ctx.guild.id)
            cfg["modules"][k] = {"on": state in ("on", "enable", "true", "yes"), "threshold": threshold}
            await self.save(ctx.guild.id, cfg)
            return await ctx.approve(f"turned `{k}` **{'on' if cfg['modules'][k]['on'] else 'off'}** (threshold **{threshold}**)")
        if arg3 is None or arg4 is None:
            raise UserError(f"Usage: `{ctx.clean_prefix}an set <action> <count> <punishment> <timeframe>` (e.g. `{ctx.clean_prefix}an set ban 3 ban 60s`)")
        try:
            count = int(arg2)
        except ValueError:
            raise UserError(f"Count must be a number, got `{arg2}`")
        await self._update_rule(ctx, action, count, arg3, arg4)

    @antinuke.command(name="module")
    async def an_module(self, ctx: Context, module: str, state: str, threshold: int = 3):
        """Toggle an individual module: `antinuke module ban on 3`"""
        await antinuke_admin(ctx)
        k = self.resolve_action(module) or module.lower()
        if k not in MODULES and k not in RULE_LOOKUP:
            raise UserError(f"Unknown module. Options: {', '.join(f'`{m}`' for m in MODULES)}")
        cfg = await self.cfg(ctx.guild.id)
        state = state.lower()
        if state not in ("on", "enable", "true", "yes", "off", "disable", "false", "no"):
            raise UserError("State must be `on` or `off`")
        threshold = max(1, min(threshold, 100))
        cfg["modules"][k] = {"on": state in ("on", "enable", "true", "yes"), "threshold": threshold}
        await self.save(ctx.guild.id, cfg)
        await ctx.approve(f"turned `{k}` **{'on' if cfg['modules'][k]['on'] else 'off'}**"
                          + ("" if k in INSTANT else f" (threshold **{threshold}**)"))

    # Direct action subcommands matching Midtown Utilities rules
    @antinuke.command(name="ban")
    async def an_cmd_ban(self, ctx: Context, count: int = None, punishment: str = None, timeframe: str = None):
        """Configure the ban rule: `,an ban 3 ban 60s`"""
        if count is None:
            return await self._show_rule(ctx, "ban")
        if punishment is None or timeframe is None:
            raise UserError(f"Usage: `{ctx.clean_prefix}an ban <count> <punishment> <timeframe>` (e.g. `{ctx.clean_prefix}an ban 3 ban 60s`)")
        await self._update_rule(ctx, "ban", count, punishment, timeframe)

    @antinuke.command(name="botadd", aliases=["bot", "bots"])
    async def an_cmd_botadd(self, ctx: Context, count: int = None, punishment: str = None, timeframe: str = None):
        """Configure the bot add rule: `,an botadd 1 ban 60s`"""
        if count is None:
            return await self._show_rule(ctx, "botadd")
        if punishment is None or timeframe is None:
            raise UserError(f"Usage: `{ctx.clean_prefix}an botadd <count> <punishment> <timeframe>`")
        await self._update_rule(ctx, "botadd", count, punishment, timeframe)

    @antinuke.command(name="channelcreate")
    async def an_cmd_channelcreate(self, ctx: Context, count: int = None, punishment: str = None, timeframe: str = None):
        """Configure the channel create rule: `,an channelcreate 2 kick 120s`"""
        if count is None:
            return await self._show_rule(ctx, "channelcreate")
        if punishment is None or timeframe is None:
            raise UserError(f"Usage: `{ctx.clean_prefix}an channelcreate <count> <punishment> <timeframe>`")
        await self._update_rule(ctx, "channelcreate", count, punishment, timeframe)

    @antinuke.command(name="channeldelete")
    async def an_cmd_channeldelete(self, ctx: Context, count: int = None, punishment: str = None, timeframe: str = None):
        """Configure the channel delete rule: `,an channeldelete 2 ban 120s`"""
        if count is None:
            return await self._show_rule(ctx, "channeldelete")
        if punishment is None or timeframe is None:
            raise UserError(f"Usage: `{ctx.clean_prefix}an channeldelete <count> <punishment> <timeframe>`")
        await self._update_rule(ctx, "channeldelete", count, punishment, timeframe)

    @antinuke.command(name="kick")
    async def an_cmd_kick(self, ctx: Context, count: int = None, punishment: str = None, timeframe: str = None):
        """Configure the kick rule: `,an kick 3 ban 120s`"""
        if count is None:
            return await self._show_rule(ctx, "kick")
        if punishment is None or timeframe is None:
            raise UserError(f"Usage: `{ctx.clean_prefix}an kick <count> <punishment> <timeframe>`")
        await self._update_rule(ctx, "kick", count, punishment, timeframe)

    @antinuke.command(name="permissions", aliases=["permission", "perms"])
    async def an_cmd_permissions(self, ctx: Context, count: int = None, punishment: str = None, timeframe: str = None):
        """Configure the permission grant rule: `,an permissions 1 strip 60s`"""
        if count is None:
            return await self._show_rule(ctx, "permissions")
        if punishment is None or timeframe is None:
            raise UserError(f"Usage: `{ctx.clean_prefix}an permissions <count> <punishment> <timeframe>`")
        await self._update_rule(ctx, "permissions", count, punishment, timeframe)

    @antinuke.command(name="rolecreate")
    async def an_cmd_rolecreate(self, ctx: Context, count: int = None, punishment: str = None, timeframe: str = None):
        """Configure the role create rule: `,an rolecreate 5 strip 120s`"""
        if count is None:
            return await self._show_rule(ctx, "rolecreate")
        if punishment is None or timeframe is None:
            raise UserError(f"Usage: `{ctx.clean_prefix}an rolecreate <count> <punishment> <timeframe>`")
        await self._update_rule(ctx, "rolecreate", count, punishment, timeframe)

    @antinuke.command(name="roledelete")
    async def an_cmd_roledelete(self, ctx: Context, count: int = None, punishment: str = None, timeframe: str = None):
        """Configure the role delete rule: `,an roledelete 5 strip 120s`"""
        if count is None:
            return await self._show_rule(ctx, "roledelete")
        if punishment is None or timeframe is None:
            raise UserError(f"Usage: `{ctx.clean_prefix}an roledelete <count> <punishment> <timeframe>`")
        await self._update_rule(ctx, "roledelete", count, punishment, timeframe)

    @antinuke.command(name="roleinvite", aliases=["invite"])
    async def an_cmd_roleinvite(self, ctx: Context, count: int = None, punishment: str = None, timeframe: str = None):
        """Configure the invite spam rule (many invites made quickly): `,an roleinvite 10 strip 60s`"""
        if count is None:
            return await self._show_rule(ctx, "roleinvite")
        if punishment is None or timeframe is None:
            raise UserError(f"Usage: `{ctx.clean_prefix}an roleinvite <count> <punishment> <timeframe>`")
        await self._update_rule(ctx, "roleinvite", count, punishment, timeframe)

    @antinuke.command(name="serverupdate", aliases=["server"])
    async def an_cmd_serverupdate(self, ctx: Context, count: int = None, punishment: str = None, timeframe: str = None):
        """Configure the server update rule: `,an serverupdate 1 strip 60s`"""
        if count is None:
            return await self._show_rule(ctx, "serverupdate")
        if punishment is None or timeframe is None:
            raise UserError(f"Usage: `{ctx.clean_prefix}an serverupdate <count> <punishment> <timeframe>`")
        await self._update_rule(ctx, "serverupdate", count, punishment, timeframe)

    @antinuke.command(name="timeout", aliases=["mute"])
    async def an_cmd_timeout(self, ctx: Context, count: int = None, punishment: str = None, timeframe: str = None):
        """Configure the timeout rule: `,an timeout 5 strip 120s`"""
        if count is None:
            return await self._show_rule(ctx, "timeout")
        if punishment is None or timeframe is None:
            raise UserError(f"Usage: `{ctx.clean_prefix}an timeout <count> <punishment> <timeframe>`")
        await self._update_rule(ctx, "timeout", count, punishment, timeframe)

    @antinuke.command(name="webhook", aliases=["webhooks"])
    async def an_cmd_webhook(self, ctx: Context, count: int = None, punishment: str = None, timeframe: str = None):
        """Configure the webhook rule: `,an webhook 1 kick 60s`"""
        if count is None:
            return await self._show_rule(ctx, "webhook")
        if punishment is None or timeframe is None:
            raise UserError(f"Usage: `{ctx.clean_prefix}an webhook <count> <punishment> <timeframe>`")
        await self._update_rule(ctx, "webhook", count, punishment, timeframe)

    @antinuke.command(name="whitelist", aliases=["wl", "trust"])
    async def an_whitelist(self, ctx: Context, user: discord.Member | discord.User):
        """Add a user or bot to the antinuke whitelist"""
        await antinuke_admin(ctx)
        cfg = await self.cfg(ctx.guild.id)
        if user.id in cfg["whitelist"]:
            return await ctx.warn(f"{user.mention} is already whitelisted")
        cfg["whitelist"].append(user.id)
        await self.save(ctx.guild.id, cfg)
        await ctx.approve(f"whitelisted {user.mention}")

    @antinuke.command(name="unwhitelist", aliases=["unwl", "untrust"])
    async def an_unwhitelist(self, ctx: Context, user: discord.Member | discord.User):
        """Remove a user or bot from the antinuke whitelist"""
        await antinuke_admin(ctx)
        cfg = await self.cfg(ctx.guild.id)
        if user.id in cfg["whitelist"]:
            cfg["whitelist"].remove(user.id)
            await self.save(ctx.guild.id, cfg)
            await ctx.approve(f"removed {user.mention} from the whitelist")
        else:
            await ctx.warn(f"{user.mention} isn't whitelisted")

    @antinuke.command(name="reset")
    async def an_reset(self, ctx: Context, action: str = None):
        """Reset an action rule or all rules to defaults: `,an reset ban` or `,an reset`"""
        await antinuke_admin(ctx)
        cfg = await self.cfg(ctx.guild.id)
        if action and action.lower() != "all":
            k = self.resolve_action(action)
            if not k or k not in RULE_LOOKUP:
                raise UserError(f"Unknown action `{action}` to reset")
            default_r = RULE_LOOKUP[k]
            cfg["modules"][k] = {
                "on": True,
                "threshold": default_r["threshold"],
                "window": default_r["window"],
                "punishment": default_r["punishment"],
            }
            await self.save(ctx.guild.id, cfg)
            await ctx.approve(f"reset the **{default_r['name']}** rule to its default")
        else:
            for r in MIDTOWN_RULES:
                k = r["key"]
                cfg["modules"][k] = {
                    "on": True,
                    "threshold": r["threshold"],
                    "window": r["window"],
                    "punishment": r["punishment"],
                }
            await self.save(ctx.guild.id, cfg)
            await ctx.approve("reset every antinuke rule to its default")

    @antinuke.group(name="preset", invoke_without_command=True)
    async def an_preset(self, ctx: Context):
        """Manage rule presets: `,an preset save/load/list`"""
        p = ctx.clean_prefix
        await ctx.neutral(f"`{p}an preset save <name>` saves the current rules\n"
                          f"`{p}an preset load <name>` loads default, strict, relaxed or one of yours\n"
                          f"`{p}an preset list` shows every preset")

    @an_preset.command(name="save")
    async def an_preset_save(self, ctx: Context, name: str):
        """Save current antinuke rules as a named preset"""
        await antinuke_admin(ctx)
        name = name.lower().strip()
        if len(name) > 30:
            raise UserError("Preset name is too long (max 30 chars)")
        cfg = await self.cfg(ctx.guild.id)
        presets = cfg.setdefault("presets", {})
        presets[name] = {k: v for k, v in cfg["modules"].items() if k in RULE_LOOKUP}
        await self.save(ctx.guild.id, cfg)
        await ctx.approve(f"saved the current rules as preset **{name}**")

    @an_preset.command(name="load")
    async def an_preset_load(self, ctx: Context, name: str):
        """Load an antinuke preset (default, strict, relaxed, or saved custom)"""
        await antinuke_admin(ctx)
        name = name.lower().strip()
        cfg = await self.cfg(ctx.guild.id)
        target = None
        if name in PRESETS:
            target = PRESETS[name]
        elif name in cfg.get("presets", {}):
            target = cfg["presets"][name]
        else:
            available = list(PRESETS.keys()) + list(cfg.get("presets", {}).keys())
            raise UserError(f"Preset `{name}` not found. Available presets: {', '.join(f'`{p}`' for p in available)}")

        self.apply_preset(cfg, target)
        await self.save(ctx.guild.id, cfg)
        await ctx.approve(f"loaded preset **{name}**")

    @an_preset.command(name="list")
    async def an_preset_list(self, ctx: Context):
        """List available antinuke presets"""
        cfg = await self.cfg(ctx.guild.id)
        builtins = ", ".join(f"`{p}`" for p in PRESETS.keys())
        custom = ", ".join(f"`{p}`" for p in cfg.get("presets", {}).keys()) or "none"
        embed = discord.Embed(title="Antinuke Presets", color=Colors.mint)
        embed.add_field(name="Built-in Presets", value=builtins, inline=False)
        embed.add_field(name="Custom Server Presets", value=custom, inline=False)
        await ctx.send(embed=embed)

    @antinuke.command(name="admins", aliases=["adminlist", "details"])
    async def an_admins_list(self, ctx: Context):
        """List antinuke admins and whitelisted accounts"""
        await antinuke_admin(ctx)
        cfg = await self.cfg(ctx.guild.id)
        embed = discord.Embed(title="Antinuke Trusted Accounts", color=Colors.mint)
        embed.add_field(name="Admins", value=", ".join(f"<@{u}>" for u in cfg["admins"]) or "none", inline=False)
        embed.add_field(name="Whitelisted", value=", ".join(f"<@{u}>" for u in cfg["whitelist"]) or "none", inline=False)
        await ctx.send(embed=embed)

    @antinuke.command(name="punishment")
    async def an_punishment(self, ctx: Context, punishment: str):
        """Set the default punishment: ban, kick or strip"""
        await antinuke_admin(ctx)
        if punishment.lower() not in ("ban", "kick", "strip"):
            raise UserError("Punishment must be `ban`, `kick` or `strip`")
        cfg = await self.cfg(ctx.guild.id)
        cfg["punishment"] = punishment.lower()
        await self.save(ctx.guild.id, cfg)
        await ctx.approve(f"set the default punishment to **{punishment.lower()}**")

    @antinuke.command(name="window")
    async def an_window(self, ctx: Context, seconds: int):
        """Set the default time window thresholds are counted over"""
        await antinuke_admin(ctx)
        cfg = await self.cfg(ctx.guild.id)
        cfg["window"] = max(5, min(seconds, 3600))
        await self.save(ctx.guild.id, cfg)
        await ctx.approve(f"set the default window to **{cfg['window']}s**")

    @antinuke.command(name="admin")
    async def an_admin(self, ctx: Context, member: discord.Member):
        """Toggle an antinuke admin (server owner only) — admins can configure antinuke and are trusted"""
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        if ctx.author.id != ctx.guild.owner_id:
            raise UserError("Only the server owner can manage antinuke admins")
        cfg = await self.cfg(ctx.guild.id)
        if member.id in cfg["admins"]:
            cfg["admins"].remove(member.id); msg = f"removed {member.mention} as an antinuke admin"
        else:
            cfg["admins"].append(member.id); msg = f"made {member.mention} an antinuke admin"
        await self.save(ctx.guild.id, cfg)
        await ctx.approve(msg)

    @antinuke.command(name="log")
    async def an_log(self, ctx: Context, channel: discord.TextChannel = None):
        """Set where antinuke alerts go (owner always gets a DM)"""
        await antinuke_admin(ctx)
        cfg = await self.cfg(ctx.guild.id)
        cfg["log"] = channel.id if channel else None
        await self.save(ctx.guild.id, cfg)
        await ctx.approve(f"antinuke alerts will go to {channel.mention}" if channel else "cleared the antinuke alert channel")

    # ================================================================ antiraid
    @commands.group(aliases=["raid"], invoke_without_command=True)
    @has_perms(administrator=True)
    async def antiraid(self, ctx: Context):
        """Filter suspicious joins: mass joins, new accounts, no avatar"""
        cfg = await self.bot.db.get_config(ctx.guild.id, "antiraid")
        embed = discord.Embed(title="Antiraid", color=Colors.main)
        mj = cfg.get("massjoin")
        embed.add_field(name="Mass join", value=f"{mj['joins']} joins / {mj['seconds']}s" if mj else "off")
        embed.add_field(name="Account age", value=f"< {cfg['age']} days" if cfg.get("age") else "off")
        embed.add_field(name="No avatar", value="on" if cfg.get("avatar") else "off")
        embed.add_field(name="Punishment", value=cfg.get("punishment", "kick"))
        embed.add_field(name="Whitelist", value=str(len(cfg.get("whitelist", []))) + " users")
        lock = {**RAID_LOCK_DEFAULTS, **(cfg.get("lockdown") or {})}
        embed.add_field(name="During a raid", value=(
            f"pause invites & DMs: **{'on' if lock['invites'] else 'off'}**\n"
            f"lock channels: **{'on' if lock['channels'] else 'off'}**"))
        state = cfg.get("raid_state")
        embed.add_field(name="Raid mode", value=f"**on** since <t:{int(state['since'])}:R>" if state else "off")
        embed.set_footer(text=f"{ctx.clean_prefix}antiraid start / end · "
                              f"{ctx.clean_prefix}antiraid lockdown <invites|channels> <on|off>")
        await ctx.send(embed=embed)

    async def _ar_set(self, ctx, key, value, msg):
        cfg = await self.bot.db.get_config(ctx.guild.id, "antiraid")
        if value is None:
            cfg.pop(key, None)
        else:
            cfg[key] = value
        await self.bot.db.set_config(ctx.guild.id, "antiraid", cfg)
        await ctx.approve(msg)

    @antiraid.command(name="massjoin")
    @has_perms(administrator=True)
    async def ar_massjoin(self, ctx: Context, joins: int = 0, seconds: int = 10):
        """Punish everyone in a join wave: `antiraid massjoin 10 10` (0 to disable)"""
        if joins and not (2 <= joins <= 100 and 1 <= seconds <= 300):
            raise UserError("Use 2–100 joins within 1–300 seconds, e.g. `antiraid massjoin 10 10`")
        await self._ar_set(ctx, "massjoin", {"joins": joins, "seconds": seconds} if joins else None,
                           f"mass join filter set to **{joins} joins in {seconds}s**" if joins else "turned off the mass join filter")

    @antiraid.command(name="age")
    @has_perms(administrator=True)
    async def ar_age(self, ctx: Context, days: int = 0):
        """Punish accounts younger than N days (0 to disable)"""
        await self._ar_set(ctx, "age", days or None, f"account age filter set to **under {days} days**" if days else "turned off the account age filter")

    @antiraid.command(name="avatar")
    @has_perms(administrator=True)
    async def ar_avatar(self, ctx: Context, state: str):
        """Punish accounts with no avatar (on/off)"""
        on = state.lower() in ("on", "true", "enable")
        await self._ar_set(ctx, "avatar", on or None, f"turned the avatar filter **{'on' if on else 'off'}**")

    @antiraid.command(name="punishment")
    @has_perms(administrator=True)
    async def ar_punishment(self, ctx: Context, punishment: str):
        """kick, ban or timeout"""
        if punishment not in ("kick", "ban", "timeout"):
            raise UserError("Use `kick`, `ban` or `timeout`")
        await self._ar_set(ctx, "punishment", punishment, f"antiraid punishment set to **{punishment}**")

    @antiraid.command(name="whitelist")
    @has_perms(administrator=True)
    async def ar_whitelist(self, ctx: Context, user: discord.User):
        """Let a specific user bypass antiraid"""
        cfg = await self.bot.db.get_config(ctx.guild.id, "antiraid")
        wl = cfg.setdefault("whitelist", [])
        if user.id in wl:
            wl.remove(user.id)
            msg = f"removed {user.mention} from the antiraid whitelist"
        else:
            wl.append(user.id)
            msg = f"added {user.mention} to the antiraid whitelist"
        await self.bot.db.set_config(ctx.guild.id, "antiraid", cfg)
        await ctx.approve(msg)

    @antiraid.command(name="start", aliases=["on", "now"])
    @has_perms(administrator=True)
    async def ar_start(self, ctx: Context):
        """Turn raid mode on now (pauses invites and DMs, locks channels if set) until joins go quiet"""
        done = await self.start_raid(ctx.guild, "Raid mode was started by hand", by=ctx.author)
        await ctx.approve("raid mode is **on**" + (": " + "; ".join(x.removeprefix("⚠️ ") for x in done) if done else ""))

    @antiraid.command(name="end", aliases=["stop", "off", "over"])
    @has_perms(administrator=True)
    async def ar_end(self, ctx: Context):
        """End raid mode now: resume invites and DMs, unlock the channels it locked"""
        cfg = await self._raid_cfg(ctx.guild.id)
        if not cfg.get("raid_state"):
            return await ctx.neutral("raid mode isn't on")
        done = await self.end_raid(ctx.guild, f"ended by {ctx.author}")
        await ctx.approve("raid mode is **off**" + (": " + "; ".join(x.removeprefix("⚠️ ") for x in done) if done else ""))

    @antiraid.command(name="lockdown", aliases=["lock"])
    @has_perms(administrator=True)
    async def ar_lockdown(self, ctx: Context, what: str, state: str):
        """What raid mode does: `antiraid lockdown invites on` (pause invites & DMs) or `antiraid lockdown channels on`"""
        what = {"invite": "invites", "invites": "invites", "dms": "invites", "channel": "channels",
                "channels": "channels", "chat": "channels"}.get(what.lower())
        if what is None:
            raise UserError("Use `invites` (pause invites & DMs) or `channels` (lock channels for @everyone)")
        on = state.lower() in ("on", "true", "enable", "yes")
        if not on and state.lower() not in ("off", "false", "disable", "no"):
            raise UserError("Use `on` or `off`")
        cfg = await self._raid_cfg(ctx.guild.id)
        cfg["lockdown"] = {**RAID_LOCK_DEFAULTS, **(cfg.get("lockdown") or {}), what: on}
        await self.bot.db.set_config(ctx.guild.id, "antiraid", cfg)
        label = "pause invites and DMs" if what == "invites" else "lock channels for @everyone"
        await ctx.approve(f"during a raid I'll **{'' if on else 'not '}{label}**")

    # ================================================================ antialt
    @commands.group(name="antialt", aliases=["alt", "antialts"], invoke_without_command=True)
    @has_perms(administrator=True)
    async def antialt(self, ctx: Context):
        """Configure Anti-Alt security to screen newly created burner accounts"""
        cfg = await self.bot.db.get_config(ctx.guild.id, "antiraid")
        embed = discord.Embed(title="🛡️ Anti-Alt Account Protection", color=Colors.main)
        age = cfg.get("age")
        embed.add_field(name="Minimum Account Age", value=f"`< {age} day(s)`" if age else "`Off`", inline=True)
        embed.add_field(name="Require Avatar", value="`Enabled`" if cfg.get("avatar") else "`Disabled`", inline=True)
        embed.add_field(name="Enforcement Action", value=f"`{cfg.get('punishment', 'kick').title()}`", inline=True)
        embed.add_field(name="Whitelisted Users", value=f"`{len(cfg.get('whitelist', []))} account(s)`", inline=True)
        embed.set_footer(text=f"Configure with {ctx.clean_prefix}antialt minage <days> or {ctx.clean_prefix}antialt action <kick|ban|timeout>")
        await ctx.send(embed=embed)

    @antialt.command(name="minage", aliases=["age", "days"])
    @has_perms(administrator=True)
    async def aa_minage(self, ctx: Context, days: int = 0):
        """Set minimum account age in days before joining is allowed (0 to disable)"""
        await self._ar_set(ctx, "age", days or None, f"anti-alt minimum account age set to **{days} days**" if days else "turned off the anti-alt age filter")

    @antialt.command(name="action", aliases=["punish", "punishment"])
    @has_perms(administrator=True)
    async def aa_action(self, ctx: Context, action: str):
        """Set action for alt accounts: `kick`, `ban` or `timeout`"""
        action = action.lower()
        if action not in ("kick", "ban", "timeout"):
            raise UserError("Action must be `kick`, `ban` or `timeout`")
        await self._ar_set(ctx, "punishment", action, f"anti-alt action set to **{action}**")

    @antialt.command(name="avatar")
    @has_perms(administrator=True)
    async def aa_avatar(self, ctx: Context, state: str):
        """Toggle blocking accounts without custom avatars: `antialt avatar on/off`"""
        on = state.lower() in ("on", "true", "enable", "yes")
        await self._ar_set(ctx, "avatar", on or None, f"turned the anti-alt avatar requirement **{'on' if on else 'off'}**")

    @antialt.command(name="whitelist", aliases=["allow", "trust"])
    @has_perms(administrator=True)
    async def aa_whitelist(self, ctx: Context, user: discord.User):
        """Whitelist an alt account so it can join without being blocked"""
        cfg = await self.bot.db.get_config(ctx.guild.id, "antiraid")
        wl = cfg.setdefault("whitelist", [])
        if user.id in wl:
            wl.remove(user.id)
            msg = f"removed {user.mention} from the anti-alt whitelist"
        else:
            wl.append(user.id)
            msg = f"added {user.mention} to the anti-alt whitelist"
        await self.bot.db.set_config(ctx.guild.id, "antiraid", cfg)
        await ctx.approve(msg)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        from core import perms as _perms
        if _perms.is_bot_developer(member.id, self.bot):
            return
        cfg = await self.bot.db.get_config(member.guild.id, "antiraid")
        if not cfg or member.id in cfg.get("whitelist", []):
            return
        reason = None
        if cfg.get("age") and (discord.utils.utcnow() - member.created_at).days < cfg["age"]:
            reason = f"account younger than {cfg['age']} days"
        elif cfg.get("avatar") and member.avatar is None:
            reason = "no avatar"
        mj = cfg.get("massjoin")
        wave: list[int] = []
        if mj and not reason:
            gid = member.guild.id
            q = self.joins[gid]
            t = time.monotonic()
            q.append((t, member.id))
            while q and t - q[0][0] > mj["seconds"]:
                q.popleft()
            if len(q) >= mj["joins"]:
                starting = self.raid_until.get(gid, 0) <= t
                self.raid_until[gid] = t + RAID_QUIET  # raid mode lasts until joins stay quiet this long
                if starting:
                    # raid mode just started: the earlier joiners in this wave are part of it too
                    wave = [uid for _, uid in q if uid != member.id]
                    asyncio.create_task(self.start_raid(member.guild, f"**{len(q)}** members joined within "
                                                                      f"{mj['seconds']}s"))
            if self.raid_until.get(gid, 0) > t:
                reason = "mass join (raid mode)"
            elif not q:
                self.joins.pop(gid, None)
        if wave:
            for uid in wave:
                m = member.guild.get_member(uid)
                if m and uid not in cfg.get("whitelist", []):
                    await self._raid_punish(m, cfg, "mass join (raid mode)")
        if not reason:
            return
        await self._raid_punish(member, cfg, reason)

    async def _raid_punish(self, member: discord.Member, cfg: dict, reason: str) -> None:
        p = cfg.get("punishment", "kick")
        try:
            if p == "ban":
                await member.ban(reason=f"Antiraid: {reason}", delete_message_seconds=3600)
            elif p == "timeout":
                await member.timeout(timedelta(days=1), reason=f"Antiraid: {reason}")
            else:
                await member.kick(reason=f"Antiraid: {reason}")
        except discord.HTTPException:
            pass

    # ================================================================ raid mode
    async def _raid_cfg(self, guild_id: int) -> dict:
        return await self.bot.db.get_config(guild_id, "antiraid")

    async def _raid_post(self, guild: discord.Guild, embed: discord.Embed) -> None:
        """Raid notices go where antinuke alerts go (the antinuke log channel) and to the server owner."""
        channel = await self._log_channel(guild, await self.cfg(guild.id))
        for dest in (channel, guild.owner):
            if dest is None:
                continue
            try:
                await dest.send(embed=embed)
            except (discord.HTTPException, AttributeError):
                pass

    async def start_raid(self, guild: discord.Guild, why: str, by=None) -> list[str]:
        """Raid mode: pause invites and member DMs (and lock channels if that's turned on) until joins go quiet."""
        cfg = await self._raid_cfg(guild.id)
        if cfg.get("raid_state"):  # already on (a second wave, or a manual start during one)
            self.raid_until[guild.id] = max(self.raid_until.get(guild.id, 0), time.monotonic() + RAID_QUIET)
            return ["raid mode was already on; it now lasts longer"]
        lock = {**RAID_LOCK_DEFAULTS, **(cfg.get("lockdown") or {})}
        state = {"since": time.time(), "invites": False, "locked": []}
        done = []
        if lock["invites"]:
            until = discord.utils.utcnow() + timedelta(seconds=RAID_PAUSE_FOR)
            try:
                await guild.edit(invites_disabled_until=until, dms_disabled_until=until,
                                 reason="Antiraid: raid mode")
                state["invites"] = True
                done.append("paused invites and DMs between members")
            except (discord.HTTPException, TypeError) as e:
                done.append(f"⚠️ couldn't pause invites ({getattr(e, 'status', 'error')}): I need Manage Server")
        if lock["channels"]:
            state["locked"] = await self._lock_channels(guild)
            done.append(f"locked {len(state['locked'])} channel(s) for @everyone")
        cfg["raid_state"] = state
        await self.bot.db.set_config(guild.id, "antiraid", cfg)
        self.raid_until[guild.id] = max(self.raid_until.get(guild.id, 0), time.monotonic() + RAID_QUIET)
        self._watch_raid(guild)

        e = discord.Embed(title="🚨 Raid mode on", color=Colors.error, timestamp=discord.utils.utcnow(),
                          description=f"{why} in **{discord.utils.escape_markdown(guild.name)}**."
                                      + (f" Started by {by.mention}." if by is not None else ""))
        e.add_field(name="What I did", value="\n".join(f"• {x}" for x in done) or "• punishing new joiners",
                    inline=False)
        e.add_field(name="Ends", value=f"after {RAID_QUIET // 60} minutes without a new join wave, "
                                       "or with `,antiraid end`", inline=False)
        await self._raid_post(guild, e)
        return done

    async def end_raid(self, guild: discord.Guild, why: str = "joins went quiet") -> list[str]:
        """Undo raid mode's lockdown: resume invites and DMs, unlock exactly the channels raid mode locked."""
        self.raid_until.pop(guild.id, None)
        task = self.raid_tasks.pop(guild.id, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        cfg = await self._raid_cfg(guild.id)
        state = cfg.pop("raid_state", None)
        if not state:
            return []
        await self.bot.db.set_config(guild.id, "antiraid", cfg)
        done = []
        if state.get("invites"):
            try:
                await guild.edit(invites_disabled_until=None, dms_disabled_until=None, reason="Antiraid: raid over")
                done.append("resumed invites and DMs")
            except (discord.HTTPException, TypeError):
                done.append("⚠️ couldn't resume invites: Discord turns them back on within the hour anyway")
        if state.get("locked"):
            unlocked = await self._unlock_channels(guild, state["locked"])
            done.append(f"unlocked {unlocked} channel(s)")
        e = discord.Embed(title="✅ Raid mode off", color=Colors.success, timestamp=discord.utils.utcnow(),
                          description=f"Raid mode in **{discord.utils.escape_markdown(guild.name)}** ended: {why}.")
        if done:
            e.add_field(name="Undone", value="\n".join(f"• {x}" for x in done), inline=False)
        await self._raid_post(guild, e)
        return done

    def _watch_raid(self, guild: discord.Guild) -> None:
        old = self.raid_tasks.get(guild.id)
        if old is not None and not old.done():
            return

        async def watch():
            while True:
                wait = self.raid_until.get(guild.id, 0) - time.monotonic()
                if wait <= 0:
                    break
                await asyncio.sleep(min(wait, 30))
            await self.end_raid(guild)
        self.raid_tasks[guild.id] = asyncio.create_task(watch())

    async def _lock_channels(self, guild: discord.Guild) -> list[int]:
        """Stop @everyone sending in text channels that are open; returns the ones it locked (to unlock later)."""
        locked = []
        for channel in getattr(guild, "text_channels", []):
            ow = channel.overwrites_for(guild.default_role)
            if ow.send_messages is False:
                continue  # already locked by someone: leave it exactly as it is
            ow.send_messages = False
            try:
                await channel.set_permissions(guild.default_role, overwrite=ow, reason="Antiraid: raid mode")
                locked.append(channel.id)
            except discord.HTTPException:
                pass
        return locked

    async def _unlock_channels(self, guild: discord.Guild, channel_ids: list[int]) -> int:
        unlocked = 0
        for channel_id in channel_ids:
            channel = guild.get_channel(channel_id)
            if channel is None:
                continue
            ow = channel.overwrites_for(guild.default_role)
            if ow.send_messages is not False:
                continue
            ow.send_messages = None
            try:
                await channel.set_permissions(guild.default_role, overwrite=ow, reason="Antiraid: raid over")
                unlocked += 1
            except discord.HTTPException:
                pass
        return unlocked

    @commands.Cog.listener()
    async def on_ready(self):
        """Raid mode survives a restart: pick up where it was, or end it if it should be over."""
        if getattr(self, "_raids_resumed", False):
            return
        self._raids_resumed = True
        for guild in list(self.bot.guilds):
            cfg = await self._raid_cfg(guild.id)
            state = cfg.get("raid_state")
            if not state:
                continue
            self.raid_until[guild.id] = time.monotonic() + RAID_QUIET
            self._watch_raid(guild)


async def setup(bot):
    await bot.add_cog(Antinuke(bot))
