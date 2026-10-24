"""Shared helpers: context, embeds, durations, variables, embed scripting, checks."""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Iterable

import discord
from discord.ext import commands

if TYPE_CHECKING:
    from core.bot import Veil  # noqa: F401  (used in a string type hint)

# ---------------------------------------------------------------- style
class Colors:
    main = 0xB10F2E
    blurple = 0x5865F2
    mint = 0x57F287
    success = 0x43B581
    warn = 0xFAA61A
    error = 0xF04747
    neutral = 0x2B2D31


class E:  # emojis
    ok = "✅"
    warn = "⚠️"
    no = "❌"
    info = "ℹ️"


class UserError(commands.CommandError):
    """Raised for user-facing problems; shown as a warning embed."""


class Ignored(commands.CheckFailure):
    """Silently swallowed (ignored channels/users)."""


# ---------------------------------------------------------------- context
class Context(commands.Context["Veil"]):
    async def send(self, *args, **kwargs):
        """Everything the bot sends replies to the invoking message (without pinging), so busy
        channels stay readable. Never pings: the bot's allowed_mentions has replied_user off.
        If the original message was deleted, Discord sends a plain message instead."""
        kwargs.setdefault("reference", self.message.to_reference(fail_if_not_exists=False))
        try:
            return await self.channel.send(*args, **kwargs)
        except discord.HTTPException:
            kwargs.pop("reference", None)
            return await self.channel.send(*args, **kwargs)

    async def _footer(self) -> str | None:
        if self.guild is None:
            return None
        from core import perms
        cmd = self.command.qualified_name if self.command else ""
        seg = f"{self.clean_prefix}{cmd}".rstrip()
        try:
            cfg = await self.bot.db.get_config(self.guild.id, "permlevels")
            is_dev = await self.bot.is_owner(self.author)
            is_owner = self.author.id == self.guild.owner_id
            lab = perms.label(cfg, self.author, is_owner=is_owner, is_dev=is_dev)
        except Exception:
            lab = None
        return f"{seg} · {lab}" if lab else (seg or None)

    async def _styled(self, color: int, text: str, **kw):
        embed = discord.Embed(description=text, color=color)
        if self.bot.user:
            embed.set_author(name=self.bot.user.display_name, icon_url=self.bot.user.display_avatar.url)
        footer = await self._footer()
        if footer:
            embed.set_footer(text=footer)
            embed.timestamp = discord.utils.utcnow()
        return await self.send(embed=embed, **kw)

    async def approve(self, text: str, **kw):
        return await self._styled(Colors.success, text, **kw)

    async def warn(self, text: str, **kw):
        return await self._styled(Colors.warn, text, **kw)

    async def deny(self, text: str, **kw):
        return await self._styled(Colors.error, text, **kw)

    async def neutral(self, text: str, **kw):
        return await self._styled(Colors.neutral, text, **kw)

    async def paginate(self, embeds: list[discord.Embed]):
        if not embeds:
            return await self.warn("Nothing to show")
        if len(embeds) == 1:
            return await self.send(embed=embeds[0])
        for i, e in enumerate(embeds, 1):
            footer = f"Page {i}/{len(embeds)}"
            e.set_footer(text=f"{e.footer.text} • {footer}" if e.footer and e.footer.text else footer)
        view = Paginator(self.author.id, embeds)
        view.message = await self.send(embed=embeds[0], view=view)
        return view.message

    async def confirm(self, text: str, timeout: float = 30) -> bool:
        view = Confirm(self.author.id, timeout=timeout)
        msg = await self.warn(text, view=view)
        await view.wait()
        try:
            await msg.delete()
        except discord.HTTPException:
            pass
        return bool(view.value)


