"""`,honeypot`: decoy channels that catch the accounts a keyword filter never sees.

A compromised account or a spam bot posts the same message in every channel it can see. A honeypot is a channel
only those accounts find: anybody who is *supposed* to be there never types in it, so a single message is enough to
act. The idea and its vocabulary come from [Bleed](https://docs.bleed.com/security/honeypot) and
[Greed](https://greed.best/docs/moderation/honeypot); no code is taken from either.

What it does, and what it deliberately leaves alone:

* punishments are `softban` (the default: ban and unban, which clears what they posted and lets the real owner
  back in once their account is secure), `ban`, `kick`, `timeout`, `quarantine` and `log`;
* staff are never caught: anyone with a Veil permission level, Discord's Administrator, or the server owner;
* the bot's own developers are never caught, and neither is the bot itself;
* a trap never fires on an `off` switch, and every catch is a moderation case in the mod log - the channel can be
  found, the case can be reviewed.
"""
from __future__ import annotations

import datetime

import discord
from discord.ext import commands

from core import perms as _perms
from core.modlog import create_case, dm_target
from core.utils import Colors, Context, UserError, has_perms, is_server_owner

MODULE = "honeypot"  # config store: {channel_id: {"action": "softban", "reason": "..."}}
ACTIONS = ("softban", "ban", "kick", "timeout", "quarantine", "log")
DEFAULT_ACTION = "softban"
TIMEOUT = datetime.timedelta(hours=1)
PURGE = 604800  # what a ban may delete: Discord's 7 days
WARNING = ("This channel is a **honeypot**: it is never used for conversation, and posting here gets the account "
           "punished automatically. If you can see this, ask an admin to hide it again.")


def canonical_action(text: str | None) -> str | None:
    """`Softban`, `soft-ban`, `sb` -> 'softban'; unknown -> None."""
    if not text:
        return None
    flat = text.lower().strip().replace("-", "").replace("_", "").replace(" ", "")
    aliases = {"sb": "softban", "soft": "softban", "b": "ban", "k": "kick", "mute": "timeout",
               "to": "timeout", "q": "quarantine", "jail": "quarantine", "none": "log", "nothing": "log"}
    flat = aliases.get(flat, flat)
    return flat if flat in ACTIONS else None


def is_staff(bot, guild, member) -> bool:
    """Anyone who belongs here: a Veil permission level, Discord's Administrator, or the owner."""
    if member.id == getattr(guild, "owner_id", None):
        return True
    if _perms.is_bot_developer(member, bot):
        return True
    return bool(getattr(member, "guild_permissions", None)
                and getattr(member.guild_permissions, "administrator", False))


async def staff_member(bot, guild, member) -> bool:
    """The async form: `is_staff`, plus the permission levels stored in the database."""
    if is_staff(bot, guild, member):
        return True
    try:
        cfg = await bot.db.get_config(guild.id, "permlevels")
    except Exception:  # a database hiccup must not turn a moderator into a target
        return True
    return _perms.rank_of(cfg, member, has_admin=False) > 0


