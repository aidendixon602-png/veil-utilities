"""Case system + mod-log channel output."""
from __future__ import annotations

import discord

from core import logembeds as fmt
from core.utils import Colors, now, ts


async def create_case(bot, guild: discord.Guild, action: str, target: discord.abc.User | discord.Object,
                      moderator: discord.abc.User, reason: str | None) -> int:
    row = await bot.db.fetchone("SELECT COALESCE(MAX(case_num), 0) + 1 AS n FROM cases WHERE guild_id=?", guild.id)
    num = row["n"]
    await bot.db.execute(
        "INSERT INTO cases VALUES(?,?,?,?,?,?,?)",
        guild.id, num, target.id, moderator.id, action, reason or "No reason provided", now(),
    )
    settings = await bot.db.get_config(guild.id, "settings")
    channel = guild.get_channel(settings.get("modlog", 0))
    if channel:
        shown = target if hasattr(target, "mention") and hasattr(target, "name") else None
        fields = [("User", fmt.person(shown) if shown else f"<@{target.id}>\n`{target.id}`", True),
                  ("Moderator", fmt.person(moderator), True), ("Action", action.title(), True),
                  ("Reason", fmt.quote(reason, 600) if reason else "*No reason given*", False)]
        embed = fmt.detail(Colors.main, f"Case #{num} — {action.title()}", subject=shown, fields=fields,
                           ids=[("User ID", target.id), ("Case", num)])
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass
    return num


async def dm_target(user: discord.abc.User, guild: discord.Guild, action: str, moderator, reason: str | None,
                    duration: str | None = None) -> None:
    embed = discord.Embed(title=f"You were {action}", color=Colors.main, timestamp=discord.utils.utcnow())
    embed.add_field(name="Server", value=guild.name)
    embed.add_field(name="Moderator", value=str(moderator))
    if duration:
        embed.add_field(name="Duration", value=duration)
    embed.add_field(name="Reason", value=reason or "No reason provided", inline=False)
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    try:
        await user.send(embed=embed)
    except (discord.HTTPException, AttributeError):
        pass


def case_line(r) -> str:
    return f"**#{r['case_num']}** {r['action']} • <@{r['user_id']}> by <@{r['mod_id']}> {ts(r['created_at'])}\n> {r['reason']}"
