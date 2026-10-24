"""Help menu styled after elite Discord utility bots: clean category cards, back navigation, and no dev clutter."""
from __future__ import annotations

import math
import time

import discord
from discord.ext import commands

from core.utils import Colors, OwnedView, fmt_duration

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


class CategoryButton(discord.ui.Button["HelpView"]):
    def __init__(self, label: str, row: int):
        super().__init__(label=label, style=discord.ButtonStyle.secondary, row=row)

    async def callback(self, it: discord.Interaction):
        await self.view.show_category(it, self.label)


class BackButton(discord.ui.Button["HelpView"]):
    def __init__(self, row: int = 0):
        super().__init__(label="Back", style=discord.ButtonStyle.secondary, row=row)

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
    def __init__(self, help_cmd: "Help", categories: dict[str, list[commands.Command]], start: str | None = None):
        super().__init__(help_cmd.context.author.id, timeout=180)
        self.help_cmd, self.categories = help_cmd, categories
        self.current = start
        self.page = 0
        self.message: discord.Message | None = None
        self._build_items()

    def _build_items(self):
        self.clear_items()
        if self.current is None:
            # Home overview: show 2 rows of clean category buttons
            labels = [c[0] for c in CATEGORIES if c[0] in self.categories]
            for i, label in enumerate(labels[:10]):
                self.add_item(CategoryButton(label, row=i // 5))
        else:
            # Category view: Back button + pagination if needed
            cmds = self.categories.get(self.current, [])
            total_pages = max(1, math.ceil(len(cmds) / PAGE_SIZE))
            self.add_item(BackButton(row=0))
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
        await it.response.edit_message(embed=embed, view=self)

    async def show_category(self, it: discord.Interaction, label: str):
        self.current = label
        self.page = 0
        self._build_items()
        embed = self.help_cmd.category_embed(label, self.categories.get(label, []), self.page)
        await it.response.edit_message(embed=embed, view=self)

    async def change_page(self, it: discord.Interaction, delta: int):
        cmds = self.categories.get(self.current, [])
        total_pages = max(1, math.ceil(len(cmds) / PAGE_SIZE))
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


class Help(commands.HelpCommand):
    def __init__(self):
        super().__init__(command_attrs={"aliases": ["h", "commands"], "help": "Show help for a command or category"})

    # ------------------------------------------------------------ embeds
    def home_embed(self) -> discord.Embed:
        bot = self.context.bot
        p = self.context.clean_prefix
        e = discord.Embed(color=Colors.blurple)
        if bot.user:
            e.set_author(name=bot.user.name, icon_url=bot.user.display_avatar.url)
        e.description = f"Select a category below to view its commands.\nprefix: {p}"
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
        except discord.HTTPException:
            devs = set()
        e = discord.Embed(title="Developer", color=Colors.main)
        if bot.user:
            e.set_author(name=bot.user.name, icon_url=bot.user.display_avatar.url)
        e.description = "Built and maintained by:\n" + ("\n".join(f"• <@{d}>" for d in devs) or "• unknown")
        e.add_field(name="Servers", value=f"{len(bot.guilds):,}")
        e.add_field(name="Users", value=f"{sum(g.member_count or 0 for g in bot.guilds):,}")
        e.add_field(name="Commands", value=str(sum(1 for _ in bot.walk_commands())))
        e.add_field(name="Uptime", value=fmt_duration(time.time() - getattr(bot, "started_at", time.time())))
        e.add_field(name="Latency", value=f"{bot.latency * 1000:.0f}ms" if bot.latency == bot.latency else "—")
        e.add_field(name="Library", value=f"discord.py {discord.__version__}")
        e.set_footer(text="This server must be authorized by a developer to use the bot")
        return e

    def secret_embed(self) -> discord.Embed:
        p = self.context.clean_prefix
        cmds = secret_commands(self.context.bot)
        e = discord.Embed(title="🔒 Secret Commands", color=Colors.error)
        lines = [f"`{p}{c.name}` - {c.short_doc or 'developer tool'}" for c in cmds]
        e.description = "\n".join(lines) or "No secret commands."
        e.set_footer(text=f"{len(cmds)} commands • developers only")
        return e

    # ------------------------------------------------------------ entry points
    async def open_menu(self, start: str | None = None):
        categories = category_commands(self.context.bot)
        view = HelpView(self, categories, start)
        if start is None:
            embed = self.home_embed()
        elif start in categories:
            embed = self.category_embed(start, categories[start], 0)
        elif start.lower() in ("dev", "developer") and await self.context.bot.is_owner(self.context.author):
            embed = await self.developer_embed()
            view.clear_items()
            view.add_item(BackButton(row=0))
        elif start.lower() == "secret" and await self.context.bot.is_owner(self.context.author):
            embed = self.secret_embed()
            view.clear_items()
            view.add_item(BackButton(row=0))
        else:
            embed = self.home_embed()
        view.message = await self.context.send(embed=embed, view=view)

    async def command_callback(self, ctx, /, *, command: str | None = None):
        if command:
            wanted = command.lower().strip()
            for label, _, _ in CATEGORIES:
                if wanted == label.lower():
                    if not ctx.bot.get_command(wanted):
                        await self.prepare_help_command(ctx, command)
                        return await self.open_menu(label)
            if wanted in ("dev", "developer"):
                if await ctx.bot.is_owner(ctx.author):
                    await self.prepare_help_command(ctx, command)
                    return await self.open_menu("developer")
                return await self.send_error_message("No command or category called that")
            if wanted == "secret":
                if await ctx.bot.is_owner(ctx.author):
                    await self.prepare_help_command(ctx, command)
                    return await self.open_menu("secret")
                return await self.send_error_message("No command or category called that")
        return await super().command_callback(ctx, command=command)

    async def send_bot_help(self, mapping):
        await self.open_menu(None)

    async def send_cog_help(self, cog):
        label = next((l for l, cogs, _ in CATEGORIES if cog.qualified_name in cogs), None)
        if cog.qualified_name in HIDDEN_COGS:
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
        if (cmd.hidden or any(p.hidden for p in cmd.parents)) \
                and not await self.context.bot.is_owner(self.context.author):
            return await self.send_error_message(f'No command called "{cmd.name}" found.')
        await self.context.send(embed=self.command_embed(cmd))

    async def send_group_help(self, group: commands.Group):
        if (group.hidden or any(p.hidden for p in group.parents)) \
                and not await self.context.bot.is_owner(self.context.author):
            return await self.send_error_message(f'No command called "{group.name}" found.')
        embed = self.command_embed(group)
        subs = sorted((c for c in group.walk_commands() if not c.hidden), key=lambda c: c.qualified_name)
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
