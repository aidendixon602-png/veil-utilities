"""Prefix help with a Veil-branded home, category and command navigation, and permission-aware details."""
from __future__ import annotations

from dataclasses import dataclass, field
import difflib
import inspect
import re
import typing

import discord
from discord.ext import commands

from core.utils import Colors, OwnedView, perm_label, respond_or_edit, shorten_md

HIDDEN_COGS = {"Owner", "Events", "Authorization", "DevTools"}
# Hide these from help only; command registration and runtime permission gates are unchanged.
HELP_EXCLUDED_COGS = {"Vanguard", "Automod"}

# Omit these from browsable category menus only. Direct command help, registration,
# aliases and all runtime permission checks remain unchanged.
CATEGORY_MENU_EXCLUDED_COMMANDS = {
    "softban", "lock", "unlock", "lockdown", "unlockdown", "botclear", "forcenickname", "hide", "unhide",
    "viewadmins", "addpermall", "autorole", "buttonrole", "reactionrole", "removepermall", "role", "vanity",
    "botchannels", "disablecommand", "enablecommand", "ignore", "modlog", "permlevels",
}

# (button label, cogs included, extra top-level commands pulled in from other cogs)
CATEGORIES: list[tuple[str, list[str], list[str]]] = [
    # Keep the browse menu focused on the main categories. General-purpose feature
    # cogs live under Utility; Security is a rank-gated select entry, separate from Antinuke.
    ("Utility", ["Utility", "Config", "Settings", "VoiceMaster", "Levels", "Giveaways", "Tickets",
                 "Feeds", "Integrations", "Fun", "AI", "Imaging"], []),
    ("Music", ["Music", "Spotify"], []),
    ("Moderation", ["Moderation", "Logging"], []),
    ("Roles", [], ["role", "autorole", "reactionrole", "buttonrole", "vanity", "stripstaff", "roles",
                   "roleblacklist", "addpermall", "removepermall", "userperm"]),
    ("Permissions", ["Permissions"], ["fakepermissions", "staffonly", "botchannels", "ignore",
                                      "disablecommand", "enablecommand", "prefix", "modlog"]),
    ("Antinuke", ["Antinuke"], ["antinuke"]),
    ("Security", [], ["antiraid", "antialt", "autosetup"]),
]

PAGE_SIZE = 10  # subcommand lists on a group's card
HELP_PAGE_SIZE = 8  # commands per page of a category
# the commands people reach for most come first; everything else follows alphabetically
PINNED: dict[str, list[str]] = {
    "Moderation": ["warn", "timeout", "untimeout", "kick", "purge", "warnings", "jail", "unjail", "cases",
                   "ban", "unban", "softban", "massban", "lock", "unlock", "lockdown", "unlockdown"],
    "Music": ["play", "skip", "queue", "pause", "resume", "stop", "volume", "loop", "song"],
    "Utility": ["userinfo", "avatar", "serverinfo", "ping", "afk", "snipe", "remind", "poll"],
    "Antinuke": ["antinuke", "antiraid", "antialt", "autosetup"],
    "Developer": ["gitpull", "restart", "version", "reload", "errors", "eval", "system", "blacklist"],
}
DEVELOPER_WORDS = ("dev", "developer", "secret", "secrets", "tokens", "secretsuite")
SECRET_WORDS = ("secret", "secrets", "tokens", "secretsuite")
HOME_CATEGORIES = ("Utility", "Music", "Moderation", "Roles", "Permissions", "Antinuke", "Developer", "Secret")
# Legacy names still open the unified moderation category.
CATEGORY_ALIASES = {"gv": "ghost vanguard", "vanguard": "ghost vanguard", "ghost": "ghost vanguard",
                    "moderator": "moderation", "moderators": "moderation", "mods": "moderation",
                    "admin": "moderation", "admins": "moderation", "administration": "moderation", "administrator": "moderation"}
MAX_ALIASES_SHOWN = 2  # per command on list pages; the full set is on the command's own card
MAX_ALIAS_LENGTH = 10
PER_PAGE = 10  # commands per category page
SUBCOMMANDS_PER_PAGE = 15  # fits both the embed and a Discord select
SUBCOMMANDS_SHOWN = 20  # a group's card lists its first subcommands, then says where the rest are
HELP_COLOR = Colors.main  # Veil accent

CATEGORY_MIN_RANKS: dict[str, int] = {
    "Utility": 0,       # Public
    "Music": 0,         # Public
    "VoiceMaster": 0,   # Public
    "Community": 0,     # Public
    "Fun": 0,           # Public
    "Images": 0,        # Public
    # each level up opens one more staff category (inside each one, commands you can't run stay hidden)
    "Moderation": 1,    # Helper+; individual checks retain their existing access requirements
    "Roles": 3,         # Admin+
    "Server": 4,        # Head Admin+
    "Automod": 4,       # Head Admin+
    "Permissions": 5,   # Manager+
    "Antinuke": 7,      # Explicit appointment, server owner or developer (checked separately)
    "Security": 7,      # Co-Owner+ for existing Antiraid / Anti-Alt checks
    "Ghost Vanguard": 0,  # everyone can type ,gv: Ghost Vanguard checks its own ranks for each command
}

# the level a Discord permission checked by a command maps to (matches has_perms' "you need ..." messages)
PERM_RANKS: dict[str, int] = {
    "administrator": 7,
    "manage_guild": 4, "manage_webhooks": 4, "manage_expressions": 4, "mention_everyone": 4,
    "manage_roles": 3, "ban_members": 3, "manage_channels": 3,
    "kick_members": 2, "moderate_members": 2, "manage_nicknames": 2,
    "manage_messages": 2,
    "veil_warn": 1, "veil_timeout": 1, "veil_cases": 1,
}

CATEGORY_EMOJIS: dict[str, str] = {
    "Utility": "🛠️",
    "Music": "🎵",
    "Moderation": "🛡️",
    "Roles": "🏷️",
    "Permissions": "🔒",
    "Antinuke": "🚨",
    "Security": "🛡️",
    "Ghost Vanguard": "👻",
    "Automod": "🤖",
    "Server": "⚙️",
    "VoiceMaster": "🎙️",
    "Community": "✨",
    "Fun": "🎲",
    "Images": "🖼️",
    "Developer": "⚡",
    "Secret": "🔒",
}

# One short line per category: shown under the list title.
CATEGORY_DESCRIPTIONS: dict[str, str] = {
    "Utility": "Lookups, AFK, snipe and everyday tools",
    "Music": "Lavalink and Spotify playback, queues, filters",
    "Moderation": "Moderation and administration tools available to your permission level",
    "Roles": "Role management tools for your permission level",
    "Permissions": "Permission and access settings for your level",
    "Antinuke": "Antinuke controls for authorized operators",
    "Security": "Antiraid and anti-alt security tools",
    "Ghost Vanguard": "Optional bridge to Ghost Vanguard commands",
    "Automod": "Message protection settings for your permission level",
    "Server": "Server configuration tools for your permission level",
    "VoiceMaster": "Temporary voice channels and VC controls",
    "Community": "Levels, tickets, giveaways, feeds and lookups",
    "Fun": "Games, actions, jokes and AI",
    "Images": "Image effects and edits",
    "Developer": "Bot owner tools: updates, restarts, blacklist, eval",
    "Secret": "Developer-only moderation and network tools",
}


# ---------------------------------------------------------------- command lookup and visibility

RBL_ROOTS = {"roleblacklist", "bypass", "hardbl", "checkbled", "stripbled", "rblauth", "rblunauth"}


def is_rbl_command(cmd) -> bool:
    root = getattr(cmd, "root_parent", None) or cmd
    return getattr(root, "name", "") in RBL_ROOTS

def category_for_command(cmd: commands.Command, bot: commands.Bot | None = None) -> str:
    """Find which category a command belongs to."""
    root = getattr(cmd, "root_parent", None) or cmd  # `role add` lives where `role` does
    name = getattr(root, "name", "")
    qname = getattr(root, "qualified_name", "")
    for label, _, extra in CATEGORIES:  # commands pulled into a category by name win over their own cog
        if name in extra or qname in extra:
            return label
    cog_name = getattr(cmd, "cog_name", None)
    if not cog_name and bot is not None and hasattr(bot, "get_command"):
        bot_cmd = bot.get_command(name) or bot.get_command(qname)
        if bot_cmd:
            cog_name = getattr(bot_cmd, "cog_name", None)
    # Help visibility and runtime authorization are separate: retain the old
    # Automod gate even though its help category is hidden.
    if cog_name == "Automod":
        return "Automod"
    if cog_name == "Vanguard":
        return "Ghost Vanguard"
    for label, cogs, _ in CATEGORIES:
        if cog_name and cog_name in cogs:
            return label
    if getattr(cmd, "cog_name", None) in HIDDEN_COGS:
        return "Developer"
    return "Community"


