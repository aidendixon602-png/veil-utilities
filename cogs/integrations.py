"""Integrations: Last.fm, Urban Dictionary, weather, GitHub, crypto, Minecraft, Roblox, translate, Wikipedia."""
from __future__ import annotations

import urllib.parse

import discord
from discord.ext import commands

from core.config import settings
from core.utils import Colors, Context, UserError, chunk_embeds, shorten, ts

LASTFM = "https://ws.audioscrobbler.com/2.0/"


class Integrations(commands.Cog):
    """Look things up across the web"""
    emoji = "🌐"

    def __init__(self, bot):
        self.bot = bot
        self.lastfm_key = settings.lastfm_api_key

    async def get_json(self, url: str, **params):
        async with self.bot.session.get(url, params=params or None) as r:
            if r.status == 404:
                raise UserError("Nothing found")
            if r.status >= 400:
                raise UserError(f"The service returned an error ({r.status})")
            return await r.json(content_type=None)

    # ================================================================ Last.fm
    async def lf(self, method: str, **params):
        if not self.lastfm_key:
            raise UserError("Last.fm isn't configured — the bot owner needs to set `LASTFM_API_KEY`")
        data = await self.get_json(LASTFM, method=method, api_key=self.lastfm_key, format="json", **params)
        if "error" in data:
            raise UserError(data.get("message", "Last.fm error"))
        return data

    async def lf_user(self, ctx: Context, member: discord.abc.User | None) -> str:
        member = member or ctx.author
        row = await self.bot.db.fetchone("SELECT username FROM lastfm WHERE user_id=?", member.id)
        if not row:
            raise UserError(f"{'You haven' if member == ctx.author else f'{member.mention} hasn'}'t linked Last.fm — "
                            f"`{ctx.clean_prefix}lf set <username>`")
        return row["username"]

    @commands.group(aliases=["lf", "fm"], invoke_without_command=True)
    async def lastfm(self, ctx: Context, member: discord.Member = None):
        """Show what you're listening to (same as `np`)"""
        await self.nowplaying(ctx, member)

    @lastfm.command(name="set", aliases=["login", "link"])
    async def lf_set(self, ctx: Context, username: str):
        """Link your Last.fm account"""
        data = await self.lf("user.getinfo", user=username)
        name = data["user"]["name"]
        await self.bot.db.execute("INSERT OR REPLACE INTO lastfm VALUES(?,?)", ctx.author.id, name)
        await ctx.approve(f"Linked Last.fm **{name}** ({int(data['user']['playcount']):,} scrobbles)")

    @lastfm.command(name="unlink", aliases=["logout"])
    async def lf_unlink(self, ctx: Context):
        """Unlink Last.fm"""
        await self.bot.db.execute("DELETE FROM lastfm WHERE user_id=?", ctx.author.id)
        await ctx.approve("Unlinked Last.fm")

    @commands.command(aliases=["np", "nowplay"])
    async def nowplaying(self, ctx: Context, member: discord.Member = None):
        """Your current/last Last.fm track"""
        user = await self.lf_user(ctx, member)
        data = await self.lf("user.getrecenttracks", user=user, limit=1, extended=1)
        tracks = data["recenttracks"]["track"]
        if not tracks:
            raise UserError("No scrobbles yet")
        t = tracks[0]
        artist = t["artist"]["name"]
        info = await self.lf("track.getinfo", artist=artist, track=t["name"], username=user)
        plays = info.get("track", {}).get("userplaycount", "0")
        total = data["recenttracks"]["@attr"]["total"]
        img = t["image"][-1]["#text"] if t.get("image") else None
        live = t.get("@attr", {}).get("nowplaying")
        e = discord.Embed(color=Colors.main)
        e.set_author(name=f"{'Now playing' if live else 'Last played'} • {user}",
                     url=f"https://last.fm/user/{user}", icon_url=(member or ctx.author).display_avatar.url)
        e.add_field(name="Track", value=f"[{t['name']}]({t['url']})")
        e.add_field(name="Artist", value=f"[{artist}](https://last.fm/music/{urllib.parse.quote(artist)})")
        if img:
            e.set_thumbnail(url=img)
        e.set_footer(text=f"{plays} plays of this track • {int(total):,} total scrobbles • {t['album']['#text']}")
        msg = await ctx.send(embed=e)
        settings = await self.bot.db.get_config(ctx.guild.id, "settings") if ctx.guild else {}
        for r in settings.get("np_reactions", ["🔥", "🗑️"]):
            try:
                await msg.add_reaction(r)
            except discord.HTTPException:
                pass

    @lastfm.command(name="reactions")
    @commands.has_permissions(manage_guild=True)
    async def lf_reactions(self, ctx: Context, *emojis: str):
        """Set the reactions added to now-playing embeds (none to disable)"""
        s = await self.bot.db.get_config(ctx.guild.id, "settings")
        s["np_reactions"] = list(emojis[:3])
        await self.bot.db.set_config(ctx.guild.id, "settings", s)
        await ctx.approve(f"Now-playing reactions: {' '.join(emojis) or 'none'}")

    @lastfm.command(name="recent", aliases=["rt"])
    async def lf_recent(self, ctx: Context, member: discord.Member = None):
        """Recent tracks"""
        user = await self.lf_user(ctx, member)
        data = await self.lf("user.getrecenttracks", user=user, limit=50)
        lines = [f"**{t['name']}** by {t['artist']['#text']}" for t in data["recenttracks"]["track"]]
        await ctx.paginate(chunk_embeds(f"{user}'s recent tracks", lines))

    async def _top(self, ctx, member, method, key, period, title):
        user = await self.lf_user(ctx, member)
        periods = {"7d": "7day", "1m": "1month", "3m": "3month", "6m": "6month", "1y": "12month", "all": "overall"}
        p = periods.get(period, "overall")
        data = await self.lf(method, user=user, period=p, limit=50)
        items = data[key][key[3:-1] if key.startswith("top") else key]
        lines = []
        for i in items:
            label = i["name"] if "artist" not in i else f"{i['name']} by {i['artist']['name']}"
            lines.append(f"**{label}** — {int(i['playcount']):,} plays")
        await ctx.paginate(chunk_embeds(f"{user}'s {title} ({period})", lines))

    @lastfm.command(name="topartists", aliases=["ta"])
    async def lf_topartists(self, ctx, period: str = "all", member: discord.Member = None):
        """Top artists — period: 7d 1m 3m 6m 1y all"""
        await self._top(ctx, member, "user.gettopartists", "topartists", period, "top artists")

    @lastfm.command(name="toptracks", aliases=["tt"])
    async def lf_toptracks(self, ctx, period: str = "all", member: discord.Member = None):
        """Top tracks"""
        await self._top(ctx, member, "user.gettoptracks", "toptracks", period, "top tracks")

    @lastfm.command(name="topalbums", aliases=["tab"])
    async def lf_topalbums(self, ctx, period: str = "all", member: discord.Member = None):
        """Top albums"""
        await self._top(ctx, member, "user.gettopalbums", "topalbums", period, "top albums")

    @lastfm.command(name="profile", aliases=["user"])
    async def lf_profile(self, ctx, member: discord.Member = None):
        """Last.fm profile"""
        user = await self.lf_user(ctx, member)
        u = (await self.lf("user.getinfo", user=user))["user"]
        e = discord.Embed(color=Colors.main, title=u["name"], url=u["url"])
        e.add_field(name="Scrobbles", value=f"{int(u['playcount']):,}")
        e.add_field(name="Artists", value=f"{int(u.get('artist_count', 0)):,}")
        e.add_field(name="Registered", value=ts(int(u["registered"]["unixtime"])))
        if u.get("image") and u["image"][-1]["#text"]:
            e.set_thumbnail(url=u["image"][-1]["#text"])
        await ctx.send(embed=e)

    @lastfm.command(name="whoknows", aliases=["wk"])
    @commands.guild_only()
    async def lf_whoknows(self, ctx: Context, *, artist: str = None):
        """Who in this server listens to an artist the most"""
        if not artist:
            user = await self.lf_user(ctx, None)
            data = await self.lf("user.getrecenttracks", user=user, limit=1)
            artist = data["recenttracks"]["track"][0]["artist"]["#text"]
        rows = await self.bot.db.fetchall("SELECT * FROM lastfm")
        members = [(ctx.guild.get_member(r["user_id"]), r["username"]) for r in rows]
        members = [m for m in members if m[0]][:40]
        results = []
        for member, uname in members:
            try:
                info = await self.lf("artist.getinfo", artist=artist, username=uname)
                plays = int(info["artist"]["stats"].get("userplaycount", 0))
                if plays:
                    results.append((plays, member))
            except UserError:
                continue
        results.sort(key=lambda x: x[0], reverse=True)
        lines = [f"{'👑 ' if i == 0 else ''}{m.mention} — **{p:,}** plays" for i, (p, m) in enumerate(results)]
        await ctx.paginate(chunk_embeds(f"Who knows {artist} in {ctx.guild.name}", lines))

    # ================================================================ lookups
    @commands.command(aliases=["ud", "define"])
    async def urban(self, ctx: Context, *, term: str):
        """Urban Dictionary definitions"""
        data = await self.get_json("https://api.urbandictionary.com/v0/define", term=term)
        defs = data.get("list", [])
        if not defs:
            raise UserError("No definitions found")
        pages = []
        for d in defs[:10]:
            e = discord.Embed(title=d["word"], url=d["permalink"], color=Colors.main,
                              description=shorten(d["definition"].replace("[", "").replace("]", ""), 2000))
            if d.get("example"):
                e.add_field(name="Example", value=shorten(d["example"].replace("[", "").replace("]", "")), inline=False)
            e.set_footer(text=f"👍 {d['thumbs_up']} • 👎 {d['thumbs_down']}")
            pages.append(e)
        await ctx.paginate(pages)

    @commands.command()
    async def weather(self, ctx: Context, *, location: str):
        """Current weather anywhere"""
        geo = await self.get_json("https://geocoding-api.open-meteo.com/v1/search", name=location, count=1)
        if not geo.get("results"):
            raise UserError("Location not found")
        g = geo["results"][0]
        w = await self.get_json("https://api.open-meteo.com/v1/forecast", latitude=g["latitude"], longitude=g["longitude"],
                                current="temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weather_code",
                                daily="temperature_2m_max,temperature_2m_min", timezone="auto", forecast_days=1)
        c = w["current"]
        codes = {0: "☀️ Clear", 1: "🌤️ Mostly clear", 2: "⛅ Partly cloudy", 3: "☁️ Overcast", 45: "🌫️ Fog",
                 51: "🌦️ Drizzle", 61: "🌧️ Rain", 63: "🌧️ Rain", 65: "🌧️ Heavy rain", 71: "🌨️ Snow",
                 73: "🌨️ Snow", 75: "❄️ Heavy snow", 80: "🌦️ Showers", 95: "⛈️ Thunderstorm"}
        cond = codes.get(c["weather_code"], codes.get(c["weather_code"] // 10 * 10, "🌡️"))
        tc = c["temperature_2m"]
        e = discord.Embed(color=Colors.main, title=f"{g['name']}, {g.get('admin1', '')} {g.get('country_code', '')}".strip(),
                          description=f"**{cond}**")
        e.add_field(name="Temperature", value=f"{tc:.0f}°C / {tc * 9 / 5 + 32:.0f}°F")
        e.add_field(name="Feels like", value=f"{c['apparent_temperature']:.0f}°C")
        e.add_field(name="High / Low", value=f"{w['daily']['temperature_2m_max'][0]:.0f}° / {w['daily']['temperature_2m_min'][0]:.0f}°")
        e.add_field(name="Humidity", value=f"{c['relative_humidity_2m']}%")
        e.add_field(name="Wind", value=f"{c['wind_speed_10m']} km/h")
        await ctx.send(embed=e)

    @commands.group(aliases=["gh"], invoke_without_command=True)
    async def github(self, ctx: Context, username: str):
        """GitHub profile lookup"""
        u = await self.get_json(f"https://api.github.com/users/{urllib.parse.quote(username)}")
        e = discord.Embed(color=Colors.neutral, title=u.get("name") or u["login"], url=u["html_url"], description=u.get("bio"))
        e.set_thumbnail(url=u["avatar_url"])
        e.add_field(name="Repos", value=u["public_repos"])
        e.add_field(name="Followers", value=f"{u['followers']:,}")
        e.add_field(name="Following", value=f"{u['following']:,}")
        if u.get("company"):
            e.add_field(name="Company", value=u["company"])
        if u.get("location"):
            e.add_field(name="Location", value=u["location"])
        e.add_field(name="Joined", value=ts(discord.utils.parse_time(u["created_at"].rstrip("Z"))))
        await ctx.send(embed=e)

    @github.command(name="repo")
    async def gh_repo(self, ctx: Context, repo: str):
        """Repository lookup: `gh repo owner/name`"""
        r = await self.get_json(f"https://api.github.com/repos/{repo}")
        e = discord.Embed(color=Colors.neutral, title=r["full_name"], url=r["html_url"], description=r.get("description"))
        e.add_field(name="⭐ Stars", value=f"{r['stargazers_count']:,}")
        e.add_field(name="🍴 Forks", value=f"{r['forks_count']:,}")
        e.add_field(name="Issues", value=f"{r['open_issues_count']:,}")
        e.add_field(name="Language", value=r.get("language") or "—")
        e.add_field(name="License", value=(r.get("license") or {}).get("spdx_id") or "—")
        e.set_thumbnail(url=r["owner"]["avatar_url"])
        await ctx.send(embed=e)

    @commands.command(aliases=["price", "coin"])
    async def crypto(self, ctx: Context, coin: str = "bitcoin", currency: str = "usd"):
        """Crypto price (by name or symbol)"""
        search = await self.get_json("https://api.coingecko.com/api/v3/search", query=coin)
        if not search.get("coins"):
            raise UserError("Coin not found")
        c = search["coins"][0]
        data = await self.get_json("https://api.coingecko.com/api/v3/simple/price", ids=c["id"], vs_currencies=currency,
                                   include_24hr_change="true", include_market_cap="true")
        d = data.get(c["id"], {})
        if currency not in d:
            raise UserError("Unsupported currency")
        change = d.get(f"{currency}_24h_change") or 0
        e = discord.Embed(color=Colors.success if change >= 0 else Colors.error, title=f"{c['name']} ({c['symbol']})")
        e.add_field(name="Price", value=f"{d[currency]:,.8g} {currency.upper()}")
        e.add_field(name="24h", value=f"{'📈' if change >= 0 else '📉'} {change:+.2f}%")
        if d.get(f"{currency}_market_cap"):
            e.add_field(name="Market cap", value=f"{d[f'{currency}_market_cap']:,.0f}")
        if c.get("large"):
            e.set_thumbnail(url=c["large"])
        await ctx.send(embed=e)

    @commands.command(aliases=["mcserver", "mc_status"])
    async def minecraft(self, ctx: Context, address: str):
        """Java server status"""
        d = await self.get_json(f"https://api.mcsrvstat.us/3/{urllib.parse.quote(address)}")
        if not d.get("online"):
            raise UserError(f"`{address}` is offline")
        e = discord.Embed(color=Colors.success, title=address,
                          description="\n".join(d.get("motd", {}).get("clean", []))[:1000] or None)
        e.add_field(name="Players", value=f"{d['players']['online']}/{d['players']['max']}")
        e.add_field(name="Version", value=d.get("version", "?"))
        e.set_thumbnail(url=f"https://api.mcsrvstat.us/icon/{address}")
        await ctx.send(embed=e)

    @commands.command(aliases=["rblx"])
    async def roblox(self, ctx: Context, username: str):
        """Roblox profile lookup"""
        async with self.bot.session.post("https://users.roblox.com/v1/usernames/users",
                                         json={"usernames": [username], "excludeBannedUsers": False}) as r:
            found = (await r.json()).get("data", [])
        if not found:
            raise UserError("User not found")
        uid = found[0]["id"]
        u = await self.get_json(f"https://users.roblox.com/v1/users/{uid}")
        friends = await self.get_json(f"https://friends.roblox.com/v1/users/{uid}/friends/count")
        followers = await self.get_json(f"https://friends.roblox.com/v1/users/{uid}/followers/count")
        thumb = await self.get_json("https://thumbnails.roblox.com/v1/users/avatar-headshot", userIds=uid, size="420x420",
                                    format="Png")
        e = discord.Embed(color=Colors.neutral, title=f"{u['displayName']} (@{u['name']})",
                          url=f"https://www.roblox.com/users/{uid}/profile", description=shorten(u.get("description") or "", 500) or None)
        e.add_field(name="Friends", value=f"{friends.get('count', 0):,}")
        e.add_field(name="Followers", value=f"{followers.get('count', 0):,}")
        e.add_field(name="Created", value=ts(discord.utils.parse_time(u["created"][:19])))
        if u.get("isBanned"):
            e.add_field(name="Status", value="🚫 Banned")
        if thumb.get("data"):
            e.set_thumbnail(url=thumb["data"][0]["imageUrl"])
        await ctx.send(embed=e)

    @commands.command(aliases=["tr"])
    async def translate(self, ctx: Context, language: str, *, text: str = None):
        """Translate text (or reply to a message): `translate es hello`"""
        if text is None and ctx.message.reference and isinstance(ctx.message.reference.resolved, discord.Message):
            text = ctx.message.reference.resolved.content
        if not text:
            raise UserError("Give some text or reply to a message")
        data = await self.get_json("https://translate.googleapis.com/translate_a/single", client="gtx", sl="auto",
                                   tl=language, dt="t", q=text)
        out = "".join(part[0] for part in data[0] if part[0])
        e = discord.Embed(color=Colors.main, description=shorten(out, 4000))
        e.set_footer(text=f"{data[2]} → {language}")
        await ctx.send(embed=e)

    @commands.command(aliases=["wiki"])
    async def wikipedia(self, ctx: Context, *, query: str):
        """Wikipedia summary"""
        s = await self.get_json("https://en.wikipedia.org/w/api.php", action="opensearch", search=query, limit=1, format="json")
        if not s[1]:
            raise UserError("No article found")
        title = s[1][0]
        d = await self.get_json(f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(title)}")
        e = discord.Embed(color=Colors.neutral, title=d["title"], url=d["content_urls"]["desktop"]["page"],
                          description=shorten(d.get("extract", ""), 1500))
        if d.get("thumbnail"):
            e.set_thumbnail(url=d["thumbnail"]["source"])
        await ctx.send(embed=e)


async def setup(bot):
    await bot.add_cog(Integrations(bot))