class OwnedView(discord.ui.View):
    """A button menu only the person who ran the command can use.

    Everyone else gets a private "this isn't yours" message. When `allow_developers` is on, the bot's
    developers (OWNER_IDS) can also use it, e.g. to help someone navigate a menu.
    """

    def __init__(self, author_id: int, *, timeout: float | None = 180, allow_developers: bool = True):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.allow_developers = allow_developers

    async def interaction_check(self, it: discord.Interaction) -> bool:
        if it.user.id == self.author_id:
            return True
        if self.allow_developers and await it.client.is_owner(it.user):
            return True
        await it.response.send_message(
            f"🔒 Only <@{self.author_id}> can use these buttons. Run the command yourself to get your own.",
            ephemeral=True)
        return False


class Paginator(OwnedView):
    def __init__(self, author_id: int, embeds: list[discord.Embed]):
        super().__init__(author_id, timeout=180)
        self.embeds, self.index = embeds, 0
        self.message: discord.Message | None = None

    async def _go(self, it: discord.Interaction, i: int):
        self.index = i % len(self.embeds)
        await it.response.edit_message(embed=self.embeds[self.index], view=self)

    @discord.ui.button(emoji="⏪", style=discord.ButtonStyle.secondary)
    async def first(self, it, _):
        await self._go(it, 0)

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.primary)
    async def prev(self, it, _):
        await self._go(it, self.index - 1)

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.primary)
    async def next(self, it, _):
        await self._go(it, self.index + 1)

    @discord.ui.button(emoji="⏩", style=discord.ButtonStyle.secondary)
    async def last(self, it, _):
        await self._go(it, len(self.embeds) - 1)

    @discord.ui.button(emoji="🗑️", style=discord.ButtonStyle.danger)
    async def close(self, it: discord.Interaction, _):
        await it.response.defer()
        await it.delete_original_response()
        self.stop()

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass


class Confirm(OwnedView):
    # confirmations guard destructive actions (nuke, massban, resets), so only the person who ran it may answer
    def __init__(self, author_id: int, timeout: float = 30):
        super().__init__(author_id, timeout=timeout, allow_developers=False)
        self.value = None

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def yes(self, it, _):
        self.value = True
        await it.response.defer()
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def no(self, it, _):
        self.value = False
        await it.response.defer()
        self.stop()


def chunk_embeds(title: str, lines: list[str], per_page: int = 10, color: int = Colors.main,
                 numbered: bool = True) -> list[discord.Embed]:
    pages = []
    for start in range(0, len(lines), per_page):
        chunk = lines[start:start + per_page]
        body = "\n".join(f"`{start + i + 1}` {l}" if numbered else l for i, l in enumerate(chunk))
        pages.append(discord.Embed(title=title, description=body, color=color))
    return pages


# ---------------------------------------------------------------- durations
_DUR = re.compile(r"(\d+)\s*(w|d|h|m|s)", re.I)
_UNITS = {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}


MAX_DURATION = 5 * 365 * 86400


def parse_duration(text: str) -> timedelta:
    total = sum(int(n) * _UNITS[u.lower()] for n, u in _DUR.findall(text or ""))
    if total <= 0:
        raise UserError(f"`{text}` isn't a valid duration (try `10m`, `2h30m`, `7d`)")
    if total > MAX_DURATION:  # timedelta overflows on absurd values
        raise UserError("That duration is too long (5 years max)")
    return timedelta(seconds=total)


class Duration(commands.Converter):
    async def convert(self, ctx, argument) -> timedelta:
        if not _DUR.fullmatch(argument.replace(" ", "")) and not re.fullmatch(r"(\d+\s*[wdhms])+", argument, re.I):
            raise commands.BadArgument(f"`{argument}` isn't a valid duration (try `10m`, `2h30m`, `7d`)")
        return parse_duration(argument)


def fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    parts = []
    for name, size in (("day", 86400), ("hour", 3600), ("minute", 60), ("second", 1)):
        n, seconds = divmod(seconds, size)
        if n:
            parts.append(f"{n} {name}{'s' if n != 1 else ''}")
    return ", ".join(parts[:3]) or "0 seconds"


