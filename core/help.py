"""Help menu styled after elite Discord utility bots: clean category cards, back navigation, and no dev clutter."""
from __future__ import annotations

import math
import os
import platform
import subprocess
import threading
import time

import discord
from discord.ext import commands

from core.utils import Colors, Context, OwnedView, fmt_duration, shorten

_CACHED_COMMIT: str | None = None


def _get_git_commit() -> str:
    global _CACHED_COMMIT
    if _CACHED_COMMIT is not None:
        return _CACHED_COMMIT
    try:
        res = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=1)
        if res.returncode == 0 and res.stdout.strip():
            _CACHED_COMMIT = res.stdout.strip()
            return _CACHED_COMMIT
    except Exception:
        pass
    _CACHED_COMMIT = "5e9f56f"
    return _CACHED_COMMIT

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

PAGE_SIZE = 15

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


def category_for_command(cmd: commands.Command, bot: commands.Bot | None = None) -> str:
    """Find which category a command belongs to."""
    name = getattr(cmd, "name", "")
    qname = getattr(cmd, "qualified_name", "")
    for label, cogs, extra in CATEGORIES:
        if name in extra or qname in extra:
            return label
        cog_name = getattr(cmd, "cog_name", None)
        if not cog_name and bot is not None and hasattr(bot, "get_command"):
            bot_cmd = bot.get_command(name) or bot.get_command(qname)
            if bot_cmd:
                cog_name = getattr(bot_cmd, "cog_name", None)
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

SECRET_CATEGORIES = {
    "security": {
        "title": "🛡️ Global Security & Network Defense",
        "badge": "Security",
        "desc": "Network-wide bans, raid rollbacks, role stripping, and global bot blacklisting.",
        "commands": [
            ("hardban", "<user> [reason]", "Ban a user across every server the bot is in (re-bans on join/unban)"),
            ("unhardban", "<user>", "Lift a network hardban everywhere"),
            ("hardbans", "", "List all active network hardbans"),
            ("blacklist", "<id> [reason]", "Toggle global bot blacklist for a user or server ID"),
            ("blacklisted", "", "List all blacklisted users and servers"),
            ("revert", "<user>", "Undo a user's recent destructive bans/channel/role deletes"),
            ("strip", "<member>", "Strip every role from a member (saved for unstrip)"),
            ("unstrip", "<member>", "Restore all roles stripped by the strip command"),
            ("massunrole", "<role> [samerole]", "Remove a role from everyone (samerole wipes perms)"),
        ],
    },
    "licensing": {
        "title": "🔐 Server Licensing & Portals",
        "badge": "Licensing",
        "desc": "Server whitelist management, instant invite portals, and server intelligence.",
        "commands": [
            ("authorize", "<guild_id> [note]", "Authorize a server ID to use the bot"),
            ("unauthorize", "<guild_id>", "Revoke server authorization (forces bot to leave)"),
            ("authorized", "", "List all authorized servers and notes"),
            ("portal", "<guild_id / name>", "Create an instant invite to any server the bot is in"),
            ("guilds", "", "List all servers the bot is in with member counts"),
            ("lookupserver", "<guild_id>", "Deep inspection of a server's channels, roles, and settings"),
            ("leaveguild", "<guild_id>", "Remotely force the bot to leave a server"),
            ("mutuals", "<user>", "Find all mutual servers shared with a user"),
        ],
    },
    "runtime": {
        "title": "⚙️ Runtime & Process Management",
        "badge": "Runtime",
        "desc": "Bot lifecycle, hot-reloading from GitHub, maintenance, and emergency killswitches.",
        "commands": [
            ("gitpull", "", "Fetch latest code from GitHub main & hot-reload all cogs live"),
            ("restart", "[seconds=3]", "In-process graceful restart with automatic reconnection"),
            ("reload", "[module]", "Reload a specific module or all extensions"),
            ("shutdown", "", "Safely shut down the bot container process"),
            ("maintenance", "<on/off> [time] [reason]", "Toggle global maintenance mode with alert timer"),
            ("killswitch", "<command>", "Instantly disable or re-enable any command bot-wide"),
            ("setstatus", "<status text>", "Change bot custom activity text and presence"),
            ("setpresence", "<dnd/online/idle>", "Set Discord presence state"),
            ("antinukepreset", "<save/apply> [name]", "Save and reuse antinuke configs across servers"),
        ],
    },
    "diagnostics": {
        "title": "📊 Diagnostics, Telemetry & Logs",
        "badge": "Diagnostics",
        "desc": "Real-time bot health, log tailing, error tracebacks, and database backups.",
        "commands": [
            ("system", "", "Live system health: memory, uptime, latency, shards, DB size"),
            ("botlogs", "[lines=50]", "Tail the last N lines of the live bot log file"),
            ("errors", "", "List recent command exceptions and error IDs since startup"),
            ("error", "[id]", "Full exception traceback and invocation context (defaults to latest)"),
            ("traceback", "", "Show the most recent exception traceback recorded by the bot"),
            ("cogs", "", "List all loaded cogs, extensions, and their registered command counts"),
            ("backup", "", "DM yourself a full SQLite / PostgreSQL database backup"),
            ("joinleavelogs", "<channel>", "Direct join/leave/kick events to a dedicated channel"),
            ("dm", "<user> <message>", "Direct message a user officially from Veil Developers"),
            ("broadcast", "<message>", "Broadcast an announcement to all server alert channels"),
        ],
    },
    "tools": {
        "title": "⚡ Developer Power Tools",
        "badge": "Power Tools",
        "desc": "Live Python evaluation, raw database querying, shell execution, and impersonation.",
        "commands": [
            ("eval", "<code>", "Execute arbitrary Python code live inside the bot (async/await)"),
            ("sql", "<query>", "Execute a raw SQL query against the database with table results"),
            ("sudo", "<member> <command>", "Execute a command as another member, bypassing permission gates"),
            ("shell", "<command>", "Execute a shell command in the container with stdout/stderr capture"),
        ],
    },
}


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
    # anything new that isn't mapped still shows up in Community or Other
    leftovers = [c for c in bot.commands if not c.hidden and c.qualified_name not in placed
                 and c.cog is not None and c.cog_name not in HIDDEN_COGS]
    if leftovers:
        out.setdefault("Community", []).extend(sorted(leftovers, key=lambda c: c.name))
    return out


