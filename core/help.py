"""Public ,help: a plain home card with one button per category, numbered paged lists and one card per command.

The developer-only menus (Developer overview, Secret Suite) live in core/devmenu.py.
"""
from __future__ import annotations

import difflib
import inspect
import math
import re
import typing

import discord
from discord.ext import commands

from core.devmenu import (  # noqa: F401  (re-exported: other modules and tests import these from core.help)
    SECRET_CATEGORIES,
    DeveloperView,
    SecretHelpView,
    developer_embed,
    diagnostics_embed,
    logs_embed,
)
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
    ("Community", ["Levels", "Giveaways", "Tickets", "Feeds", "Integrations", "Fun", "Imaging", "AI"], []),
]

PAGE_SIZE = 10
MAX_ALIASES_SHOWN = 2  # per command on list pages; the full set is on the command's own card
HELP_COLOR = Colors.blurple

CATEGORY_MIN_RANKS: dict[str, int] = {
    "Utility": 0,       # Public
    "Music": 0,         # Public
    "VoiceMaster": 0,   # Public
    "Community": 0,     # Public
    "Moderation": 1,    # Helper+ (rank 1)
    "Roles": 3,         # Admin+ (rank 3)
    "Automod": 3,       # Admin+ (rank 3)
    "Server": 3,        # Admin+ (rank 3)
    "Antinuke": 4,      # Head Admin+ (rank 4)
    "Permissions": 4,   # Head Admin+ (rank 4)
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
    "Developer": "⚡",
    "Secret": "🔑",
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
    "Community": "Levels, tickets, giveaways, feeds, AI and fun",
    "Developer": "Bot telemetry and diagnostics",
    "Secret": "Developer command suite",
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
    cat = category_for_command(cmd, bot)
    cat_rank = CATEGORY_MIN_RANKS.get(cat, 0)
    callback = getattr(cmd, "callback", cmd)
    perms_list = getattr(callback, "__veil_perms__", [])
    uperm_rank = 0
    for p in perms_list:
        if isinstance(p, str) and p.startswith("uperm:"):
            lvl = p.split(":", 1)[1].strip().lower()
            from core.perms import PRESETS
            if lvl in PRESETS:
                uperm_rank = max(uperm_rank, PRESETS[lvl][1])
    return max(cat_rank, uperm_rank)


def rank_name(rank: int) -> str:
    from core.perms import PRESETS
    return next((name for name, r, _ in PRESETS.values() if r == rank), f"Rank {rank}")


def perms_of(cmd: commands.Command) -> list[str]:
    perms = list(getattr(cmd.callback, "__veil_perms__", []))
    for check in cmd.checks:
        for cell in getattr(check, "__closure__", None) or ():
            val = cell.cell_contents
            if isinstance(val, dict) and all(isinstance(v, bool) for v in val.values()):
                perms.extend(val)
    return sorted(set(perms))


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


def blurb(cmd, limit: int = 70) -> str:
    """One-line description for list pages."""
    text = ((getattr(cmd, "short_doc", None) or "").strip().splitlines() or [""])[0].strip().rstrip(".")
    if not text:
        return "No description yet"
    return shorten_md(text[0].upper() + text[1:], limit)


def sub_count(cmd) -> int:
    if not isinstance(cmd, commands.Group):
        return 0
    return sum(1 for c in cmd.walk_commands() if not c.hidden)


def alias_hint(cmd) -> str:
    aliases = list(getattr(cmd, "aliases", None) or [])
    if not aliases:
        return ""
    shown = ", ".join(aliases[:MAX_ALIASES_SHOWN])
    extra = len(aliases) - MAX_ALIASES_SHOWN
    return f" *({shown}{f' +{extra}' if extra > 0 else ''})*"


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

class CategoryButton(discord.ui.Button["HelpView"]):
    """One plain button per category; the open one is highlighted and Secret stays red."""

    def __init__(self, label: str, row: int, selected: bool = False):
        style = (discord.ButtonStyle.primary if selected
                 else discord.ButtonStyle.danger if label == "Secret" else discord.ButtonStyle.secondary)
        super().__init__(label=label, style=style, row=row)
        self.category = label

    async def callback(self, it: discord.Interaction):
        await self.view.show_category(it, self.category)


class PrevButton(discord.ui.Button["HelpView"]):
    def __init__(self, disabled: bool, row: int = 3):
        super().__init__(emoji="◀", style=discord.ButtonStyle.secondary, disabled=disabled, row=row)

    async def callback(self, it: discord.Interaction):
        await self.view.change_page(it, -1)


class PageIndicator(discord.ui.Button["HelpView"]):
    def __init__(self, page: int, pages: int, row: int = 3):
        super().__init__(label=f"{page + 1}/{pages}", style=discord.ButtonStyle.secondary, disabled=True, row=row)


class NextButton(discord.ui.Button["HelpView"]):
    def __init__(self, disabled: bool, row: int = 3):
        super().__init__(emoji="▶", style=discord.ButtonStyle.secondary, disabled=disabled, row=row)

    async def callback(self, it: discord.Interaction):
        await self.view.change_page(it, 1)


class HomeButton(discord.ui.Button["HelpView"]):
    def __init__(self, row: int = 3):
        super().__init__(label="Home", emoji="🏠", style=discord.ButtonStyle.secondary, row=row)

    async def callback(self, it: discord.Interaction):
        await self.view.show_home(it)


class CloseButton(discord.ui.Button["HelpView"]):
    def __init__(self, row: int = 3):
        super().__init__(emoji="🗑️", style=discord.ButtonStyle.secondary, row=row)

    async def callback(self, it: discord.Interaction):
        self.view.stop()
        await it.response.defer()
        try:
            await it.delete_original_response()
        except discord.HTTPException:
            pass


BUTTONS_PER_ROW = 5
MAX_CATEGORY_ROWS = 4  # the last row is kept for the pager / Home / close


class HelpView(OwnedView):
    """Home card with a button per category; a pager appears on lists longer than a page."""

    def __init__(self, help_cmd: Help, categories: dict[str, list[commands.Command]], start: str | None = None,
                 is_owner: bool = False, author_rank: int = 0):
        super().__init__(help_cmd.context.author.id, timeout=180)
        self.help_cmd, self.categories = help_cmd, categories
        self.current = start
        self.is_owner = is_owner
        self.author_rank = author_rank
        self.page = 0
        self.message: discord.Message | None = None
        self._build_items()

    # -- what this person can see
    @property
    def bot(self):
        return getattr(getattr(self.help_cmd, "context", None), "bot", None)

    def labels(self) -> list[str]:
        return category_labels(self.categories, self.is_owner, self.author_rank)

    def commands_in(self, label: str | None) -> list[commands.Command]:
        return allowed(self.categories.get(label, []), self.bot, self.is_owner, self.author_rank)

    def pages(self) -> int:
        return max(1, math.ceil(len(self.commands_in(self.current)) / PAGE_SIZE))

    # -- layout
    def _category_names(self) -> list[str]:
        names = self.labels()
        if self.is_owner:
            names += ["Developer", "Secret"]
        return names[:BUTTONS_PER_ROW * MAX_CATEGORY_ROWS]

    def _build_items(self):
        self.clear_items()
        names = self._category_names()
        for i, label in enumerate(names):
            self.add_item(CategoryButton(label, i // BUTTONS_PER_ROW, selected=(label == self.current)))
        if self.current is None:
            return  # the home card is just the category buttons
        row = min(max(1, math.ceil(len(names) / BUTTONS_PER_ROW)), MAX_CATEGORY_ROWS)
        pages = self.pages()
        if pages > 1:
            self.add_item(PrevButton(self.page <= 0, row))
            self.add_item(PageIndicator(self.page, pages, row))
            self.add_item(NextButton(self.page >= pages - 1, row))
        self.add_item(HomeButton(row))
        self.add_item(CloseButton(row))

    # -- navigation
    async def show_home(self, it: discord.Interaction):
        self.current, self.page = None, 0
        self._build_items()
        await respond_or_edit(it, self.help_cmd.home_embed(self.author_rank, self.categories, self.is_owner), self)

    async def show_category(self, it: discord.Interaction, label: str):
        if label in ("Developer", "Secret"):
            if not self.is_owner:
                return await it.response.send_message("🔒 This category is restricted to bot developers.", ephemeral=True)
            self.current, self.page = label, 0
            if label == "Developer":
                view = DeveloperView(self.help_cmd, it.user.id)
                embed = await self.help_cmd.developer_embed()
            else:
                view = SecretHelpView(self.help_cmd.context, help_cmd=self.help_cmd, initial_category="tokens")
                embed = view.get_embed()
            return await respond_or_edit(it, embed, view)

        if not self.is_owner and self.author_rank < CATEGORY_MIN_RANKS.get(label, 0):
            return await it.response.send_message("🔒 You do not have permission to view this category.", ephemeral=True)

        self.current, self.page = label, 0
        self._build_items()
        await respond_or_edit(it, self.help_cmd.category_embed(label, self.commands_in(label), 0), self)

    async def change_page(self, it: discord.Interaction, delta: int):
        self.page = max(0, min(self.page + delta, self.pages() - 1))
        self._build_items()
        await respond_or_edit(it, self.help_cmd.category_embed(self.current, self.commands_in(self.current), self.page), self)

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
    def home_embed(self, author_rank: int = 0, categories: dict[str, list] | None = None,
                   is_owner: bool = False) -> discord.Embed:
        """The plain home card. (The arguments are kept so callers don't change; the buttons carry the detail.)"""
        bot = self.context.bot
        p = self.context.clean_prefix
        e = discord.Embed(color=HELP_COLOR, description=f"Select a category below to view its commands.\nprefix: {p}")
        if bot and getattr(bot, "user", None):
            e.set_author(name=bot.user.name, icon_url=bot.user.display_avatar.url)
        return e

    def category_embed(self, label: str, cmds: list[commands.Command], page: int = 0) -> discord.Embed:
        p = self.context.clean_prefix
        pages = max(1, math.ceil(len(cmds) / PAGE_SIZE))
        page = max(0, min(page, pages - 1))
        start = page * PAGE_SIZE
        lines = []
        if CATEGORY_DESCRIPTIONS.get(label):
            lines.append(f"-# {CATEGORY_DESCRIPTIONS[label]}\n")
        if not cmds:
            lines.append("No commands in this category.")
        for n, cmd in enumerate(cmds[start:start + PAGE_SIZE], start=start + 1):
            subs = sub_count(cmd)
            more = f" · {count(subs, 'subcommand')}" if subs else ""
            lines.append(f"`{n:>2}` **{cmd.name}**{alias_hint(cmd)} — {blurb(cmd)}{more}")
        e = discord.Embed(title=f"{CATEGORY_EMOJIS.get(label, '📁')} {label}", color=HELP_COLOR,
                          description=shorten_md("\n".join(lines), 4000))
        e.set_footer(text=f"Page {page + 1}/{pages} · {count(len(cmds), 'command')} · {p}help <command> for details")
        return e

    def command_embed(self, cmd: commands.Command) -> discord.Embed:
        p = self.context.clean_prefix
        bot = getattr(self.context, "bot", None)
        e = discord.Embed(title=cmd.qualified_name, color=HELP_COLOR,
                          description=cmd.help or cmd.short_doc or "No description yet.")
        if bot and getattr(bot, "user", None):
            e.set_author(name=bot.user.name, icon_url=bot.user.display_avatar.url)

        block = f"Syntax: {usage(p, cmd)}"
        example = example_for(cmd, p)
        if example:
            block += f"\nExample: {example}"
        e.add_field(name="Usage", value=f"```\n{block}\n```", inline=False)

        if getattr(cmd, "aliases", None):
            e.add_field(name="Aliases", value=", ".join(f"`{a}`" for a in cmd.aliases), inline=True)

        needs = []
        rank = min_rank_for_command(cmd, bot)
        if rank > 0:
            needs.append(f"{rank_name(rank)}+")
        needs += [x.replace("_", " ").title() for x in perms_of(cmd) if not x.lower().startswith("uperm")]
        e.add_field(name="Permissions", value=" · ".join(needs) if needs else "Everyone", inline=True)

        module = category_for_command(cmd, bot)
        subs = sub_count(cmd)
        e.set_footer(text=f"Module: {module}" + (f" · {count(subs, 'subcommand')}" if subs else "")
                     + " · <required> [optional]")
        return e

    # developer menus live in core/devmenu.py; these stay as thin wrappers for the views and tests
    async def developer_embed(self) -> discord.Embed:
        return await developer_embed(self.context)

    async def diagnostics_embed(self) -> discord.Embed:
        return await diagnostics_embed(self.context)

    async def logs_embed(self) -> discord.Embed:
        return await logs_embed(self.context)

    def secret_embed(self) -> discord.Embed:
        return SecretHelpView(self.context, help_cmd=self).get_embed()

    def tokens_embed(self) -> discord.Embed:
        return SecretHelpView(self.context, help_cmd=self, initial_category="tokens").build_tokens_embed()

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
    async def show_home_interaction(self, it: discord.Interaction):
        """Back to the home card from the developer menus."""
        categories = category_commands(self.context.bot)
        is_owner = await self.is_owner_check()
        rank = await self.get_author_rank()
        view = HelpView(self, categories, None, is_owner=is_owner, author_rank=rank)
        view.message = getattr(it, "message", None)
        await respond_or_edit(it, self.home_embed(rank, categories, is_owner), view)

    async def open_menu(self, start: str | None = None):
        categories = category_commands(self.context.bot)
        is_owner = await self.is_owner_check()
        rank = await self.get_author_rank()
        wanted = (start or "").lower()

        if is_owner and wanted in ("secret", "tokens", "secrets", "secretsuite"):
            view = SecretHelpView(self.context, help_cmd=self,
                                  initial_category="tokens" if wanted in ("tokens", "secrets") else "all")
            view.message = await self.context.send(embed=view.get_embed(), view=view)
            return
        if is_owner and wanted in ("dev", "developer"):
            view = DeveloperView(self, self.context.author.id)
            view.message = await self.context.send(embed=await self.developer_embed(), view=view)
            return

        if start is not None and not is_owner and rank < CATEGORY_MIN_RANKS.get(start, 0):
            return await self.send_error_message(f'No command or category called "{start}" found.')

        view = HelpView(self, categories, start if start in categories else None, is_owner=is_owner, author_rank=rank)
        if view.current is None:
            embed = self.home_embed(rank, categories, is_owner)
        else:
            embed = self.category_embed(start, view.commands_in(start), 0)
        view.message = await self.context.send(embed=embed, view=view)

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
            if wanted in ("dev", "developer", "secret", "tokens", "secrets", "secretsuite"):
                if is_owner:
                    await self.prepare_help_command(ctx, command)
                    return await self.open_menu("developer" if wanted in ("dev", "developer") else wanted)
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
        if not await self._can_see(group):
            return await self.send_error_message(f'No command called "{group.name}" found.')
        is_owner = await self.is_owner_check()
        rank = 0 if is_owner else await self.get_author_rank()
        bot = getattr(self.context, "bot", None)
        embed = self.command_embed(group)
        subs = sorted((c for c in group.walk_commands() if not c.hidden), key=lambda c: c.qualified_name)
        subs = allowed(subs, bot, is_owner, rank)
        if subs:
            p = self.context.clean_prefix
            lines = [f"`{n:>2}` **{c.qualified_name[len(group.qualified_name):].strip()}**"
                     + (f" `{c.signature.strip()}`" if c.signature.strip() else "") + f" — {blurb(c, 60)}"
                     for n, c in enumerate(subs, start=1)]
            chunks, cur = [], ""
            for line in lines:
                if len(cur) + len(line) + 1 > 1000:
                    chunks.append(cur)
                    cur = ""
                cur += line + "\n"
            chunks.append(cur)
            chunks = [c.rstrip() for c in chunks if c.strip()]
            for i, chunk in enumerate(chunks[:5]):
                embed.add_field(name="Subcommands" if i == 0 else "​", value=chunk, inline=False)
            if len(chunks) > 5:
                embed.add_field(name="​", value=f"…and more. `{p}help {group.qualified_name} <subcommand>` for any of them.",
                                inline=False)
        await self.context.send(embed=embed)

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
        await self.context.send(embed=discord.Embed(color=Colors.warn, description=f"⚠️ {error}"))