ADMIN_SPLIT = 3  # retain the former Admin category gate for higher-risk moderation commands


def command_category_gate(cmd, bot=None) -> int:
    """Keep runtime rank gates independent of the visible help grouping."""
    root = getattr(cmd, "root_parent", None) or cmd
    # Only the read-only assignment views moved down to Admin; retain the old
    # Permissions category gate for every assignment-changing subcommand.
    if getattr(cmd, "qualified_name", "") in {"userperm add", "userperm remove", "userperm remove deleted"}:
        return CATEGORY_MIN_RANKS["Permissions"]
    category = category_for_command(cmd, bot)
    if category == "Moderation" and own_rank(root) >= ADMIN_SPLIT:
        return ADMIN_SPLIT
    return CATEGORY_MIN_RANKS.get(category, 0)


def own_rank(cmd) -> int:
    """The level a command's own checks ask for (its permissions and uperm), ignoring which category it's in."""
    rank = 0
    for p in getattr(getattr(cmd, "callback", cmd), "__veil_perms__", []):
        if isinstance(p, str) and p in PERM_RANKS:
            rank = max(rank, PERM_RANKS[p])
        if isinstance(p, str) and p.startswith("uperm:"):
            from core.perms import PRESETS, canonical
            lvl = canonical(p.split(":", 1)[1].strip().lower())
            if lvl in PRESETS:
                rank = max(rank, PRESETS[lvl][1])
    return rank


def min_rank_for_command(cmd: commands.Command, bot: commands.Bot | None = None) -> int:
    """Return the minimum rank required to view help for this command."""
    if any(getattr(check, "__veil_owner_only__", False) for check in getattr(cmd, "checks", [])):
        from core.perms import OWNER_RANK
        return OWNER_RANK  # server owner only: nobody below sees it
    cat_rank = command_category_gate(cmd, bot)
    return max(cat_rank, own_rank(cmd))


def rank_name(rank: int) -> str:
    from core.perms import DEV_NAME, DEV_RANK, OWNER_NAME, OWNER_RANK, PRESETS
    if rank >= DEV_RANK:
        return DEV_NAME
    if rank >= OWNER_RANK:
        return OWNER_NAME
    return next((name for name, r, _ in sorted(PRESETS.values(), key=lambda item: item[1]) if r >= rank), f"Rank {rank}")


def perms_of(cmd: commands.Command) -> list[str]:
    perms = list(getattr(cmd.callback, "__veil_perms__", []))
    for check in cmd.checks:
        for cell in getattr(check, "__closure__", None) or ():
            val = cell.cell_contents
            if isinstance(val, dict) and all(isinstance(v, bool) for v in val.values()):
                perms.extend(val)
    return sorted(set(perms))


_ARG_NAMES: list[tuple[type, str]] = [
    (discord.Member, "member"), (discord.User, "user"), (discord.Role, "role"), (discord.TextChannel, "channel"),
    (discord.VoiceChannel, "channel"), (discord.abc.GuildChannel, "channel"), (bool, "on/off"), (int, "number"),
    (float, "number"), (str, "text"),
]


def _arg_name(name: str, param: inspect.Parameter) -> str:
    """A readable name: `arg1: discord.Role` reads as `role`."""
    if not re.fullmatch(r"arg\d*", name):
        return name
    annotation = param.annotation
    for kind in typing.get_args(annotation) or (annotation,):
        for base, label in _ARG_NAMES:
            if isinstance(kind, type) and issubclass(kind, base):
                return label
    return "value"


def short_args(cmd) -> str:
    """`<member> [reason]`: required in angle brackets, optional in square ones, no default values. A command's own
    `usage=` wins (commands that parse their arguments by hand say what they take there)."""
    usage_text = getattr(cmd, "usage", None)
    if isinstance(usage_text, str) and usage_text.strip():
        return usage_text.strip()
    out = []
    for name, param in (getattr(cmd, "clean_params", None) or {}).items():
        if name in ("ctx", "self"):  # commands built by a factory can carry these through
            continue
        required = param.default is inspect.Parameter.empty and param.kind != inspect.Parameter.VAR_POSITIONAL
        label = _arg_name(name, param)
        out.append(f"<{label}>" if required else f"[{label}]")
    return " ".join(out)


def description(cmd, limit: int = 110) -> str:
    """The command's first sentence for a category page, minus usage examples and "— see ..." tails."""
    text = ((getattr(cmd, "short_doc", None) or "").strip().splitlines() or [""])[0].strip()
    text = re.sub(r"^`[^`]*`\s*[—–:-]?\s*", "", text)         # a line that opens with an example
    text = re.sub(r"\s*\([^()]*`[^()]*\)", "", text)          # "(`none` to remove)": examples live on the command card
    text = re.split(r"\s+[—–]\s+|\.\s", text, maxsplit=1)[0]   # first sentence, no dash tail
    text = re.sub(r":\s*`.*$", "", text).strip().rstrip(".,;:")  # "Seek to a time: `seek 1:30`"
    return shorten_md(text, limit) if text else "no description yet"


def sentence(text: str) -> str:
    """'Ban a member' -> 'ban a member.' (acronyms like 'AI' or 'DJ' keep their capitals)."""
    if not text:
        return text
    if len(text) > 1 and text[1].islower():
        text = text[0].lower() + text[1:]
    return text if text.endswith((".", "!", "?", "…")) else text + "."


def usage(prefix: str, cmd: commands.Command) -> str:
    return f"{prefix}{cmd.qualified_name} {cmd.signature}".strip()


def category_commands(bot: commands.Bot) -> dict[str, list[commands.Command]]:
    """Resolve every category to its visible top-level commands (each command appears once)."""
    pulled = {name for _, _, extra in CATEGORIES for name in extra}
    out: dict[str, list[commands.Command]] = {}
    placed: set[str] = set()
    for label, cogs, extra in CATEGORIES:
        cmds = [bot.get_command(n) for n in extra]
        for cog_name in cogs:
            cog = bot.get_cog(cog_name)
            if cog:
                cmds += [c for c in cog.get_commands() if c.name not in pulled]
        cmds = sorted({c.qualified_name: c for c in cmds if c and not c.hidden
                       and c.name not in CATEGORY_MENU_EXCLUDED_COMMANDS}.values(), key=lambda c: c.name)
        if label == "Antinuke":
            # A complete browsable security reference, including nested commands. Visibility
            # still checks every command and its parents before a page is built.
            cmds += [sub for root in cmds if isinstance(root, commands.Group)
                     for sub in root.walk_commands() if not sub.hidden]
        if cmds:
            out[label] = cmds
            placed.update(c.qualified_name for c in cmds)
        elif label in out and not out[label]:
            del out[label]
    # Anything new that isn't mapped still shows up in Utility, the catch-all category.
    leftovers = [c for c in bot.commands if not c.hidden and c.qualified_name not in placed
                 and c.name not in CATEGORY_MENU_EXCLUDED_COMMANDS and c.cog is not None and c.cog_name not in HIDDEN_COGS | HELP_EXCLUDED_COGS]
    if leftovers:
        out.setdefault("Utility", []).extend(sorted(leftovers, key=lambda c: c.name))
    return out


@dataclass
class HelpAccess:
    """Permission inputs captured when help opens, without running command callbacks or checks."""
    levels: dict
    rank: int
    granted: set[str]
    native: discord.Permissions
    has_levels: bool
    disabled: set[str]
    bypass_disabled: bool
    bypass_permissions: bool
    guild_available: bool
    config_available: bool
    role_ids: set[str]
    role_restrictions: dict | None
    antinuke_admin: bool = False
    direct_granted: set[str] = field(default_factory=set)

    def permits(self, cmd: commands.Command, *, direct_override: bool = False) -> bool:
        from core import perms as _perms

        root = getattr(cmd, "root_parent", None) or cmd
        if not self.bypass_disabled and (cmd.qualified_name in self.disabled or root.name in self.disabled):
            return False
        if self.bypass_permissions:
            return True
        from core.command_access import roles_allow_command
        if not roles_allow_command(cmd, self.role_ids, self.role_restrictions):
            return False
        metadata = getattr(getattr(cmd, "callback", None), "__veil_perms__", [])
        required = {p for p in metadata if isinstance(p, str) and not p.startswith("uperm:")}
        levels = [p.split(":", 1)[1].strip().lower() for p in metadata
                  if isinstance(p, str) and p.startswith("uperm:")]
        if (required or levels) and (not self.guild_available or not self.config_available):
            return False
        for level in levels:
            target = _perms.all_levels(self.levels).get(_perms.canonical(level))
            if self.rank < (target["rank"] if target else 1) and not direct_override:
                return False
        if required:
            if "administrator" in self.granted:
                return True
            return required <= self.granted
        return True