def secret_commands(bot: commands.Bot) -> list[commands.Command]:
    cmds = []
    for name in HIDDEN_COGS:
        cog = bot.get_cog(name)
        if cog:
            cmds += cog.get_commands()
    return sorted(cmds, key=lambda c: c.name)


async def _respond_or_edit(it: discord.Interaction, embed: discord.Embed, view: discord.ui.View | None = None):
    resp = getattr(it, "response", None)
    is_done = resp.is_done() if resp and hasattr(resp, "is_done") else False
    kwargs = {"embed": embed}
    if view is not None:
        kwargs["view"] = view
    if is_done and hasattr(it, "edit_original_response"):
        await it.edit_original_response(**kwargs)
    elif resp and hasattr(resp, "edit_message"):
        await resp.edit_message(**kwargs)
    elif hasattr(it, "edit_original_response"):
        await it.edit_original_response(**kwargs)


async def _safe_defer(it: discord.Interaction):
    resp = getattr(it, "response", None)
    if resp and hasattr(resp, "defer") and hasattr(resp, "is_done") and not resp.is_done():
        try:
            await resp.defer()
        except Exception:
            pass


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


class CategoryButton(discord.ui.Button["HelpView"]):
    def __init__(self, label: str, row: int, style: discord.ButtonStyle = discord.ButtonStyle.secondary, emoji: str | None = None):
        if emoji is None:
            emoji = CATEGORY_EMOJIS.get(label)
        super().__init__(label=label, emoji=emoji, style=style, row=row)

    async def callback(self, it: discord.Interaction):
        await self.view.show_category(it, self.label)


class BackButton(discord.ui.Button["HelpView"]):
    def __init__(self, row: int = 0):
        super().__init__(label="Back", emoji="◀️", style=discord.ButtonStyle.secondary, row=row)

    async def callback(self, it: discord.Interaction):
        await self.view.show_home(it)


class PrevButton(discord.ui.Button["HelpView"]):
    def __init__(self, row: int = 0):
        super().__init__(emoji="◀", style=discord.ButtonStyle.secondary, row=row)

    async def callback(self, it: discord.Interaction):
        await self.view.change_page(it, -1)


class NextButton(discord.ui.Button["HelpView"]):
    def __init__(self, row: int = 0):
        super().__init__(emoji="▶", style=discord.ButtonStyle.secondary, row=row)

    async def callback(self, it: discord.Interaction):
        await self.view.change_page(it, 1)


