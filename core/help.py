"""Public ,help: a plain home card with one button per category. A category is one page: each command with its
arguments and what it does, then its aliases, and a Back button. `,help <command>` has the full card. Developers get a
Developer button that opens the same kind of page for the developer commands.
"""
from __future__ import annotations

import difflib
import inspect
import re
import typing

import discord
from discord.ext import commands

from core.utils import Colors, OwnedView, respond_or_edit, shorten_md

HIDDEN_COGS = {"Owner", "Events", "Authorization", "DevTools"}

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
    ("Automod", ["Automod"], []),
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
    "Moderation": ["ban", "kick", "timeout", "untimeout", "warn", "purge", "lock", "unlock", "jail", "unjail",
                   "unban", "warnings", "cases"],
    "Music": ["play", "skip", "queue", "pause", "resume", "stop", "volume", "loop", "song"],
    "Utility": ["userinfo", "avatar", "serverinfo", "ping", "afk", "snipe", "remind", "poll"],
    "Antinuke": ["antinuke", "antiraid", "antialt"],
    "Developer": ["gitpull", "restart", "version", "reload", "errors", "eval", "system", "blacklist"],
}
DEVELOPER_WORDS = ("dev", "developer", "secret", "secrets", "tokens", "secretsuite")
MAX_ALIASES_SHOWN = 2  # per command on list pages; the full set is on the command's own card
MAX_ALIAS_LENGTH = 10
HELP_COLOR = Colors.neutral  # greed / bleed: quiet grey cards, the content does the talking

CATEGORY_MIN_RANKS: dict[str, int] = {
    "Utility": 0,       # Public
    "Music": 0,         # Public
    "VoiceMaster": 0,   # Public
    "Community": 0,     # Public
    "Fun": 0,           # Public
    "Images": 0,        # Public
    # each level up opens one more staff category (inside each one, commands you can't run stay hidden)
    "Moderation": 1,    # Helper+ (Moderator+ also sees kick, timeout, warn...)
    "Roles": 3,         # Admin+
    "Server": 4,        # Head Admin+
    "Automod": 5,       # Bot Manager+
    "Permissions": 6,   # Overseer+
    "Antinuke": 7,      # Supreme Leader+
}

# the level a Discord permission checked by a command maps to (matches has_perms' "you need ..." messages)
PERM_RANKS: dict[str, int] = {
    "administrator": 7,
    "manage_guild": 4, "manage_webhooks": 4, "manage_expressions": 4, "mention_everyone": 4,
    "manage_roles": 3, "ban_members": 3, "manage_channels": 3,
    "kick_members": 2, "moderate_members": 2, "manage_nicknames": 2,
    "manage_messages": 1,
}

CATEGORY_EMOJIS: dict[str, str] = {
    "Utility": "🛠️",
    "Music": "🎵",
    "Moderation": "🛡️",
    "Roles": "🏷️",
    "Permissions": "🔒",
    "Antinuke": "🚨",
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
    "Moderation": "Warns, mutes, kicks, bans, purge and logs",
    "Roles": "Role commands, autoroles, reaction roles, role blacklist",
    "Permissions": "Bot ranks (uperm), staff limits and command access",
    "Antinuke": "Real-time server protection and anti-raid",
    "Automod": "Spam, link and invite filtering",
    "Server": "Server setup, welcome messages and settings",
    "VoiceMaster": "Temporary voice channels and VC controls",
    "Community": "Levels, tickets, giveaways, feeds and lookups",
    "Fun": "Games, actions, jokes and AI",
    "Images": "Image effects and edits",
    "Developer": "Bot owner tools: updates, restarts, blacklist, eval",
}


# ---------------------------------------------------------------- command lookup and visibility

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
    for label, cogs, _ in CATEGORIES:
        if cog_name and cog_name in cogs:
            return label
    if getattr(cmd, "cog_name", None) in HIDDEN_COGS:
        return "Developer"
    return "Community"