def visible_command(cmd, bot, is_owner: bool, rank: int, access: HelpAccess | None = None) -> bool:
    """Apply visibility to the command and its ancestors; this is not a replacement for runtime checks."""
    chain = [cmd, *getattr(cmd, "parents", [])]
    if any(getattr(c, "cog_name", None) in HELP_EXCLUDED_COGS for c in chain):
        return False
    if is_owner:
        return True
    if any(getattr(c, "hidden", False) or getattr(c, "cog_name", None) in HIDDEN_COGS for c in chain):
        return False
    root = getattr(cmd, "root_parent", None) or cmd
    is_antinuke = getattr(root, "name", "") == "antinuke" and getattr(root, "cog_name", None) == "Antinuke"
    # An assigned staff rank (even Supreme Leader) is not an Antinuke appointment.
    if is_antinuke and not (access and (access.bypass_permissions or access.antinuke_admin)):
        return False
    from core.utils import command_permission_requirements, permissions_cover, requires_explicit_rank
    for c in chain:
        needed = command_permission_requirements(c)
        direct_override = bool(access and not requires_explicit_rank(c) and needed
                               and ("administrator" in access.direct_granted or needed & access.direct_granted)
                               and permissions_cover(access.granted, needed))
        if access is None:
            if any(getattr(check, "__veil_owner_only__", False) for check in getattr(c, "checks", [])):
                return False
            if rank < min_rank_for_command(c, bot):
                return False
        else:
            # The static rank is a guide, not a substitute for explicit grants and custom levels.
            # Owner-only checks still need their own higher gate.
            if any(getattr(check, "__veil_owner_only__", False) for check in getattr(c, "checks", [])):
                if not access.guild_available or not access.bypass_permissions:
                    return False
            elif (rank < command_category_gate(c, bot) and not direct_override
                  and not (is_antinuke and access.antinuke_admin)):
                return False
            if not access.permits(c, direct_override=direct_override):
                return False
    return True


def allowed(cmds: typing.Iterable[commands.Command], bot, is_owner: bool, rank: int,
            access: HelpAccess | None = None) -> list[commands.Command]:
    """The commands this person may see in help, including inherited restrictions."""
    return [c for c in cmds if visible_command(c, bot, is_owner, rank, access)]


def category_labels(categories: dict[str, list], is_owner: bool, rank: int,
                    access: HelpAccess | None = None) -> list[str]:
    """Category names this person can open, in menu order."""
    from core.utils import command_permission_requirements, permissions_cover

    def direct_category(label: str) -> bool:
        if not access or not access.direct_granted:
            return False
        for root in categories.get(label, []):
            candidates = [root, *root.walk_commands()] if isinstance(root, commands.Group) else [root]
            for cmd in candidates:
                needed = command_permission_requirements(cmd)
                if (needed and ("administrator" in access.direct_granted or needed & access.direct_granted)
                        and permissions_cover(access.granted, needed)
                        and visible_command(cmd, None, is_owner, rank, access)):
                    return True
        return False

    return [label for label, _, _ in CATEGORIES if label in categories and (
        (is_owner or bool(access and (access.bypass_permissions or access.antinuke_admin)))
        if label == "Antinuke" else (is_owner or rank >= CATEGORY_MIN_RANKS.get(label, 0) or direct_category(label)))]


# ---------------------------------------------------------------- text helpers

def count(n: int, singular: str, plural: str | None = None) -> str:
    return f"{n} {singular if n == 1 else plural or singular + 's'}"


_ASIDE = re.compile(r"\s*\([^()]*\)")  # "(or reset)", "(0–150)": detail for the command's own page
_CLAUSE = re.compile(r"\s+[—–-]\s+|;\s|:\s|\.\s")
LINE_WIDTH = 70  # characters that fit on one line of a help list in Discord, number badge included


def blurb(cmd, limit: int = 60) -> str:
    """One short line for list pages: the description's first clause without asides, so every entry fits on one
    line. Examples, notes in brackets and "see ..." tails stay on the command's own page (`help <command>`)."""
    text = ((getattr(cmd, "short_doc", None) or "").strip().splitlines() or [""])[0].strip()
    text = _ASIDE.sub("", text)
    text = _CLAUSE.split(text, maxsplit=1)[0].strip().rstrip(".,")
    if not text:
        return "No description yet"
    return shorten_md(text[0].upper() + text[1:], limit)


def sub_count(cmd) -> int:
    if not isinstance(cmd, commands.Group):
        return 0
    return sum(1 for c in cmd.walk_commands() if not c.hidden)


def alias_hint(cmd) -> str:
    """The shortest one or two aliases, in brackets. Long ones crowd the line; all of them are on the command's page."""
    aliases = sorted((a for a in getattr(cmd, "aliases", None) or [] if len(a) <= MAX_ALIAS_LENGTH), key=len)
    if not aliases:
        return ""
    return f" *({', '.join(aliases[:MAX_ALIASES_SHOWN])})*"


_EXAMPLE_BY_NAME = {
    "reason": "spamming", "duration": "10m", "time": "10m", "length": "10m", "amount": "10", "count": "10",
    "limit": "10", "number": "5", "user": "@user", "member": "@user", "target": "@user", "role": "@role",
    "channel": "#general", "url": "https://example.com", "link": "https://example.com", "query": "query",
    "search": "query", "song": "song name", "text": "hello", "message": "hello", "content": "hello",
    "name": "name", "color": "#ff0000", "colour": "#ff0000", "emoji": "😀", "code": "code",
}
_EXAMPLE_BY_TYPE: list[tuple[type, str]] = [
    (discord.Member, "@user"), (discord.User, "@user"), (discord.Role, "@role"),
    (discord.TextChannel, "#general"), (discord.VoiceChannel, "#voice"), (discord.abc.GuildChannel, "#general"),
    (bool, "on"), (int, "5"), (float, "1.5"),
]


def _example_value(param: inspect.Parameter) -> str:
    ann = getattr(param.annotation, "converter", param.annotation)  # Greedy[X] -> X
    for _ in range(4):  # Optional[X] / X | None / Union[...] -> first real member
        origin = typing.get_origin(ann)
        if origin is None:
            break
        args = [a for a in typing.get_args(ann) if a is not type(None)]
        if origin is typing.Literal:
            return str(args[0]) if args else param.name
        ann = args[0] if args else str
    if param.name in _EXAMPLE_BY_NAME:
        return _EXAMPLE_BY_NAME[param.name]
    if isinstance(ann, type):
        for kind, example in _EXAMPLE_BY_TYPE:
            if issubclass(ann, kind):
                return example
    return param.name


def example_for(cmd, prefix: str) -> str:
    """A believable example invocation built from the required arguments (or the command's own `example` extra)."""
    try:
        extras = getattr(cmd, "extras", None) or {}
        if extras.get("example"):
            return f"{prefix}{cmd.qualified_name} {extras['example']}".strip()
        params = [p for p in (getattr(cmd, "clean_params", None) or {}).values()
                  if p.default is inspect.Parameter.empty and p.kind is not inspect.Parameter.VAR_POSITIONAL]
        if not params or any(re.fullmatch(r"arg\d*", p.name) for p in params):
            return ""  # placeholder names say nothing; commands like that set extras={"example": ...}
        words = [_example_value(p) for p in params]
        optional = [p for p in (getattr(cmd, "clean_params", None) or {}).values() if p.default is not inspect.Parameter.empty]
        if any(p.name == "reason" for p in optional):  # a reason makes moderation examples read like real use
            words.append("spamming")
        return f"{prefix}{cmd.qualified_name} " + " ".join(words)
    except Exception:
        return ""


# ---------------------------------------------------------------- view components

def page_order(label: str, cmds: list[commands.Command]) -> list[commands.Command]:
    pinned = PINNED.get(label, [])
    rank = {name: i for i, name in enumerate(pinned)}
    return sorted(cmds, key=lambda c: (rank.get(c.name, len(pinned)) if not getattr(c, "parent", None)
                                       else len(pinned), c.qualified_name))


