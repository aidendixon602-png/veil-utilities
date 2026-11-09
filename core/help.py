"""Prefix help with a Veil-branded home, category and command navigation, and permission-aware details."""
from __future__ import annotations

from dataclasses import dataclass
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

# (button label, cogs included, extra top-level commands pulled in from other cogs)
CATEGORIES: list[tuple[str, list[str], list[str]]] = [
    ("Utility", ["Utility"], []),
    ("Music", ["Music", "Spotify"], []),
    ("Moderation", ["Moderation", "Logging"], []),
    ("Roles", [], ["role", "autorole", "reactionrole", "buttonrole", "vanity", "stripstaff", "roles",
                   "roleblacklist", "addpermall", "removepermall"]),
    ("Permissions", ["Permissions"], ["fakepermissions", "staffonly", "botchannels", "ignore",
                                      "disablecommand", "enablecommand", "prefix", "modlog"]),
    ("Antinuke", ["Antinuke"], []),
    ("Server", ["Config", "Settings"], []),
    ("VoiceMaster", ["VoiceMaster"], []),
    ("Community", ["Levels", "Giveaways", "Tickets", "Feeds", "Integrations"], []),
    ("Fun", ["Fun", "AI"], []),
    ("Images", ["Imaging"], []),
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
# Legacy names still open the unified moderation category.
CATEGORY_ALIASES = {"gv": "ghost vanguard", "vanguard": "ghost vanguard", "ghost": "ghost vanguard",
                    "moderator": "moderation", "moderators": "moderation", "mods": "moderation",
                    "admin": "moderation", "admins": "moderation", "administration": "moderation", "administrator": "moderation"}
MAX_ALIASES_SHOWN = 2  # per command on list pages; the full set is on the command's own card
MAX_ALIAS_LENGTH = 10
PER_PAGE = 10  # commands per category page
SUBCOMMANDS_PER_PAGE = 15  # fits both the embed and a Discord select
HELP_COLOR = 0xB10F2E  # Veil accent

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
    "Server": 4,        # Server Manager+
    "Automod": 4,       # Server Manager+
    "Permissions": 6,   # Co-Owner+
    "Antinuke": 7,      # Owner+
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
    "Ghost Vanguard": "👻",
    "Automod": "🤖",
    "Server": "⚙️",
    "VoiceMaster": "🎙️",
    "Community": "✨",
    "Fun": "🎲",
    "Images": "🖼️",
    "Developer": "⚡",
}