def ordinal(n: int) -> str:
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def ts(dt: datetime | float, style: str = "R") -> str:
    if isinstance(dt, (int, float)):
        return f"<t:{int(dt)}:{style}>"
    return discord.utils.format_dt(dt, style)


def now() -> float:
    return time.time()


# ---------------------------------------------------------------- variables
_VAR = re.compile(r"\{([a-zA-Z_.]+)\}")


class _Vars(dict):
    """Variables dict whose expensive entries are computed on first lookup."""

    def __init__(self):
        super().__init__()
        self.lazy: dict[str, callable] = {}

    def get(self, key, default=None):
        if key not in self and key in self.lazy:
            self[key] = self.lazy.pop(key)()
        return super().get(key, default)


def _join_position(member: discord.Member) -> int:
    return sum(1 for m in member.guild.members if m.joined_at and m.joined_at <= member.joined_at)


def build_vars(member: discord.Member | discord.User | None = None, guild: discord.Guild | None = None,
               channel: discord.abc.GuildChannel | None = None, extra: dict | None = None) -> dict[str, str]:
    v = _Vars()
    n = datetime.now(timezone.utc)
    v["date"] = n.strftime("%B %d, %Y")
    v["time"] = n.strftime("%H:%M UTC")
    if member:
        v.update({
            "user": str(member), "user.mention": member.mention, "user.name": member.name,
            "user.display_name": member.display_name, "user.id": str(member.id),
            "user.avatar": member.display_avatar.url,
            "user.created_at": ts(member.created_at, "R"),
            "user.bot": "Yes" if member.bot else "No",
        })
        if isinstance(member, discord.Member):
            v["user.joined_at"] = ts(member.joined_at, "R") if member.joined_at else "unknown"
            v["user.top_role"] = member.top_role.name
            if member.guild and member.joined_at:
                # O(members) — only computed if a template actually uses it (sticky messages run on every message)
                v.lazy["user.join_position"] = lambda: str(_join_position(member))
                v.lazy["user.join_position.format"] = lambda: ordinal(_join_position(member))
    if guild:
        v.update({
            "guild.name": guild.name, "guild.id": str(guild.id),
            "guild.count": str(guild.member_count), "guild.count.format": ordinal(guild.member_count or 0),
            "guild.icon": guild.icon.url if guild.icon else "",
            "guild.banner": guild.banner.url if guild.banner else "",
            "guild.boost_count": str(guild.premium_subscription_count),
            "guild.boost_tier": str(guild.premium_tier),
            "guild.owner": str(guild.owner) if guild.owner else "",
            "guild.vanity": guild.vanity_url_code or "",
        })
    if channel:
        v.update({"channel.name": channel.name, "channel.mention": channel.mention, "channel.id": str(channel.id)})
    if extra:
        v.update(extra)
    return v


def replace_vars(text: str, variables: dict[str, str]) -> str:
    return _VAR.sub(lambda m: variables.get(m.group(1).lower(), m.group(0)), text)


# ---------------------------------------------------------------- embed scripting
def parse_color(value: str) -> int:
    value = value.strip().lower().lstrip("#")
    named = {"red": 0xF04747, "green": 0x43B581, "blue": 0x5865F2, "yellow": 0xFAA61A, "black": 0x010101,
             "white": 0xFFFFFF, "purple": 0x9B59B6, "pink": 0xFF73FA, "orange": 0xE67E22, "main": Colors.main,
             "invisible": Colors.neutral}
    if value in named:
        return named[value]
    if value.startswith("0x"):
        value = value[2:]
    if len(value) == 3 and all(c in "0123456789abcdef" for c in value):
        value = "".join(c * 2 for c in value)  # #f0a -> #ff00aa
    if not re.fullmatch(r"[0-9a-f]{1,6}", value):
        return Colors.main
    return int(value, 16)