def developer_commands(bot) -> list[commands.Command]:
    """Owner and bot-operations commands shown in Developer (excluding Secret tools)."""
    cmds = []
    for cog_name in sorted(HIDDEN_COGS - {"DevTools"}):
        cog = bot.get_cog(cog_name) if bot is not None and hasattr(bot, "get_cog") else None
        if cog:
            cmds += [c for c in cog.get_commands() if c.parent is None]
    return sorted({c.qualified_name: c for c in cmds}.values(), key=lambda c: c.name)


def secret_commands(bot) -> list[commands.Command]:
    """Restricted DevTools commands shown only in the separate Secret category."""
    cog = bot.get_cog("DevTools") if bot is not None and hasattr(bot, "get_cog") else None
    cmds = [c for c in cog.get_commands() if c.parent is None] if cog else []
    return sorted({c.qualified_name: c for c in cmds}.values(), key=lambda c: c.name)


HOME = "__home__"


class CategorySelect(discord.ui.Select["HelpView"]):
    """Jump to any category available in this menu."""

    def __init__(self, labels: list[str], sizes: dict[str, int], current: str | None):
        options = [discord.SelectOption(label=label, value=label, default=label == current,
                                        description=count(sizes.get(label, 0), "command")) for label in labels]
        super().__init__(placeholder="More categories", options=options[:25], min_values=1, max_values=1, row=0)

    async def callback(self, it: discord.Interaction):
        choice = self.values[0]
        if choice == HOME:
            await self.view.show_home(it)
        else:
            await self.view.show_category(it, choice)


class CategoryButton(discord.ui.Button["HelpView"]):
    """Open a visible category from the help home screen."""

    def __init__(self, category: str, row: int):
        style = discord.ButtonStyle.danger if category == "Secret" else discord.ButtonStyle.secondary
        super().__init__(label=category[:80], style=style, row=row)
        self.category = category

    async def callback(self, it: discord.Interaction):
        await self.view.show_category(it, self.category)


class CommandSelect(discord.ui.Select["HelpView"]):
    """Open the details for a command on the current category page."""

    def __init__(self, commands_on_page: list[commands.Command], prefix: str, current: str | None):
        options = [discord.SelectOption(label=f"{prefix}{c.qualified_name}"[:100], value=c.qualified_name,
                                        description=blurb(c, 100), default=c.qualified_name == current)
                   for c in commands_on_page]
        super().__init__(placeholder="Open a command", options=options, min_values=1, max_values=1, row=1)

    async def callback(self, it: discord.Interaction):
        await self.view.show_command(it, self.values[0])


class SubcommandSelect(discord.ui.Select["HelpView"]):
    """Browse visible direct subcommands of the open group."""

    def __init__(self, items: list[commands.Command], prefix: str):
        options = [discord.SelectOption(label=f"{prefix}{c.qualified_name}"[:100], value=c.qualified_name,
                                        description=blurb(c, 100)) for c in items]
        super().__init__(placeholder="Open a subcommand", options=options, min_values=1, max_values=1, row=2)

    async def callback(self, it: discord.Interaction):
        await self.view.show_subcommand(it, self.values[0])


class PageButton(discord.ui.Button["HelpView"]):
    def __init__(self, label: str, step: int, disabled: bool):
        super().__init__(label=label, style=discord.ButtonStyle.secondary, disabled=disabled, row=3)
        self.step = step

    async def callback(self, it: discord.Interaction):
        await self.view.turn(it, self.step)


class SubPageButton(discord.ui.Button["HelpView"]):
    def __init__(self, label: str, step: int, disabled: bool):
        super().__init__(label=label, style=discord.ButtonStyle.secondary, disabled=disabled, row=3)
        self.step = step

    async def callback(self, it: discord.Interaction):
        await self.view.turn_subpage(it, self.step)


class HelpHomeButton(discord.ui.Button["HelpView"]):
    def __init__(self):
        super().__init__(label="Home", style=discord.ButtonStyle.secondary, row=3)

    async def callback(self, it: discord.Interaction):
        await self.view.show_home(it)


class CategoryBackButton(discord.ui.Button["HelpView"]):
    def __init__(self):
        super().__init__(label="Back", style=discord.ButtonStyle.secondary, row=3)

    async def callback(self, it: discord.Interaction):
        await self.view.show_category_page(it)


