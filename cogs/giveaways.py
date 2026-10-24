"""Giveaways with button entry, requirements, reroll and persistence across restarts."""
from __future__ import annotations

import logging
import random

import discord
from discord.ext import commands, tasks

from core.utils import Colors, Context, Duration, UserError, chunk_embeds, has_perms, now, ts

log = logging.getLogger("veil.giveaways")


class EnterButton(discord.ui.DynamicItem[discord.ui.Button], template=r"gw:(?P<id>\d+)"):
    def __init__(self, gid: int = 0):
        super().__init__(discord.ui.Button(emoji="🎉", label="Enter", style=discord.ButtonStyle.primary,
                                           custom_id=f"gw:{gid}"))

    @classmethod
    async def from_custom_id(cls, it, item, match):
        return cls(int(match["id"]))

    async def callback(self, it: discord.Interaction):
        bot = it.client
        gw = await bot.db.fetchone("SELECT * FROM giveaways WHERE message_id=?", it.message.id)
        if not gw or gw["ended"]:
            return await it.response.send_message("This giveaway has ended.", ephemeral=True)
        if gw["required_role"] and not it.user.get_role(gw["required_role"]):
            return await it.response.send_message(f"You need <@&{gw['required_role']}> to enter.", ephemeral=True)
        # toggle without a check-then-insert race (a fast double click used to hit the primary key and error)
        cur = await bot.db.execute("INSERT OR IGNORE INTO giveaway_entries VALUES(?,?)", it.message.id, it.user.id)
        if cur.rowcount:
            msg = "🎉 You're entered!"
        else:
            await bot.db.execute("DELETE FROM giveaway_entries WHERE message_id=? AND user_id=?", it.message.id, it.user.id)
            msg = "You left the giveaway."
        count = (await bot.db.fetchone("SELECT COUNT(*) c FROM giveaway_entries WHERE message_id=?", it.message.id))["c"]
        embed = it.message.embeds[0]
        embed.set_footer(text=f"{count} entries")
        await it.response.edit_message(embed=embed)
        await it.followup.send(msg, ephemeral=True)