def min_rank_for_command(cmd: commands.Command, bot: commands.Bot | None = None) -> int:
    """Return the minimum rank required to view help for this command."""
    if any(getattr(check, "__veil_owner_only__", False) for check in getattr(cmd, "checks", [])):
        from core.perms import OWNER_RANK
        return OWNER_RANK  # server owner only: nobody below sees it
    cat = category_for_command(cmd, bot)
    cat_rank = CATEGORY_MIN_RANKS.get(cat, 0)
    callback = getattr(cmd, "callback", cmd)
    perms_list = getattr(callback, "__veil_perms__", [])
    uperm_rank = 0
    for p in perms_list:
        if isinstance(p, str) and p in PERM_RANKS:  # has_perms(...): the level that grants that permission
            uperm_rank = max(uperm_rank, PERM_RANKS[p])
        if isinstance(p, str) and p.startswith("uperm:"):
            from core.perms import PRESETS, canonical
            lvl = canonical(p.split(":", 1)[1].strip().lower())
            if lvl in PRESETS:
                uperm_rank = max(uperm_rank, PRESETS[lvl][1])
    return max(cat_rank, uperm_rank)


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
        if cmds:
            out[label] = cmds
            placed.update(c.qualified_name for c in cmds)
    # anything new that isn't mapped still shows up in Community
    leftovers = [c for c in bot.commands if not c.hidden and c.qualified_name not in placed
                 and c.cog is not None and c.cog_name not in HIDDEN_COGS]
    if leftovers:
        out.setdefault("Community", []).extend(sorted(leftovers, key=lambda c: c.name))
    return out


def allowed(cmds: typing.Iterable[commands.Command], bot, is_owner: bool, rank: int) -> list[commands.Command]:
    """The commands this person is allowed to see in help."""
    return [c for c in cmds if is_owner or rank >= min_rank_for_command(c, bot)]


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
        return f"{prefix}{cmd.qualified_name} " + " ".join(_example_value(p) for p in params)
    except Exception:
        return ""


# ---------------------------------------------------------------- view components

def page_order(label: str, cmds: list[commands.Command]) -> list[commands.Command]:
    pinned = PINNED.get(label, [])
    rank = {name: i for i, name in enumerate(pinned)}
    return sorted(cmds, key=lambda c: (rank.get(c.name, len(pinned)), c.name))


def developer_commands(bot) -> list[commands.Command]:
    """The developer-only commands (bot owner tools), shown to developers as the Developer category."""
    cmds = []
    for cog_name in sorted(HIDDEN_COGS):
        cog = bot.get_cog(cog_name) if bot is not None and hasattr(bot, "get_cog") else None
        if cog:
            cmds += [c for c in cog.get_commands() if c.parent is None]
    return sorted({c.qualified_name: c for c in cmds}.values(), key=lambda c: c.name)


class CategorySelect(discord.ui.Select["HelpView"]):
    """The category dropdown (greed / bleed style): Home, then one entry per category this person may open."""

    def __init__(self, labels: list[str], sizes: dict[str, int], current: str | None):
        options = [discord.SelectOption(label="Home", value=HOME, description="back to the start",
                                        default=current is None)]
        options += [discord.SelectOption(label=label, value=label, default=label == current,
                                         description=count(sizes.get(label, 0), "command"))
                    for label in labels]
        super().__init__(placeholder="Choose a category…", options=options[:25], min_values=1, max_values=1)

    async def callback(self, it: discord.Interaction):
        choice = self.values[0]
        if choice == HOME:
            await self.view.show_home(it)
        else:
            await self.view.show_category(it, choice)


HOME = "__home__"


