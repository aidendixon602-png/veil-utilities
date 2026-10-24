"""Social feeds: post new YouTube videos, Twitch streams, SoundCloud tracks, Reddit posts, Instagram/TikTok/X posts
(via an RSSHub instance) and any RSS feed into a channel."""
from __future__ import annotations

import asyncio
import os
import re
import time

import aiohttp
import discord
import feedparser
from discord.ext import commands, tasks

from core.config import settings
from core.utils import Colors, Context, UserError, build_vars, chunk_embeds, has_perms, send_script, shorten

PLATFORMS = {
    "youtube": ("YouTube", 0xFF0000), "twitch": ("Twitch", 0x9146FF), "soundcloud": ("SoundCloud", 0xFF5500),
    "reddit": ("Reddit", 0xFF4500), "instagram": ("Instagram", 0xE1306C), "tiktok": ("TikTok", 0x010101),
    "x": ("X", 0x0F1419), "rss": ("RSS", Colors.main),
}
ALIASES = {"yt": "youtube", "twitter": "x", "ig": "instagram", "insta": "instagram", "sc": "soundcloud",
           "tt": "tiktok", "subreddit": "reddit", "r": "reddit"}
RSSHUB_ROUTES = {  # override with RSSHUB_ROUTE_<PLATFORM> if your instance uses different routes
    "instagram": "/instagram/user/{user}",
    "tiktok": "/tiktok/user/@{user}",
    "x": "/twitter/user/{user}",
}
MAX_FEEDS = 30
MAX_FEED_BYTES = 5 * 1024 * 1024