def build_message(script: str, variables: dict[str, str] | None = None) -> dict:
    """Turn a script into send() kwargs.

    Plain text is sent as-is. Scripts starting with {embed} use `$v` separated blocks:
    {embed}$v{title: Hi}$v{description: Welcome {user.mention}}$v{color: #ff0000}
    $v{author: name && icon && url}$v{footer: text && icon}$v{image: url}$v{thumbnail: url}
    $v{field: name && value && inline}$v{button: label && url}$v{content: text}$v{timestamp}
    """
    variables = variables or {}
    if not script.lower().startswith("{embed}"):
        return {"content": replace_vars(script, variables)[:2000]}

    # Parse the script's structure *before* filling in variables. Otherwise a member whose name contains
    # `}$v{button: ...` or `&&` could inject their own blocks (links, images, buttons) into bot messages.
    embed = discord.Embed(color=Colors.main)
    content, view, has_embed = None, None, False
    for part in script[len("{embed}"):].split("$v"):
        part = part.strip()
        if not (part.startswith("{") and part.endswith("}")):
            continue
        key, _, raw_val = part[1:-1].partition(":")
        key = key.strip().lower()
        val = replace_vars(raw_val.strip(), variables)
        args = [replace_vars(a.strip(), variables) for a in raw_val.split("&&")]
        if key == "content":
            content = val
        elif key == "title":
            embed.title, has_embed = val[:256], True
        elif key == "description":
            embed.description, has_embed = val[:4096], True
        elif key == "url":
            embed.url = val
        elif key == "color":
            embed.color = parse_color(val)
        elif key == "image":
            embed.set_image(url=val or None); has_embed = True
        elif key == "thumbnail":
            embed.set_thumbnail(url=val or None); has_embed = True
        elif key == "timestamp":
            embed.timestamp = discord.utils.utcnow()
        elif key == "footer":
            embed.set_footer(text=args[0][:2048], icon_url=args[1] if len(args) > 1 and args[1] else None)
            has_embed = True
        elif key == "author":
            embed.set_author(name=args[0][:256], icon_url=args[1] if len(args) > 1 and args[1] else None,
                             url=args[2] if len(args) > 2 and args[2] else None)
            has_embed = True
        elif key == "field" and len(args) >= 2:
            inline = len(args) < 3 or args[2].lower() not in ("false", "no", "0")
            embed.add_field(name=args[0][:256], value=args[1][:1024], inline=inline); has_embed = True
        elif key == "button" and len(args) >= 2 and args[1].startswith("http"):
            view = view or discord.ui.View()
            view.add_item(discord.ui.Button(label=args[0][:80], url=args[1]))
    out: dict = {"content": content}
    if has_embed:
        out["embed"] = embed
    if view:
        out["view"] = view
    if not content and not has_embed:
        out["content"] = "\u200b"
    return out


async def send_script(dest: discord.abc.Messageable, script: str, variables: dict | None = None, **kw):
    payload = build_message(script, variables)
    payload.update(kw)
    payload.setdefault("allowed_mentions", discord.AllowedMentions(users=True, roles=False, everyone=False))
    return await dest.send(**payload)


# ---------------------------------------------------------------- checks
async def missing_perms(ctx: Context, **perms: bool) -> list[str]:
    """Permissions the author lacks, after counting owner, admin, fake permissions and perm levels."""
    if await ctx.bot.is_owner(ctx.author):
        return []
    p = ctx.channel.permissions_for(ctx.author)
    if p.administrator:
        return []
    missing = [k for k, v in perms.items() if getattr(p, k, None) != v]
    if not missing:
        return []
    fake = await ctx.bot.db.get_config(ctx.guild.id, "fakeperms")
    granted: set[str] = set()
    for role in ctx.author.roles:
        granted.update(fake.get(str(role.id), []))
    from core import perms as _perms
    granted.update(_perms.level_perms(await ctx.bot.db.get_config(ctx.guild.id, "permlevels"), ctx.author))
    if "administrator" in granted:
        return []
    return [m for m in missing if m not in granted]