class HelpView(OwnedView):
    """Home card and one card per category, switched with a dropdown. Every card is built when the menu opens, so a
    pick only swaps the embed: one quick edit."""

    def __init__(self, help_cmd: Help, categories: dict[str, list[commands.Command]], start: str | None = None,
                 is_owner: bool = False, author_rank: int = 0):
        super().__init__(help_cmd.context.author.id, timeout=300)  # 5 minutes to read before the menu goes
        self.help_cmd, self.categories = help_cmd, categories
        self.is_owner = is_owner
        self.author_rank = author_rank
        self.message: discord.Message | None = None
        self.pages: dict[str, discord.Embed] = {}
        self.sizes: dict[str, int] = {}
        for label in self.labels():
            cmds = page_order(label, self.commands_in(label))
            self.sizes[label] = len(cmds)
            self.pages[label] = help_cmd.category_embed(label, cmds)
        self.home = help_cmd.home_embed(author_rank, categories, is_owner, total=sum(self.sizes.values()),
                                        sections=len(self.pages))
        self.current = start if start in self.pages else None
        self._build_items()

    @property
    def bot(self):
        return getattr(getattr(self.help_cmd, "context", None), "bot", None)

    def labels(self) -> list[str]:
        names = category_labels(self.categories, self.is_owner, self.author_rank)
        return (names + (["Developer"] if self.is_owner else []))[:24]  # + Home = Discord's 25 options

    def commands_in(self, label: str | None) -> list[commands.Command]:
        if label == "Developer":
            return developer_commands(self.bot) if self.is_owner else []
        return allowed(self.categories.get(label, []), self.bot, self.is_owner, self.author_rank)

    def embed(self) -> discord.Embed:
        return self.home if self.current is None else self.pages[self.current]

    def _build_items(self):
        self.clear_items()
        self.add_item(CategorySelect(list(self.pages), self.sizes, self.current))

    async def show_category(self, it: discord.Interaction, label: str):
        if label not in self.pages:  # not theirs to open (a stale menu, or a rank that changed)
            return await it.response.send_message("🔒 You can't open that category.", ephemeral=True)
        self.current = label
        self._build_items()
        await respond_or_edit(it, self.embed(), self)

    async def show_home(self, it: discord.Interaction):
        self.current = None
        self._build_items()
        await respond_or_edit(it, self.home, self)

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.delete()
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
        """The start card: what you can use here and how to look things up; the dropdown does the rest."""
        p = self.context.clean_prefix
        lines = ["**information**"]
        if total is not None:
            lines.append(f"> **{total}** commands you can use"
                         + (f" in **{sections}** categories" if sections else ""))
        lines += [f"> prefix: `{p}`", f"> `{p}help <command>` for a command's syntax and an example", "",
                  "choose a category from the menu below."]
        e = self._brand(discord.Embed(color=HELP_COLOR, description="\n".join(lines)))
        bot = getattr(self.context, "bot", None)
        if bot and getattr(bot, "user", None):
            e.set_thumbnail(url=bot.user.display_avatar.url)
        return e

    def category_embed(self, label: str, cmds: list[commands.Command], page: int = 0) -> discord.Embed:
        """A category: every command name in one code block (groups marked with *), like greed / bleed."""
        p = self.context.clean_prefix
        names = [f"{c.name}*" if isinstance(c, commands.Group) else c.name for c in cmds]
        body = shorten_md(", ".join(names), 3900) if names else "no commands in this category"
        e = self._brand(discord.Embed(color=HELP_COLOR, title=label, description=f"```\n{body}\n```"))
        groups = any(isinstance(c, commands.Group) for c in cmds)
        e.set_footer(text=f"{count(len(cmds), 'command')}" + (" · * has subcommands" if groups else "")
                     + f" · {p}help <command> for more")
        return e

    def command_embed(self, cmd: commands.Command, page: tuple[int, int] | None = None) -> discord.Embed:
        """A command card, greed / bleed style: what it does, Aliases / Parameters / Information side by side,
        then a Usage block with the syntax and an example. The footer names the module (and page, for groups)."""
        p = self.context.clean_prefix
        bot = getattr(self.context, "bot", None)
        kind = "Group" if isinstance(cmd, commands.Group) else "Command"
        e = self._brand(discord.Embed(title=f"{kind}: {cmd.qualified_name}", color=HELP_COLOR,
                                      description=cmd.help or cmd.short_doc or "no description yet"))

        aliases = ", ".join(getattr(cmd, "aliases", None) or []) or "n/a"
        own_usage = getattr(cmd, "usage", None)
        if isinstance(own_usage, str) and own_usage.strip():  # commands that read their own arguments say so here
            params = list(dict.fromkeys(re.findall(r"[<\[]([^<>\[\]|]+)", own_usage)))
        else:
            params = [_arg_name(n, prm) for n, prm in (getattr(cmd, "clean_params", None) or {}).items()
                      if n not in ("ctx", "self")]
        needs = []
        rank = min_rank_for_command(cmd, bot)
        if rank > 0:
            needs.append(f"{rank_name(rank)}+")
        needs += [x.replace("_", " ").title() for x in perms_of(cmd) if not x.lower().startswith("uperm")]
        e.add_field(name="Aliases", value=shorten_md(aliases, 1024), inline=True)
        e.add_field(name="Parameters", value=", ".join(params) or "n/a", inline=True)
        e.add_field(name="Information", value=("⚠️ " + ", ".join(needs)) if needs else "n/a", inline=True)

        block = f"Syntax: {usage(p, cmd)}"
        example = example_for(cmd, p)
        if example:
            block += f"\nExample: {example}"
        e.add_field(name="Usage", value=f"```\n{block}\n```", inline=False)

        module = category_for_command(cmd, bot).lower()
        where = f"Page {page[0]}/{page[1]} · " if page and page[1] > 1 else ""
        e.set_footer(text=f"{where}Module: {module} · <required> [optional]")
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

    # ------------------------------------------------------------ entry points
    async def open_menu(self, start: str | None = None):
        categories = category_commands(self.context.bot)
        is_owner = await self.is_owner_check()
        rank = await self.get_author_rank()
        if (start or "").lower() in DEVELOPER_WORDS:
            start = "Developer" if is_owner else None
        if start is not None and not is_owner and rank < CATEGORY_MIN_RANKS.get(start, 0):
            return await self.send_error_message(f'No command or category called "{start}" found.')
        view = HelpView(self, categories, start, is_owner=is_owner, author_rank=rank)
        view.message = await self.context.send(embed=view.embed(), view=view)

    async def command_callback(self, ctx, /, *, command: str | None = None):
        if command:
            wanted = command.lower().strip()
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
        is_owner = await self.is_owner_check()
        if (cmd.hidden or any(p.hidden for p in cmd.parents)) and not is_owner:
            return False
        if is_owner:
            return True
        return await self.get_author_rank() >= min_rank_for_command(cmd, getattr(self.context, "bot", None))

    async def send_command_help(self, cmd):
        if not await self._can_see(cmd):
            return await self.send_error_message(f'No command called "{cmd.name}" found.')
        await self.context.send(embed=self.command_embed(cmd))

    async def send_group_help(self, group: commands.Group):
        """A group's card, then one card per subcommand this person can use, paged with ◀ ▶ (greed / bleed)."""
        if not await self._can_see(group):
            return await self.send_error_message(f'No command called "{group.name}" found.')
        is_owner = await self.is_owner_check()
        rank = 0 if is_owner else await self.get_author_rank()
        bot = getattr(self.context, "bot", None)
        subs = sorted((c for c in group.walk_commands() if not c.hidden), key=lambda c: c.qualified_name)
        subs = allowed(subs, bot, is_owner, rank)
        cards = [group, *subs]
        pages = [self.command_embed(c, (i, len(cards))) for i, c in enumerate(cards, 1)]
        if len(pages) == 1:
            return await self.context.send(embed=pages[0])
        from core.utils import Paginator
        view = Paginator(self.context.author.id, pages)
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
            names: list[str] = []
            for cmd in bot.walk_commands():
                if cmd.hidden and not is_owner:
                    continue
                if not is_owner and rank < min_rank_for_command(cmd, bot):
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
