"""Server systems: welcome/goodbye/boost, autorole, reaction roles, button roles, autoresponders,
reaction triggers, sticky messages, starboard, counters, bump reminders, vanity rewards."""
from __future__ import annotations

import asyncio
import re

import discord
from discord.ext import commands, tasks

from core.utils import (Colors, Context, UserError, build_message, build_vars, check_role, chunk_embeds,
                        has_perms, is_dangerous, now, send_script, shorten)


def self_assignable(role: discord.Role) -> bool:
    """Checked when a self-role is *claimed*, not just when the panel was made: if staff later give the role
    admin/ban/etc., a months-old button or reaction must not hand those permissions to anyone who clicks."""
    me = role.guild.me
    return not role.managed and not role.is_default() and role < me.top_role and not is_dangerous(role.permissions)

DISBOARD_ID = 302050872383242240
MSG_LINK = re.compile(r"discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)")


class RoleButton(discord.ui.DynamicItem[discord.ui.Button], template=r"rb:(?P<role>\d+)"):
    """Persistent self-role button that survives restarts without registration."""

    def __init__(self, role_id: int, label: str | None = None, emoji: str | None = None):
        super().__init__(discord.ui.Button(label=label, emoji=emoji, style=discord.ButtonStyle.secondary,
                                           custom_id=f"rb:{role_id}"))
        self.role_id = role_id

    @classmethod
    async def from_custom_id(cls, it, item, match):
        return cls(int(match["role"]))

    async def callback(self, it: discord.Interaction):
        role = it.guild.get_role(self.role_id)
        if not role:
            return await it.response.send_message("That role no longer exists.", ephemeral=True)
        if not self_assignable(role) and role not in it.user.roles:
            return await it.response.send_message(
                f"{role.mention} has staff permissions now, so I won't hand it out. Ask a server admin.", ephemeral=True)
        try:
            if role in it.user.roles:
                await it.user.remove_roles(role, reason="button role")
                msg = f"Removed {role.mention}"
            else:
                await it.user.add_roles(role, reason="button role")
                msg = f"Added {role.mention}"
        except discord.Forbidden:
            msg = "I can't manage that role."
        await it.response.send_message(msg, ephemeral=True)


