"""Chat filters: invites, links, spam, caps, mass mentions, emojis, blocked words, regex."""
from __future__ import annotations

import re
import time
from collections import defaultdict, deque
from datetime import timedelta

import discord
from discord.ext import commands

from core.utils import Colors, Context, UserError, chunk_embeds, has_perms

INVITE = re.compile(r"(discord\.(gg|io|me|li)|discord(app)?\.com/invite)/[\w-]+", re.I)
URL = re.compile(r"https?://\S+|www\.\S+", re.I)
EMOJI = re.compile(r"<a?:\w+:\d+>|[\U0001F300-\U0001FAFF☀-➿]")
FILTERS = ("invites", "links", "spam", "caps", "massmention", "emojis", "words", "regex", "zalgo", "newlines")
ZALGO = re.compile(r"[̀-ͯ҉]{3,}")


class Automod(commands.Cog):
    """Automatic chat filters"""
    emoji = "🧹"

    def __init__(self, bot):
        self.bot = bot
        self.spam: dict[tuple[int, int], deque] = defaultdict(deque)

    async def cfg(self, gid):
        return await self.bot.db.get_config(gid, "filters", {"exempt_roles": [], "exempt_channels": [],
                                                             "action": "delete", "timeout": 60})

    async def save(self, gid, cfg):
        await self.bot.db.set_config(gid, "filters", cfg)

    # ---------------------------------------------------------------- engine
    def check(self, cfg: dict, m: discord.Message) -> str | None:
        c = m.content
        if cfg.get("invites") and INVITE.search(c):
            return "invite links aren't allowed"
        if cfg.get("links") and URL.search(c):
            allowed = cfg.get("links_allow", [])
            if not all(any(a in u for a in allowed) for u in URL.findall(c)):
                return "links aren't allowed"
        if cfg.get("massmention") and len(set(m.raw_mentions)) >= cfg["massmention"]:
            return "too many mentions"
        if cfg.get("caps"):
            letters = [ch for ch in c if ch.isalpha()]
            if len(letters) >= 10 and sum(ch.isupper() for ch in letters) / len(letters) * 100 >= cfg["caps"]:
                return "too many capital letters"
        if cfg.get("emojis") and len(EMOJI.findall(c)) >= cfg["emojis"]:
            return "too many emojis"
        if cfg.get("newlines") and c.count("\n") >= cfg["newlines"]:
            return "too many lines"
        if cfg.get("zalgo") and ZALGO.search(c):
            return "zalgo text isn't allowed"
        low = c.lower()
        for w in cfg.get("words", []):
            if w.endswith("*") and w[:-1] in low or re.search(rf"\b{re.escape(w)}\b", low):
                return "that word isn't allowed"
        for pattern in cfg.get("regex", []):
            try:
                if re.search(pattern, c, re.I):
                    return "message matched a blocked pattern"
            except re.error:
                continue
        if cfg.get("spam"):
            limit, secs = cfg["spam"]
            q = self.spam[(m.guild.id, m.author.id)]
            t = time.monotonic()
            q.append(t)
            while q and t - q[0] > secs:
                q.popleft()
            if len(q) > limit:
                return "slow down — you're sending messages too fast"
        return None

    @commands.Cog.listener()
    async def on_message(self, m: discord.Message):
        if not m.guild or m.author.bot or not isinstance(m.author, discord.Member):
            return
        cfg = await self.cfg(m.guild.id)
        if not any(cfg.get(f) for f in FILTERS):
            return
        if m.author.guild_permissions.manage_messages or m.channel.id in cfg.get("exempt_channels", []) \
                or any(r.id in cfg.get("exempt_roles", []) for r in m.author.roles):
            return
        why = self.check(cfg, m)
        if not why:
            return
        try:
            await m.delete()
        except discord.HTTPException:
            pass
        if cfg.get("action") == "timeout":
            try:
                await m.author.timeout(timedelta(seconds=cfg.get("timeout", 60)), reason=f"Automod: {why}")
            except discord.HTTPException:
                pass
        try:
            await m.channel.send(embed=discord.Embed(color=Colors.warn, description=f"⚠️ {m.author.mention}: {why}"),
                                 delete_after=5)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        if before.content != after.content:
            await self.on_message(after)

    # ---------------------------------------------------------------- commands
    @commands.group(aliases=["automod", "am"], invoke_without_command=True)
    @has_perms(manage_guild=True)
    async def filter(self, ctx: Context):
        """View automod filters"""
        cfg = await self.cfg(ctx.guild.id)
        embed = discord.Embed(title="Automod filters", color=Colors.main)
        for f in FILTERS:
            v = cfg.get(f)
            if f in ("words", "regex"):
                v = f"{len(v)} entries" if v else None
            elif f == "spam" and v:
                v = f"{v[0]} msgs / {v[1]}s"
            embed.add_field(name=f, value=f"🟢 {v}" if v and v is not True else ("🟢 on" if v else "🔴 off"))
        embed.add_field(name="action", value=f"{cfg.get('action')} ({cfg.get('timeout')}s)" if cfg.get('action') == "timeout" else "delete")
        embed.set_footer(text="Members with Manage Messages are exempt")
        await ctx.send(embed=embed)

    async def _toggle(self, ctx, key, value, label):
        cfg = await self.cfg(ctx.guild.id)
        if value in (None, 0, False):
            cfg.pop(key, None)
        else:
            cfg[key] = value
        await self.save(ctx.guild.id, cfg)
        await ctx.approve(f"**{label}** filter {'enabled' if value else 'disabled'}")

    @staticmethod
    def _on(state: str) -> bool:
        return state.lower() in ("on", "true", "enable", "yes")

    @filter.command(name="invites")
    @has_perms(manage_guild=True)
    async def f_invites(self, ctx, state: str):
        """Block Discord invites (on/off)"""
        await self._toggle(ctx, "invites", self._on(state), "Invite")

    @filter.command(name="links")
    @has_perms(manage_guild=True)
    async def f_links(self, ctx, state: str):
        """Block all links (on/off)"""
        await self._toggle(ctx, "links", self._on(state), "Link")

    @filter.command(name="allowlink")
    @has_perms(manage_guild=True)
    async def f_allowlink(self, ctx, domain: str):
        """Toggle a domain allowed through the link filter (e.g. youtube.com)"""
        cfg = await self.cfg(ctx.guild.id)
        lst = cfg.setdefault("links_allow", [])
        lst.remove(domain) if domain in lst else lst.append(domain)
        await self.save(ctx.guild.id, cfg)
        await ctx.approve(f"Allowed domains: {', '.join(lst) or 'none'}")

    @filter.command(name="spam")
    @has_perms(manage_guild=True)
    async def f_spam(self, ctx, messages: int = 0, seconds: int = 5):
        """Limit messages per N seconds: `filter spam 5 5` (0 disables)"""
        await self._toggle(ctx, "spam", [messages, seconds] if messages else None, "Spam")

    @filter.command(name="caps")
    @has_perms(manage_guild=True)
    async def f_caps(self, ctx, percent: int = 0):
        """Block messages over N% caps (0 disables)"""
        await self._toggle(ctx, "caps", percent, "Caps")

    @filter.command(name="massmention", aliases=["mentions"])
    @has_perms(manage_guild=True)
    async def f_mentions(self, ctx, limit: int = 0):
        """Block messages with N+ mentions (0 disables)"""
        await self._toggle(ctx, "massmention", limit, "Mass mention")

    @filter.command(name="emojis")
    @has_perms(manage_guild=True)
    async def f_emojis(self, ctx, limit: int = 0):
        """Block messages with N+ emojis (0 disables)"""
        await self._toggle(ctx, "emojis", limit, "Emoji")

    @filter.command(name="newlines")
    @has_perms(manage_guild=True)
    async def f_newlines(self, ctx, limit: int = 0):
        """Block messages with N+ line breaks (0 disables)"""
        await self._toggle(ctx, "newlines", limit, "Newline")

    @filter.command(name="zalgo")
    @has_perms(manage_guild=True)
    async def f_zalgo(self, ctx, state: str):
        """Block zalgo/glitch text"""
        await self._toggle(ctx, "zalgo", self._on(state), "Zalgo")

    @filter.command(name="word", aliases=["words", "add"])
    @has_perms(manage_guild=True)
    async def f_word(self, ctx, *, word: str):
        """Toggle a blocked word (end with * to match partial words)"""
        cfg = await self.cfg(ctx.guild.id)
        lst = cfg.setdefault("words", [])
        word = word.lower()
        if word in lst:
            lst.remove(word); msg = f"Unblocked `{word}`"
        else:
            lst.append(word); msg = f"Blocked `{word}`"
        await self.save(ctx.guild.id, cfg)
        await ctx.approve(msg)

    @filter.command(name="regex")
    @has_perms(manage_guild=True)
    async def f_regex(self, ctx, *, pattern: str):
        """Toggle a blocked regex pattern"""
        try:
            re.compile(pattern)
        except re.error as e:
            raise UserError(f"Invalid regex: {e}")
        cfg = await self.cfg(ctx.guild.id)
        lst = cfg.setdefault("regex", [])
        lst.remove(pattern) if pattern in lst else lst.append(pattern)
        await self.save(ctx.guild.id, cfg)
        await ctx.approve(f"Toggled pattern `{pattern}`")

    @filter.command(name="list")
    @has_perms(manage_guild=True)
    async def f_list(self, ctx):
        """List blocked words and patterns"""
        cfg = await self.cfg(ctx.guild.id)
        lines = [f"`{w}`" for w in cfg.get("words", [])] + [f"regex `{p}`" for p in cfg.get("regex", [])]
        await ctx.paginate(chunk_embeds("Blocked words", lines, 20))

    @filter.command(name="exempt")
    @has_perms(manage_guild=True)
    async def f_exempt(self, ctx, target: discord.Role | discord.TextChannel):
        """Toggle a role or channel bypassing filters"""
        cfg = await self.cfg(ctx.guild.id)
        key = "exempt_roles" if isinstance(target, discord.Role) else "exempt_channels"
        lst = cfg.setdefault(key, [])
        lst.remove(target.id) if target.id in lst else lst.append(target.id)
        await self.save(ctx.guild.id, cfg)
        await ctx.approve(f"Toggled exemption for {target.mention}")

    @filter.command(name="action")
    @has_perms(manage_guild=True)
    async def f_action(self, ctx, action: str, seconds: int = 60):
        """What happens on a violation: `delete` or `timeout <seconds>`"""
        if action not in ("delete", "timeout"):
            raise UserError("Action must be `delete` or `timeout`")
        cfg = await self.cfg(ctx.guild.id)
        cfg["action"], cfg["timeout"] = action, seconds
        await self.save(ctx.guild.id, cfg)
        await ctx.approve(f"Violations → **{action}**" + (f" for {seconds}s" if action == "timeout" else ""))


async def setup(bot):
    await bot.add_cog(Automod(bot))
