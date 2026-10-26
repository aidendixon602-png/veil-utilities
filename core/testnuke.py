"""`,testnuke`: a fake nuke in the test server, to check the antinuke really catches and undoes one.

The test server is picked in Discord: the first confirmed `,testnuke` makes the server it ran in the test server,
and `,testserver set` / `,testserver clear` change it. (TEST_GUILD_ID in .env still works and wins if it's set.)

The bot rapidly creates roles and channels named `testnuke-…`. While the test runs, the antinuke treats those
creations as an attack by a simulated attacker (see cogs.antinuke.TestRun): it counts them against the Role
Create and Channel Create rules, alerts the log channel and deletes them, but punishes nobody. Whatever is left
afterwards can be removed with `,testcleanup`.

Only developers whose IDs are in OWNER_IDS can run either command; server permissions (Administrator included)
don't count, and only the developer who started a test can confirm it.
"""
from __future__ import annotations

import asyncio
import time

import discord
from discord.ext import commands

from core.config import settings
from core.utils import Colors, UserError

MAX_EACH = 15  # most roles / channels one test creates
WAIT_FOR_ANTINUKE = 20.0  # seconds to give the antinuke to react before reporting
CONFIRM_TIMEOUT = 60


def env_developer_only():
    """A check that only passes for developer IDs written in OWNER_IDS. Being a server admin, the server owner, or a
    developer found automatically from the Discord application is not enough for these."""
    async def predicate(ctx) -> bool:
        if ctx.author.id in settings.owner_ids:
            return True
        raise UserError("Only developers listed in `OWNER_IDS` in the bot's .env can run this. "
                        "Server permissions don't count.")
    return commands.check(predicate)


async def saved_test_guild_id(bot) -> int | None:
    """The test server: TEST_GUILD_ID in .env if it's set (it wins), otherwise the one picked with the commands."""
    if settings.test_guild_id:
        return settings.test_guild_id
    g = await bot.db.get_config(0, "global")
    return g.get("test_guild_id")


async def save_test_guild(bot, guild_id: int | None) -> None:
    g = await bot.db.get_config(0, "global")
    if guild_id is None:
        g.pop("test_guild_id", None)
    else:
        g["test_guild_id"] = guild_id
    await bot.db.set_config(0, "global", g)


async def test_guild(bot, ctx) -> tuple[discord.Guild, bool]:
    """(the server to test in, whether it still has to become the test server). The command must run in the test
    server; with none picked yet, the server it's run in becomes the test server once the test is confirmed."""
    if ctx.guild is None:
        raise UserError("Run this in the server you want to test in.")
    current = await saved_test_guild_id(bot)
    if current is None:
        return ctx.guild, True
    if ctx.guild.id != current:
        other = bot.get_guild(current) if hasattr(bot, "get_guild") else None
        name = f"**{discord.utils.escape_markdown(other.name)}**" if other else f"`{current}`"
        raise UserError(f"Tests only run in the test server ({name}). Run `testserver set` here to make this "
                        "server the test server instead.")
    return ctx.guild, False


class ConfirmTest(discord.ui.View):
    """Confirm Test / Cancel. Only the developer who started the test can press either."""

    def __init__(self, author_id: int):
        super().__init__(timeout=CONFIRM_TIMEOUT)
        self.author_id = author_id
        self.confirmed: bool | None = None

    async def interaction_check(self, it: discord.Interaction) -> bool:
        if it.user.id == self.author_id:
            return True
        await it.response.send_message("🔒 Only the developer who started this test can confirm or cancel it.",
                                       ephemeral=True)
        return False

    async def _finish(self, it: discord.Interaction, confirmed: bool) -> None:
        self.confirmed = confirmed
        for item in self.children:
            item.disabled = True
        await it.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="Confirm Test", style=discord.ButtonStyle.danger)
    async def confirm(self, it: discord.Interaction, button: discord.ui.Button):
        await self._finish(it, True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, it: discord.Interaction, button: discord.ui.Button):
        await self._finish(it, False)


def rule_limit(cfg: dict, key: str, default: int) -> tuple[bool, int, int]:
    """(on, count, seconds) for a rule, from the server's antinuke settings."""
    mod = (cfg.get("modules") or {}).get(key) or {}
    return bool(cfg.get("enabled") and mod.get("on")), int(mod.get("threshold", default)), int(mod.get("window", 120))


def warning_embed(guild: discord.Guild, cfg: dict, roles: int, channels: int, prefix: str,
                  becomes_test_server: bool = False) -> discord.Embed:
    role_on, role_n, role_s = rule_limit(cfg, "rolecreate", 5)
    chan_on, chan_n, chan_s = rule_limit(cfg, "channelcreate", 2)
    lines = [
        f"This will rapidly create **{roles}** role(s) and **{channels}** channel(s) named `testnuke-…` in "
        f"**{discord.utils.escape_markdown(guild.name)}** to check the antinuke catches it.",
        "",
        "• Role Create rule: " + (f"**{role_n} in {role_s}s**" if role_on else "**off**"),
        "• Channel Create rule: " + (f"**{chan_n} in {chan_s}s**" if chan_on else "**off**"),
        "• The attacker is simulated: the antinuke alerts and deletes what it catches, but nobody is punished.",
        f"• Anything left over is removed with `{prefix}testcleanup`.",
    ]
    if becomes_test_server:
        lines += ["", "📌 No test server is picked yet: confirming makes **this server** the test server, so tests "
                      f"can only run here from now on (`{prefix}testserver` to change it)."]
    if not cfg.get("enabled"):
        lines += ["", f"⚠️ The antinuke is **off** here, so nothing will be caught. Turn it on with `{prefix}an`."]
    e = discord.Embed(title="⚠️ Test nuke", color=Colors.warn, description="\n".join(lines))
    e.set_footer(text="Only the developer who started this can confirm it · cancels itself after a minute")
    return e


