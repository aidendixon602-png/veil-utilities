"""Utility & info: snipe, afk, reminders, user/server info, avatars, emojis, embeds, stats, polls."""
from __future__ import annotations

import ast
import logging
import operator
import platform
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks

from core.utils import (Colors, Context, Duration, UserError, build_message, build_vars, chunk_embeds,
                        fmt_duration, has_perms, now, shorten, ts)

log = logging.getLogger("veil.utility")

CUSTOM_EMOJI = re.compile(r"<(a?):(\w+):(\d+)>")


class SnipeView(discord.ui.View):
    def __init__(self, author_id: int, queue: list[dict], index: int = 1):
        super().__init__(timeout=90)
        self.author_id = author_id
        self.queue = queue
        self.index = index
        self._sync()

    def _sync(self):
        self.prev_button.disabled = self.index <= 1
        self.next_button.disabled = self.index >= len(self.queue)

    def embed(self) -> discord.Embed:
        d = self.queue[self.index - 1]
        e = discord.Embed(description=d["content"] or None, color=Colors.main)
        e.set_author(name=str(d["author"]), icon_url=d["author"].display_avatar.url)
        media = d["attachments"] + d["stickers"]
        if media:
            e.set_image(url=media[0])
            if len(media) > 1:
                e.add_field(name="More attachments", value="\n".join(media[1:])[:1024])
        e.set_footer(text=f"Deleted {fmt_duration(time.time() - d['time'])} ago • {self.index}/{len(self.queue)}")
        return e

    @discord.ui.button(label="Prev", emoji="◀", style=discord.ButtonStyle.secondary)
    async def prev_button(self, it: discord.Interaction, btn: discord.ui.Button):
        if it.user.id != self.author_id:
            return await it.response.send_message("Only the person who ran the command can use these buttons", ephemeral=True)
        self.index = max(1, self.index - 1)
        self._sync()
        await it.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="Next", emoji="▶", style=discord.ButtonStyle.secondary)
    async def next_button(self, it: discord.Interaction, btn: discord.ui.Button):
        if it.user.id != self.author_id:
            return await it.response.send_message("Only the person who ran the command can use these buttons", ephemeral=True)
        self.index = min(len(self.queue), self.index + 1)
        self._sync()
        await it.response.edit_message(embed=self.embed(), view=self)