class HelpView(OwnedView):
    """Browse the caller's visible categories and command details without leaving the menu."""

    def __init__(self, help_cmd: Help, categories: dict[str, list[commands.Command]], start: str | None = None,
                 is_owner: bool = False, author_rank: int = 0, access: HelpAccess | None = None):
        super().__init__(help_cmd.context.author.id, timeout=300)
        self.help_cmd, self.categories = help_cmd, categories
        self.is_owner, self.author_rank, self.access = is_owner, author_rank, access
        self.message: discord.Message | None = None
        self.pages: dict[str, discord.Embed] = {}
        self.books: dict[str, list[discord.Embed]] = {}
        self.items: dict[str, list[commands.Command]] = {}
        self.sizes: dict[str, int] = {}
        self.page = 0
        self.selected: commands.Command | None = None
        self.stack: list[tuple[commands.Command, int]] = []
        self.subpage = 0
        for label in self.labels():
            cmds = page_order(label, self.commands_in(label))
            self.sizes[label] = len(cmds)
            compact_security = label == "Antinuke" and help_cmd.security_overview(cmds) is not None
            self.items[label] = [c for c in cmds if getattr(c, "parent", None) is None] if compact_security else cmds
            self.books[label] = help_cmd.category_pages(label, cmds)
            self.pages[label] = self.books[label][0]
        self.home = help_cmd.home_embed(author_rank, categories, is_owner, total=sum(self.sizes.values()),
                                        sections=len(self.pages), visible_categories=list(self.pages))
        self.current = start if start in self.pages else None
        self._build_items()

    async def interaction_check(self, it: discord.Interaction) -> bool:
        if not await super().interaction_check(it):
            return False
        if self.is_owner and not await self.bot.is_owner(it.user):
            await it.response.send_message("This menu is no longer available.", ephemeral=True)
            return False
        from core.utils import owner_or_developer
        guild = getattr(it, "guild", None)
        original_guild = getattr(self.help_cmd.context, "guild", None)
        if self.access and self.access.bypass_permissions and original_guild:
            if (not guild or guild.id != original_guild.id
                    or not owner_or_developer(it.user, guild, self.bot)):
                await it.response.send_message("This menu is no longer available.", ephemeral=True)
                return False
        if self.access and self.access.antinuke_admin and original_guild:
            from core.utils import owner_or_developer
            if not guild or guild.id != original_guild.id or not owner_or_developer(it.user, guild, self.bot):
                await it.response.send_message("This menu is no longer available.", ephemeral=True)
                return False
        return True

    @property
    def bot(self):
        return getattr(getattr(self.help_cmd, "context", None), "bot", None)

    def labels(self) -> list[str]:
        names = category_labels(self.categories, self.is_owner, self.author_rank, self.access)
        return (names + (["Developer", "Secret"] if self.is_owner else []))[:24]

    def commands_in(self, label: str | None) -> list[commands.Command]:
        if label == "Developer":
            return developer_commands(self.bot) if self.is_owner else []
        if label == "Secret":
            return secret_commands(self.bot) if self.is_owner else []
        return allowed(self.categories.get(label, []), self.bot, self.is_owner, self.author_rank, self.access)

    def current_items(self) -> list[commands.Command]:
        start = self.page * PER_PAGE
        return self.items.get(self.current, [])[start:start + PER_PAGE]

    def subcommands(self) -> list[commands.Command]:
        if not isinstance(self.selected, commands.Group):
            return []
        return sorted(allowed(self.selected.commands, self.bot, self.is_owner, self.author_rank, self.access), key=lambda c: c.name)

    def subpages(self) -> int:
        return max(1, (len(self.subcommands()) + SUBCOMMANDS_PER_PAGE - 1) // SUBCOMMANDS_PER_PAGE)

    def current_subcommands(self) -> list[commands.Command]:
        start = self.subpage * SUBCOMMANDS_PER_PAGE
        return self.subcommands()[start:start + SUBCOMMANDS_PER_PAGE]

    def embed(self) -> discord.Embed:
        if self.current is None:
            return self.home
        if self.selected is not None:
            card = self.help_cmd.command_embed(self.selected, visible_subcommands=self.current_subcommands(),
                                               access=self.access)
            if self.subpages() > 1:
                card.set_footer(text=f"{card.footer.text} · subcommands {self.subpage + 1}/{self.subpages()}")
            return card
        return self.books[self.current][self.page]

    def _build_items(self):
        self.clear_items()
        if self.current is None:
            primary = [label for label in HOME_CATEGORIES if label in self.pages]
            other = [label for label in self.pages if label not in HOME_CATEGORIES]
            for index, label in enumerate(primary):
                self.add_item(CategoryButton(label, row=index // 5))
            if other:
                # Keep every other accessible category available without crowding the home screen.
                select = CategorySelect(other, self.sizes, self.current)
                select.row = 2
                self.add_item(select)
            return
        items = self.current_items()
        if items and self.selected is None:
            self.add_item(CommandSelect(items, self.help_cmd.context.clean_prefix, None))
        subcommands = self.current_subcommands() if self.selected else []
        if subcommands:
            self.add_item(SubcommandSelect(subcommands, self.help_cmd.context.clean_prefix))
        self.add_item(HelpHomeButton())
        if self.selected:
            self.add_item(CategoryBackButton())
            total = self.subpages()
            if total > 1:
                self.add_item(SubPageButton("Previous", -1, disabled=self.subpage == 0))
                self.add_item(discord.ui.Button(label=f"{self.subpage + 1} / {total}", disabled=True, row=3))
                self.add_item(SubPageButton("Next", 1, disabled=self.subpage >= total - 1))
        else:
            total = len(self.books[self.current])
            if total > 1:
                self.add_item(PageButton("Previous", -1, disabled=self.page == 0))
                self.add_item(discord.ui.Button(label=f"{self.page + 1} / {total}", disabled=True, row=3))
                self.add_item(PageButton("Next", 1, disabled=self.page >= total - 1))

    async def turn(self, it: discord.Interaction, step: int):
        if self.current not in self.books or self.selected is not None:
            return await it.response.send_message("This page is no longer available.", ephemeral=True)
        total = len(self.books[self.current])
        self.page = max(0, min(self.page + step, total - 1))
        self._build_items()
        await respond_or_edit(it, self.embed(), self)

    async def show_command(self, it: discord.Interaction, name: str):
        cmd = next((c for c in self.current_items() if c.qualified_name == name), None)
        if cmd is None or not visible_command(cmd, self.bot, self.is_owner, self.author_rank, self.access):
            return await it.response.send_message("This command is not available in this menu.", ephemeral=True)
        if is_rbl_command(cmd):
            from core.utils import owner_or_developer
            if not owner_or_developer(it.user, getattr(it, "guild", None), self.bot):
                return await it.response.send_message("This command is not available in this menu.", ephemeral=True)
            return await it.response.send_message(embed=self.help_cmd.command_embed(cmd, access=self.access), ephemeral=True)
        self.selected, self.stack, self.subpage = cmd, [], 0
        self._build_items()
        await respond_or_edit(it, self.embed(), self)

    async def show_subcommand(self, it: discord.Interaction, name: str):
        cmd = next((c for c in self.current_subcommands() if c.qualified_name == name), None)
        if cmd is None or not visible_command(cmd, self.bot, self.is_owner, self.author_rank, self.access):
            return await it.response.send_message("This subcommand is not available in this menu.", ephemeral=True)
        self.stack.append((self.selected, self.subpage))
        self.selected, self.subpage = cmd, 0
        self._build_items()
        await respond_or_edit(it, self.embed(), self)

    async def turn_subpage(self, it: discord.Interaction, step: int):
        if not isinstance(self.selected, commands.Group):
            return await it.response.send_message("This page is no longer available.", ephemeral=True)
        self.subpage = max(0, min(self.subpage + step, self.subpages() - 1))
        self._build_items()
        await respond_or_edit(it, self.embed(), self)

    async def show_category_page(self, it: discord.Interaction):
        if self.current not in self.books:
            return await self.show_home(it)
        if self.stack:
            self.selected, self.subpage = self.stack.pop()
        else:
            self.selected, self.subpage = None, 0
        self._build_items()
        await respond_or_edit(it, self.embed(), self)

    async def show_category(self, it: discord.Interaction, label: str):
        if label not in self.pages:
            return await it.response.send_message("You can't open that category.", ephemeral=True)
        self.current, self.page, self.selected = label, 0, None
        self.stack, self.subpage = [], 0
        self._build_items()
        await respond_or_edit(it, self.embed(), self)

    async def show_home(self, it: discord.Interaction):
        self.current, self.page, self.selected = None, 0, None
        self.stack, self.subpage = [], 0
        self._build_items()
        await respond_or_edit(it, self.home, self)

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass


# ---------------------------------------------------------------- the card system
#
# Every help card is built from the same small set of parts, so the whole menu reads as one surface:
#
#   > headline             the command's first line, as a quote, so it reads before anything else
#   -# detail              the rest of the description, quiet
#   -# ◇ Use               a bare ```bash block of the invocations worth copying
#   -# ◇ Access            one line: Rank, Needs, then Aliases
#   **Subcommands**        `,role add` · Add a role to a member, one line each
#   -# • note              an aside that explains a caveat or an unavailable command
#
# Entries stay narrow on purpose: Discord renders a code block in a fixed-width font, so a line over about
# 56 columns scrolls sideways on a phone. Everything that would wrap is either shortened or dropped, never left
# to break mid-sentence.

# the subcommand shapes worth showing in a group's `Use` block: the everyday verbs first
COMMON_VERBS = ("add", "remove", "list", "create", "delete", "edit", "set", "toggle", "reset", "view", "info")
SUB_SECTION = "◇"
NOTE = "-#"
WIDE = 56  # a code block wider than this wraps on mobile; long lines are turned into plain entries instead
INLINE_MAX = 52  # a chip column has to stay on one line
ENTRY_MAX = 62  # a `command · text` entry: longer descriptions drop, they don't wrap raggedly


def chip(text: str) -> str:
    return f"`{text}`"


def code_block(lines: list[str], lang: str = "bash") -> str:
    return f"```{lang}\n" + "\n".join(lines) + "\n```"


def section(title: str, *, quiet: bool = False) -> str:
    """A section heading: `**Access**`, or the smaller `-# ◇ Access` for secondary parts of a card."""
    return f"{NOTE} {SUB_SECTION} {title}" if quiet else f"**{title}**"


def bullet(text: str, *, mark: str = "•") -> str:
    return f"{NOTE} {mark} {text}"


def entry(name: str, body: str, prefix: str = "") -> str:
    """`<command>` · what it does — the line a select menu shows, reused on cards so both read the same."""
    return f"{chip(prefix + name)} · {body}"


def _short_name(cmd) -> str:
    """`antinuke admin` -> `an admin`: the same short form the category lists use, so chips stay narrow."""
    name = getattr(cmd, "qualified_name", getattr(cmd, "name", ""))
    return "an" + name[len("antinuke"):] if name == "antinuke" or name.startswith("antinuke ") else name


def _one_line(text: str, limit: int) -> str:
    """Collapse a multi-line description into one sentence for an inline entry."""
    flat = " ".join(part.strip() for part in (text or "").splitlines() if part.strip())
    return shorten_md(flat, limit)


def sectioned(rows: list[tuple[str, list[tuple[str, str]]]], *,
              limit: int = ENTRY_MAX) -> list[str]:
    """Rows of `(command, text)` grouped under `(heading, rows)`:

        **◈  Member roles**
        **Commands**
        `,role add` · Give a role to a member
        `,role remove` · Take it away again

    A row whose text won't fit on one line keeps the command and drops the text — the select menu below the
    card still shows every description, so a card never grows a ragged second line. Empty headings are
    skipped, which keeps a trimmed-down card free of "no commands" placeholders.
    """
    lines: list[str] = []
    for heading, items in rows:
        items = [(name, _one_line(text, 120)) for name, text in items if text is not None]
        if not items:
            continue
        if lines:
            lines.append("")
        if heading:
            lines.append(section(heading))
        for name, text in items:
            lines.append(f"{chip(name)} · {text}" if text and len(name) + len(text) + 5 <= limit
                         else chip(name))
    return lines


def chips_columns(names: list[str], *, limit: int = INLINE_MAX) -> list[str]:
    """Pack `a · b · c` chip lines as full as they can get without wrapping: fewer, fuller lines."""
    rows: list[list[str]] = [[]]
    for name in names:
        candidate = " · ".join(chip(n) for n in [*rows[-1], name])
        if rows[-1] and len(candidate) > limit:
            rows.append([name])
        else:
            rows[-1].append(name)
    return [" · ".join(chip(n) for n in row) for row in rows if row]


def script(lines: list[str]) -> list[str]:
    """A usage block, or plain lines when one line would wrap on mobile. A fence can't take `-#`, so it stays bare."""
    lines = [line for line in lines if line]
    if not lines:
        return []
    if max(len(line) for line in lines) > WIDE:
        return [f"{NOTE} {chip(line)}" for line in lines]
    return [code_block(lines)]


def facts(pairs: list[tuple[str, str]]) -> str:
    """`Rank **Admin** · Needs Manage Roles`: the access line, labels first, empty parts left out."""
    return " · ".join(f"{label} {value}" for label, value in pairs if value)


def alias_chips(cmd, limit: int = 4) -> str:
    r"""`\`r\` \`changerole\` \`cr\``: the short aliases worth showing; the rest are counted, not listed."""
    aliases = [a for a in (getattr(cmd, "aliases", None) or []) if a]
    shown = " ".join(chip(a) for a in aliases[:limit])
    rest = len(aliases) - limit
    return shown + (f" +{rest}" if rest > 0 else "")


def sub_rows(cmd, prefix: str, subs: list[commands.Command], *, per_line: int = 3) -> list[str]:
    """Subcommands as chip lines, or a `command · what it does` line each when chips would carry no meaning."""
    names = [prefix + getattr(c, "qualified_name", c) for c in subs]
    if all(len(" · ".join(f"`{n}`" for n in names[i:i + per_line])) <= INLINE_MAX
           for i in range(0, len(names), per_line)):
        return chips_columns(names)
    return [entry(getattr(c, "qualified_name", c), blurb(c, 58), prefix) for c in subs]


# ---------------------------------------------------------------- the help command

class Help(commands.HelpCommand):
    def __init__(self):
        super().__init__(command_attrs={"aliases": ["h", "commands"], "help": "Show help for a command or category"})

    # ------------------------------------------------------------ embeds
    # ------------------------------------------------------------ embeds
    def _brand(self, e: discord.Embed, *, section: str | None = None) -> discord.Embed:
        """Veil's author line: `Veil Utilities` (or `Veil Utilities · Command reference` on cards)."""
        bot = getattr(self.context, "bot", None)
        name = getattr(getattr(bot, "user", None), "name", None)
        avatar = getattr(getattr(getattr(bot, "user", None), "display_avatar", None), "url", None)
        title = (name or "Veil Utilities") + (f" · {section}" if section else "")
        if isinstance(avatar, str) and avatar:
            e.set_author(name=title, icon_url=avatar)
        else:
            e.set_author(name=title)
        return e

    def home_embed(self, author_rank: int = 0, categories: dict[str, list] | None = None,
                   is_owner: bool = False, *, total: int | None = None, sections: int | None = None,
                   visible_categories: list[str] | None = None) -> discord.Embed:
        """Describe only categories already permitted by the menu's visibility checks."""
        p = self.context.clean_prefix
        count_text = f"{count(total, 'command')} across {count(sections, 'category', 'categories')}" \
            if total is not None and sections else "Every command this server's bot offers"
        desc = (f"{section('Command reference')}\n"
                f"Pick a category to browse; each card lists only commands you can use.\n\n"
                f"{bullet(f'prefix {chip(p)} · {count_text}')}")
        e = discord.Embed(color=HELP_COLOR, description=desc)
        self._brand(e)
        e.title = None
        e.set_footer(text=None)
        return e

    def command_line(self, cmd: commands.Command) -> str:
        """One command on a category page, with a marker for groups."""
        p = self.context.clean_prefix
        more = " · *group*" if isinstance(cmd, commands.Group) else ""
        name = cmd.qualified_name
        if name.startswith("antinuke "):
            name = "an " + name[len("antinuke "):]
        return f"`{p}{name}` — {blurb(cmd, 46)}{more}"

    def alias_line(self, cmd: commands.Command, limit: int | None = None) -> str:
        """Fully qualified alias invocations, including parent groups."""
        parent = getattr(cmd, "parent", None)
        path = parent.qualified_name if parent else ""
        if path == "antinuke" or path.startswith("antinuke "):
            path = "an" + path[len("antinuke"):]
        prefix = self.context.clean_prefix
        aliases = cmd.aliases if limit is None else cmd.aliases[:limit]
        text = ", ".join(f"`{prefix}{path + ' ' if path else ''}{alias}`" for alias in aliases)
        remaining = len(cmd.aliases) - len(aliases)
        return text + (f" · +{remaining} more" if remaining else "")

    # ------------------------------------------------------------ command cards

    def _about_lines(self, cmd) -> list[str]:
        """The description: the first line is the headline, the rest is the detail under it."""
        about = (getattr(cmd, "help", None) or getattr(cmd, "short_doc", None) or "").strip()
        about = "\n".join(ln for ln in about.splitlines() if not ln.startswith("Example:")).strip()
        about = about or "No description yet."
        head, _, tail = about.partition("\n")
        head = _one_line(head, 300)
        head = head[0].upper() + head[1:] if head else head
        tail = _one_line(tail, 280)
        return [f"> {head}", "", f"{NOTE} {tail}"] if tail else [f"> {head}"]

    def _use_lines(self, cmd, local_subs: list[commands.Command]) -> list[str]:
        """How to type it: the uses worth copying."""
        p = self.context.clean_prefix
        args = short_args(cmd)
        own = f"{p}{cmd.qualified_name}" + (f" {args}" if args else "")
        example = ""
        written = [ln for ln in (getattr(cmd, "help", None) or "").splitlines() if ln.startswith("Example:")]
        if written:  # the docstring's own `Example: ...` line beats a guessed invocation
            example = written[0].split(":", 1)[1].strip()
        else:
            guess = example_for(cmd, p)
            example = guess if guess and guess != f"{p}{cmd.qualified_name}" else ""
        example = example.strip().strip("`").strip()
        if isinstance(cmd, commands.Group) and local_subs:
            # the base form plus the everyday shapes a subcommand takes: `,role add <member> <role>` first,
            # not whichever subcommand happens to sort first alphabetically
            order = {verb: i for i, verb in enumerate(COMMON_VERBS)}
            ranked = sorted(local_subs, key=lambda c: (order.get(c.name, len(COMMON_VERBS)), c.name))
            shapes = []
            for sub in ranked:
                sub_args = short_args(sub)
                shapes.append(f"{p}{sub.qualified_name}" + (f" {sub_args}" if sub_args else ""))
            return script([own, *dict.fromkeys(shapes)][:5])
        lines = [own]
        if example and example != own:
            lines.append(example)
        return script(lines)

    def _access_note(self, cmd, access: HelpAccess | None) -> tuple[str | None, str | None]:
        """`(rank name, note)`: the level this card asks for, and a quiet note when the reader can't run it."""
        chain = [cmd, *cmd.parents]
        rank = max(min_rank_for_command(item, getattr(self.context, "bot", None)) for item in chain)
        display = rank_name(rank) if rank > 0 else None
        if access is not None:
            from core import perms as _perms
            levels = _perms.all_levels(access.levels)
            for item in chain:
                for requirement in getattr(item.callback, "__veil_perms__", []):
                    if isinstance(requirement, str) and requirement.startswith("uperm:"):
                        key = _perms.canonical(requirement.split(":", 1)[1].strip().lower())
                        target = levels.get(key)
                        if target and target["rank"] > rank:
                            rank, display = target["rank"], target["name"]
            if not access.permits(cmd, direct_override=True):
                return display, "you can't run this one with your current access"
        return display, None

    def _sub_rows(self, cmd, prefix: str, subs: list[commands.Command]) -> list[str]:
        """Direct subcommands, then the deeper ones, each with one line saying what it does."""
        direct = [c for c in subs if getattr(c, "parent", None) is cmd]
        rows: list[tuple[str, list[tuple[str, str]]]] = [
            ("Subcommands", [(prefix + _short_name(c), blurb(c, 58)) for c in direct])]
        deeper = [c for c in subs if getattr(c, "parent", None) is not cmd]
        if deeper:
            rows.append(("Groups", [(prefix + _short_name(c), blurb(c, 58)) for c in deeper]))
        return sectioned(rows)

    def command_embed(self, cmd: commands.Command, page: tuple[int, int] | None = None, *,
                      visible_subcommands: list[commands.Command] | None = None,
                      access: HelpAccess | None = None) -> discord.Embed:
        """A command's card, in the Veil house style:

            > Ban a member, or anyone by user ID        <- the headline
            -# Add `--purge` to delete their messages. <- the docstring's detail, quiet

            -# ◇ Use
            ```bash
            ,ban <user> [reason]
            ,ban @user spamming
            ```

            -# ◇ Access
            Rank **Admin** · Needs Ban Members
            **Aliases** `r`

            **Subcommands**                            <- groups only
            `,role add` · Add a role to a member
        """
        p = self.context.clean_prefix
        bot = getattr(self.context, "bot", None)
        if cmd.qualified_name == "roleblacklist":
            return self.roleblacklist_embed(access=access)
        lines = self._about_lines(cmd)
        children = getattr(cmd, "commands", []) if isinstance(cmd, commands.Group) else []
        subs = sorted(visible_subcommands if visible_subcommands is not None else
                      (c for c in children if not c.hidden), key=lambda c: c.name)
        use = self._use_lines(cmd, subs if isinstance(cmd, commands.Group) else [])
        if use:
            lines += ["", section("Use", quiet=True), *use]
        rank, note = self._access_note(cmd, access)
        needs = [perm_label(x) for item in [cmd, *cmd.parents]
                 for x in perms_of(item) if not x.lower().startswith("uperm")]
        needs = list(dict.fromkeys(needs))
        pairs = [("Rank", f"**{rank}**" if rank else ""), ("Needs", " · ".join(needs))]
        summary = facts(pairs)
        aliases = alias_chips(cmd)
        if summary or aliases or note:
            lines += ["", section("Access", quiet=True)]
            if summary:
                lines.append(summary)
            if aliases:
                lines.append(f"**Aliases** {aliases}")
            if note:
                lines.append(bullet(note, mark="!"))
        if isinstance(cmd, commands.Group) and (page is None or page[0] == 1):
            shown = subs[:SUBCOMMANDS_SHOWN]
            rows = self._sub_rows(cmd, p, shown)
            if rows:
                lines += [""] + rows
                if len(subs) > len(shown):
                    lines.append(bullet(f"and {len(subs) - len(shown)} more · open each one with "
                                        f"{chip(f'{p}help {cmd.qualified_name}')}"))
        e = self._brand(discord.Embed(title=f"{p}{cmd.qualified_name}", color=HELP_COLOR,
                                     description=shorten_md("\n".join(lines), 4000)), section="Command reference")
        where = f"page {page[0]}/{page[1]} · " if page and page[1] > 1 else ""
        e.set_footer(text=f"{where}{category_for_command(cmd, bot)} · <required> [optional]")
        return e

    def roleblacklist_embed(self, access: HelpAccess | None = None) -> discord.Embed:
        """The role-blacklist control centre: three protection tiers, one glance each."""
        p = self.context.clean_prefix
        lines = ["> Keep sensitive roles out of the bot's hands and off your staff's.",
                 f"{NOTE} Three separate tiers, and a role can sit in more than one.",
                 "",
                 "**Ordinary blacklist** · refuses assignment and strips holders",
                 *script([f"{p}rbl list", f"{p}rbl add <role>", f"{p}rbl remove <role>",
                          f"{p}rbl check [member]", f"{p}rbl strip"]),
                 "",
                 section("Exceptions & authorized staff", quiet=True),
                 entry("rbl authorize <member or role>", "Let someone assign a blacklisted role", p),
                 entry("rbl authorized", "Who may do that today", p),
                 entry("rbl unallow <member> <role>", "Remove one exception", p),
                 bullet("individual exceptions never grant Antinuke trust"),
                 "",
                 "**Rate-limited bypass** · tempers the soft checks only",
                 entry("bypass <member or role>", "Exempt someone from those checks", p),
                 entry("bypass remove <member or role>", "Take the exemption back", p),
                 entry("bypass list", "See every exemption", p),
                 bullet("this never lifts the hard blacklist or Antinuke"),
                 "",
                 "**Hard blacklist** · permanent, developer-managed",
                 entry("hardbl add <role>", "Permanently off-limits", p),
                 entry("hardbl remove <role>", "Release a role", p),
                 entry("hardbl list", "The permanent list", p),
                 bullet("only bot developers can hand out a hard-blacklisted role")]
        e = self._brand(discord.Embed(title="Role blacklist", color=Colors.caution,
                                     description=shorten_md("\n".join(lines), 4000)), section="Control center")
        e.set_footer(text="Server owner & bot developers only · <required> [optional]")
        return e

    # ------------------------------------------------------------ category cards

    def security_overview(self, cmds: list[commands.Command]) -> discord.Embed | None:
        """Compact security command names; details and aliases stay in the group selector.

        Return None if the catalog no longer fits Discord's limits so the ordinary
        paginated reference can render it without truncating commands.
        """
        roots = [cmd for cmd in cmds if getattr(cmd, "parent", None) is None]
        if len(roots) > PER_PAGE or any(
                getattr(cmd, "parent", None) is not None and getattr(cmd, "root_parent", None) not in roots
                for cmd in cmds):
            return None  # use regular pages if roots cannot all be shown or a parent is missing
        prefix = self.context.clean_prefix
        lines = [f"> {CATEGORY_DESCRIPTIONS.get('Antinuke', 'Security tools')}", "", section("Commands")]
        for root in roots:
            name = root.qualified_name
            display = "an" if name == "antinuke" and "an" in getattr(root, "aliases", []) else name
            children = sorted((cmd.qualified_name[len(name) + 1:] for cmd in cmds
                               if getattr(cmd, "root_parent", None) is root), key=str.casefold)
            lines += ["", f"{NOTE} {SUB_SECTION} {chip(prefix + display)} · {blurb(root, 50)}"]
            if children:
                lines += chips_columns([f"{prefix}{display} {child}" for child in children])
        if not roots:
            lines.append(bullet("No commands in this category."))
        body = "\n".join(lines)
        if len(body) > 4000:
            return None
        e = self._brand(discord.Embed(color=HELP_COLOR, title="Antinuke commands",
                                     description=body), section="Security")
        e.set_footer(text="Select a group · <required> [optional]")
        return e

    def category_pages(self, label: str, cmds: list[commands.Command]) -> list[discord.Embed]:
        """A category as pages of up to PER_PAGE commands, a line each with what the command does."""
        if label == "Antinuke":
            overview = self.security_overview(cmds)
            if overview is not None:
                return [overview]
        chunks = [cmds[i:i + PER_PAGE] for i in range(0, len(cmds), PER_PAGE)] or [[]]
        pages = []
        for n, chunk in enumerate(chunks, 1):
            entries = []
            for c in chunk:
                entries.append(self.command_line(c))
                if getattr(c, "aliases", None):
                    entries.append(f"-# Aliases: {self.alias_line(c, MAX_ALIASES_SHOWN)}")
            # Title = <Category> commands, description = short intro + the commands, footer = paging and counts
            body = (f"> {CATEGORY_DESCRIPTIONS.get(label, 'Browse commands')}\n\n"
                    f"{section('Commands')}\n" + "\n".join(entries))
            if not chunk:
                body = f"> {CATEGORY_DESCRIPTIONS.get(label, 'Browse commands')}\n\n" + bullet("No commands here yet.")
            e = self._brand(discord.Embed(color=HELP_COLOR, title=f"{label} commands",
                                         description=shorten_md(body, 4000)), section="Command reference")
            e.set_footer(text=f"Page {n}/{len(chunks)} · {count(len(cmds), 'command')} · Select a command below")
            pages.append(e)
        return pages

    def category_embed(self, label: str, cmds: list[commands.Command], page: int = 0) -> discord.Embed:
        """One page of a category."""
        pages = self.category_pages(label, cmds)
        return pages[max(0, min(page, len(pages) - 1))]

    # ------------------------------------------------------------ who is asking
    async def is_owner_check(self) -> bool:
        from core import perms as _perms
        bot = getattr(self.context, "bot", None)
        author = getattr(self.context, "author", None)
        if not author:
            return False
        if _perms.is_app_operator(author, bot):
            return True
        if bot:
            try:
                # `is_owner` is broad (it includes protected people), so the Developer/Secret
                # categories are gated on the strict operator check: no protected person sees them.
                if await bot.is_owner(author) and not _perms.is_whitelisted_person(author):
                    return True
            except Exception:
                pass
        return False

    async def get_author_rank(self) -> int:
        from core import perms as _perms
        if await self.is_owner_check():
            return _perms.DEV_RANK
        guild = getattr(self.context, "guild", None)
        author = getattr(self.context, "author", None)
        if not author:
            return 0
        if guild and getattr(guild, "owner_id", None) == getattr(author, "id", None):
            return _perms.OWNER_RANK
        bot = getattr(self.context, "bot", None)
        if guild and bot and getattr(bot, "db", None):
            try:
                cfg = await bot.db.get_config(guild.id, "permlevels")
                return _perms.rank_of(cfg, author, is_owner=False, is_dev=False, has_admin=False)
            except Exception:
                return 0
        return 0

    async def help_access(self, rank: int) -> HelpAccess | None:
        """Mirror stable user permission inputs; runtime checks remain the authority for execution."""
        from core import perms as _perms

        guild = getattr(self.context, "guild", None)
        author = getattr(self.context, "author", None)
        bot = getattr(self.context, "bot", None)
        db = getattr(bot, "db", None)
        if not author or not db:
            return None
        native = discord.Permissions.none()
        channel = getattr(self.context, "channel", None)
        if channel and callable(getattr(channel, "permissions_for", None)):
            try:
                native = channel.permissions_for(author)
            except (AttributeError, TypeError):
                pass
        bypass_disabled = bool(guild and author.id == getattr(guild, "owner_id", None))
        if not guild:
            return HelpAccess({}, rank, set(), native, False, set(), bypass_disabled, False, False, True, set(), {})
        try:
            cfg = await db.get_config(guild.id, "permlevels") or {}
            fake = await db.get_config(guild.id, "fakeperms") or {}
            settings = await db.get_config(guild.id, "settings") or {}
            restrictions = await db.get_config(guild.id, "command_restrictions")
            # Antinuke is visible to its actual operators only: the server owner or a real developer.
            # Appointed admins are trusted but cannot configure it, and protected people hold the
            # developer rank without the operator surfaces, so neither reveals the category.
            appointed = author.id == getattr(guild, "owner_id", None) or _perms.is_app_operator(author, bot)
            granted = _perms.level_perms(cfg, author)
            fake_grants = {p for role in getattr(author, "roles", []) for p in fake.get(str(role.id), [])}
            from core.utils import expand_permission_aliases, native_permission_names
            direct = native_permission_names(self.context) | expand_permission_aliases(fake_grants)
            if "moderate_members" in fake_grants:
                direct.update(_perms.MODERATION_ACTIONS)
            granted = expand_permission_aliases(granted)
            granted.update(direct)
            disabled = set(settings.get("disabled", []))
            return HelpAccess(cfg, rank, granted, native, bool(cfg.get("users") or cfg.get("roles")),
                              disabled, bypass_disabled, author.id == getattr(guild, "owner_id", None), True, True,
                              {str(role.id) for role in getattr(author, "roles", [])}, restrictions,
                              appointed, direct)
        except Exception:
            # An unavailable permission store must not make protected commands appear usable.
            return HelpAccess({}, rank, set(), native, True, set(), bypass_disabled,
                              author.id == getattr(guild, "owner_id", None), True, False, set(), None)

    # ------------------------------------------------------------ entry points
    async def open_menu(self, start: str | None = None):
        categories = category_commands(self.context.bot)
        is_owner = await self.is_owner_check()
        rank = await self.get_author_rank()
        if (start or "").lower() in DEVELOPER_WORDS:
            start = ("Secret" if start.lower() in SECRET_WORDS else "Developer") if is_owner else None
        access = None if is_owner else await self.help_access(rank)
        if (start is not None and start not in category_labels(categories, is_owner, rank, access)
                and not (start in ("Developer", "Secret") and is_owner)):
            return await self.send_error_message(f'No command or category called "{start}" found.')
        view = HelpView(self, categories, start, is_owner=is_owner, author_rank=rank, access=access)
        view.message = await self.context.send(embed=view.embed(), view=view)

    async def command_callback(self, ctx, /, *, command: str | None = None):
        if command:
            wanted = command.lower().strip()
            wanted = CATEGORY_ALIASES.get(wanted, wanted) if not ctx.bot.get_command(wanted) else wanted
            is_owner = await self.is_owner_check()
            rank = await self.get_author_rank()
            for label, _, _ in CATEGORIES:
                if wanted == label.lower():
                    access = None if is_owner else await self.help_access(rank)
                    if label not in category_labels(category_commands(self.context.bot), is_owner, rank, access):
                        return await self.send_error_message(f'No command or category called "{command}" found.')
                    if not ctx.bot.get_command(wanted):
                        await self.prepare_help_command(ctx, command)
                        return await self.open_menu(label)
            if wanted in DEVELOPER_WORDS:
                if is_owner:
                    await self.prepare_help_command(ctx, command)
                    return await self.open_menu("Secret" if wanted in SECRET_WORDS else "Developer")
                return await self.send_error_message("No command or category called that")
        return await super().command_callback(ctx, command=command)

    async def send_bot_help(self, mapping):
        await self.open_menu(None)

    async def send_cog_help(self, cog):
        label = next((l for l, cogs, _ in CATEGORIES if cog.qualified_name in cogs), None)
        if cog.qualified_name in HIDDEN_COGS:
            return await self.send_error_message("No command or category called that")
        if label:
            is_owner = await self.is_owner_check()
            rank = await self.get_author_rank()
            access = None if is_owner else await self.help_access(rank)
            if label not in category_labels(category_commands(self.context.bot), is_owner, rank, access):
                return await self.send_error_message("No command or category called that")
        await self.open_menu(label)

    async def _can_see(self, cmd) -> bool:
        if is_rbl_command(cmd):
            from core.utils import owner_or_developer
            return owner_or_developer(getattr(self.context, "author", None),
                                      getattr(self.context, "guild", None), getattr(self.context, "bot", None))
        is_owner = await self.is_owner_check()
        rank = 0 if is_owner else await self.get_author_rank()
        access = None if is_owner else await self.help_access(rank)
        return visible_command(cmd, getattr(self.context, "bot", None), is_owner, rank, access)

    async def send_command_help(self, cmd):
        if not await self._can_see(cmd):
            return await self.send_error_message(f'No command called "{cmd.name}" found.')
        is_owner = await self.is_owner_check()
        rank = 0 if is_owner else await self.get_author_rank()
        access = None if is_owner else await self.help_access(rank)
        if is_rbl_command(cmd):
            self.context._private_rbl = True
        await self.context.send(embed=self.command_embed(cmd, access=access))

    async def send_group_help(self, group: commands.Group):
        """A group's card, then one card per subcommand this person can use, paged with ◀ ▶ (greed / bleed)."""
        if not await self._can_see(group):
            return await self.send_error_message(f'No command called "{group.name}" found.')
        is_owner = await self.is_owner_check()
        rank = 0 if is_owner else await self.get_author_rank()
        bot = getattr(self.context, "bot", None)
        access = None if is_owner else await self.help_access(rank)
        if is_rbl_command(group):
            self.context._private_rbl = True
        if group.qualified_name == "roleblacklist":
            return await self.context.send(embed=self.command_embed(group, access=access))
        subs = sorted(allowed(group.walk_commands(), bot, is_owner, rank, access), key=lambda c: c.qualified_name)
        cards = [group, *subs]
        pages = [self.command_embed(c, (i, len(cards)), visible_subcommands=(
            allowed(c.commands, bot, is_owner, rank, access) if isinstance(c, commands.Group) else None), access=access)
            for i, c in enumerate(cards, 1)]
        if len(pages) == 1:
            return await self.context.send(embed=pages[0])
        from core.utils import Paginator
        guild_id = self.context.guild.id if is_rbl_command(group) else None
        view = Paginator(self.context.author.id, pages, guild_id=guild_id)
        view.message = await self.context.send(embed=pages[0], view=view)

    # ------------------------------------------------------------ errors
    async def command_not_found(self, string: str) -> str:
        text = f'No command called "{string}" found.'
        near = await self._suggest(string)
        if near:
            text += " Did you mean " + ", ".join(f"`{n}`" for n in near) + "?"
        return text

    async def _suggest(self, typed: str) -> list[str]:
        """Close matches among the commands this person can see."""
        bot = getattr(self.context, "bot", None)
        if bot is None or not hasattr(bot, "walk_commands"):
            return []
        try:
            is_owner = await self.is_owner_check()
            rank = 0 if is_owner else await self.get_author_rank()
            access = None if is_owner else await self.help_access(rank)
            names: list[str] = []
            for cmd in bot.walk_commands():
                if not visible_command(cmd, bot, is_owner, rank, access):
                    continue
                names.append(cmd.qualified_name)
                if cmd.parent is None:
                    names += list(cmd.aliases)
            return difflib.get_close_matches(typed.lower(), sorted(set(names)), n=3, cutoff=0.6)
        except Exception:
            return []

    async def send_error_message(self, error):
        warn = getattr(self.context, "warn", None)
        if warn is not None:
            return await warn(str(error))
        await self.context.send(embed=discord.Embed(color=Colors.caution, description=f"⚠️ {error}"))