class Config(commands.Cog):
    """Welcome messages, roles, responders, starboard and more"""
    emoji = "🧩"

    def __init__(self, bot):
        self.bot = bot
        self.sticky_lock: dict[int, asyncio.Lock] = {}
        self.ar_cache: dict[int, list] = {}

    async def cog_load(self):
        self.bot.add_dynamic_items(RoleButton)
        self.counter_loop.start()
        self.bump_loop.start()

    async def cog_unload(self):
        self.counter_loop.cancel()
        self.bump_loop.cancel()

    async def get(self, gid, mod):
        return await self.bot.db.get_config(gid, mod)

    async def put(self, gid, mod, data):
        await self.bot.db.set_config(gid, mod, data)

    # ================================================================ greet / leave / boost
    async def _greet_cmd(self, ctx: Context, kind: str, channel, message):
        cfg = await self.get(ctx.guild.id, kind)
        if channel is None and message is None:
            ch = ctx.guild.get_channel(cfg.get("channel", 0))
            return await ctx.neutral(f"**{kind}**: {ch.mention if ch else 'off'}\n```{cfg.get('message', 'not set')}```")
        if channel and message is None:
            raise UserError("Give a message too. Variables: `{user.mention}`, `{guild.name}`, `{guild.count}`…")
        cfg.update(channel=channel.id, message=message)
        await self.put(ctx.guild.id, kind, cfg)
        await ctx.approve(f"**{kind}** messages will be sent to {channel.mention}. Test with `{ctx.clean_prefix}{kind} test`")

    async def _greet_send(self, member: discord.Member, kind: str):
        cfg = await self.get(member.guild.id, kind)
        ch = member.guild.get_channel(cfg.get("channel", 0))
        if not ch or not cfg.get("message"):
            return
        try:
            msg = await send_script(ch, cfg["message"], build_vars(member, member.guild, ch))
            if cfg.get("delete_after"):
                await msg.delete(delay=cfg["delete_after"])
        except discord.HTTPException:
            pass

    @commands.group(aliases=["greet"], invoke_without_command=True)
    @has_perms(manage_guild=True)
    async def welcome(self, ctx, channel: discord.TextChannel = None, *, message: str = None):
        """Set the welcome message: `welcome #ch {embed}$v{description: hi {user.mention}}`"""
        await self._greet_cmd(ctx, "welcome", channel, message)

    @welcome.command(name="test")
    @has_perms(manage_guild=True)
    async def welcome_test(self, ctx):
        """Preview the welcome message"""
        await self._greet_send(ctx.author, "welcome")

    @welcome.command(name="off", aliases=["disable"])
    @has_perms(manage_guild=True)
    async def welcome_off(self, ctx):
        """Disable welcome messages"""
        await self.put(ctx.guild.id, "welcome", {})
        await ctx.approve("Disabled welcome messages")

    @welcome.command(name="deleteafter")
    @has_perms(manage_guild=True)
    async def welcome_da(self, ctx, seconds: int = 0):
        """Auto-delete welcome messages after N seconds (0 = never)"""
        cfg = await self.get(ctx.guild.id, "welcome")
        cfg["delete_after"] = seconds or None
        await self.put(ctx.guild.id, "welcome", cfg)
        await ctx.approve(f"Welcome messages delete after **{seconds}s**" if seconds else "Welcome messages stay")

    @commands.group(aliases=["leave"], invoke_without_command=True)
    @has_perms(manage_guild=True)
    async def goodbye(self, ctx, channel: discord.TextChannel = None, *, message: str = None):
        """Set the goodbye message"""
        await self._greet_cmd(ctx, "goodbye", channel, message)

    @goodbye.command(name="test")
    @has_perms(manage_guild=True)
    async def goodbye_test(self, ctx):
        """Preview the goodbye message"""
        await self._greet_send(ctx.author, "goodbye")

    @goodbye.command(name="off")
    @has_perms(manage_guild=True)
    async def goodbye_off(self, ctx):
        """Disable goodbye messages"""
        await self.put(ctx.guild.id, "goodbye", {})
        await ctx.approve("Disabled goodbye messages")

    @commands.group(invoke_without_command=True)
    @has_perms(manage_guild=True)
    async def boost(self, ctx, channel: discord.TextChannel = None, *, message: str = None):
        """Set the boost thank-you message"""
        await self._greet_cmd(ctx, "boost", channel, message)

    @boost.command(name="test")
    @has_perms(manage_guild=True)
    async def boost_test(self, ctx):
        """Preview the boost message"""
        await self._greet_send(ctx.author, "boost")

    @boost.command(name="off")
    @has_perms(manage_guild=True)
    async def boost_off(self, ctx):
        """Disable boost messages"""
        await self.put(ctx.guild.id, "boost", {})
        await ctx.approve("Disabled boost messages")

    @boost.command(name="role")
    @has_perms(manage_roles=True)
    async def boost_role(self, ctx, role: discord.Role = None):
        """Extra role given to boosters (removed when they stop)"""
        cfg = await self.get(ctx.guild.id, "boost")
        if role:
            check_role(ctx, role)
        cfg["role"] = role.id if role else None
        await self.put(ctx.guild.id, "boost", cfg)
        await ctx.approve(f"Booster reward role: {role.mention if role else 'none'}")

    @commands.command(aliases=["variables"])
    async def placeholders(self, ctx):
        """List variables you can use in messages"""
        v = build_vars(ctx.author, ctx.guild, ctx.channel)
        await ctx.paginate(chunk_embeds("Variables", [f"`{{{k}}}` → {shorten(val, 60)}" for k, val in v.items()], 15, numbered=False))

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        await self._greet_send(member, "welcome")
        ar = await self.get(member.guild.id, "autorole")
        ids = ar.get("bots" if member.bot else "humans", []) + ar.get("all", [])
        roles = [r for r in (member.guild.get_role(i) for i in ids) if r and r < member.guild.me.top_role]
        if roles:
            await asyncio.sleep(ar.get("delay", 0))
            try:
                await member.add_roles(*roles, reason="autorole")
            except discord.HTTPException:
                pass

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        await self._greet_send(member, "goodbye")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.premium_since == after.premium_since:
            return
        cfg = await self.get(after.guild.id, "boost")
        role = after.guild.get_role(cfg.get("role") or 0)
        if after.premium_since and not before.premium_since:
            await self._greet_send(after, "boost")
            if role:
                try:
                    await after.add_roles(role, reason="booster reward")
                except discord.HTTPException:
                    pass
        elif before.premium_since and not after.premium_since and role:
            try:
                await after.remove_roles(role, reason="stopped boosting")
            except discord.HTTPException:
                pass

    # ================================================================ autorole
    @commands.group(invoke_without_command=True)
    @has_perms(manage_roles=True)
    async def autorole(self, ctx):
        """Roles given automatically on join"""
        ar = await self.get(ctx.guild.id, "autorole")
        lines = [f"{k}: {', '.join(f'<@&{r}>' for r in v)}" for k, v in ar.items() if isinstance(v, list) and v]
        await ctx.neutral("\n".join(lines) or "No autoroles set")

    async def _ar(self, ctx, bucket, role: discord.Role):
        check_role(ctx, role)
        ar = await self.get(ctx.guild.id, "autorole")
        lst = ar.setdefault(bucket, [])
        if role.id in lst:
            lst.remove(role.id); msg = f"Removed {role.mention} from **{bucket}** autoroles"
        else:
            lst.append(role.id); msg = f"Added {role.mention} to **{bucket}** autoroles"
        await self.put(ctx.guild.id, "autorole", ar)
        await ctx.approve(msg)

    @autorole.command(name="add", aliases=["all"])
    @has_perms(manage_roles=True)
    async def autorole_add(self, ctx, *, role: discord.Role):
        """Toggle a role for everyone who joins"""
        await self._ar(ctx, "all", role)

    @autorole.command(name="humans")
    @has_perms(manage_roles=True)
    async def autorole_humans(self, ctx, *, role: discord.Role):
        """Toggle a role for humans only"""
        await self._ar(ctx, "humans", role)

    @autorole.command(name="bots")
    @has_perms(manage_roles=True)
    async def autorole_bots(self, ctx, *, role: discord.Role):
        """Toggle a role for bots only"""
        await self._ar(ctx, "bots", role)

    @autorole.command(name="delay")
    @has_perms(manage_roles=True)
    async def autorole_delay(self, ctx, seconds: int):
        """Wait N seconds before giving autoroles (helps with verification gates)"""
        ar = await self.get(ctx.guild.id, "autorole")
        ar["delay"] = max(0, min(seconds, 600))
        await self.put(ctx.guild.id, "autorole", ar)
        await ctx.approve(f"Autorole delay: **{ar['delay']}s**")

    # ================================================================ reaction roles
    async def _resolve_message(self, ctx, link: str) -> discord.Message:
        m = MSG_LINK.search(link)
        try:
            if m:
                ch = ctx.guild.get_channel(int(m.group(2)))
                return await ch.fetch_message(int(m.group(3)))
            return await ctx.channel.fetch_message(int(link))
        except (ValueError, AttributeError, discord.HTTPException):
            raise UserError("I couldn't find that message — use a message link")

    @commands.group(aliases=["rr"], invoke_without_command=True)
    @has_perms(manage_roles=True)
    async def reactionrole(self, ctx):
        """Give roles when members react to a message"""
        await ctx.send_help(ctx.command)

    @reactionrole.command(name="add")
    @has_perms(manage_roles=True)
    async def rr_add(self, ctx, message: str, emoji: str, *, role: discord.Role):
        """`rr add <message link> 🎮 @Gamer`"""
        check_role(ctx, role)
        if is_dangerous(role.permissions):
            raise UserError(f"{role.mention} has staff permissions — it can't be a self-role")
        msg = await self._resolve_message(ctx, message)
        try:
            await msg.add_reaction(emoji)
        except discord.HTTPException:
            raise UserError("I can't use that emoji")
        rr = await self.get(ctx.guild.id, "reactionroles")
        rr.setdefault(str(msg.id), {})[emoji] = role.id
        await self.put(ctx.guild.id, "reactionroles", rr)
        await ctx.approve(f"Reacting {emoji} on [that message]({msg.jump_url}) gives {role.mention}")

    @reactionrole.command(name="remove")
    @has_perms(manage_roles=True)
    async def rr_remove(self, ctx, message: str, emoji: str):
        """Remove a reaction role"""
        msg = await self._resolve_message(ctx, message)
        rr = await self.get(ctx.guild.id, "reactionroles")
        if rr.get(str(msg.id), {}).pop(emoji, None) is None:
            raise UserError("No reaction role for that emoji")
        await self.put(ctx.guild.id, "reactionroles", rr)
        await ctx.approve("Removed reaction role")

    @reactionrole.command(name="list")
    @has_perms(manage_roles=True)
    async def rr_list(self, ctx):
        """List reaction roles"""
        rr = await self.get(ctx.guild.id, "reactionroles")
        lines = [f"`{mid}` {e} → <@&{r}>" for mid, m in rr.items() for e, r in m.items()]
        await ctx.paginate(chunk_embeds("Reaction roles", lines))

    async def _rr_event(self, payload: discord.RawReactionActionEvent, add: bool):
        if not payload.guild_id or payload.user_id == self.bot.user.id:
            return
        rr = await self.get(payload.guild_id, "reactionroles")
        role_id = rr.get(str(payload.message_id), {}).get(str(payload.emoji))
        if not role_id:
            return
        guild = self.bot.get_guild(payload.guild_id)
        member = guild.get_member(payload.user_id)
        role = guild.get_role(role_id)
        if member and role and not member.bot and (not add or self_assignable(role)):
            try:
                await (member.add_roles if add else member.remove_roles)(role, reason="reaction role")
            except discord.HTTPException:
                pass

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        await self._rr_event(payload, True)
        await self._starboard(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        await self._rr_event(payload, False)
        await self._starboard(payload)

    # ---------------------------------------------------------------- button roles
    @commands.command(aliases=["buttonroles", "rolemenu"])
    @has_perms(manage_roles=True)
    async def buttonrole(self, ctx, roles: commands.Greedy[discord.Role], *, title: str = "Pick your roles"):
        """Post a panel of self-role buttons: `buttonrole @Red @Blue @Green Colors`"""
        if not roles or len(roles) > 25:
            raise UserError("Give 1–25 roles")
        for r in roles:
            check_role(ctx, r)
            if is_dangerous(r.permissions):
                raise UserError(f"{r.mention} has staff permissions — it can't be a self-role")
        view = discord.ui.View(timeout=None)
        for r in roles:
            view.add_item(RoleButton(r.id, label=r.name[:80]))
        embed = discord.Embed(title=title, color=Colors.main,
                              description="\n".join(f"{r.mention}" for r in roles) + "\n\nClick a button to toggle a role.")
        await ctx.send(embed=embed, view=view)
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    # ================================================================ autoresponder
    @commands.group(aliases=["ar", "autoresponse"], invoke_without_command=True)
    @has_perms(manage_guild=True)
    async def autoresponder(self, ctx):
        """Automatic replies to trigger phrases"""
        await ctx.send_help(ctx.command)

    @autoresponder.command(name="add")
    @has_perms(manage_guild=True)
    async def ar_add(self, ctx, *, args: str):
        """`ar add hello, Hi {user.mention}!` — flags: --exact --reply --delete (supports embed scripts)"""
        if "," not in args:
            raise UserError("Separate the trigger and response with a comma: `ar add trigger, response`")
        trigger, response = (x.strip() for x in args.split(",", 1))
        flags = {f: f"--{f}" in response for f in ("exact", "reply", "delete")}
        for f in flags:
            response = response.replace(f"--{f}", "")
        cfg = await self.get(ctx.guild.id, "autoresponder")
        if len(cfg) >= 150:
            raise UserError("You can have at most 150 autoresponders")
        cfg[trigger.lower()] = {"response": response.strip(), **flags}
        await self.put(ctx.guild.id, "autoresponder", cfg)
        await ctx.approve(f"Added autoresponder for `{trigger}`")

    @autoresponder.command(name="remove", aliases=["delete"])
    @has_perms(manage_guild=True)
    async def ar_remove(self, ctx, *, trigger: str):
        """Remove an autoresponder"""
        cfg = await self.get(ctx.guild.id, "autoresponder")
        if cfg.pop(trigger.lower(), None) is None:
            raise UserError("No autoresponder with that trigger")
        await self.put(ctx.guild.id, "autoresponder", cfg)
        await ctx.approve(f"Removed `{trigger}`")

    @autoresponder.command(name="list")
    @has_perms(manage_guild=True)
    async def ar_list(self, ctx):
        """List autoresponders"""
        cfg = await self.get(ctx.guild.id, "autoresponder")
        await ctx.paginate(chunk_embeds("Autoresponders", [f"`{t}` → {shorten(v['response'], 60)}" for t, v in cfg.items()]))

    # ---------------------------------------------------------------- reaction triggers
    @commands.group(aliases=["rt"], invoke_without_command=True)
    @has_perms(manage_guild=True)
    async def reactiontrigger(self, ctx):
        """Auto-react to messages containing a word"""
        await ctx.send_help(ctx.command)

    @reactiontrigger.command(name="add")
    @has_perms(manage_guild=True)
    async def rt_add(self, ctx, emoji: str, *, trigger: str):
        """`rt add 💀 skull`"""
        try:
            await ctx.message.add_reaction(emoji)
        except discord.HTTPException:
            raise UserError("I can't use that emoji")
        cfg = await self.get(ctx.guild.id, "reactiontriggers")
        lst = cfg.setdefault(trigger.lower(), [])
        if emoji not in lst:
            lst.append(emoji)
        await self.put(ctx.guild.id, "reactiontriggers", cfg)
        await ctx.approve(f"Will react {emoji} to `{trigger}`")

    @reactiontrigger.command(name="remove")
    @has_perms(manage_guild=True)
    async def rt_remove(self, ctx, *, trigger: str):
        """Remove all reactions for a trigger"""
        cfg = await self.get(ctx.guild.id, "reactiontriggers")
        cfg.pop(trigger.lower(), None)
        await self.put(ctx.guild.id, "reactiontriggers", cfg)
        await ctx.approve(f"Removed trigger `{trigger}`")

    @reactiontrigger.command(name="list")
    @has_perms(manage_guild=True)
    async def rt_list(self, ctx):
        """List reaction triggers"""
        cfg = await self.get(ctx.guild.id, "reactiontriggers")
        await ctx.paginate(chunk_embeds("Reaction triggers", [f"`{t}` → {' '.join(e)}" for t, e in cfg.items()]))

    # ---------------------------------------------------------------- sticky messages
    @commands.group(invoke_without_command=True)
    @has_perms(manage_messages=True)
    async def sticky(self, ctx, channel: discord.TextChannel = None, *, message: str = None):
        """Keep a message pinned at the bottom of a channel (supports embed scripts)"""
        if not channel or not message:
            return await ctx.send_help(ctx.command)
        cfg = await self.get(ctx.guild.id, "sticky")
        sent = await send_script(channel, message, build_vars(ctx.author, ctx.guild, channel))
        cfg[str(channel.id)] = {"message": message, "last": sent.id}
        await self.put(ctx.guild.id, "sticky", cfg)
        await ctx.approve(f"Sticky message set in {channel.mention}")

    @sticky.command(name="remove")
    @has_perms(manage_messages=True)
    async def sticky_remove(self, ctx, channel: discord.TextChannel):
        """Remove a sticky message"""
        cfg = await self.get(ctx.guild.id, "sticky")
        cfg.pop(str(channel.id), None)
        await self.put(ctx.guild.id, "sticky", cfg)
        await ctx.approve(f"Removed sticky in {channel.mention}")

    # ---------------------------------------------------------------- on_message hub
    @commands.Cog.listener()
    async def on_message(self, m: discord.Message):
        if not m.guild or m.author.bot:
            if m.guild and m.author.id == DISBOARD_ID:
                await self._bump_detect(m)
            return
        low = m.content.lower()
        # autoresponders
        ar = await self.get(m.guild.id, "autoresponder")
        for trig, data in ar.items():
            hit = low == trig if data.get("exact") else re.search(rf"(?<!\w){re.escape(trig)}(?!\w)", low)
            if hit:
                payload = build_message(data["response"], build_vars(m.author, m.guild, m.channel))
                try:
                    if data.get("reply"):
                        await m.reply(**payload)
                    else:
                        await m.channel.send(**payload)
                    if data.get("delete"):
                        await m.delete()
                except discord.HTTPException:
                    pass
                break
        # reaction triggers
        rt = await self.get(m.guild.id, "reactiontriggers")
        for trig, emojis in rt.items():
            if trig in low:
                for e in emojis[:5]:
                    try:
                        await m.add_reaction(e)
                    except discord.HTTPException:
                        pass
        # sticky
        st = await self.get(m.guild.id, "sticky")
        data = st.get(str(m.channel.id))
        if data:
            lock = self.sticky_lock.setdefault(m.channel.id, asyncio.Lock())
            if lock.locked():
                return
            async with lock:
                await asyncio.sleep(2)
                try:
                    old = m.channel.get_partial_message(data["last"])
                    await old.delete()
                except discord.HTTPException:
                    pass
                try:
                    sent = await send_script(m.channel, data["message"], build_vars(m.author, m.guild, m.channel))
                    data["last"] = sent.id
                    await self.put(m.guild.id, "sticky", st)
                except discord.HTTPException:
                    pass

    # ================================================================ starboard
    @commands.group(aliases=["sb"], invoke_without_command=True)
    @has_perms(manage_guild=True)
    async def starboard(self, ctx, channel: discord.TextChannel = None, threshold: int = 3, emoji: str = "⭐"):
        """Set up a starboard: `starboard #stars 3 ⭐`"""
        if not channel:
            return await ctx.send_help(ctx.command)
        cfg = await self.get(ctx.guild.id, "starboard")
        cfg.update(channel=channel.id, threshold=max(1, threshold), emoji=emoji, posts=cfg.get("posts", {}))
        await self.put(ctx.guild.id, "starboard", cfg)
        await ctx.approve(f"Starboard → {channel.mention} at **{threshold}** {emoji}")

    @starboard.command(name="off")
    @has_perms(manage_guild=True)
    async def starboard_off(self, ctx):
        """Disable the starboard"""
        await self.put(ctx.guild.id, "starboard", {})
        await ctx.approve("Starboard disabled")

    async def _starboard(self, p: discord.RawReactionActionEvent):
        if not p.guild_id:
            return
        cfg = await self.get(p.guild_id, "starboard")
        if not cfg.get("channel") or str(p.emoji) != cfg.get("emoji") or p.channel_id == cfg["channel"]:
            return
        guild = self.bot.get_guild(p.guild_id)
        board = guild.get_channel(cfg["channel"])
        src = guild.get_channel(p.channel_id)
        if not board or not src:
            return
        try:
            msg = await src.fetch_message(p.message_id)
        except discord.HTTPException:
            return
        reaction = discord.utils.find(lambda r: str(r.emoji) == cfg["emoji"], msg.reactions)
        count = reaction.count if reaction else 0
        posts = cfg.setdefault("posts", {})
        existing = posts.get(str(msg.id))
        header = f"{cfg['emoji']} **{count}** {src.mention}"
        if count < cfg["threshold"]:
            if existing:
                try:
                    await board.get_partial_message(existing).delete()
                except discord.HTTPException:
                    pass
                posts.pop(str(msg.id))
                await self.put(p.guild_id, "starboard", cfg)
            return
        if existing:
            try:
                await board.get_partial_message(existing).edit(content=header)
            except discord.HTTPException:
                pass
            return
        e = discord.Embed(description=msg.content or None, color=0xFFAC33, timestamp=msg.created_at)
        e.set_author(name=msg.author.display_name, icon_url=msg.author.display_avatar.url)
        img = next((a.url for a in msg.attachments if a.content_type and a.content_type.startswith("image")), None)
        if img:
            e.set_image(url=img)
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Jump to message", url=msg.jump_url))
        sent = await board.send(content=header, embed=e, view=view)
        posts[str(msg.id)] = sent.id
        if len(posts) > 2000:
            for k in list(posts)[:500]:
                posts.pop(k)
        await self.put(p.guild_id, "starboard", cfg)

    # ================================================================ counters
    @commands.group(invoke_without_command=True)
    @has_perms(manage_channels=True)
    async def counter(self, ctx):
        """Channels that display live server stats"""
        await ctx.send_help(ctx.command)

    @counter.command(name="add")
    @has_perms(manage_channels=True)
    async def counter_add(self, ctx, kind: str, *, template: str = None):
        """`counter add members Members: {count}` — kinds: members, humans, bots, boosts, online"""
        kind = kind.lower()
        if kind not in ("members", "humans", "bots", "boosts", "online"):
            raise UserError("Kinds: members, humans, bots, boosts, online")
        template = template or f"{kind.title()}: {{count}}"
        ch = await ctx.guild.create_voice_channel(template.replace("{count}", str(self._count(ctx.guild, kind))),
                                                  overwrites={ctx.guild.default_role: discord.PermissionOverwrite(connect=False)},
                                                  position=0)
        cfg = await self.get(ctx.guild.id, "counters")
        cfg[str(ch.id)] = {"kind": kind, "template": template}
        await self.put(ctx.guild.id, "counters", cfg)
        await ctx.approve(f"Created counter {ch.mention} (updates every 10 minutes)")

    @counter.command(name="remove")
    @has_perms(manage_channels=True)
    async def counter_remove(self, ctx, channel: discord.VoiceChannel):
        """Remove a counter (and its channel)"""
        cfg = await self.get(ctx.guild.id, "counters")
        cfg.pop(str(channel.id), None)
        await self.put(ctx.guild.id, "counters", cfg)
        await channel.delete()
        await ctx.approve("Removed counter")

    @staticmethod
    def _count(g: discord.Guild, kind: str) -> int:
        return {
            "members": lambda: g.member_count,
            "humans": lambda: sum(not m.bot for m in g.members),
            "bots": lambda: sum(m.bot for m in g.members),
            "boosts": lambda: g.premium_subscription_count,
            "online": lambda: sum(m.status != discord.Status.offline for m in g.members),
        }[kind]()

    @tasks.loop(minutes=10)
    async def counter_loop(self):
        for gid, cfg in await self.bot.db.guilds_with("counters"):
            guild = self.bot.get_guild(gid)
            if not guild:
                continue
            for cid, data in cfg.items():
                ch = guild.get_channel(int(cid))
                if not ch:
                    continue
                name = data["template"].replace("{count}", f"{self._count(guild, data['kind']):,}")
                if ch.name != name:
                    try:
                        await ch.edit(name=name)
                    except discord.HTTPException:
                        pass

    @counter_loop.before_loop
    async def _wait1(self):
        await self.bot.wait_until_ready()

    # ================================================================ bump reminder
    @commands.group(aliases=["bumpreminder"], invoke_without_command=True)
    @has_perms(manage_guild=True)
    async def bump(self, ctx, state: str = None):
        """Remind the server to /bump on Disboard every 2 hours (`bump on`/`bump off`)"""
        if state not in ("on", "off"):
            return await ctx.send_help(ctx.command)
        cfg = await self.get(ctx.guild.id, "bump")
        cfg["on"] = state == "on"
        cfg.setdefault("message", "{embed}$v{title: Time to bump!}$v{description: Run `/bump` to bump the server ⏰}")
        cfg.setdefault("thanks", "{embed}$v{description: Thanks for bumping {user.mention}! I'll remind you in 2 hours.}")
        await self.put(ctx.guild.id, "bump", cfg)
        await ctx.approve(f"Bump reminders **{state}**")

    @bump.command(name="message")
    @has_perms(manage_guild=True)
    async def bump_message(self, ctx, *, message: str):
        """Set the reminder message"""
        cfg = await self.get(ctx.guild.id, "bump")
        cfg["message"] = message
        await self.put(ctx.guild.id, "bump", cfg)
        await ctx.approve("Updated bump reminder message")

    @bump.command(name="thankyou", aliases=["thanks"])
    @has_perms(manage_guild=True)
    async def bump_thanks(self, ctx, *, message: str):
        """Set the thank-you message"""
        cfg = await self.get(ctx.guild.id, "bump")
        cfg["thanks"] = message
        await self.put(ctx.guild.id, "bump", cfg)
        await ctx.approve("Updated bump thank-you message")

    async def _bump_detect(self, m: discord.Message):
        if not m.embeds or "bump done" not in (m.embeds[0].description or "").lower():
            return
        cfg = await self.get(m.guild.id, "bump")
        if not cfg.get("on"):
            return
        user = m.interaction_metadata.user if m.interaction_metadata else None
        cfg.update(channel=m.channel.id, due=now() + 7200, user=user.id if user else None)
        await self.put(m.guild.id, "bump", cfg)
        if cfg.get("thanks"):
            await send_script(m.channel, cfg["thanks"], build_vars(user or m.guild.me, m.guild, m.channel))

    @tasks.loop(minutes=1)
    async def bump_loop(self):
        for gid, cfg in await self.bot.db.guilds_with("bump"):
            if not cfg.get("on") or not cfg.get("due") or cfg["due"] > now():
                continue
            guild = self.bot.get_guild(gid)
            ch = guild.get_channel(cfg.get("channel", 0)) if guild else None
            live = await self.get(gid, "bump")
            live["due"] = None
            await self.put(gid, "bump", live)
            if ch:
                member = guild.get_member(cfg.get("user") or 0) or guild.me
                try:
                    await send_script(ch, cfg["message"], build_vars(member, guild, ch))
                except discord.HTTPException:
                    pass

    @bump_loop.before_loop
    async def _wait2(self):
        await self.bot.wait_until_ready()

    # ================================================================ vanity rewards
    @commands.group(invoke_without_command=True)
    @has_perms(manage_guild=True)
    async def vanity(self, ctx, role: discord.Role = None, *, text: str = None):
        """Reward members who put a text (e.g. your invite) in their status: `vanity @Supporter /myserver`"""
        if not role or not text:
            return await ctx.send_help(ctx.command)
        check_role(ctx, role)
        await self.put(ctx.guild.id, "vanity", {"role": role.id, "text": text.lower()})
        await ctx.approve(f"Members with `{text}` in their status get {role.mention}")

    @vanity.command(name="off")
    @has_perms(manage_guild=True)
    async def vanity_off(self, ctx):
        """Disable vanity rewards"""
        await self.put(ctx.guild.id, "vanity", {})
        await ctx.approve("Vanity rewards disabled")

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        cfg = await self.get(after.guild.id, "vanity")
        if not cfg.get("role"):
            return

        def has(m):
            return any(isinstance(a, discord.CustomActivity) and a.name and cfg["text"] in a.name.lower()
                       for a in m.activities)

        b, a = has(before), has(after)
        if b == a:
            return
        role = after.guild.get_role(cfg["role"])
        if not role:
            return
        try:
            if a and role not in after.roles:
                await after.add_roles(role, reason="vanity reward")
            elif not a and role in after.roles and after.status != discord.Status.offline:
                await after.remove_roles(role, reason="vanity removed")
        except discord.HTTPException:
            pass


async def setup(bot):
    await bot.add_cog(Config(bot))
