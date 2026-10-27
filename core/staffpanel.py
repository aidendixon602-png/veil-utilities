"""`,mod @member` and `,admin [@member]`: button panels that run the real moderation commands.

Every button runs the actual command (`warn`, `timeout`, `ban`...) through its own checks first, so a panel can never
do more than its user could by typing the command: permission levels, fake permissions, hierarchy, the role
blacklist, the antinuke's limits and the command logs all apply exactly as usual. Buttons a person can't use are
greyed out when the panel opens. Actions that need a reason ask for one in a small form.
"""
from __future__ import annotations

import discord
from discord.ext import commands

from core.utils import Colors, Context, OwnedView, UserError, ts

# (label, command, style, asks for a reason, extra form field)
MOD_ACTIONS = [
    ("Warn", "warn", discord.ButtonStyle.secondary, True, None),
    ("Timeout", "timeout", discord.ButtonStyle.secondary, True, "duration"),
    ("Untimeout", "untimeout", discord.ButtonStyle.secondary, False, None),
    ("Kick", "kick", discord.ButtonStyle.danger, True, None),
    ("Jail", "jail", discord.ButtonStyle.secondary, True, None),
    ("Unjail", "unjail", discord.ButtonStyle.secondary, False, None),
    ("Warnings", "warnings", discord.ButtonStyle.secondary, False, None),
]
ADMIN_MEMBER_ACTIONS = [
    ("Ban", "ban", discord.ButtonStyle.danger, True, None),
    ("Softban", "softban", discord.ButtonStyle.danger, True, None),
    ("Strip staff", "stripstaff", discord.ButtonStyle.secondary, False, None),
    ("Quarantine", "quarantine", discord.ButtonStyle.secondary, True, None),
]
ADMIN_SERVER_ACTIONS = [
    ("Lockdown", "lockdown", discord.ButtonStyle.danger, False, None),
    ("Unlockdown", "unlockdown", discord.ButtonStyle.secondary, False, None),
    ("Raid mode on", "antiraid start", discord.ButtonStyle.danger, False, None),
    ("Raid mode off", "antiraid end", discord.ButtonStyle.secondary, False, None),
    ("Panic", "antinuke panic", discord.ButtonStyle.danger, False, None),
]
MEMBER_PARAM = {"ban": "user"}  # the commands' own name for their member argument, where it isn't `member`


async def can_use(ctx: Context, name: str) -> bool:
    """Whether this person passes every check of a command (levels, permissions, cooldowns...)."""
    cmd = ctx.bot.get_command(name)
    if cmd is None:
        return False
    try:
        return await cmd.can_run(ctx)
    except (commands.CommandError, UserError):
        return False
    except Exception:
        return False


async def run_command(ctx: Context, name: str, **kwargs) -> None:
    """Run a command for the panel's owner exactly as if they had typed it: checks first, then the command, with
    `ctx.command` set so its logs, cases and antinuke limits name the right command."""
    cmd = ctx.bot.get_command(name)
    if cmd is None:
        raise UserError(f"`{name}` isn't available right now")
    if not await cmd.can_run(ctx):
        raise UserError(f"you can't use `{name}`")
    previous = ctx.command
    ctx.command = cmd
    try:
        await ctx.invoke(cmd, **kwargs)
        ctx.bot.dispatch("command_completion", ctx)  # the command log shows what the button did
    finally:
        ctx.command = previous


class ReasonForm(discord.ui.Modal):
    """The small form a button opens when its command takes a reason (and, for a timeout, how long)."""

    def __init__(self, panel: "StaffPanel", label: str, name: str, extra: str | None):
        super().__init__(title=f"{label} {panel.member}" if panel.member else label, timeout=120)
        self.panel, self.name = panel, name
        self.duration = None
        if extra == "duration":
            self.duration = discord.ui.TextInput(label="How long", placeholder="10m, 1h, 1d (default 5m)",
                                                 required=False, max_length=10)
            self.add_item(self.duration)
        self.reason = discord.ui.TextInput(label="Reason", placeholder="optional", required=False, max_length=400,
                                           style=discord.TextStyle.paragraph)
        self.add_item(self.reason)

    async def on_submit(self, it: discord.Interaction):
        kwargs = {}
        if self.reason.value.strip():
            kwargs["reason"] = self.reason.value.strip()
        if self.duration is not None and self.duration.value.strip():
            from core.utils import Duration
            try:
                kwargs["duration"] = await Duration().convert(self.panel.ctx, self.duration.value.strip())
            except Exception:
                return await it.response.send_message("⚠️ That length isn't valid. Try `10m`, `1h` or `1d`.",
                                                      ephemeral=True)
        await self.panel.act(it, self.name, kwargs)