class Feeds(commands.Cog):
    """Auto-post new content from social platforms"""
    emoji = "📡"

    def __init__(self, bot):
        self.bot = bot
        self.rsshub = settings.rsshub_url
        self.twitch_id = settings.twitch_client_id
        self.twitch_secret = settings.twitch_client_secret
        self._twitch_token: tuple[str, float] | None = None

    async def cog_load(self):
        self.poll.start()

    async def cog_unload(self):
        self.poll.cancel()

    async def cfg(self, gid):
        return await self.bot.db.get_config(gid, "feeds", {"next": 1, "items": {}})

    # ================================================================ fetching
    async def get_text(self, url: str) -> str:
        # feed URLs can come from users (rss platform), so use the public-only session and cap the size.
        # The owner's own RSSHUB_URL is trusted (it's often self-hosted on localhost).
        trusted = bool(self.rsshub) and url.startswith(self.rsshub.rstrip("/") + "/")
        session = self.bot.session if trusted else self.bot.public_session
        try:
            async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=20),
                    headers={"User-Agent": "Mozilla/5.0 (compatible; veil-utilities feeds)"}) as r:
                if r.status >= 400:
                    raise UserError(f"The feed returned an error ({r.status})")
                raw = await r.content.read(MAX_FEED_BYTES + 1)
                if len(raw) > MAX_FEED_BYTES:
                    raise UserError("That feed is too large")
                return raw.decode(r.get_encoding() if r.charset else "utf-8", errors="replace")
        except (aiohttp.ClientError, OSError):
            raise UserError("I couldn't reach that feed") from None

    async def parse(self, url: str):
        text = await self.get_text(url)
        return await asyncio.to_thread(feedparser.parse, text)

    async def resolve(self, platform: str, account: str) -> tuple[str, str]:
        """Return (feed url, display label)."""
        account = account.strip().lstrip("@")
        if platform == "youtube":
            if re.fullmatch(r"UC[\w-]{22}", account):
                cid = account
            else:
                page = await self.get_text(f"https://www.youtube.com/@{account}")
                m = re.search(r'"(?:channelId|externalId)":"(UC[\w-]{22})"', page) or \
                    re.search(r"youtube\.com/channel/(UC[\w-]{22})", page)
                if not m:
                    raise UserError("I couldn't find that YouTube channel — try its channel ID (starts with `UC`)")
                cid = m.group(1)
            return f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}", account
        if platform == "reddit":
            sub = account.removeprefix("r/")
            return f"https://www.reddit.com/r/{sub}/new/.rss", f"r/{sub}"
        if platform == "soundcloud":
            page = await self.get_text(f"https://soundcloud.com/{account}")
            m = re.search(r"soundcloud:users:(\d+)", page)
            if not m:
                raise UserError("I couldn't find that SoundCloud user")
            return f"https://feeds.soundcloud.com/users/soundcloud:users:{m.group(1)}/sounds.rss", account
        if platform in RSSHUB_ROUTES:
            route = os.getenv(f"RSSHUB_ROUTE_{platform.upper()}", RSSHUB_ROUTES[platform])
            return self.rsshub + route.format(user=account), f"@{account}"
        if platform == "twitch":
            return f"twitch:{account.lower()}", account.lower()
        if platform == "rss":
            if not account.startswith("http"):
                raise UserError("Give the full feed URL")
            return account, account.split("/")[2]
        raise UserError("Unknown platform")

    async def twitch_token(self) -> str:
        if not (self.twitch_id and self.twitch_secret):
            raise UserError("Twitch alerts need `TWITCH_CLIENT_ID` and `TWITCH_CLIENT_SECRET` (free at dev.twitch.tv)")
        if self._twitch_token and self._twitch_token[1] > time.time() + 60:
            return self._twitch_token[0]
        async with self.bot.session.post("https://id.twitch.tv/oauth2/token", params={
                "client_id": self.twitch_id, "client_secret": self.twitch_secret,
                "grant_type": "client_credentials"}) as r:
            data = await r.json()
        self._twitch_token = (data["access_token"], time.time() + data["expires_in"])
        return data["access_token"]

    async def twitch_entries(self, logins: list[str]) -> dict[str, list[dict]]:
        token = await self.twitch_token()
        out: dict[str, list[dict]] = {l: [] for l in logins}
        for i in range(0, len(logins), 100):
            params = [("user_login", l) for l in logins[i:i + 100]]
            async with self.bot.session.get("https://api.twitch.tv/helix/streams", params=params, headers={
                    "Client-ID": self.twitch_id, "Authorization": f"Bearer {token}"}) as r:
                data = await r.json()
            for s in data.get("data", []):
                out[s["user_login"].lower()] = [{
                    "id": s["id"], "title": s["title"], "link": f"https://twitch.tv/{s['user_login']}",
                    "author": s["user_name"], "image": s["thumbnail_url"].replace("{width}", "640").replace("{height}", "360")
                    + f"?t={int(time.time())}", "extra": f"Playing **{s.get('game_name') or 'something'}** for {s['viewer_count']:,} viewers"}]
        return out

    @staticmethod
    def entries_of(parsed) -> list[dict]:
        out = []
        for e in parsed.entries[:15]:
            img = None
            for key in ("media_thumbnail", "media_content"):
                if e.get(key):
                    img = e[key][0].get("url")
                    break
            if not img:
                m = re.search(r'<img[^>]+src="([^"]+)"', e.get("summary", "") or "")
                img = m.group(1) if m else None
            out.append({"id": e.get("id") or e.get("link"), "title": e.get("title") or "New post",
                        "link": e.get("link"), "author": e.get("author") or parsed.feed.get("title", ""),
                        "image": img, "extra": shorten(re.sub(r"<[^>]+>", "", e.get("summary", "") or ""), 300)})
        return out

    # ================================================================ posting
    async def post(self, guild: discord.Guild, feed: dict, entry: dict):
        channel = guild.get_channel(feed["channel"])
        if not channel:
            return
        name, color = PLATFORMS[feed["type"]]
        role = f"<@&{feed['role']}> " if feed.get("role") else ""
        mentions = discord.AllowedMentions(roles=True, users=False, everyone=False)
        try:
            if feed.get("message"):
                v = build_vars(None, guild, channel, {
                    "feed.title": entry["title"], "feed.link": entry["link"] or "", "feed.author": entry["author"],
                    "feed.platform": name, "feed.account": feed["label"], "feed.image": entry.get("image") or ""})
                await send_script(channel, (role + feed["message"]) if role else feed["message"], v,
                                  allowed_mentions=mentions)
                return
            live = feed["type"] == "twitch"
            e = discord.Embed(color=color, title=shorten(entry["title"], 256), url=entry["link"],
                              description=shorten(entry.get("extra") or "", 500) or None,
                              timestamp=discord.utils.utcnow())
            e.set_author(name=f"{entry['author'] or feed['label']} • {name}")
            if entry.get("image"):
                e.set_image(url=entry["image"])
            verb = "is live" if live else "posted"
            view = discord.ui.View()
            if entry.get("link"):
                view.add_item(discord.ui.Button(label="Watch" if live or feed["type"] == "youtube" else "Open", url=entry["link"]))
            await channel.send(f"{role}**{feed['label']}** {verb} on {name}!", embed=e, view=view, allowed_mentions=mentions)
        except discord.HTTPException:
            pass

    @tasks.loop(minutes=5)
    async def poll(self):
        rows = await self.bot.db.guilds_with("feeds")
        jobs: dict[str, list[tuple[int, str]]] = {}
        for gid, cfg in rows:
            if not self.bot.get_guild(gid):
                continue
            for fid, f in cfg.get("items", {}).items():
                jobs.setdefault(f["url"], []).append((gid, fid))
        if not jobs:
            return
        results: dict[str, list[dict]] = {}
        twitch = [u.split(":", 1)[1] for u in jobs if u.startswith("twitch:")]
        if twitch and self.twitch_id:
            try:
                for login, entries in (await self.twitch_entries(twitch)).items():
                    results[f"twitch:{login}"] = entries
            except Exception:
                pass
        for url in jobs:
            if url.startswith("twitch:"):
                continue
            try:
                results[url] = self.entries_of(await self.parse(url))
            except Exception:
                continue
            await asyncio.sleep(0.5)
        for url, targets in jobs.items():
            entries = results.get(url)
            if entries is None:
                continue
            for gid, fid in targets:
                cfg = await self.cfg(gid)
                feed = cfg["items"].get(fid)
                if not feed:
                    continue
                seen = feed.setdefault("seen", [])
                fresh = [e for e in entries if e["id"] and e["id"] not in seen]
                for entry in reversed(fresh[:3]):  # oldest first, cap bursts
                    await self.post(self.bot.get_guild(gid), feed, entry)
                if fresh:
                    feed["seen"] = ([e["id"] for e in fresh] + seen)[:60]
                    await self.bot.db.set_config(gid, "feeds", cfg)

    @poll.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()

    # ================================================================ commands
    @commands.group(name="feeds", aliases=["socials", "notify", "alerts"], invoke_without_command=True)
    @has_perms(manage_guild=True)
    async def feed(self, ctx: Context):
        """Post new content from YouTube, Twitch, SoundCloud, Reddit, Instagram, TikTok, X or any RSS feed"""
        await ctx.send_help(ctx.command)

    @feed.command(name="add")
    @has_perms(manage_guild=True)
    async def feed_add(self, ctx: Context, platform: str, account: str, channel: discord.TextChannel = None):
        """`feeds add youtube MrBeast #videos` — platforms: youtube, twitch, soundcloud, reddit, instagram, tiktok, x, rss"""
        platform = ALIASES.get(platform.lower(), platform.lower())
        if platform not in PLATFORMS:
            raise UserError(f"Platforms: {', '.join(f'`{p}`' for p in PLATFORMS)}")
        channel = channel or ctx.channel
        cfg = await self.cfg(ctx.guild.id)
        if len(cfg["items"]) >= MAX_FEEDS:
            raise UserError(f"You can have at most {MAX_FEEDS} feeds")
        async with ctx.typing():
            url, label = await self.resolve(platform, account)
            if any(f["url"] == url and f["channel"] == channel.id for f in cfg["items"].values()):
                raise UserError("That feed is already posting to that channel")
            if platform == "twitch":
                entries = (await self.twitch_entries([label]))[label]
            else:
                try:
                    parsed = await self.parse(url)
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    raise UserError("I couldn't reach that feed")
                if not parsed.entries and parsed.bozo:
                    hint = (" (Instagram, TikTok and X go through RSSHub — the public instance often blocks them; "
                            "self-host one and set `RSSHUB_URL`)") if platform in RSSHUB_ROUTES else ""
                    raise UserError(f"I couldn't read that feed{hint}")
                entries = self.entries_of(parsed)
        fid = str(cfg["next"])
        cfg["next"] += 1
        cfg["items"][fid] = {"type": platform, "label": label, "url": url, "channel": channel.id,
                             "seen": [e["id"] for e in entries if e["id"]][:60], "message": None, "role": None}
        await self.bot.db.set_config(ctx.guild.id, "feeds", cfg)
        await ctx.approve(f"Feed `#{fid}`: new **{PLATFORMS[platform][0]}** posts from **{label}** → {channel.mention} "
                          f"(checked every 5 minutes)")

    @feed.command(name="remove", aliases=["delete"])
    @has_perms(manage_guild=True)
    async def feed_remove(self, ctx: Context, feed_id: str):
        """Remove a feed by its ID (see `feeds list`)"""
        cfg = await self.cfg(ctx.guild.id)
        f = cfg["items"].pop(feed_id.lstrip("#"), None)
        if not f:
            raise UserError("No feed with that ID")
        await self.bot.db.set_config(ctx.guild.id, "feeds", cfg)
        await ctx.approve(f"Removed the {PLATFORMS[f['type']][0]} feed for **{f['label']}**")

    @feed.command(name="list")
    @has_perms(manage_guild=True)
    async def feed_list(self, ctx: Context):
        """List feeds"""
        cfg = await self.cfg(ctx.guild.id)
        lines = [f"`#{fid}` **{PLATFORMS[f['type']][0]}** {f['label']} → <#{f['channel']}>"
                 + (f" pings <@&{f['role']}>" if f.get("role") else "") for fid, f in cfg["items"].items()]
        await ctx.paginate(chunk_embeds("Feeds", lines, numbered=False))

    @feed.command(name="ping", aliases=["role"])
    @has_perms(manage_guild=True)
    async def feed_ping(self, ctx: Context, feed_id: str, role: discord.Role = None):
        """Ping a role when a feed posts (omit to stop)"""
        cfg = await self.cfg(ctx.guild.id)
        f = cfg["items"].get(feed_id.lstrip("#"))
        if not f:
            raise UserError("No feed with that ID")
        f["role"] = role.id if role else None
        await self.bot.db.set_config(ctx.guild.id, "feeds", cfg)
        await ctx.approve(f"Feed `#{feed_id}` will ping {role.mention}" if role else "Feed won't ping anyone")

    @feed.command(name="message")
    @has_perms(manage_guild=True)
    async def feed_message(self, ctx: Context, feed_id: str, *, message: str = None):
        """Custom post message (embed scripts + {feed.title} {feed.link} {feed.author} {feed.image} {feed.account}); `none` resets"""
        cfg = await self.cfg(ctx.guild.id)
        f = cfg["items"].get(feed_id.lstrip("#"))
        if not f:
            raise UserError("No feed with that ID")
        f["message"] = None if message in (None, "none") else message
        await self.bot.db.set_config(ctx.guild.id, "feeds", cfg)
        await ctx.approve("Updated the feed message")

    @feed.command(name="test")
    @has_perms(manage_guild=True)
    async def feed_test(self, ctx: Context, feed_id: str):
        """Post the latest item from a feed right now"""
        cfg = await self.cfg(ctx.guild.id)
        f = cfg["items"].get(feed_id.lstrip("#"))
        if not f:
            raise UserError("No feed with that ID")
        async with ctx.typing():
            if f["type"] == "twitch":
                entries = (await self.twitch_entries([f["label"]]))[f["label"]]
                if not entries:
                    raise UserError(f"**{f['label']}** isn't live right now")
            else:
                entries = self.entries_of(await self.parse(f["url"]))
        if not entries:
            raise UserError("That feed has no posts yet")
        await self.post(ctx.guild, f, entries[0])
        await ctx.message.add_reaction("✅")


async def setup(bot):
    await bot.add_cog(Feeds(bot))