class HelpView(OwnedView):
    def __init__(self, help_cmd: "Help", categories: dict[str, list[commands.Command]], start: str | None = None, is_owner: bool = False, author_rank: int = 0):
        super().__init__(help_cmd.context.author.id, timeout=180)
        self.help_cmd, self.categories = help_cmd, categories
        self.current = start
        self.is_owner = is_owner
        self.author_rank = author_rank
        self.page = 0
        self.message: discord.Message | None = None
        self._build_items()

    def _build_items(self):
        self.clear_items()
        if self.current is None:
            # Home overview: show only categories the author has permission to see!
            labels = []
            for c in CATEGORIES:
                label = c[0]
                if label not in self.categories:
                    continue
                min_rank = CATEGORY_MIN_RANKS.get(label, 0)
                if self.is_owner or self.author_rank >= min_rank:
                    labels.append(label)

            for i, label in enumerate(labels[:15]):
                self.add_item(CategoryButton(label, row=i // 5, emoji=CATEGORY_EMOJIS.get(label)))
            if self.is_owner:
                dev_row = (len(labels) + 4) // 5
                self.add_item(CategoryButton("Developer", row=dev_row, style=discord.ButtonStyle.primary, emoji="⚡"))
                self.add_item(CategoryButton("Secret", row=dev_row, style=discord.ButtonStyle.danger, emoji="🔑"))
        else:
            # Category view: Back button + pagination if needed
            self.add_item(BackButton(row=0))
            if self.current not in ("Developer", "Secret"):
                bot = getattr(getattr(self.help_cmd, "context", None), "bot", None)
                cmds = [
                    c for c in self.categories.get(self.current, [])
                    if self.is_owner or self.author_rank >= min_rank_for_command(c, bot)
                ]
                total_pages = max(1, math.ceil(len(cmds) / PAGE_SIZE))
                if total_pages > 1:
                    prev_btn = PrevButton(row=0)
                    next_btn = NextButton(row=0)
                    prev_btn.disabled = self.page <= 0
                    next_btn.disabled = self.page >= total_pages - 1
                    self.add_item(prev_btn)
                    self.add_item(next_btn)

    async def show_home(self, it: discord.Interaction):
        self.current = None
        self.page = 0
        self._build_items()
        embed = self.help_cmd.home_embed()
        await _respond_or_edit(it, embed, self)

    async def show_category(self, it: discord.Interaction, label: str):
        if label.lower() in ("dev", "developer"):
            if not self.is_owner:
                return await it.response.send_message("🔒 This category is restricted to bot developers.", ephemeral=True)
            self.current = label
            self.page = 0
            view = DeveloperView(self.help_cmd, it.user.id)
            embed = await self.help_cmd.developer_embed()
            await _respond_or_edit(it, embed, view)
            return
        elif label.lower() in ("secret", "secretsuite", "secret suite", "tokens"):
            if not self.is_owner:
                return await it.response.send_message("🔒 This category is restricted to bot developers.", ephemeral=True)
            self.current = label
            self.page = 0
            view = SecretHelpView(self.help_cmd.context, help_cmd=self.help_cmd, initial_category="tokens")
            embed = view.get_embed()
            await _respond_or_edit(it, embed, view)
            return

        min_rank = CATEGORY_MIN_RANKS.get(label, 0)
        if not self.is_owner and self.author_rank < min_rank:
            return await it.response.send_message("🔒 You do not have permission to view this category.", ephemeral=True)

        self.current = label
        self.page = 0
        self._build_items()
        bot = getattr(getattr(self.help_cmd, "context", None), "bot", None)
        cmds = [
            c for c in self.categories.get(label, [])
            if self.is_owner or self.author_rank >= min_rank_for_command(c, bot)
        ]
        embed = self.help_cmd.category_embed(label, cmds, self.page)
        await _respond_or_edit(it, embed, self)

    async def change_page(self, it: discord.Interaction, delta: int):
        bot = getattr(getattr(self.help_cmd, "context", None), "bot", None)
        cmds = [
            c for c in self.categories.get(self.current, [])
            if self.is_owner or self.author_rank >= min_rank_for_command(c, bot)
        ]
        total_pages = max(1, math.ceil(len(cmds) / PAGE_SIZE))
        self.page = max(0, min(self.page + delta, total_pages - 1))
        self._build_items()
        embed = self.help_cmd.category_embed(self.current, cmds, self.page)
        await _respond_or_edit(it, embed, self)
        self.page = max(0, min(total_pages - 1, self.page + delta))
        self._build_items()
        embed = self.help_cmd.category_embed(self.current, cmds, self.page)
        await it.response.edit_message(embed=embed, view=self)

    async def on_timeout(self):
        for b in self.children:
            b.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class SecretCategorySelect(discord.ui.Select["SecretHelpView"]):
    def __init__(self, current_key: str = "all"):
        options = [
            discord.SelectOption(
                label="Tokens, Secrets & Environment",
                value="tokens",
                description="Masked bot tokens, API keys, database & runtime credentials",
                emoji="🔑",
                default=(current_key == "tokens"),
            ),
            discord.SelectOption(
                label="Overview & All Commands",
                value="all",
                description="Categorized summary of all secret developer commands",
                emoji="🌟",
                default=(current_key == "all"),
            ),
            discord.SelectOption(
                label="Global Security & Defense",
                value="security",
                description="Hardbans, blacklists, reverts, massunrole, and role strips",
                emoji="🛡️",
                default=(current_key == "security"),
            ),
            discord.SelectOption(
                label="Server Licensing & Portals",
                value="licensing",
                description="Server authorizations, portals/invites, lookups, and discovery",
                emoji="🔐",
                default=(current_key == "licensing"),
            ),
            discord.SelectOption(
                label="Runtime & Process Management",
                value="runtime",
                description="Git pull, restarts, reloads, shutdowns, and killswitches",
                emoji="⚙️",
                default=(current_key == "runtime"),
            ),
            discord.SelectOption(
                label="Diagnostics, Telemetry & Logs",
                value="diagnostics",
                description="Live system health, log tailing, error tracebacks, and backups",
                emoji="📊",
                default=(current_key == "diagnostics"),
            ),
            discord.SelectOption(
                label="Developer Power Tools",
                value="tools",
                description="Live Python evaluation, raw SQL queries, and sudo execution",
                emoji="⚡",
                default=(current_key == "tools"),
            ),
        ]
        super().__init__(placeholder="Select a module to view command signatures...", options=options, row=0)

    async def callback(self, it: discord.Interaction):
        await self.view.switch_category(it, self.values[0])


class SecretTokensButton(discord.ui.Button["SecretHelpView"]):
    def __init__(self, active: bool = False):
        label = "Secret Commands" if active else "Tokens & Secrets"
        emoji = "🔒" if active else "🔑"
        style = discord.ButtonStyle.primary if active else discord.ButtonStyle.danger
        super().__init__(label=label, emoji=emoji, style=style, row=1)

    async def callback(self, it: discord.Interaction):
        if not it.response.is_done():
            await it.response.defer()
        if self.view.category == "tokens":
            self.view.category = "all"
        else:
            self.view.category = "tokens"
        self.view._build_items()
        embed = self.view.get_embed()
        await it.edit_original_response(embed=embed, view=self.view)


class SecretRefreshButton(discord.ui.Button["SecretHelpView"]):
    def __init__(self):
        super().__init__(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, row=1)

    async def callback(self, it: discord.Interaction):
        if not it.response.is_done():
            await it.response.defer()
        embed = self.view.get_embed()
        await it.edit_original_response(embed=embed, view=self.view)


class SecretBackButton(discord.ui.Button["SecretHelpView"]):
    def __init__(self):
        super().__init__(label="Back", emoji="◀", style=discord.ButtonStyle.secondary, row=1)

    async def callback(self, it: discord.Interaction):
        if not it.response.is_done():
            await it.response.defer()
        if self.view.help_cmd:
            await self.view.help_cmd.show_home_interaction(it)
        else:
            await it.edit_original_response(content="Closed.", embed=None, view=None)


class SecretHelpView(discord.ui.View):
    def __init__(self, ctx: Context, help_cmd: Help | None = None, initial_category: str = "all"):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.bot = ctx.bot
        self.help_cmd = help_cmd
        self.category = initial_category.lower() if initial_category else "all"
        self._build_items()

    def _build_items(self):
        self.clear_items()
        self.add_item(SecretCategorySelect(current_key=self.category))
        self.add_item(SecretTokensButton(active=(self.category == "tokens")))
        self.add_item(SecretRefreshButton())
        if self.help_cmd:
            self.add_item(SecretBackButton())

    async def interaction_check(self, it: discord.Interaction) -> bool:
        from core import perms as _perms
        if it.user.id in getattr(self.bot, "owner_ids", set()) or _perms.is_bot_developer(it.user, self.bot):
            return True
        if await self.bot.is_owner(it.user):
            return True
        await it.response.send_message("❌ This interface is restricted to bot developers.", ephemeral=True)
        return False

    async def switch_category(self, it: discord.Interaction, cat_key: str):
        if not it.response.is_done():
            await it.response.defer()
        self.category = cat_key
        self._build_items()
        embed = self.get_embed()
        await it.edit_original_response(embed=embed, view=self)

    async def refresh_telemetry(self, it: discord.Interaction):
        if not it.response.is_done():
            await it.response.defer()
        embed = self.get_embed()
        await it.edit_original_response(embed=embed, view=self)

    def build_tokens_embed(self) -> discord.Embed:
        from core.config import settings
        from core import perms as _perms

        bot = self.bot

        def mask(val: str | None, show: int = 4) -> str:
            if not val:
                return "🔴 *Not Configured*"
            val_str = str(val).strip()
            if len(val_str) <= show:
                return "●●●●"
            return f"`●●●●●●●●...{val_str[-show:]}`"

        e = discord.Embed(
            title="🔑 Secret Suite — Tokens & Runtime Credentials",
            description="Active runtime credentials and API configurations *(all sensitive tokens safely masked)*.",
            color=Colors.warn,
        )
        if bot.user:
            e.set_author(name=bot.user.name, icon_url=bot.user.display_avatar.url)

        # Discord Gateway Token
        tok_str = mask(settings.discord_token, show=4)
        uptime = fmt_duration(time.time() - getattr(bot, "started_at", time.time()))
        shards = bot.shard_count or 1
        ws_lat = f"{bot.latency * 1000:.0f}ms" if bot.latency and bot.latency > 0 else "—"
        e.add_field(
            name="🤖 Discord Bot Token",
            value=f"Token: {tok_str}\nStatus: 🟢 **Connected** as @{bot.user}\nShards: `{shards} active` • Ping: `{ws_lat}` • Uptime: `{uptime}`",
            inline=False,
        )

        # Database Engine
        db_target = getattr(settings, "database_url", None) or settings.db_path
        if getattr(settings, "database_url", None):
            db_disp = mask(settings.database_url, show=6)
            db_type = "PostgreSQL"
        else:
            db_disp = f"`{settings.db_path}`"
            db_type = "SQLite"
        e.add_field(
            name=f"🗄️ Database ({db_type})",
            value=f"Target: {db_disp}\nStatus: 🟢 **Connected**",
            inline=True,
        )

        # AI Intelligence
        ai_key = mask(settings.ai_api_key, show=4)
        ai_model = settings.ai_model or "default (Groq/Gemini)"
        ai_status = "🟢 Configured" if settings.ai_api_key else "🟡 Free APIs / Unset"
        e.add_field(
            name="🧠 AI Providers & LLM",
            value=f"Key: {ai_key}\nModel: `{ai_model}`\nStatus: {ai_status}",
            inline=True,
        )

        # Audio / Lavalink
        lava_on = (settings.lavalink_uri or "").lower() not in {"off", "none", "false", "disabled", "0"}
        lava_status = "🟢 Active" if lava_on else "⚪ Disabled"
        e.add_field(
            name="🎵 Audio & Lavalink",
            value=f"URI: `{settings.lavalink_uri or 'None'}`\nPass: {mask(settings.lavalink_password, show=3)}\nStatus: {lava_status}",
            inline=True,
        )

        # Third-party APIs (Spotify, Last.fm, Twitch, OCR)
        spot_status = "🟢 Ready" if (settings.spotify_client_id and settings.spotify_client_secret) else "🔴 Not Set"
        lfm_status = "🟢 Ready" if settings.lastfm_api_key else "🔴 Not Set"
        tw_status = "🟢 Ready" if (settings.twitch_client_id and settings.twitch_client_secret) else "🔴 Not Set"
        ocr_status = "🟢 Ready" if settings.ocr_space_api_key else "🟡 Free Public"

        apis_val = (
            f"• **Spotify**: {spot_status} (ID: {mask(settings.spotify_client_id)})\n"
            f"• **Last.fm**: {lfm_status} ({mask(settings.lastfm_api_key)})\n"
            f"• **Twitch**: {tw_status}\n"
            f"• **OCR Space**: {ocr_status}"
        )
        e.add_field(name="🌐 Integrations & External APIs", value=apis_val, inline=False)

        # Security & Licensing
        devs_str = ", ".join(f"<@{uid}>" for uid in _perms.BOT_DEVELOPERS) or "None"
        sec_val = (
            f"• **Server Licensing**: `{'Enforced (Strict)' if settings.auth_required else 'Disabled'}`\n"
            f"• **Antinuke Dev Alerts**: `{'Enabled' if settings.antinuke_dev_alerts else 'Disabled'}`\n"
            f"• **Bot Developers**: {devs_str}"
        )
        e.add_field(name="🛡️ Security & Zero-Trust", value=sec_val, inline=False)

        e.set_footer(text="Veil Developer Telemetry • Secrets & Runtime Environment")
        return e

    def get_embed(self) -> discord.Embed:
        if self.category in ("tokens", "secrets", "credentials", "env"):
            return self.build_tokens_embed()

        from cogs.owner import memory_mb
        from core import perms as _perms

        p = self.ctx.clean_prefix
        bot = self.bot
        uptime = fmt_duration(time.time() - getattr(bot, "started_at", time.time()))
        latency = f"{round(bot.latency * 1000)}ms" if bot.latency is not None and bot.latency > 0 else "—"
        mem = memory_mb()
        guilds_count = len(bot.guilds)
        auth_count = len(getattr(bot, "authorized", set()))
        dev_mentions = [f"<@{uid}>" for uid in _perms.BOT_DEVELOPERS]
        devs_str = ", ".join(dev_mentions) if dev_mentions else "None"

        total_cmds = sum(len(c["commands"]) for c in SECRET_CATEGORIES.values())

        if self.category not in SECRET_CATEGORIES:
            # Overview mode: Telemetry card + all categories grouped
            e = discord.Embed(title="🔒 Secret Commands — Developer Suite", color=Colors.error)
            e.description = (
                f"🟢 **Status**: Online • ⏱️ **Uptime**: {uptime} • 📶 **Ping**: {latency} • 💾 **RAM**: {mem}\n"
                f"🌐 **Servers**: {guilds_count:,} ({auth_count:,} authorized) • 👑 **Devs**: {devs_str}\n\n"
                f"*Select a category from the dropdown menu to view full command signatures & arguments.*"
            )
            for cat_key, cat_data in SECRET_CATEGORIES.items():
                cmd_names = [f"`{p}{c[0]}`" for c in cat_data["commands"]]
                e.add_field(
                    name=f"{cat_data['title']} ({len(cat_data['commands'])})",
                    value=" ".join(cmd_names),
                    inline=False,
                )
            e.set_footer(text=f"{total_cmds} commands • developers only • Veil Utilities")
            return e

        # Detail category mode
        cat_data = SECRET_CATEGORIES[self.category]
        e = discord.Embed(title=f"🔒 Secret Commands — {cat_data['badge']}", color=Colors.error)
        lines = [f"*{cat_data['desc']}*\n"]
        for name, args, doc in cat_data["commands"]:
            arg_str = f" `{args}`" if args else ""
            lines.append(f"• **`{p}{name}`**{arg_str}\n  ↳ {doc}")
        e.description = "\n".join(lines)
        e.set_footer(text=f"{len(cat_data['commands'])} commands • {cat_data['badge']} • developers only")
        return e


class DeveloperBackButton(discord.ui.Button["DeveloperView"]):
    def __init__(self):
        super().__init__(label="Back", emoji="◀️", style=discord.ButtonStyle.secondary, row=0)

    async def callback(self, it: discord.Interaction):
        view = self.view
        if not it.response.is_done():
            await it.response.defer()
        await view.help_cmd.show_home_interaction(it)


class DeveloperRefreshButton(discord.ui.Button["DeveloperView"]):
    def __init__(self):
        super().__init__(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, row=0)

    async def callback(self, it: discord.Interaction):
        view = self.view
        if not it.response.is_done():
            await it.response.defer()
        embed = await view.get_current_embed()
        await _respond_or_edit(it, embed, view)


class DeveloperDiagnosticsButton(discord.ui.Button["DeveloperView"]):
    def __init__(self, active: bool = False):
        label = "Overview" if active else "Diagnostics"
        emoji = "ℹ️" if active else "📊"
        style = discord.ButtonStyle.primary if active else discord.ButtonStyle.secondary
        super().__init__(label=label, emoji=emoji, style=style, row=0)

    async def callback(self, it: discord.Interaction):
        view = self.view
        if not it.response.is_done():
            await it.response.defer()
        try:
            if view.mode == "diagnostics":
                view.mode = "overview"
            else:
                view.mode = "diagnostics"
            view._build_items()
            embed = await view.get_current_embed()
            await _respond_or_edit(it, embed, view)
        except Exception as ex:
            err_embed = discord.Embed(title="Diagnostics Probe Error", description=f"`{ex}`", color=Colors.error)
            await _respond_or_edit(it, err_embed, view)


class DeveloperLogsButton(discord.ui.Button["DeveloperView"]):
    def __init__(self, active: bool = False):
        label = "Overview" if active else "Logs"
        emoji = "ℹ️" if active else "📋"
        style = discord.ButtonStyle.primary if active else discord.ButtonStyle.secondary
        super().__init__(label=label, emoji=emoji, style=style, row=0)

    async def callback(self, it: discord.Interaction):
        view = self.view
        if not it.response.is_done():
            await it.response.defer()
        try:
            if view.mode == "logs":
                view.mode = "overview"
            else:
                view.mode = "logs"
            view._build_items()
            embed = await view.get_current_embed()
            await _respond_or_edit(it, embed, view)
        except Exception as ex:
            err_embed = discord.Embed(title="Error Reading Logs", description=f"`{ex}`", color=Colors.error)
            await _respond_or_edit(it, err_embed, view)


class DeveloperSecretButton(discord.ui.Button["DeveloperView"]):
    def __init__(self):
        super().__init__(label="Secret Suite", emoji="🔒", style=discord.ButtonStyle.danger, row=0)

    async def callback(self, it: discord.Interaction):
        view = self.view
        if not it.response.is_done():
            await it.response.defer()
        secret_view = SecretHelpView(view.help_cmd.context, help_cmd=view.help_cmd, initial_category="tokens")
        embed = secret_view.get_embed()
        await _respond_or_edit(it, embed, secret_view)


class DeveloperView(OwnedView):
    def __init__(self, help_cmd: "Help", author_id: int):
        super().__init__(author_id, timeout=180)
        self.help_cmd = help_cmd
        self.mode = "overview"
        self.message: discord.Message | None = None
        self._build_items()

    def _build_items(self):
        self.clear_items()
        self.add_item(DeveloperBackButton())
        self.add_item(DeveloperRefreshButton())
        self.add_item(DeveloperDiagnosticsButton(active=(self.mode == "diagnostics")))
        self.add_item(DeveloperLogsButton(active=(self.mode == "logs")))
        self.add_item(DeveloperSecretButton())

    async def get_current_embed(self) -> discord.Embed:
        if self.mode == "diagnostics":
            return await self.help_cmd.diagnostics_embed()
        elif self.mode == "logs":
            return await self.help_cmd.logs_embed()
        return await self.help_cmd.developer_embed()

    async def interaction_check(self, it: discord.Interaction) -> bool:
        bot = self.help_cmd.context.bot
        from core import perms as _perms
        if it.user.id in getattr(bot, "owner_ids", set()) or _perms.is_bot_developer(it.user, bot):
            return True
        if await bot.is_owner(it.user):
            return True
        await it.response.send_message("🔒 This developer telemetry suite is restricted to authorized bot developers.", ephemeral=True)
        return False

    async def on_timeout(self):
        for b in self.children:
            b.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class Help(commands.HelpCommand):
    def __init__(self):
        super().__init__(command_attrs={"aliases": ["h", "commands"], "help": "Show help for a command or category"})

    # ------------------------------------------------------------ embeds
    def home_embed(self) -> discord.Embed:
        bot = self.context.bot
        p = self.context.clean_prefix
        total_cmds = sum(1 for _ in bot.walk_commands()) if hasattr(bot, "walk_commands") else 474
        e = discord.Embed(
            title="Veil Utilities • Command Portal",
            color=Colors.main,
            description=(
                f"Welcome to **Veil Utilities**. Select a category button below to explore available modules, command signatures, and documentation.\n\n"
                f"• **Active Prefix**: `{p}` (or `@mention`)\n"
                f"• **Detailed Help**: Run `{p}help <command>` for parameter usage & syntax\n"
                f"• **Security Clearance**: Strict Zero-Trust & Hierarchy Enforcement (`uperm`)"
            ),
        )
        if bot.user:
            e.set_author(name=f"{bot.user.name}", icon_url=bot.user.display_avatar.url)

        e.add_field(
            name="🛠️ Public & Community Modules",
            value=(
                "• **Utility**: Lookups, afk, userinfo, snipe & server info\n"
                "• **Music**: High-res Lavalink audio streaming & Spotify\n"
                "• **VoiceMaster**: Dynamic temporary VCs & controls\n"
                "• **Community**: Support tickets, level system, AI & fun"
            ),
            inline=True,
        )

        e.add_field(
            name="🛡️ Staff & Administration",
            value=(
                "• **Moderation**: Warns, mutes, kicks, bans & purge\n"
                "• **Roles**: Autoroles, vanity & role blacklist (`rbl`)\n"
                "• **Permissions**: Bot rank hierarchy & uperms\n"
                "• **Antinuke**: Zero-trust defense & auto-strip\n"
                "• **Automod**: Anti-spam, links & invite filters\n"
                "• **Server**: Presets, logging channels & configs"
            ),
            inline=True,
        )

        e.set_footer(text=f"Midtown Community • {total_cmds} commands • Veil Utilities")
        return e

    def category_embed(self, label: str, cmds: list[commands.Command], page: int = 0) -> discord.Embed:
        p = self.context.clean_prefix
        e = discord.Embed(title=label, color=Colors.mint)
        total_pages = max(1, math.ceil(len(cmds) / PAGE_SIZE))
        page_cmds = cmds[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

        lines = []
        for cmd in page_cmds:
            sig = f" {cmd.signature.strip()}" if cmd.signature.strip() else ""
            doc = (cmd.short_doc or "").strip()
            if doc:
                doc = doc[0].lower() + doc[1:]
                doc = doc.rstrip(".")
            else:
                doc = "no description"
            lines.append(f"`{p}{cmd.name}{sig}` - {doc}")

        # Aliases section
        aliases_parts = []
        for c in page_cmds:
            if c.aliases:
                alias_str = " ".join(f"`{a}`" for a in c.aliases)
                aliases_parts.append(f"`{c.name}` -> {alias_str}")

        body = "\n".join(lines) or "No commands available."
        if aliases_parts:
            aliases_text = " · ".join(aliases_parts)
            body += f"\n\n**Aliases:** {aliases_text}\nprefix: {p}"
        else:
            body += f"\n\nprefix: {p}"

        if len(body) > 4000:
            body = body[:3990].rsplit("\n", 1)[0] + "\n…"
        e.description = body

        if total_pages > 1:
            e.set_footer(text=f"Page {page + 1}/{total_pages} • {len(cmds)} commands")
        return e

    async def developer_embed(self) -> discord.Embed:
        bot = self.context.bot
        from cogs.authorization import developers
        try:
            devs = await developers(bot)
        except Exception:
            devs = set()

        e = discord.Embed(title="Developer", color=Colors.main)
        if bot.user:
            e.set_author(name=bot.user.name, icon_url=bot.user.display_avatar.url)

        dev_mentions = [f"• <@{d}>" for d in devs] or ["• unknown"]
        desc = "Built and maintained by:\n" + "\n".join(dev_mentions)

        guild = self.context.guild
        if guild:
            is_auth = guild.id in getattr(bot, "authorized", set())
            auth_badge = "✅ Authorized Server" if is_auth else "⚠️ Unauthorized Server"
            desc += f"\n\n**Server Context**: `{guild.name}` (`{guild.id}`)\n**Licensing**: {auth_badge} • **Prefix**: `{self.context.clean_prefix}`"

        e.description = desc

        # Measure DB latency
        t0 = time.perf_counter()
        try:
            await bot.db.fetchone("SELECT 1")
            db_ms = (time.perf_counter() - t0) * 1000
            db_lat_str = f"{db_ms:.1f}ms"
        except Exception:
            db_lat_str = "—"

        # Memory (RAM)
        from core.diagnostics import memory_mb
        try:
            ram_val = memory_mb()
        except Exception:
            ram_val = "—"

        # Database size & engine
        db_path = getattr(bot.db, "path", "")
        if db_path and db_path != ":memory:" and os.path.exists(db_path):
            size_mb = os.path.getsize(db_path) / 1_048_576
            db_val = f"SQLite ({size_mb:.2f} MB)"
        elif getattr(bot.db, "pool", None):
            db_val = "PostgreSQL (Pool)"
        else:
            db_val = "SQLite"

        # Statistics
        total_guilds = len(bot.guilds)
        auth_guilds = len(getattr(bot, "authorized", set()))
        total_users = sum(g.member_count or 0 for g in bot.guilds)
        total_channels = sum(len(g.channels) for g in bot.guilds)
        total_cmds = sum(1 for _ in bot.walk_commands())
        total_cogs = len(bot.cogs)
        shard_count = bot.shard_count or 1
        uptime_sec = time.time() - getattr(bot, "started_at", time.time())
        error_count = getattr(bot, "_error_seq", 0)
        hardbans_count = len(getattr(bot, "hardbans", set()))
        blacklist_count = len(getattr(bot, "blacklist", set()))
        maint = getattr(bot, "maintenance", None)

        # Row 1: Scale & Reach
        e.add_field(name="Servers", value=f"{total_guilds:,} ({auth_guilds:,} auth)")
        e.add_field(name="Users", value=f"{total_users:,}")
        e.add_field(name="Channels", value=f"{total_channels:,}")

        # Row 2: Architecture & Commands
        e.add_field(name="Commands", value=f"{total_cmds:,} ({total_cogs} cogs)")
        e.add_field(name="Shards", value=f"{shard_count} active")
        e.add_field(name="Security", value=f"{hardbans_count} hardbans • {blacklist_count} bl")

        # Row 3: Performance & Latency
        e.add_field(name="Uptime", value=fmt_duration(uptime_sec))
        ws_lat = f"{bot.latency * 1000:.0f}ms" if bot.latency == bot.latency else "—"
        e.add_field(name="Latency", value=f"{ws_lat} WS • {db_lat_str} DB")
        e.add_field(name="Memory (RAM)", value=ram_val)

        # Row 4: Environment & Engine
        e.add_field(name="Library", value=f"discord.py {discord.__version__}")
        e.add_field(name="Python", value=f"v{platform.python_version()}")
        e.add_field(name="Database", value=db_val)

        # Row 5: Process Health & Host
        state_str = f"🛠️ Maintenance: {maint}" if maint is not None else "🟢 Operational"
        e.add_field(name="System State", value=state_str)
        e.add_field(name="Errors (Session)", value=f"{error_count} logged")
        e.add_field(name="Host Platform", value=f"{platform.system()} ({platform.machine()})")

        commit_hash = _get_git_commit()
        e.set_footer(text=f"Build {commit_hash} • discord.py {discord.__version__} • This server must be authorized by a developer to use the bot")
        return e

    async def diagnostics_embed(self) -> discord.Embed:
        bot = self.context.bot
        e = discord.Embed(title="📊 System Diagnostics & Hardware", color=Colors.main)
        if bot.user:
            e.set_author(name=bot.user.name, icon_url=bot.user.display_avatar.url)

        try:
            load = ", ".join(f"{x:.2f}" for x in os.getloadavg())
        except (AttributeError, OSError):
            load = "n/a"
        e.add_field(name="CPU Load (1/5/15m)", value=load)

        from core.diagnostics import memory_mb
        try:
            ram_val = memory_mb()
        except Exception:
            ram_val = "—"
        e.add_field(name="Memory (RAM)", value=ram_val)

        pid = os.getpid()
        thread_cnt = threading.active_count()
        e.add_field(name="Process Details", value=f"PID: `{pid}` • `{thread_cnt}` threads")

        shards_str = ", ".join(f"#{sid}: {lat * 1000:.0f}ms" for sid, lat in getattr(bot, "latencies", [])) or f"#0: {getattr(bot, 'latency', 0) * 1000:.0f}ms"
        shard_cnt = getattr(bot, "shard_count", 1) or 1
        e.add_field(name=f"Shards ({shard_cnt})", value=shorten(shards_str, 1024), inline=False)

        db_path = getattr(getattr(bot, "db", None), "path", "")
        if db_path and db_path != ":memory:" and os.path.exists(db_path):
            try:
                size_mb = os.path.getsize(db_path) / 1_048_576
                e.add_field(name="Database File", value=f"`{db_path}` ({size_mb:.2f} MB)")
            except Exception:
                e.add_field(name="Database File", value=f"`{db_path}`")
        else:
            e.add_field(name="Database", value="In-Memory / Postgres")

        e.add_field(name="Errors (Total Recorded)", value=f"{getattr(bot, '_error_seq', 0)}")
        killswitched = ", ".join(f"`{c}`" for c in sorted(getattr(bot, "killswitch", set()))) or "none"
        e.add_field(name="Kill-switched Commands", value=killswitched)

        maint = getattr(bot, "maintenance", None)
        e.add_field(name="Maintenance Status", value=f"🛠️ Active: {maint}" if maint else "🟢 Normal / Off")

        e.set_footer(text=f"Python {platform.python_version()} • {len(bot.extensions)} extensions • {sum(1 for _ in bot.walk_commands())} commands")
        return e

    async def logs_embed(self) -> discord.Embed:
        bot = self.context.bot
        from core.config import settings
        path = getattr(settings, "log_file", "veil.log")
        tail_lines = []
        if os.path.exists(path):
            try:
                with open(path, "rb") as f:
                    f.seek(0, os.SEEK_END)
                    f.seek(max(0, f.tell() - 50_000))
                    tail_lines = f.read().decode("utf-8", "replace").splitlines()[-18:]
            except Exception as ex:
                tail_lines = [f"Error reading log file: {ex}"]

        log_text = "\n".join(tail_lines) if tail_lines else "(No log entries found or log file empty)"
        if len(log_text) > 3800:
            log_text = log_text[-3800:]

        e = discord.Embed(title="📋 Console Log Stream (Tail)", color=Colors.main)
        if bot.user:
            e.set_author(name=bot.user.name, icon_url=bot.user.display_avatar.url)
        e.description = f"```log\n{log_text}\n```"
        e.set_footer(text=f"Live console tail • {path} • Veil Developers")
        return e

    def secret_embed(self) -> discord.Embed:
        view = SecretHelpView(self.context, help_cmd=self)
        return view.get_embed()

    def tokens_embed(self) -> discord.Embed:
        view = SecretHelpView(self.context, help_cmd=self, initial_category="tokens")
        return view.build_tokens_embed()

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

    async def show_home_interaction(self, it: discord.Interaction):
        categories = category_commands(self.context.bot)
        is_owner = await self.is_owner_check()
        author_rank = await self.get_author_rank()
        view = HelpView(self, categories, None, is_owner=is_owner, author_rank=author_rank)
        embed = self.home_embed()
        if it.response.is_done():
            await it.edit_original_response(embed=embed, view=view)
        else:
            await it.response.edit_message(embed=embed, view=view)

    # ------------------------------------------------------------ entry points
    async def open_menu(self, start: str | None = None):
        categories = category_commands(self.context.bot)
        is_owner = await self.is_owner_check()
        author_rank = await self.get_author_rank()

        if start is not None and start.lower() in ("secret", "tokens", "secrets", "secretsuite") and is_owner:
            view = SecretHelpView(self.context, help_cmd=self, initial_category="tokens" if start.lower() in ("tokens", "secrets") else "all")
            embed = view.get_embed()
            view.message = await self.context.send(embed=embed, view=view)
            return

        if start is not None and start.lower() in ("dev", "developer") and is_owner:
            view = DeveloperView(self, self.context.author.id)
            embed = await self.developer_embed()
            view.message = await self.context.send(embed=embed, view=view)
            return

        if start is not None:
            min_rank = CATEGORY_MIN_RANKS.get(start, 0)
            if not is_owner and author_rank < min_rank:
                return await self.send_error_message(f'No command or category called "{start}" found.')

        view = HelpView(self, categories, start, is_owner=is_owner, author_rank=author_rank)
        if start is None:
            embed = self.home_embed()
        elif start in categories:
            cmds = [
                c for c in categories[start]
                if is_owner or author_rank >= min_rank_for_command(c, self.context.bot)
            ]
            embed = self.category_embed(start, cmds, 0)
        else:
            embed = self.home_embed()
        view.message = await self.context.send(embed=embed, view=view)

    async def command_callback(self, ctx, /, *, command: str | None = None):
        if command:
            wanted = command.lower().strip()
            is_owner = await self.is_owner_check()
            author_rank = await self.get_author_rank()
            for label, _, _ in CATEGORIES:
                if wanted == label.lower():
                    min_rank = CATEGORY_MIN_RANKS.get(label, 0)
                    if not is_owner and author_rank < min_rank:
                        return await self.send_error_message(f'No command or category called "{command}" found.')
                    if not ctx.bot.get_command(wanted):
                        await self.prepare_help_command(ctx, command)
                        return await self.open_menu(label)
            if wanted in ("dev", "developer"):
                if is_owner:
                    await self.prepare_help_command(ctx, command)
                    return await self.open_menu("developer")
                return await self.send_error_message("No command or category called that")
            if wanted in ("secret", "tokens", "secrets", "secretsuite"):
                if is_owner:
                    await self.prepare_help_command(ctx, command)
                    return await self.open_menu(wanted)
                return await self.send_error_message("No command or category called that")
        return await super().command_callback(ctx, command=command)

    async def send_bot_help(self, mapping):
        await self.open_menu(None)

    async def send_cog_help(self, cog):
        label = next((l for l, cogs, _ in CATEGORIES if cog.qualified_name in cogs), None)
        if cog.qualified_name in HIDDEN_COGS:
            return await self.send_error_message("No command or category called that")
        if label:
            min_rank = CATEGORY_MIN_RANKS.get(label, 0)
            is_owner = await self.is_owner_check()
            author_rank = await self.get_author_rank()
            if not is_owner and author_rank < min_rank:
                return await self.send_error_message("No command or category called that")
        await self.open_menu(label)

    def command_embed(self, cmd: commands.Command):
        p = self.context.clean_prefix
        e = discord.Embed(color=Colors.blurple)
        e.title = cmd.qualified_name
        e.description = cmd.help or cmd.short_doc or "No description"
        e.add_field(name="Usage", value=f"```{usage(p, cmd)}```", inline=False)
        if cmd.aliases:
            e.add_field(name="Aliases", value=", ".join(f"`{a}`" for a in cmd.aliases))
        perms = perms_of(cmd)
        if perms:
            e.add_field(name="Permissions", value=", ".join(f"`{x.replace('_', ' ')}`" for x in perms))
        e.set_footer(text="<required> [optional]")
        return e

    async def send_command_help(self, cmd):
        is_owner = await self.is_owner_check()
        if (cmd.hidden or any(p.hidden for p in cmd.parents)) and not is_owner:
            return await self.send_error_message(f'No command called "{cmd.name}" found.')
        if not is_owner:
            author_rank = await self.get_author_rank()
            req_rank = min_rank_for_command(cmd, getattr(self.context, "bot", None))
            if author_rank < req_rank:
                return await self.send_error_message(f'No command called "{cmd.name}" found.')
        await self.context.send(embed=self.command_embed(cmd))

    async def send_group_help(self, group: commands.Group):
        is_owner = await self.is_owner_check()
        if (group.hidden or any(p.hidden for p in group.parents)) and not is_owner:
            return await self.send_error_message(f'No command called "{group.name}" found.')
        author_rank = 0 if is_owner else await self.get_author_rank()
        if not is_owner:
            req_rank = min_rank_for_command(group, getattr(self.context, "bot", None))
            if author_rank < req_rank:
                return await self.send_error_message(f'No command called "{group.name}" found.')
        embed = self.command_embed(group)
        bot = getattr(self.context, "bot", None)
        subs = sorted(
            (c for c in group.walk_commands() if not c.hidden and (is_owner or author_rank >= min_rank_for_command(c, bot))),
            key=lambda c: c.qualified_name
        )
        if subs:
            lines = [f"`{c.qualified_name} {c.signature}`".replace(" `", "`") + f" — {c.short_doc or ''}"
                     for c in subs]
            chunks, cur = [], ""
            for line in lines:
                if len(cur) + len(line) + 1 > 1000:
                    chunks.append(cur)
                    cur = ""
                cur += line + "\n"
            chunks.append(cur)
            for i, c in enumerate(chunks[:5]):
                embed.add_field(name="Subcommands" if i == 0 else "​", value=c, inline=False)
        await self.context.send(embed=embed)

    async def send_error_message(self, error):
        await self.context.send(embed=discord.Embed(color=Colors.warn, description=f"⚠️ {error}"))