class ActionButton(discord.ui.Button["StaffPanel"]):
    def __init__(self, label: str, name: str, style, asks: bool, extra: str | None, allowed: bool, row: int):
        super().__init__(label=label, style=style, disabled=not allowed, row=row)
        self.name, self.asks, self.extra = name, asks, extra

    async def callback(self, it: discord.Interaction):
        if self.asks:
            return await it.response.send_modal(ReasonForm(self.view, self.label, self.name, self.extra))
        await self.view.act(it, self.name, {})


class StaffPanel(OwnedView):
    """A row of action buttons about one member (or the server), for the person who opened it."""

    def __init__(self, ctx: Context, member: discord.Member | None, title: str):
        super().__init__(ctx.author.id, timeout=180)
        self.ctx, self.member, self.title = ctx, member, title
        self.message: discord.Message | None = None

    @classmethod
    async def build(cls, ctx: Context, member: discord.Member | None, title: str, actions: list) -> "StaffPanel":
        view = cls(ctx, member, title)
        for i, (label, name, style, asks, extra) in enumerate(actions):
            view.add_item(ActionButton(label, name, style, asks, extra, await can_use(ctx, name), row=i // 4))
        return view

    async def act(self, it: discord.Interaction, name: str, kwargs: dict) -> None:
        if not it.response.is_done():
            await it.response.defer()
        if self.member is not None:
            kwargs.setdefault(MEMBER_PARAM.get(name, "member"), self.member)
        try:
            await run_command(self.ctx, name, **kwargs)
        except (commands.CommandError, UserError) as e:
            text = str(getattr(e, "original", e)) or "that didn't work"
            await it.followup.send(f"⚠️ {text}", ephemeral=True)

    async def on_timeout(self):
        if self.message is not None:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass


async def member_card(ctx: Context, member: discord.Member, title: str) -> discord.Embed:
    """Who you're about to act on: account age, join date, top role, timeout and warnings."""
    lines = [f"**{member.mention}** · `{member.id}`"]
    created, joined = getattr(member, "created_at", None), getattr(member, "joined_at", None)
    if created:
        lines.append(f"**Account:** created {ts(created, 'R')}")
    if joined:
        lines.append(f"**Joined:** {ts(joined, 'R')}")
    top = getattr(member, "top_role", None)
    if top is not None and not top.is_default():
        lines.append(f"**Top role:** {top.mention}")
    until = getattr(member, "timed_out_until", None)
    if until and until > discord.utils.utcnow():
        lines.append(f"**Timed out:** until {ts(until, 'f')}")
    try:
        row = await ctx.bot.db.fetchone("SELECT COUNT(*) AS n FROM warns WHERE guild_id=? AND user_id=?",
                                        ctx.guild.id, member.id)
        lines.append(f"**Warnings:** {row['n'] if row else 0}")
    except Exception:
        pass
    e = discord.Embed(color=Colors.neutral, title=title, description="\n".join(lines))
    e.set_thumbnail(url=getattr(getattr(member, "display_avatar", None), "url", None))
    e.set_footer(text="buttons you can't use are greyed out · every action is logged")
    return e


def server_card(ctx: Context) -> discord.Embed:
    e = discord.Embed(color=Colors.neutral, title="Admin panel", description=(
        f"server-wide actions for **{discord.utils.escape_markdown(ctx.guild.name)}**.\n"
        f"-# `{ctx.clean_prefix}admin @member` for ban, softban, strip staff and quarantine"))
    e.set_footer(text="buttons you can't use are greyed out · every action is logged")
    return e