def has_perms(**perms: bool):
    """Like has_permissions, but also honours roles granted *fake permissions* via the bot."""

    async def predicate(ctx: Context) -> bool:
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        still = await missing_perms(ctx, **perms)
        if still:
            raise commands.MissingPermissions(still)
        return True

    pred = commands.check(predicate)

    def deco(func):
        func = pred(func)
        # stash for the help command
        extras = getattr(func, "__veil_perms__", [])
        func.__veil_perms__ = extras + list(perms)
        return func

    return deco


def is_developer():
    async def predicate(ctx: Context):
        if await ctx.bot.is_owner(ctx.author):
            return True
        raise UserError("This command is restricted to the bot's developers")
    return commands.check(predicate)


def is_server_owner():
    async def predicate(ctx: Context):
        if ctx.guild and (ctx.author.id == ctx.guild.owner_id or await ctx.bot.is_owner(ctx.author)):
            return True
        raise UserError("Only the **server owner** can use this command")
    return commands.check(predicate)


async def antinuke_admin(ctx: Context) -> bool:
    if ctx.guild is None:  # subcommands don't inherit the group's guild_only() when invoke_without_command=True
        raise commands.NoPrivateMessage()
    if ctx.author.id == ctx.guild.owner_id or await ctx.bot.is_owner(ctx.author):
        return True
    cfg = await ctx.bot.db.get_config(ctx.guild.id, "antinuke")
    if ctx.author.id in cfg.get("admins", []):
        return True
    raise UserError("Only the **server owner** or an **antinuke admin** can use this")


async def antinuke_guard(ctx: Context, module: str, amount: int = 1) -> None:
    """Call right before a destructive action done through the bot (see Antinuke.guard). Raises UserError to block."""
    cog = ctx.bot.get_cog("Antinuke")
    if cog is not None and ctx.guild is not None:
        await cog.guard(ctx, module, amount)


def check_hierarchy(ctx: Context, target: discord.Member, action: str = "moderate") -> None:
    if target.id == ctx.author.id:
        raise UserError(f"You can't {action} yourself")
    if target.id == ctx.guild.owner_id:
        raise UserError(f"You can't {action} the server owner")
    if ctx.bot.owner_ids and target.id in ctx.bot.owner_ids:
        raise UserError(f"You can't {action} a bot developer")
    if target == ctx.guild.me:
        raise UserError(f"I can't {action} myself")
    if ctx.author.id != ctx.guild.owner_id and target.top_role >= ctx.author.top_role:
        raise UserError(f"You can't {action} {target.mention}, their top role is equal or higher than yours")
    if target.top_role >= ctx.guild.me.top_role:
        raise UserError(f"I can't {action} {target.mention}, their top role is above mine")


def check_role(ctx: Context, role: discord.Role) -> None:
    if role.is_default() or role.managed:
        raise UserError(f"{role.mention} can't be assigned")
    if role >= ctx.guild.me.top_role:
        raise UserError(f"{role.mention} is above my top role")
    if ctx.author.id != ctx.guild.owner_id and role >= ctx.author.top_role:
        raise UserError(f"{role.mention} is above or equal to your top role")


DANGEROUS_PERMS = (
    "administrator", "ban_members", "kick_members", "manage_guild", "manage_roles",
    "manage_channels", "manage_webhooks", "mention_everyone", "manage_expressions",
)


def is_dangerous(perms: discord.Permissions) -> bool:
    return any(getattr(perms, p, False) for p in DANGEROUS_PERMS)


def shorten(text: str, n: int = 1024) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def human_join(items: Iterable[str], conj: str = "and") -> str:
    items = list(items)
    if len(items) <= 2:
        return f" {conj} ".join(items)
    return ", ".join(items[:-1]) + f", {conj} {items[-1]}"
