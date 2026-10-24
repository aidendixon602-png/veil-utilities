"""Help menu: category buttons (Utility, Music, Moderation, Roles, …) with a developer-only Secret category."""
from __future__ import annotations

import time

import discord
from discord.ext import commands

from core.utils import Colors, OwnedView, fmt_duration

HIDDEN_COGS = {"Owner", "Events", "Authorization"}

# (button label, cogs included, extra top-level commands pulled in from other cogs)
CATEGORIES: list[tuple[str, list[str], list[str]]] = [
    ("Utility", ["Utility"], []),
    ("Music", ["Music", "Spotify"], []),
    ("Moderation", ["Moderation", "Logging"], []),
    ("Roles", [], ["role", "autorole", "reactionrole", "buttonrole", "vanity", "stripstaff", "roles",
                   "roleblacklist", "addpermall", "removepermall"]),
    ("Permissions", ["Permissions"], ["fakepermissions", "ignore", "disablecommand", "enablecommand", "modlog"]),
    ("Antinuke", ["Antinuke"], []),
    ("Automod", ["Automod"], []),
    ("Server", ["Config", "Settings"], []),
    ("VoiceMaster", ["VoiceMaster"], []),
    ("Community", ["Levels", "Giveaways", "Tickets"], []),
    ("Social", ["Feeds", "Integrations"], []),
    ("Fun", ["Fun"], []),
    ("Images & AI", ["Imaging", "AI"], []),
]
DEV_LABEL, SECRET_LABEL = "Developer", "Secret"


def perms_of(cmd: commands.Command) -> list[str]:
    perms = list(getattr(cmd.callback, "__veil_perms__", []))
    for check in cmd.checks:
        # discord.py has_permissions closures keep the dict in their cell vars
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
    # anything new that isn't mapped still shows up
    leftovers = [c for c in bot.commands if not c.hidden and c.qualified_name not in placed
                 and c.cog is not None and c.cog_name not in HIDDEN_COGS]
    if leftovers:
        out["Other"] = sorted(leftovers, key=lambda c: c.name)
    return out


def secret_commands(bot: commands.Bot) -> list[commands.Command]:
    cmds = []
    for name in HIDDEN_COGS:
        cog = bot.get_cog(name)
        if cog:
            cmds += cog.get_commands()
    return sorted(cmds, key=lambda c: c.name)


class CategoryButton(discord.ui.Button["HelpView"]):
    def __init__(self, label: str, style: discord.ButtonStyle, row: int):
        super().__init__(label=label, style=style, row=row)
        self.base_style = style

    async def callback(self, it: discord.Interaction):
        view = self.view
        if self.label == SECRET_LABEL and not await it.client.is_owner(it.user):
            return await it.response.send_message("🔒 The Secret category is only for the bot's developers.",
                                                  ephemeral=True)
        target = None if view.current == self.label else self.label  # click again → back home
        await view.show(it, target)