# One short line per category: shown under the list title.
CATEGORY_DESCRIPTIONS: dict[str, str] = {
    "Utility": "Lookups, AFK, snipe and everyday tools",
    "Music": "Lavalink and Spotify playback, queues, filters",
    "Moderation": "Moderation and administration tools available to your permission level",
    "Roles": "Role management tools for your permission level",
    "Permissions": "Permission and access settings for your level",
    "Antinuke": "Protection settings available to your permission level",
    "Ghost Vanguard": "Optional bridge to Ghost Vanguard commands",
    "Automod": "Message protection settings for your permission level",
    "Server": "Server configuration tools for your permission level",
    "VoiceMaster": "Temporary voice channels and VC controls",
    "Community": "Levels, tickets, giveaways, feeds and lookups",
    "Fun": "Games, actions, jokes and AI",
    "Images": "Image effects and edits",
    "Developer": "Bot owner tools: updates, restarts, blacklist, eval",
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
    return next((name for name, r, _ in PRESETS.values() if r == rank), f"Rank {rank}")


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
        cmds = sorted({c.qualified_name: c for c in cmds if c and not c.hidden}.values(), key=lambda c: c.name)
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
    # anything new that isn't mapped still shows up in Community
    leftovers = [c for c in bot.commands if not c.hidden and c.qualified_name not in placed
                 and c.cog is not None and c.cog_name not in HIDDEN_COGS | HELP_EXCLUDED_COGS]
    if leftovers:
        out.setdefault("Community", []).extend(sorted(leftovers, key=lambda c: c.name))
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

    def permits(self, cmd: commands.Command) -> bool:
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
            if self.rank < (target["rank"] if target else 1):
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
    for c in chain:
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
            elif rank < command_category_gate(c, bot):
                return False
            if not access.permits(c):
                return False
    return True


def allowed(cmds: typing.Iterable[commands.Command], bot, is_owner: bool, rank: int,
            access: HelpAccess | None = None) -> list[commands.Command]:
    """The commands this person may see in help, including inherited restrictions."""
    return [c for c in cmds if visible_command(c, bot, is_owner, rank, access)]


def category_labels(categories: dict[str, list], is_owner: bool, rank: int) -> list[str]:
    """Category names this person can open, in menu order."""
    return [label for label, _, _ in CATEGORIES
            if label in categories and (is_owner or rank >= CATEGORY_MIN_RANKS.get(label, 0))]


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
    """The developer-only commands (bot owner tools), shown to developers as the Developer category."""
    cmds = []
    for cog_name in sorted(HIDDEN_COGS):
        cog = bot.get_cog(cog_name) if bot is not None and hasattr(bot, "get_cog") else None
        if cog:
            cmds += [c for c in cog.get_commands() if c.parent is None]
    return sorted({c.qualified_name: c for c in cmds}.values(), key=lambda c: c.name)


HOME = "__home__"


class CategorySelect(discord.ui.Select["HelpView"]):
    """Jump to any category available in this menu."""

    def __init__(self, labels: list[str], sizes: dict[str, int], current: str | None):
        options = [discord.SelectOption(label=label, value=label, default=label == current,
                                        emoji=CATEGORY_EMOJIS.get(label),
                                        description=count(sizes.get(label, 0), "command")) for label in labels]
        super().__init__(placeholder="Choose a category", options=options[:25], min_values=1, max_values=1, row=0)

    async def callback(self, it: discord.Interaction):
        choice = self.values[0]
        if choice == HOME:
            await self.view.show_home(it)
        else:
            await self.view.show_category(it, choice)


class CategoryButton(discord.ui.Button["HelpView"]):
    """Open a visible category from the help home screen."""

    def __init__(self, category: str, row: int):
        super().__init__(label=category[:80], style=discord.ButtonStyle.secondary, row=row)
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
            self.items[label] = cmds
            self.sizes[label] = len(cmds)
            self.books[label] = help_cmd.category_pages(label, cmds)
            self.pages[label] = self.books[label][0]
        self.home = help_cmd.home_embed(author_rank, categories, is_owner, total=sum(self.sizes.values()),
                                        sections=len(self.pages))
        self.current = start if start in self.pages else None
        self._build_items()

    async def interaction_check(self, it: discord.Interaction) -> bool:
        if not await super().interaction_check(it):
            return False
        from core.utils import owner_or_developer
        guild = getattr(it, "guild", None)
        original_guild = getattr(self.help_cmd.context, "guild", None)
        if self.access and self.access.bypass_permissions and original_guild:
            if (not guild or guild.id != original_guild.id
                    or not owner_or_developer(it.user, guild, self.bot)):
                await it.response.send_message("This menu is no longer available.", ephemeral=True)
                return False
        return True

    @property
    def bot(self):
        return getattr(getattr(self.help_cmd, "context", None), "bot", None)

    def labels(self) -> list[str]:
        names = category_labels(self.categories, self.is_owner, self.author_rank)
        return (names + (["Developer"] if self.is_owner else []))[:24]

    def commands_in(self, label: str | None) -> list[commands.Command]:
        if label == "Developer":
            return developer_commands(self.bot) if self.is_owner else []
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
            if len(self.pages) > 15:
                # Discord allows five button rows; use the existing select for a larger catalog.
                self.add_item(CategorySelect(list(self.pages), self.sizes, self.current))
            else:
                for index, label in enumerate(self.pages):
                    self.add_item(CategoryButton(label, row=index // 3))
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


# ---------------------------------------------------------------- the help command

class Help(commands.HelpCommand):
    def __init__(self):
        super().__init__(command_attrs={"aliases": ["h", "commands"], "help": "Show help for a command or category"})

    # ------------------------------------------------------------ embeds
    def _brand(self, e: discord.Embed) -> discord.Embed:
        bot = getattr(self.context, "bot", None)
        if bot and getattr(bot, "user", None):
            e.set_author(name=bot.user.name, icon_url=bot.user.display_avatar.url)
        return e

    def home_embed(self, author_rank: int = 0, categories: dict[str, list] | None = None,
                   is_owner: bool = False, *, total: int | None = None, sections: int | None = None) -> discord.Embed:
        """Overview of the caller's visible help entries and how to browse them."""
        p = self.context.clean_prefix
        lines = ["Select a category below to view its commands.", "", f"**Prefix:** `{p}`"]
        return self._brand(discord.Embed(color=Colors.neutral, description="\n".join(lines)))

    def command_line(self, cmd: commands.Command) -> str:
        """One command on a category page, with a marker for groups."""
        p = self.context.clean_prefix
        more = " · *group*" if isinstance(cmd, commands.Group) else ""
        name = cmd.qualified_name
        if name.startswith("antinuke "):
            name = "an " + name[len("antinuke "):]
        return f"`{p}{name}` — {blurb(cmd, 60)}{more}"

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

    def category_pages(self, label: str, cmds: list[commands.Command]) -> list[discord.Embed]:
        """A category as pages of up to PER_PAGE commands, a line each with what the command does."""
        chunks = [cmds[i:i + PER_PAGE] for i in range(0, len(cmds), PER_PAGE)] or [[]]
        pages = []
        for n, chunk in enumerate(chunks, 1):
            entries = "\n".join(self.command_line(c) + (
                "\n-# Aliases: " + self.alias_line(c, None if label == "Antinuke" else MAX_ALIASES_SHOWN)
                if getattr(c, "aliases", None) else "") for c in chunk) or "No commands in this category."
            intro = CATEGORY_DESCRIPTIONS.get(label, "Browse commands")
            body = f"{intro}\n\n**Commands**\n{entries}"
            e = self._brand(discord.Embed(color=HELP_COLOR, title=f"{label} commands",
                                          description=shorten_md(body, 4000)))
            e.set_footer(text=f"Page {n}/{len(chunks)} · {count(len(cmds), 'command')} · Select a command below")
            pages.append(e)
        return pages

    def category_embed(self, label: str, cmds: list[commands.Command], page: int = 0) -> discord.Embed:
        """One page of a category."""
        pages = self.category_pages(label, cmds)
        return pages[max(0, min(page, len(pages) - 1))]

    def command_embed(self, cmd: commands.Command, page: tuple[int, int] | None = None, *,
                      visible_subcommands: list[commands.Command] | None = None,
                      access: HelpAccess | None = None) -> discord.Embed:
        """A command's card: what it does, then Usage, Example, Aliases and what it needs, one line each. Nothing
        is shown that doesn't apply (no "n/a"), and default values stay out of the syntax. A group's card lists its
        subcommands."""
        p = self.context.clean_prefix
        bot = getattr(self.context, "bot", None)
        if cmd.qualified_name == "roleblacklist":
            e = self._brand(discord.Embed(title=f"{p}rbl · Role blacklist management", color=HELP_COLOR,
                description=(
                    f"**Usage** `{p}rbl`\n"
                    "Only the server owner and bot developers can manage these settings.\n\n"
                    f"**Ordinary blacklist**\n`{p}rbl add <role>` · `{p}rbl remove <role>` · `{p}rbl list`\n"
                    f"`{p}rbl check [member]` · `{p}rbl strip`\n"
                    f"`{p}rbl authorize <member or role>` · `{p}rbl unauthorize <member or role>`\n"
                    f"`{p}rbl authorized`\n\n"
                    f"**Soft bypass**\n`{p}bypass add <member or role>` · `{p}bypass remove <member or role>` · `{p}bypass list`\n"
                    "Soft bypass does not bypass hard blacklist or antinuke.\n\n"
                    f"**Hard blacklist**\n`{p}hardbl add <role>` · `{p}hardbl remove <role>` · `{p}hardbl list`\n"
                    "Only bot developers can assign hard-blacklisted roles. Discord-side enforcement is limited."
                )))
            e.set_footer(text="Use help with an individual command for details")
            return e
        about = (cmd.help or cmd.short_doc or "").strip() or "No description yet."
        # an "Example: `...`" line in the description is the command's own example: it goes in the Example slot
        written = [ln for ln in about.splitlines() if ln.startswith("Example:")]
        about = "\n".join(ln for ln in about.splitlines() if not ln.startswith("Example:")).strip()
        about = about or "No description yet."
        lines = ["**About**", about[0].upper() + about[1:], ""]
        lines.append(f"**Usage** `{p}{cmd.qualified_name}{(' ' + short_args(cmd)) if short_args(cmd) else ''}`")
        example = written[0].split(":", 1)[1].strip() if written else (f"`{example_for(cmd, p)}`" if example_for(cmd, p) else "")
        if example and example != f"`{p}{cmd.qualified_name}`":
            lines.append(f"**Example** {example}")
        aliases = [a for a in (getattr(cmd, "aliases", None) or [])]
        if aliases:
            lines.append("**Aliases** " + " ".join(f"`{a}`" for a in aliases[:8]))
        needs = []
        chain = [cmd, *cmd.parents]
        rank = max(min_rank_for_command(item, bot) for item in chain)
        display_rank = rank_name(rank) if rank > 0 else None
        if access is not None:
            from core import perms as _perms
            levels = _perms.all_levels(access.levels)
            for item in chain:
                for requirement in getattr(item.callback, "__veil_perms__", []):
                    if isinstance(requirement, str) and requirement.startswith("uperm:"):
                        key = _perms.canonical(requirement.split(":", 1)[1].strip().lower())
                        target = levels.get(key)
                        if target and target["rank"] > rank:
                            rank, display_rank = target["rank"], target["name"]
        if display_rank:
            needs.append(f"{display_rank} level")
        needs += [perm_label(x) for item in chain for x in perms_of(item) if not x.lower().startswith("uperm")]
        if needs:
            lines.append("**Requires** " + ", ".join(dict.fromkeys(needs)))
        if isinstance(cmd, commands.Group) and (page is None or page[0] == 1):
            subs = sorted(visible_subcommands if visible_subcommands is not None else
                          (c for c in cmd.commands if not c.hidden), key=lambda c: c.name)
            if subs:
                lines += ["", "**Subcommands**"]
                lines += [f"`{p}{c.qualified_name}` · {blurb(c, 60)}" for c in subs[:15]]
                if len(subs) > 15:
                    lines.append(f"-# and {len(subs) - 15} more · use `{p}help {cmd.qualified_name}` to browse")
        e = self._brand(discord.Embed(title=f"{p}{cmd.qualified_name}", color=HELP_COLOR,
                                      description=shorten_md("\n".join(lines), 4000)))
        where = f"page {page[0]}/{page[1]} · " if page and page[1] > 1 else ""
        e.set_footer(text=f"{where}{category_for_command(cmd, bot)} · <required> [optional]")
        return e

    # ------------------------------------------------------------ who is asking
    async def is_owner_check(self) -> bool:
        from core import perms as _perms
        bot = getattr(self.context, "bot", None)
        author = getattr(self.context, "author", None)
        if not author:
            return False
        if _perms.is_bot_developer(author, bot):
            return True
        if bot:
            try:
                if await bot.is_owner(author):
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
            granted = _perms.level_perms(cfg, author)
            fake_grants = {p for role in getattr(author, "roles", []) for p in fake.get(str(role.id), [])}
            granted.update(fake_grants)
            if "moderate_members" in fake_grants:
                granted.update(_perms.MODERATION_ACTIONS)
            disabled = set(settings.get("disabled", []))
            return HelpAccess(cfg, rank, granted, native, bool(cfg.get("users") or cfg.get("roles")),
                              disabled, bypass_disabled, author.id == getattr(guild, "owner_id", None), True, True,
                              {str(role.id) for role in getattr(author, "roles", [])}, restrictions)
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
            start = "Developer" if is_owner else None
        if start is not None and not is_owner and rank < CATEGORY_MIN_RANKS.get(start, 0):
            return await self.send_error_message(f'No command or category called "{start}" found.')
        access = None if is_owner else await self.help_access(rank)
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
                    if not is_owner and rank < CATEGORY_MIN_RANKS.get(label, 0):
                        return await self.send_error_message(f'No command or category called "{command}" found.')
                    if not ctx.bot.get_command(wanted):
                        await self.prepare_help_command(ctx, command)
                        return await self.open_menu(label)
            if wanted in DEVELOPER_WORDS:
                if is_owner:
                    await self.prepare_help_command(ctx, command)
                    return await self.open_menu("Developer")
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
            if not is_owner and await self.get_author_rank() < CATEGORY_MIN_RANKS.get(label, 0):
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