class Honeypot(commands.Cog):
    """Bait channels that punish whoever posts in them"""

    emoji = "🍯"

    def __init__(self, bot):
        self.bot = bot

    async def _cfg(self, guild) -> dict:
        return await self.bot.db.get_config(guild.id, MODULE)

    async def _save(self, guild, cfg: dict) -> None:
        await self.bot.db.set_config(guild.id, MODULE, cfg)

    # ------------------------------------------------------------ setup
    @commands.group(name="honeypot", aliases=["hp", "honey"], invoke_without_command=True)
    @commands.guild_only()
    @is_server_owner()
    async def honeypot(self, ctx: Context):
        """Set up bait channels that punish accounts posting in them

        A compromised account posts everywhere it can see; a channel nobody else uses catches it on the first
        message. `,honeypot setup` makes one, `,honeypot add #channel softban` uses a channel you already have.
        """
        await ctx.send_help(ctx.command)

    @honeypot.command(name="setup", aliases=["create", "make"])
    @has_perms(manage_guild=True)
    @is_server_owner()
    async def honeypot_setup(self, ctx: Context, channel: discord.TextChannel = None,
                             action: str = DEFAULT_ACTION):
        """Make a honeypot channel, hidden from everyone but staff

        Example: `,honeypot setup` (makes `#honeypot`), or `,honeypot setup #vault ban`
        """
        picked = canonical_action(action)
        if picked is None:
            raise UserError(f"Use one of {', '.join(f'`{a}`' for a in ACTIONS)}.")
        if channel is None:
            if not ctx.guild.me.guild_permissions.manage_channels:
                raise UserError("I need **Manage Channels** to make one, or give me a channel: "
                                f"`{ctx.clean_prefix}honeypot add #channel`.")
            try:
                channel = await ctx.guild.create_text_channel(
                    "honeypot", category=None, reason="Veil Utilities: honeypot channel",
                    topic="Honeypot · posting here is punished automatically")
            except discord.HTTPException:
                raise UserError("Couldn't make the channel; give me one instead: "
                                f"`{ctx.clean_prefix}honeypot add #channel`.")
        await self._register(ctx, channel, picked)
        await ctx.approve(f"{channel.mention} is a honeypot · **{picked}** · {WARNING.split(':')[0].lower()} "
                          "make sure members can't see it")

    @honeypot.command(name="add")
    @has_perms(manage_guild=True)
    @is_server_owner()
    async def honeypot_add(self, ctx: Context, channel: discord.TextChannel, action: str = DEFAULT_ACTION):
        """Use an existing channel as a honeypot

        Example: `,honeypot add #staff-vault softban`
        """
        picked = canonical_action(action)
        if picked is None:
            raise UserError(f"Use one of {', '.join(f'`{a}`' for a in ACTIONS)}.")
        await self._register(ctx, channel, picked)
        await ctx.approve(f"{channel.mention} is a honeypot · **{picked}**")

    async def _register(self, ctx: Context, channel: discord.TextChannel, action: str) -> None:
        cfg = await self._cfg(ctx.guild)
        cfg[str(channel.id)] = {"action": action, "by": ctx.author.id}
        await self._save(ctx.guild, cfg)
        # the warning is the difference between a trap and an accident: pin it where the bot can
        try:
            await channel.send(WARNING)
        except discord.HTTPException:
            pass

    @honeypot.command(name="remove", aliases=["delete", "del"])
    @has_perms(manage_guild=True)
    @is_server_owner()
    async def honeypot_remove(self, ctx: Context, channel: discord.TextChannel = None):
        """Stop treating a channel as a honeypot (the channel is not deleted)"""
        cfg = await self._cfg(ctx.guild)
        if channel is None and len(cfg) == 1:
            channel = ctx.guild.get_channel(int(next(iter(cfg))))
        if channel is None:
            raise UserError(f"Which channel? `{ctx.clean_prefix}honeypot remove #channel`")
        if cfg.pop(str(channel.id), None) is None:
            raise UserError(f"{channel.mention} isn't a honeypot.")
        await self._save(ctx.guild, cfg)
        await ctx.approve(f"{channel.mention} is no longer a honeypot; the channel is still there")

    @honeypot.command(name="list", aliases=["show"])
    @has_perms(manage_guild=True)
    @is_server_owner()
    async def honeypot_list(self, ctx: Context):
        """Show every honeypot channel and what it does"""
        cfg = await self._cfg(ctx.guild)
        if not cfg:
            return await ctx.neutral(f"no honeypots yet; `{ctx.clean_prefix}honeypot setup` makes one")
        lines = []
        for cid, entry in cfg.items():
            channel = ctx.guild.get_channel(int(cid))
            where = channel.mention if channel else f"`{cid}` (deleted)"
            lines.append(f"{where} — **{entry.get('action', DEFAULT_ACTION)}**")
        await ctx.send(embed=discord.Embed(title="Honeypots", color=Colors.main, description="\n".join(lines)))

    @honeypot.command(name="action", aliases=["punish", "punishment"])
    @has_perms(manage_guild=True)
    @is_server_owner()
    async def honeypot_action(self, ctx: Context, channel: discord.TextChannel, action: str):
        """Pick what a honeypot does: softban, ban, kick, timeout, jail or log

        Example: `,honeypot action #honeypot ban`
        """
        picked = canonical_action(action)
        if picked is None:
            raise UserError(f"Use one of {', '.join(f'`{a}`' for a in ACTIONS)}.")
        cfg = await self._cfg(ctx.guild)
        if str(channel.id) not in cfg:
            raise UserError(f"{channel.mention} isn't a honeypot; add it with "
                            f"`{ctx.clean_prefix}honeypot add {channel.mention} {picked}`")
        cfg[str(channel.id)]["action"] = picked
        await self._save(ctx.guild, cfg)
        await ctx.approve(f"{channel.mention} now does **{picked}**")

    # ------------------------------------------------------------ catching
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot:
            return
        if not message.content and not message.attachments:  # joins, pins, calls: not a post
            return
        cfg = await self._cfg(message.guild)
        entry = cfg.get(str(message.channel.id))
        if not entry:
            return
        member = message.author
        if await staff_member(self.bot, message.guild, member):
            return  # a moderator testing it, or someone who belongs here: never punish
        action = entry.get("action", DEFAULT_ACTION)
        await self._catch(message, action)

    async def _catch(self, message: discord.Message, action: str) -> None:
        guild, member = message.guild, message.author
        reason = f"Honeypot: posted in #{getattr(message.channel, 'name', '?')}"
        if action != "log":
            try:
                await dm_target(member, guild, "caught by a honeypot channel", self.bot.user, reason)
            except Exception:
                pass  # a ban usually blocks DMs afterwards, so this is best effort and before the punishment
        done, failed = False, None
        try:
            if action == "softban":
                await member.ban(reason=reason, delete_message_seconds=PURGE)
                await guild.unban(member, reason=reason)
                done = True
            elif action == "ban":
                await member.ban(reason=reason, delete_message_seconds=PURGE)
                done = True
            elif action == "kick":
                await member.kick(reason=reason)
                done = True
            elif action == "timeout":
                await member.timeout(TIMEOUT, reason=reason)
                done = True
            elif action == "quarantine":
                done = await self._quarantine(guild, member, reason)
        except discord.HTTPException as err:
            failed = f"Discord said no ({err.status})"
        # a catch is a case, whatever happened: the card is how a moderator finds it later
        try:
            num = await create_case(self.bot, guild, action, member, self.bot.user, reason)
        except Exception:
            num = None
        await self._report(guild, message, action, done, failed, num)

    async def _quarantine(self, guild, member, reason: str) -> bool:
        """Strip the member's roles and give them the quarantine role, the same way `,quarantine` does."""
        cfg = await self.bot.db.get_config(guild.id, "quarantine")
        role = guild.get_role(cfg.get("role", 0)) or discord.utils.get(guild.roles, name="Quarantined")
        if role is None:
            return False
        keep = [r for r in member.roles if r.managed or r.is_default() or r >= guild.me.top_role]
        removed = [r.id for r in member.roles if r not in keep]
        await member.edit(roles=keep + [role], reason=reason)
        cfg.setdefault("quarantined", {})[str(member.id)] = removed
        cfg["role"] = role.id
        await self.bot.db.set_config(guild.id, "quarantine", cfg)
        return True

    async def _report(self, guild, message, action: str, done: bool, failed: str | None, num: int | None) -> None:
        """Say what happened where a moderator will see it: the honeypot channel, or the mod log."""
        row = (f"**{message.author}** (`{message.author.id}`) posted in {message.channel.mention}\n"
               f"**Action:** {action}" + ("" if done else f" · failed: {failed or 'no permission'}"))
        if num is not None:
            row += f" · case **#{num}**"
        if message.content:
            row += f"\n> {message.content[:500]}"
        embed = discord.Embed(title="🍯 Honeypot triggered", color=Colors.caution if failed else Colors.mint,
                              description=row, timestamp=discord.utils.utcnow())
        embed.set_footer(text=f"User ID: {message.author.id}")
        target = message.channel
        try:
            await message.delete()
        except discord.HTTPException:
            pass
        try:
            await target.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass


async def setup(bot):
    await bot.add_cog(Honeypot(bot))