async def run_test(antinuke, guild: discord.Guild, started_by: int, roles: int, channels: int) -> dict:
    """Create the roles and channels as fast as Discord allows, give the antinuke time to react, then report."""
    from cogs.antinuke import TEST_PREFIX
    run = antinuke.start_test(guild.id, started_by)
    started = time.monotonic()
    made_roles, made_channels, failures = [], [], []
    try:
        async def make_role(i):
            role = await guild.create_role(name=f"{TEST_PREFIX}role-{i}", reason="Antinuke test (,testnuke)")
            run.created.add(role.id)
            made_roles.append(role)

        async def make_channel(i):
            channel = await guild.create_text_channel(f"{TEST_PREFIX}channel-{i}", reason="Antinuke test (,testnuke)")
            run.created.add(channel.id)
            made_channels.append(channel)

        results = await asyncio.gather(*(make_role(i) for i in range(1, roles + 1)),
                                       *(make_channel(i) for i in range(1, channels + 1)), return_exceptions=True)
        failures = [r for r in results if isinstance(r, Exception)]

        def left() -> tuple[list, list]:
            return ([r for r in made_roles if guild.get_role(r.id) is not None],
                    [c for c in made_channels if guild.get_channel(c.id) is not None])

        deadline = time.monotonic() + WAIT_FOR_ANTINUKE
        while time.monotonic() < deadline:
            roles_left, channels_left = left()
            if not roles_left and not channels_left:
                break
            await asyncio.sleep(0.5)
        # the antinuke deletes first and alerts after: give the alert a moment so the report counts it
        try:
            await asyncio.wait_for(run.alerted.wait(), timeout=max(0.1, min(5.0, deadline - time.monotonic())))
        except asyncio.TimeoutError:
            pass
        roles_left, channels_left = left()
    finally:
        antinuke.end_test(guild.id)
    return {"roles": len(made_roles), "channels": len(made_channels), "failures": failures,
            "roles_left": len(roles_left), "channels_left": len(channels_left),
            "incidents": list(run.incidents), "seconds": time.monotonic() - started}


def report_embed(result: dict, prefix: str) -> discord.Embed:
    made = result["roles"] + result["channels"]
    left = result["roles_left"] + result["channels_left"]
    caught = result["incidents"]
    if not caught:
        color, verdict = Colors.error, ("❌ The antinuke didn't react. Check it's on (`,an`), the Role Create and "
                                        "Channel Create rules are on, and I have View Audit Log.")
    elif left:
        color, verdict = Colors.warn, f"⚠️ The antinuke caught it but **{left}** thing(s) are still there."
    else:
        color, verdict = Colors.success, "✅ The antinuke caught it and removed everything the test made."
    lines = [verdict, "",
             f"created: **{result['roles']}** role(s), **{result['channels']}** channel(s)",
             f"removed by the antinuke: **{made - left}** · left over: **{left}**"]
    from cogs.antinuke import LABELS
    for inc in caught:
        lines.append(f"caught: **{LABELS.get(inc.module, inc.module)}** at {inc.threshold} in {inc.window}s")
    if result["failures"]:
        first = result["failures"][0]
        lines.append(f"⚠️ {len(result['failures'])} creation(s) failed ({getattr(first, 'status', type(first).__name__)})")
    if left:
        lines.append(f"-# remove the rest with `{prefix}testcleanup`")
    e = discord.Embed(title="Test nuke finished", color=color, description="\n".join(lines))
    e.set_footer(text=f"took {result['seconds']:.1f}s · simulated attacker, nobody was punished")
    return e


async def cleanup(antinuke, guild: discord.Guild) -> tuple[int, int, int]:
    """Delete every role and channel named `testnuke-…`. Returns (roles, channels, failures)."""
    from cogs.antinuke import TEST_PREFIX
    if antinuke is not None:
        antinuke.end_test(guild.id)
    roles = channels = failed = 0
    for role in [r for r in guild.roles if r.name.startswith(TEST_PREFIX)]:
        try:
            await role.delete(reason="Antinuke test cleanup (,testcleanup)")
            roles += 1
        except discord.HTTPException:
            failed += 1
    for channel in [c for c in guild.channels if c.name.startswith(TEST_PREFIX)]:
        try:
            await channel.delete(reason="Antinuke test cleanup (,testcleanup)")
            channels += 1
        except discord.HTTPException:
            failed += 1
    return roles, channels, failed