class Utility(commands.Cog):
    """Handy everyday tools"""
    emoji = "🛠️"

    def __init__(self, bot):
        self.bot = bot
        self.deleted: dict[int, deque] = defaultdict(lambda: deque(maxlen=50))
        self.edited: dict[int, deque] = defaultdict(lambda: deque(maxlen=50))
        self.reactions: dict[int, deque] = defaultdict(lambda: deque(maxlen=50))

    async def cog_load(self):
        self.reminder_loop.start()

    async def cog_unload(self):
        self.reminder_loop.cancel()

    # ================================================================ snipe
    @commands.Cog.listener()
    async def on_message_delete(self, m: discord.Message):
        if m.guild and not m.author.bot:
            self.deleted[m.channel.id].appendleft({
                "author": m.author, "content": m.content, "time": time.time(),
                "attachments": [a.proxy_url for a in m.attachments],
                "stickers": [s.url for s in m.stickers]})

    @commands.Cog.listener()
    async def on_message_edit(self, b: discord.Message, a: discord.Message):
        if a.guild and not a.author.bot and b.content != a.content:
            self.edited[a.channel.id].appendleft({"author": a.author, "before": b.content, "after": a.content,
                                                  "time": time.time(), "url": a.jump_url})

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, p: discord.RawReactionActionEvent):
        if p.guild_id:
            self.reactions[p.channel_id].appendleft({"user": p.user_id, "emoji": str(p.emoji),
                                                     "message": p.message_id, "time": time.time()})

    async def _snipe_allowed(self, ctx):
        cfg = await self.bot.db.get_config(ctx.guild.id, "settings")
        if cfg.get("snipe_disabled"):
            raise UserError("Snipe is disabled in this server")

    @commands.command(aliases=["s"])
    @commands.guild_only()
    async def snipe(self, ctx: Context, index: int = 1):
        """Show a recently deleted message"""
        await self._snipe_allowed(ctx)
        q = list(self.deleted[ctx.channel.id])
        if not q or not 1 <= index <= len(q):
            raise UserError("Nothing to snipe" if not q else f"Pick 1–{len(q)}")
        if len(q) > 1:
            view = SnipeView(ctx.author.id, q, index)
            await ctx.send(embed=view.embed(), view=view)
        else:
            d = q[index - 1]
            e = discord.Embed(description=d["content"] or None, color=Colors.main)
            e.set_author(name=str(d["author"]), icon_url=d["author"].display_avatar.url)
            media = d["attachments"] + d["stickers"]
            if media:
                e.set_image(url=media[0])
                if len(media) > 1:
                    e.add_field(name="More attachments", value="\n".join(media[1:])[:1024])
            e.set_footer(text=f"Deleted {fmt_duration(time.time() - d['time'])} ago • 1/1")
            await ctx.send(embed=e)

    @commands.command(aliases=["es"])
    @commands.guild_only()
    async def editsnipe(self, ctx: Context, index: int = 1):
        """Show a recently edited message"""
        await self._snipe_allowed(ctx)
        q = self.edited[ctx.channel.id]
        if not q or not 1 <= index <= len(q):
            raise UserError("Nothing to snipe")
        d = q[index - 1]
        e = discord.Embed(color=Colors.main, description=f"[jump]({d['url']})")
        e.add_field(name="Before", value=shorten(d["before"] or "*empty*"), inline=False)
        e.add_field(name="After", value=shorten(d["after"] or "*empty*"), inline=False)
        e.set_author(name=str(d["author"]), icon_url=d["author"].display_avatar.url)
        e.set_footer(text=f"Edited {fmt_duration(time.time() - d['time'])} ago • {index}/{len(q)}")
        await ctx.send(embed=e)

    @commands.command(aliases=["rs"])
    @commands.guild_only()
    async def reactionsnipe(self, ctx: Context):
        """Show recently removed reactions"""
        await self._snipe_allowed(ctx)
        q = list(self.reactions[ctx.channel.id])[:10]
        if not q:
            raise UserError("Nothing to snipe")
        lines = [f"<@{r['user']}> removed {r['emoji']} ({fmt_duration(time.time() - r['time'])} ago)" for r in q]
        await ctx.neutral("\n".join(lines))

    @commands.command(aliases=["cs"])
    @has_perms(manage_messages=True)
    async def clearsnipe(self, ctx: Context):
        """Clear snipe history for this channel"""
        for d in (self.deleted, self.edited, self.reactions):
            d.pop(ctx.channel.id, None)
        await ctx.approve("cleared the snipe history for this channel")

    @commands.command()
    @has_perms(manage_guild=True)
    async def togglesnipe(self, ctx: Context):
        """Enable/disable snipe commands server-wide"""
        cfg = await self.bot.db.get_config(ctx.guild.id, "settings")
        cfg["snipe_disabled"] = not cfg.get("snipe_disabled")
        await self.bot.db.set_config(ctx.guild.id, "settings", cfg)
        await ctx.approve(f"turned snipe **{'off' if cfg['snipe_disabled'] else 'on'}** for this server")

    # ================================================================ afk
    @commands.command()
    @commands.guild_only()
    async def afk(self, ctx: Context, *, reason: str = "AFK"):
        """Set an AFK status; people who mention you are told"""
        await self.bot.db.execute("INSERT OR REPLACE INTO afk VALUES(?,?,?,?)", ctx.guild.id, ctx.author.id,
                                  shorten(reason, 200), now())
        await ctx.approve(f"you're now AFK: **{shorten(reason, 200)}**")

    @commands.Cog.listener()
    async def on_message(self, m: discord.Message):
        if not m.guild or m.author.bot:
            return
        # stats
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        await self.bot.db.execute(
            "INSERT INTO msgstats VALUES(?,?,?,1) ON CONFLICT(guild_id,user_id,day) DO UPDATE SET count=count+1",
            m.guild.id, m.author.id, day)
        # afk return
        row = await self.bot.db.fetchone("SELECT since FROM afk WHERE guild_id=? AND user_id=?", m.guild.id, m.author.id)
        if row and not m.content.lower().lstrip(",.!?;").startswith("afk"):
            await self.bot.db.execute("DELETE FROM afk WHERE guild_id=? AND user_id=?", m.guild.id, m.author.id)
            await m.channel.send(embed=discord.Embed(color=Colors.neutral,
                                 description=f"welcome back {m.author.mention}, you were away for **{fmt_duration(now() - row['since'])}**"),
                                 delete_after=10)
        for user in m.mentions[:5]:
            r = await self.bot.db.fetchone("SELECT reason, since FROM afk WHERE guild_id=? AND user_id=?", m.guild.id, user.id)
            if r and user != m.author:
                await m.reply(embed=discord.Embed(color=Colors.neutral,
                              description=f"{user.mention} is AFK: **{r['reason']}** · {ts(r['since'])}"), delete_after=15)

    # ================================================================ reminders
    @commands.group(aliases=["remindme", "reminder"], invoke_without_command=True)
    async def remind(self, ctx: Context, duration: Duration, *, reminder: str = "something"):
        """`remind 2h30m take out the trash`"""
        count = (await self.bot.db.fetchone("SELECT COUNT(*) c FROM reminders WHERE user_id=?", ctx.author.id))["c"]
        if count >= 25:
            raise UserError(f"You have 25 reminders already — remove some with `{ctx.clean_prefix}remind delete <id>`")
        reminder = reminder[:1000]
        due = now() + duration.total_seconds()
        await self.bot.db.execute("INSERT INTO reminders(user_id,channel_id,due,content,created) VALUES(?,?,?,?,?)",
                                  ctx.author.id, ctx.channel.id, due, reminder, now())
        await ctx.approve(f"I'll remind you {ts(due)}: **{shorten(reminder, 200)}**")

    @remind.command(name="list")
    async def remind_list(self, ctx: Context):
        """Your reminders"""
        rows = await self.bot.db.fetchall("SELECT * FROM reminders WHERE user_id=? ORDER BY due", ctx.author.id)
        if not rows:
            return await ctx.warn("you have no reminders")
        await ctx.paginate(chunk_embeds(f"Your reminders ({len(rows)})",
                                        [f"`{r['id']}` {ts(r['due'])} — {shorten(r['content'], 80)}" for r in rows],
                                        numbered=False))

    @remind.command(name="delete", aliases=["remove", "cancel"])
    async def remind_delete(self, ctx: Context, reminder_id: int):
        """Cancel a reminder"""
        cur = await self.bot.db.execute("DELETE FROM reminders WHERE id=? AND user_id=?", reminder_id, ctx.author.id)
        if not cur.rowcount:
            raise UserError("No reminder with that ID")
        await ctx.approve(f"deleted reminder `{reminder_id}`")

    @tasks.loop(seconds=10)
    async def reminder_loop(self):
        for r in await self.bot.db.fetchall("SELECT * FROM reminders WHERE due<=?", now()):
            await self.bot.db.execute("DELETE FROM reminders WHERE id=?", r["id"])
            ch = self.bot.get_channel(r["channel_id"])
            e = discord.Embed(color=Colors.main, title="⏰ Reminder", description=r["content"])
            e.set_footer(text=f"Set {fmt_duration(now() - r['created'])} ago")
            try:
                if ch:
                    await ch.send(f"<@{r['user_id']}>", embed=e, allowed_mentions=discord.AllowedMentions(users=True))
                else:
                    user = await self.bot.fetch_user(r["user_id"])
                    await user.send(embed=e)
            except discord.HTTPException:
                pass
            except Exception:
                log.exception("Reminder %s failed", r["id"])

    @reminder_loop.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()

    # ================================================================ info
    @commands.command(aliases=["ui", "info", "whois", "user"])
    async def userinfo(self, ctx: Context, user: discord.Member | discord.User = None):
        """Info about a user"""
        user = user or ctx.author
        fetched = await self.bot.fetch_user(user.id)
        e = discord.Embed(color=user.color if getattr(user, "color", None) and user.color.value else Colors.main)
        e.set_author(name=f"{user} ({user.id})", icon_url=user.display_avatar.url)
        e.set_thumbnail(url=user.display_avatar.url)
        e.add_field(name="Created", value=f"{ts(user.created_at, 'D')}\n{ts(user.created_at)}")
        if isinstance(user, discord.Member):
            if user.joined_at:
                e.add_field(name="Joined", value=f"{ts(user.joined_at, 'D')}\n{ts(user.joined_at)}")
            if user.premium_since:
                e.add_field(name="Boosting since", value=ts(user.premium_since, "D"))
            members = sorted(user.guild.members, key=lambda m: m.joined_at or discord.utils.utcnow())
            pos = members.index(user) + 1 if user in members else None
            roles = [r.mention for r in user.roles[1:][::-1]]
            e.add_field(name=f"Roles ({len(roles)})", value=shorten(" ".join(roles) or "none"), inline=False)
            if pos:
                e.set_footer(text=f"Join position #{pos:,}")
        badges = [f.name.replace("_", " ") for f in user.public_flags.all()]
        if badges:
            e.add_field(name="Badges", value=", ".join(badges), inline=False)
        if fetched.banner:
            e.set_image(url=fetched.banner.url)
        await ctx.send(embed=e)

    @commands.command(aliases=["si", "guildinfo"])
    @commands.guild_only()
    async def serverinfo(self, ctx: Context):
        """Info about this server"""
        g = ctx.guild
        e = discord.Embed(title=g.name, color=Colors.main, description=g.description)
        if g.icon:
            e.set_thumbnail(url=g.icon.url)
        if g.banner:
            e.set_image(url=g.banner.url)
        e.add_field(name="Owner", value=f"<@{g.owner_id}>")
        e.add_field(name="Created", value=ts(g.created_at))
        e.add_field(name="Members", value=f"{g.member_count:,}\n{sum(m.bot for m in g.members)} bots")
        e.add_field(name="Channels", value=f"{len(g.text_channels)} text • {len(g.voice_channels)} voice\n{len(g.categories)} categories")
        e.add_field(name="Roles", value=str(len(g.roles)))
        e.add_field(name="Emojis", value=f"{len(g.emojis)}/{g.emoji_limit * 2}")
        e.add_field(name="Boosts", value=f"{g.premium_subscription_count} (tier {g.premium_tier})")
        e.add_field(name="Verification", value=str(g.verification_level))
        e.add_field(name="Vanity", value=f"/{g.vanity_url_code}" if g.vanity_url_code else "none")
        e.set_footer(text=f"ID {g.id}")
        await ctx.send(embed=e)

    @commands.command(aliases=["av", "pfp"])
    async def avatar(self, ctx: Context, user: discord.Member | discord.User = None):
        """Get a user's avatar"""
        user = user or ctx.author
        a = user.avatar or user.default_avatar
        e = discord.Embed(color=Colors.main, title=f"{user.name}'s avatar", url=a.url)
        e.set_image(url=a.url)
        view = discord.ui.View()
        for fmt in ("png", "jpg", "webp") + (("gif",) if a.is_animated() else ()):
            view.add_item(discord.ui.Button(label=fmt.upper(), url=a.with_format(fmt).with_size(4096).url))
        await ctx.send(embed=e, view=view)

    @commands.command(aliases=["sav", "spfp"])
    @commands.guild_only()
    async def serveravatar(self, ctx: Context, member: discord.Member = None):
        """Get a member's server-specific avatar"""
        member = member or ctx.author
        if not member.guild_avatar:
            raise UserError(f"{member.mention} has no server avatar")
        e = discord.Embed(color=Colors.main, title=f"{member.name}'s server avatar")
        e.set_image(url=member.guild_avatar.url)
        await ctx.send(embed=e)

    @commands.command()
    async def banner(self, ctx: Context, user: discord.User = None):
        """Get a user's banner"""
        user = await self.bot.fetch_user((user or ctx.author).id)
        if not user.banner:
            raise UserError(f"**{user}** has no banner")
        e = discord.Embed(color=user.accent_color or Colors.main, title=f"{user.name}'s banner", url=user.banner.url)
        e.set_image(url=user.banner.url)
        await ctx.send(embed=e)

    @commands.command(aliases=["servericon"])
    @commands.guild_only()
    async def icon(self, ctx: Context):
        """Get the server icon"""
        if not ctx.guild.icon:
            raise UserError("This server has no icon")
        await ctx.send(embed=discord.Embed(color=Colors.main, title=ctx.guild.name).set_image(url=ctx.guild.icon.url))

    @commands.command(aliases=["serverbanner"])
    @commands.guild_only()
    async def sbanner(self, ctx: Context):
        """Get the server banner"""
        if not ctx.guild.banner:
            raise UserError("This server has no banner")
        await ctx.send(embed=discord.Embed(color=Colors.main, title=ctx.guild.name).set_image(url=ctx.guild.banner.url))

    @commands.command(aliases=["ri"])
    @commands.guild_only()
    async def roleinfo(self, ctx: Context, *, role: discord.Role):
        """Info about a role"""
        e = discord.Embed(color=role.color, title=role.name)
        e.add_field(name="ID", value=f"`{role.id}`")
        e.add_field(name="Members", value=str(len(role.members)))
        e.add_field(name="Color", value=str(role.color))
        e.add_field(name="Created", value=ts(role.created_at))
        e.add_field(name="Hoisted / Mentionable", value=f"{role.hoist} / {role.mentionable}")
        e.add_field(name="Position", value=str(role.position))
        perms = [p.replace("_", " ") for p, v in role.permissions if v]
        e.add_field(name="Key permissions", value=shorten(", ".join(perms) or "none"), inline=False)
        if role.display_icon and isinstance(role.display_icon, discord.Asset):
            e.set_thumbnail(url=role.display_icon.url)
        await ctx.send(embed=e)

    @commands.command()
    @commands.guild_only()
    async def inrole(self, ctx: Context, *, role: discord.Role):
        """List members with a role"""
        await ctx.paginate(chunk_embeds(f"Members in {role.name} ({len(role.members)})",
                                        [f"{m.mention} (`{m.id}`)" for m in role.members], 15))

    @commands.command(aliases=["mc"])
    @commands.guild_only()
    async def membercount(self, ctx: Context):
        """Member count breakdown"""
        g = ctx.guild
        bots = sum(m.bot for m in g.members)
        recent = sum(1 for m in g.members if m.joined_at and (discord.utils.utcnow() - m.joined_at).days < 1)
        e = discord.Embed(color=Colors.main, title=g.name)
        e.add_field(name="Total", value=f"{g.member_count:,}")
        e.add_field(name="Humans", value=f"{g.member_count - bots:,}")
        e.add_field(name="Bots", value=f"{bots:,}")
        e.set_footer(text=f"+{recent} in the last 24h")
        await ctx.send(embed=e)

    @commands.command(aliases=["firstmsg"])
    @commands.guild_only()
    async def firstmessage(self, ctx: Context, channel: discord.TextChannel = None):
        """Jump to the first message in a channel"""
        channel = channel or ctx.channel
        async for m in channel.history(limit=1, oldest_first=True):
            return await ctx.neutral(f"[first message in {channel.mention}]({m.jump_url}) by {m.author.mention} {ts(m.created_at)}")
        raise UserError("No messages found")

    # ================================================================ emojis
    @commands.command(name="emoji", aliases=["e", "jumbo", "enlarge"])
    async def emoji_cmd(self, ctx: Context, emoji: str):
        """Enlarge a custom emoji"""
        m = CUSTOM_EMOJI.fullmatch(emoji)
        if not m:
            raise UserError("Give a custom emoji")
        url = f"https://cdn.discordapp.com/emojis/{m.group(3)}.{'gif' if m.group(1) else 'png'}?size=1024"
        await ctx.send(embed=discord.Embed(color=Colors.main, title=m.group(2), url=url).set_image(url=url))

    @commands.command(aliases=["addemoji", "stealemoji"])
    @has_perms(manage_expressions=True)
    async def steal(self, ctx: Context, *emojis: str):
        """Add emojis from other servers (or reply to a message containing them)"""
        source = " ".join(emojis)
        if not source and ctx.message.reference and isinstance(ctx.message.reference.resolved, discord.Message):
            source = ctx.message.reference.resolved.content
        found = CUSTOM_EMOJI.findall(source)[:10]
        if not found:
            raise UserError("No custom emojis found")
        added = []
        for anim, name, eid in found:
            url = f"https://cdn.discordapp.com/emojis/{eid}.{'gif' if anim else 'png'}"
            async with self.bot.session.get(url) as r:
                data = await r.read()
            try:
                added.append(str(await ctx.guild.create_custom_emoji(name=name, image=data, reason=f"by {ctx.author}")))
            except discord.HTTPException as e:
                await ctx.warn(f"couldn't add `{name}`: {e.text}")
        if added:
            await ctx.approve(f"added {' '.join(added)}")

    # ================================================================ embeds / say
    @commands.command(aliases=["ce", "embed"])
    @has_perms(manage_messages=True)
    async def createembed(self, ctx: Context, *, script: str):
        """Send a message from an embed script — see `embedhelp`"""
        payload = build_message(script, build_vars(ctx.author, ctx.guild, ctx.channel))
        await ctx.send(**payload)

    @commands.command()
    async def embedhelp(self, ctx: Context):
        """How to write embed scripts"""
        e = discord.Embed(title="Embed scripting", color=Colors.main, description=(
            "Start with `{embed}` and separate parts with `$v`:\n"
            "```{embed}$v{title: Welcome!}$v{description: Hi {user.mention}}\n$v{color: #b10f2e}$v{thumbnail: {user.avatar}}\n"
            "$v{field: Members && {guild.count} && true}\n$v{footer: {guild.name} && {guild.icon}}$v{button: Rules && https://...}```\n"
            "Parts: `title` `description` `color` `url` `image` `thumbnail` `author: name && icon && url` "
            "`footer: text && icon` `field: name && value && inline` `button: label && url` `content` `timestamp`\n\n"
            f"Use variables anywhere — see `{ctx.clean_prefix}placeholders`. Works in welcome, goodbye, boost, "
            "autoresponders, sticky messages, level-ups and bump reminders."))
        await ctx.send(embed=e)

    @commands.command(aliases=["copyembed"])
    @has_perms(manage_messages=True)
    async def embedcode(self, ctx: Context, message_link: str = None):
        """Turn an existing embed into a script (reply to it or give a link)"""
        msg = None
        if ctx.message.reference and isinstance(ctx.message.reference.resolved, discord.Message):
            msg = ctx.message.reference.resolved
        elif message_link:
            msg = await self.bot.get_cog("Config")._resolve_message(ctx, message_link)
        if not msg or not msg.embeds:
            raise UserError("Reply to a message with an embed")
        e = msg.embeds[0]
        parts = ["{embed}"]
        if msg.content:
            parts.append(f"{{content: {msg.content}}}")
        if e.title:
            parts.append(f"{{title: {e.title}}}")
        if e.description:
            parts.append(f"{{description: {e.description}}}")
        if e.color:
            parts.append(f"{{color: #{e.color.value:06x}}}")
        if e.author and e.author.name:
            parts.append(f"{{author: {e.author.name} && {e.author.icon_url or ''}}}")
        for f in e.fields:
            parts.append(f"{{field: {f.name} && {f.value} && {str(f.inline).lower()}}}")
        if e.image:
            parts.append(f"{{image: {e.image.url}}}")
        if e.thumbnail:
            parts.append(f"{{thumbnail: {e.thumbnail.url}}}")
        if e.footer and e.footer.text:
            parts.append(f"{{footer: {e.footer.text} && {e.footer.icon_url or ''}}}")
        await ctx.send(f"```{shorten('$v'.join(parts), 1990)}```")

    @commands.command()
    @has_perms(manage_messages=True)
    async def say(self, ctx: Context, channel: discord.TextChannel | None = None, *, message: str):
        """Make the bot say something (embed scripts supported)"""
        dest = channel or ctx.channel
        await dest.send(**build_message(message, build_vars(ctx.author, ctx.guild, dest)),
                        allowed_mentions=discord.AllowedMentions.none())
        if dest == ctx.channel:
            try:
                await ctx.message.delete()
            except discord.HTTPException:
                pass

    # ================================================================ staff notes
    @commands.group(aliases=["note"], invoke_without_command=True)
    @has_perms(manage_messages=True)
    @commands.guild_only()
    async def notes(self, ctx: Context, *, member: discord.Member):
        """View staff notes for a member"""
        rows = await self.bot.db.fetchall(
            "SELECT id, note, author_id, created_at FROM member_notes WHERE guild_id=? AND user_id=? ORDER BY id ASC",
            ctx.guild.id, member.id
        )
        if not rows:
            return await ctx.warn(f"{member.mention} has no notes")
        lines = [f"`#{r['id']}` {r['note']} — <@{r['author_id']}> ({ts(r['created_at'], 'R')})" for r in rows]
        await ctx.paginate(chunk_embeds(f"Notes for {member} ({len(rows)})", lines))

    @notes.command(name="add")
    @has_perms(manage_messages=True)
    @commands.guild_only()
    async def notes_add(self, ctx: Context, member: discord.Member, *, note: str):
        """Add a staff note for a member"""
        if len(note) > 1000:
            raise UserError("Note cannot exceed 1000 characters")
        await self.bot.db.execute(
            "INSERT INTO member_notes(guild_id, user_id, author_id, note, created_at) VALUES(?,?,?,?,?)",
            ctx.guild.id, member.id, ctx.author.id, note, now()
        )
        await ctx.approve(f"added a note for {member.mention}")

    @notes.command(name="clear", aliases=["remove", "del"])
    @has_perms(manage_messages=True)
    @commands.guild_only()
    async def notes_clear(self, ctx: Context, member: discord.Member):
        """Clear all staff notes for a member"""
        await self.bot.db.execute(
            "DELETE FROM member_notes WHERE guild_id=? AND user_id=?",
            ctx.guild.id, member.id
        )
        await ctx.approve(f"cleared the notes for {member.mention}")

    # ================================================================ polls / misc
    @commands.command()
    @has_perms(manage_messages=True)
    async def poll(self, ctx: Context, *, question: str):
        """Quick poll — `poll Pizza? | Yes | No | Maybe` for options"""
        parts = [p.strip() for p in question.split("|") if p.strip()]
        e = discord.Embed(color=Colors.main, title=parts[0])
        e.set_footer(text=f"Poll by {ctx.author}")
        msg = await ctx.send(embed=e)
        if len(parts) == 1:
            for r in ("👍", "👎"):
                await msg.add_reaction(r)
        else:
            nums = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
            e.description = "\n".join(f"{nums[i]} {o}" for i, o in enumerate(parts[1:11]))
            await msg.edit(embed=e)
            for i in range(len(parts[1:11])):
                await msg.add_reaction(nums[i])

    _OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
            ast.Pow: operator.pow, ast.Mod: operator.mod, ast.FloorDiv: operator.floordiv, ast.USub: operator.neg}

    _MAX_BITS = 2048  # ~600 digits; keeps every step cheap and the answer inside one embed

    def _eval(self, node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return self._bounded(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in self._OPS:
            l, r = self._eval(node.left), self._eval(node.right)
            if isinstance(node.op, ast.Pow):
                if abs(r) > 100:
                    raise ValueError("exponent too large")
                # estimate the size *before* computing: (10**100)**100**... used to exhaust memory
                if isinstance(l, int) and abs(l) > 1 and abs(l).bit_length() * abs(r) > self._MAX_BITS:
                    raise ValueError("result too large")
            return self._bounded(self._OPS[type(node.op)](l, r))
        if isinstance(node, ast.UnaryOp) and type(node.op) in self._OPS:
            return self._OPS[type(node.op)](self._eval(node.operand))
        raise ValueError("unsupported expression")

    def _bounded(self, value):
        if isinstance(value, complex):
            raise ValueError("complex result")
        if isinstance(value, int) and value.bit_length() > self._MAX_BITS:
            raise ValueError("result too large")
        return value

    @commands.command(aliases=["calc", "math"])
    async def calculate(self, ctx: Context, *, expression: str):
        """Safely evaluate a math expression"""
        try:
            result = self._eval(ast.parse(expression.replace("^", "**").replace("x", "*"), mode="eval").body)
        except (ValueError, SyntaxError, ZeroDivisionError, OverflowError, RecursionError) as e:
            raise UserError(f"Can't calculate that: {e}") from None
        await ctx.neutral(f"`{expression}` = **{result:,}**" if isinstance(result, int) else f"`{expression}` = **{result:,.6g}**")

    # ================================================================ message stats
    @commands.command(aliases=["msgs"])
    @commands.guild_only()
    async def messages(self, ctx: Context, member: discord.Member = None):
        """Message stats for a member"""
        member = member or ctx.author
        rows = await self.bot.db.fetchall("SELECT day, count FROM msgstats WHERE guild_id=? AND user_id=? ORDER BY day DESC",
                                          ctx.guild.id, member.id)
        total = sum(r["count"] for r in rows)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        week = sum(r["count"] for r in rows[:7])
        e = discord.Embed(color=Colors.main)
        e.set_author(name=member.display_name, icon_url=member.display_avatar.url)
        e.add_field(name="Today", value=f"{next((r['count'] for r in rows if r['day'] == today), 0):,}")
        e.add_field(name="Last 7 active days", value=f"{week:,}")
        e.add_field(name="All time", value=f"{total:,}")
        if rows:
            spark = " ".join(str(r["count"]) for r in reversed(rows[:7]))
            e.set_footer(text=f"Recent days: {spark}")
        await ctx.send(embed=e)

    @commands.command(aliases=["topmsgs", "topmessages"])
    @commands.guild_only()
    async def messageboard(self, ctx: Context, days: int = 0):
        """Most active members (optionally over the last N days)"""
        if days:
            since = datetime.fromtimestamp(now() - days * 86400, timezone.utc).strftime("%Y-%m-%d")
            rows = await self.bot.db.fetchall("SELECT user_id, SUM(count) c FROM msgstats WHERE guild_id=? AND day>=? "
                                              "GROUP BY user_id ORDER BY c DESC LIMIT 100", ctx.guild.id, since)
        else:
            rows = await self.bot.db.fetchall("SELECT user_id, SUM(count) c FROM msgstats WHERE guild_id=? "
                                              "GROUP BY user_id ORDER BY c DESC LIMIT 100", ctx.guild.id)
        if not rows:
            return await ctx.warn("no messages have been counted yet")
        await ctx.paginate(chunk_embeds(f"Top chatters{f' ({days}d)' if days else ''}",
                                        [f"<@{r['user_id']}> — **{r['c']:,}** messages" for r in rows]))

    # ================================================================ bot info
    @commands.command()
    async def ping(self, ctx: Context):
        """Latency"""
        t = time.perf_counter()
        msg = await ctx.neutral("pinging…")
        api = (time.perf_counter() - t) * 1000
        t_db = time.perf_counter()
        try:
            await self.bot.db.fetchone("SELECT 1")
            db_ms = (time.perf_counter() - t_db) * 1000
            db_str = f" · database **{db_ms:.1f}ms**"
        except Exception:
            db_str = ""
        await msg.edit(embed=discord.Embed(color=Colors.neutral,
                       description=f"gateway **{self.bot.latency * 1000:.0f}ms** · api **{api:.0f}ms**{db_str}"))

    @commands.command(aliases=["bi", "about", "stats"])
    async def botinfo(self, ctx: Context):
        """About the bot"""
        b = self.bot
        from cogs.owner import memory_mb
        e = discord.Embed(color=Colors.main, title=b.user.name,
                          description="**veil utilities** — a fast all-in-one bot: antinuke, moderation, VoiceMaster, leveling, music, Last.fm and more.")
        e.set_thumbnail(url=b.user.display_avatar.url)
        e.add_field(name="Servers", value=f"{len(b.guilds):,}")
        e.add_field(name="Users", value=f"{sum(g.member_count or 0 for g in b.guilds):,}")
        e.add_field(name="Channels", value=f"{sum(len(g.channels) for g in b.guilds):,}")
        e.add_field(name="Commands", value=str(sum(1 for _ in b.walk_commands())))
        e.add_field(name="Uptime", value=fmt_duration(time.time() - b.started_at))
        e.add_field(name="Latency", value=f"{b.latency * 1000:.0f}ms")
        e.add_field(name="Memory (RAM)", value=memory_mb())
        e.add_field(name="Library", value=f"discord.py {discord.__version__}")
        e.add_field(name="Python", value=f"v{platform.python_version()}")
        await ctx.send(embed=e)

    @commands.command(aliases=["up"])
    async def uptime(self, ctx: Context):
        """How long the bot has been online"""
        await ctx.neutral(f"up for **{fmt_duration(time.time() - self.bot.started_at)}** (since {ts(self.bot.started_at)})")

    @commands.command(aliases=["inv"])
    async def invite(self, ctx: Context):
        """Invite the bot"""
        url = discord.utils.oauth_url(self.bot.user.id, permissions=discord.Permissions(administrator=True),
                                      scopes=("bot", "applications.commands"))
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Invite", url=url))
        await ctx.neutral(f"invite **{self.bot.user.name}** to your server", view=view)


async def setup(bot):
    await bot.add_cog(Utility(bot))