class Giveaways(commands.Cog):
    """Host giveaways"""
    emoji = "🎉"

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_dynamic_items(EnterButton)
        self.checker.start()

    async def cog_unload(self):
        self.checker.cancel()

    def gw_embed(self, prize, host_id, ends, winners, role_id=None) -> discord.Embed:
        e = discord.Embed(title=prize, color=Colors.main,
                          description=f"Ends {ts(ends)} ({ts(ends, 'f')})\nHosted by <@{host_id}>\nWinners: **{winners}**")
        if role_id:
            e.description += f"\nRequired role: <@&{role_id}>"
        e.set_footer(text="0 entries")
        return e

    @commands.group(aliases=["gw", "g"], invoke_without_command=True)
    async def giveaway(self, ctx: Context):
        """Run giveaways"""
        await ctx.send_help(ctx.command)

    @giveaway.command(name="start", aliases=["create"])
    @has_perms(manage_guild=True)
    async def gw_start(self, ctx: Context, duration: Duration, winners: str, *, prize: str):
        """`gw start 1d 2w Nitro` — add `--role @Role` for a requirement"""
        n = int(winners.rstrip("wW")) if winners.rstrip("wW").isdigit() else None
        if not n or not 1 <= n <= 50:
            raise UserError("Winners must be like `1w` or `3`")
        role = None
        if "--role" in prize:
            prize, _, rarg = prize.partition("--role")
            role = await commands.RoleConverter().convert(ctx, rarg.strip())
        prize = prize.strip()
        if not prize:
            raise UserError("Give a prize, e.g. `gw start 1d 1w Nitro`")
        if len(prize) > 250:
            raise UserError("Keep the prize under 250 characters")
        if duration.total_seconds() < 10 or duration.total_seconds() > 60 * 86400:
            raise UserError("Giveaways can last from 10 seconds to 60 days")
        ends = now() + duration.total_seconds()
        msg = await ctx.send(embed=self.gw_embed(prize.strip(), ctx.author.id, ends, n, role.id if role else None),
                             view=discord.ui.View(timeout=None).add_item(EnterButton(0)))
        await msg.edit(view=discord.ui.View(timeout=None).add_item(EnterButton(msg.id)))
        await self.bot.db.execute("INSERT INTO giveaways VALUES(?,?,?,?,?,?,?,0,?)", msg.id, ctx.guild.id,
                                  ctx.channel.id, ctx.author.id, prize.strip(), n, ends, role.id if role else None)
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    async def finish(self, gw, reroll: bool = False) -> list[int] | None:
        if not reroll:
            # claim the giveaway atomically so the checker loop and `gw end` can't both draw winners
            cur = await self.bot.db.execute("UPDATE giveaways SET ended=1 WHERE message_id=? AND ended=0",
                                            gw["message_id"])
            if not cur.rowcount:
                return None
        entries = [r["user_id"] for r in await self.bot.db.fetchall(
            "SELECT user_id FROM giveaway_entries WHERE message_id=?", gw["message_id"])]
        guild = self.bot.get_guild(gw["guild_id"])
        if guild:
            entries = [u for u in entries if guild.get_member(u)]
        winners = random.sample(entries, min(gw["winners"], len(entries)))
        ch = self.bot.get_channel(gw["channel_id"])
        if not ch:
            return winners
        mention = ", ".join(f"<@{w}>" for w in winners) or "nobody (no valid entries)"
        if not reroll:
            try:
                msg = await ch.fetch_message(gw["message_id"])
                e = msg.embeds[0]
                e.description = f"Ended {ts(now())}\nHosted by <@{gw['host_id']}>\nWinners: {mention}"
                e.color = Colors.neutral
                await msg.edit(embed=e, view=None)
            except discord.HTTPException:
                pass
        try:
            await ch.send(f"🎉 {'New winner' if reroll else 'Congratulations'} {mention}! You won **{gw['prize']}**",
                          allowed_mentions=discord.AllowedMentions(users=True))
        except discord.HTTPException:
            pass
        return winners

    @tasks.loop(seconds=15)
    async def checker(self):
        for gw in await self.bot.db.fetchall("SELECT * FROM giveaways WHERE ended=0 AND ends<=?", now()):
            # an unhandled error would stop the task loop for good, and no giveaway would ever end again
            try:
                await self.finish(gw)
            except Exception:
                log.exception("Failed to finish giveaway %s", gw["message_id"])

    @checker.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()

    @giveaway.command(name="end")
    @has_perms(manage_guild=True)
    async def gw_end(self, ctx: Context, message_id: int):
        """End a giveaway early"""
        gw = await self.bot.db.fetchone("SELECT * FROM giveaways WHERE message_id=? AND guild_id=? AND ended=0",
                                        message_id, ctx.guild.id)
        if not gw:
            raise UserError("No active giveaway with that message ID")
        if await self.finish(gw) is None:
            raise UserError("That giveaway already ended")
        await ctx.approve("Ended the giveaway")

    @giveaway.command(name="reroll")
    @has_perms(manage_guild=True)
    async def gw_reroll(self, ctx: Context, message_id: int, winners: int = 1):
        """Pick new winner(s)"""
        gw = await self.bot.db.fetchone("SELECT * FROM giveaways WHERE message_id=? AND guild_id=? AND ended=1",
                                        message_id, ctx.guild.id)
        if not gw:
            raise UserError("No ended giveaway with that message ID")
        gw = dict(gw)
        gw["winners"] = max(1, min(winners, 50))
        await self.finish(gw, reroll=True)

    @giveaway.command(name="list")
    async def gw_list(self, ctx: Context):
        """Active giveaways"""
        rows = await self.bot.db.fetchall("SELECT * FROM giveaways WHERE guild_id=? AND ended=0", ctx.guild.id)
        lines = [f"**{r['prize']}** in <#{r['channel_id']}> ends {ts(r['ends'])} (`{r['message_id']}`)" for r in rows]
        await ctx.paginate(chunk_embeds("Active giveaways", lines))

    @giveaway.command(name="cancel", aliases=["delete"])
    @has_perms(manage_guild=True)
    async def gw_cancel(self, ctx: Context, message_id: int):
        """Cancel a giveaway without picking winners"""
        gw = await self.bot.db.fetchone("SELECT * FROM giveaways WHERE message_id=? AND guild_id=?", message_id, ctx.guild.id)
        if not gw:
            raise UserError("No giveaway with that ID")
        await self.bot.db.execute("DELETE FROM giveaways WHERE message_id=?", message_id)
        await self.bot.db.execute("DELETE FROM giveaway_entries WHERE message_id=?", message_id)
        ch = self.bot.get_channel(gw["channel_id"])
        if ch:
            try:
                await ch.get_partial_message(message_id).delete()
            except discord.HTTPException:
                pass
        await ctx.approve("Cancelled the giveaway")


async def setup(bot):
    await bot.add_cog(Giveaways(bot))
