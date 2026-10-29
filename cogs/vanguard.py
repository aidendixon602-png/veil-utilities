"""`,gv`: Ghost Vanguard's commands, typed with Veil's prefix.

Ghost Vanguard (the server's guard) has no slash commands. Its commands are `,gv` followed by what used to come
after the slash: `,gv raid mode on`, `,gv antinuke whitelist add @user`, `,gv help`. Veil checks Ghost Vanguard is
in the server and reacts 🛡️ to the message; that reaction from Veil's own account is the hand-off, and Ghost
Vanguard then runs the command with its own ranks, permissions and confirmations and answers in the channel.
"""
from __future__ import annotations

import discord
from discord.ext import commands

from core import perms
from core.utils import Context, UserError

HANDOFF_EMOJI = "🛡️"  # Ghost Vanguard only acts on this reaction, and only from Veil's account


class Vanguard(commands.Cog):
    """Ghost Vanguard's commands: `,gv help` lists them"""
    emoji = "👻"

    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="gv", aliases=["vanguard", "ghost"])
    @commands.guild_only()
    @commands.cooldown(2, 3, commands.BucketType.user)
    async def gv(self, ctx: Context, *, command: str = None):
        """Run a Ghost Vanguard command: `,gv raid mode on`, `,gv antinuke status`, `,gv help` for the list"""
        if not perms.ghost_guards(ctx.guild):
            raise UserError("Ghost Vanguard isn't in this server, so there's nothing to hand this to")
        try:
            await ctx.message.add_reaction(HANDOFF_EMOJI)
        except discord.Forbidden:
            raise UserError("I need **Add Reactions** in this channel to hand commands to Ghost Vanguard")
        except discord.HTTPException:
            raise UserError("Discord didn't let me hand that over, try again in a moment")


async def setup(bot):
    await bot.add_cog(Vanguard(bot))