class HelpView(OwnedView):
    def __init__(self, help_cmd: "Help", categories: dict[str, list[commands.Command]], start: str | None = None):
        super().__init__(help_cmd.context.author.id, timeout=180)
        self.help_cmd, self.categories, self.current = help_cmd, categories, start
        self.message: discord.Message | None = None
        labels = list(categories) + [DEV_LABEL, SECRET_LABEL]
        for i, label in enumerate(labels[:25]):
            style = discord.ButtonStyle.danger if label == SECRET_LABEL else discord.ButtonStyle.secondary
            self.add_item(CategoryButton(label, style, row=i // 5))
        self._highlight()

    def _highlight(self):
        for b in self.children:
            if isinstance(b, CategoryButton):
                b.style = discord.ButtonStyle.primary if b.label == self.current and b.label != SECRET_LABEL \
                    else b.base_style

    async def show(self, it: discord.Interaction, label: str | None):
        self.current = label
        self._highlight()
        embed = await self.help_cmd.embed_for(label, self.categories)
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
    def _base(self) -> discord.Embed:
        bot = self.context.bot
        e = discord.Embed(color=Colors.main)
        if bot.user:
            e.set_author(name=bot.user.name, icon_url=bot.user.display_avatar.url)
        return e

    def home_embed(self) -> discord.Embed:
        e = self._base()
        e.description = (f"Select a category below to view its commands.\n\n"
                         f"**prefix:** `{self.context.clean_prefix}`")
        return e

    def list_embed(self, label: str, cmds: list[commands.Command], secret: bool = False) -> discord.Embed:
        p = self.context.clean_prefix
        e = self._base()
        e.color = Colors.error if secret else Colors.main
        def visible_subs(c):
            return [x for x in c.walk_commands() if secret or not x.hidden] if isinstance(c, commands.Group) else []

        expand = sum(1 + len(visible_subs(c)) for c in cmds) <= 40
        lines = []
        for c in cmds:
            subs = visible_subs(c)
            if expand and subs:
                lines.append(f"`{c.name}` — {c.short_doc or 'No description'}")
                lines += [f"\u2003`{x.qualified_name}` — {x.short_doc or ''}" for x in sorted(subs, key=lambda x: x.qualified_name)]
            else:
                extra = f" *(+{len(subs)})*" if subs else ""
                lines.append(f"`{c.name}`{extra} — {c.short_doc or 'No description'}")
        body = "\n".join(lines)
        if len(body) > 4000:
            body = body[:3990].rsplit("\n", 1)[0] + "\n…"
        e.title = f"🔒 {label}" if secret else label
        e.description = body or "No commands."
        total = sum(1 + len(list(c.walk_commands())) if isinstance(c, commands.Group) else 1 for c in cmds)
        e.set_footer(text=f"{total} command{'s' if total != 1 else ''} • {p}help <command> for details"
                     + (" • developers only" if secret else ""))
        return e

    async def developer_embed(self) -> discord.Embed:
        bot = self.context.bot
        from cogs.authorization import developers  # local import to avoid a cycle at startup
        try:
            devs = await developers(bot)
        except discord.HTTPException:
            devs = set()
        e = self._base()
        e.title = "Developer"
        e.description = "Built and maintained by:\n" + ("\n".join(f"• <@{d}>" for d in devs) or "• unknown")
        e.add_field(name="Servers", value=f"{len(bot.guilds):,}")
        e.add_field(name="Users", value=f"{sum(g.member_count or 0 for g in bot.guilds):,}")
        e.add_field(name="Commands", value=str(sum(1 for _ in bot.walk_commands())))
        e.add_field(name="Uptime", value=fmt_duration(time.time() - getattr(bot, "started_at", time.time())))
        e.add_field(name="Latency", value=f"{bot.latency * 1000:.0f}ms" if bot.latency == bot.latency else "—")
        e.add_field(name="Library", value=f"discord.py {discord.__version__}")
        e.set_footer(text="This server must be authorized by a developer to use the bot")
        return e

    async def embed_for(self, label: str | None, categories: dict) -> discord.Embed:
        if label is None:
            return self.home_embed()
        if label == DEV_LABEL:
            return await self.developer_embed()
        if label == SECRET_LABEL:
            return self.list_embed("Secret", secret_commands(self.context.bot), secret=True)
        return self.list_embed(label, categories.get(label, []))

    # ------------------------------------------------------------ entry points
    async def open_menu(self, start: str | None = None):
        categories = category_commands(self.context.bot)
        view = HelpView(self, categories, start)
        view.message = await self.get_destination().send(embed=await self.embed_for(start, categories), view=view)

    async def command_callback(self, ctx, /, *, command: str | None = None):
        # `,help moderation` / `,help images & ai` opens the menu on that category
        if command:
            wanted = command.lower().strip()
            for label in [c[0] for c in CATEGORIES] + [DEV_LABEL, "Other"]:
                if wanted == label.lower() or wanted == label.lower().split()[0]:
                    if not ctx.bot.get_command(wanted):
                        await self.prepare_help_command(ctx, command)
                        return await self.open_menu(label)
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
        embed = self._base()
        embed.title = cmd.qualified_name
        embed.description = cmd.help or cmd.short_doc or "No description"
        embed.add_field(name="Usage", value=f"```{usage(p, cmd)}```", inline=False)
        if cmd.aliases:
            embed.add_field(name="Aliases", value=", ".join(f"`{a}`" for a in cmd.aliases))
        perms = perms_of(cmd)
        if perms:
            embed.add_field(name="Permissions", value=", ".join(f"`{x.replace('_', ' ')}`" for x in perms))
        embed.set_footer(text="<required> [optional]")
        return embed

    async def send_command_help(self, cmd):
        if cmd.hidden and not await self.context.bot.is_owner(self.context.author):
            return await self.send_error_message(f'No command called "{cmd.name}" found.')
        await self.get_destination().send(embed=self.command_embed(cmd))

    async def send_group_help(self, group: commands.Group):
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
        await self.get_destination().send(embed=embed)

    async def send_error_message(self, error):
        await self.get_destination().send(embed=discord.Embed(color=Colors.warn, description=f"⚠️ {error}"))
