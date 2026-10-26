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
        mention = target.mention if hasattr(target, "mention") else f"<@{target.id}>"
        embed = fmt.card(Colors.main, moderator, f"**Case #{num}** · {action.title()}\n{mention}{fmt.why(reason)}",
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
