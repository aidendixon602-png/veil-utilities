"""Integrations: Last.fm, Urban Dictionary, weather, GitHub, crypto, Minecraft, Roblox, translate, Wikipedia."""
from __future__ import annotations

import asyncio
import urllib.parse
from datetime import datetime, timezone

import aiohttp
import discord
from discord.ext import commands

from core.config import settings
from core.utils import Colors, Context, UserError, chunk_embeds, shorten, ts, requires_uperm

LASTFM = "https://ws.audioscrobbler.com/2.0/"
LASTFM_NOIMAGE = "2a96cbd8b46e442fc41c2b86b821562f"  # Last.fm's grey star placeholder

WEATHER_CODES = {
    0: "☀️ Clear", 1: "🌤️ Mostly clear", 2: "⛅ Partly cloudy", 3: "☁️ Overcast", 45: "🌫️ Fog", 48: "🌫️ Freezing fog",
    51: "🌦️ Light drizzle", 53: "🌦️ Drizzle", 55: "🌦️ Heavy drizzle", 56: "🌧️ Freezing drizzle",
    57: "🌧️ Freezing drizzle", 61: "🌧️ Light rain", 63: "🌧️ Rain", 65: "🌧️ Heavy rain", 66: "🌧️ Freezing rain",
    67: "🌧️ Freezing rain", 71: "🌨️ Light snow", 73: "🌨️ Snow", 75: "❄️ Heavy snow", 77: "🌨️ Snow grains",
    80: "🌦️ Showers", 81: "🌧️ Heavy showers", 82: "⛈️ Violent showers", 85: "🌨️ Snow showers",
    86: "❄️ Heavy snow showers", 95: "⛈️ Thunderstorm", 96: "⛈️ Thunderstorm with hail",
    99: "⛈️ Severe thunderstorm with hail",
}

LANGUAGE_NAMES = {
    "english": "en", "spanish": "es", "french": "fr", "german": "de", "italian": "it", "portuguese": "pt",
    "russian": "ru", "japanese": "ja", "korean": "ko", "chinese": "zh-CN", "arabic": "ar", "hindi": "hi",
    "turkish": "tr", "dutch": "nl", "polish": "pl", "swedish": "sv", "ukrainian": "uk", "vietnamese": "vi",
    "indonesian": "id", "thai": "th", "greek": "el", "hebrew": "iw", "filipino": "tl", "tagalog": "tl",
    "romanian": "ro", "czech": "cs", "danish": "da", "finnish": "fi", "norwegian": "no", "hungarian": "hu",
    "persian": "fa", "urdu": "ur", "bengali": "bn", "malay": "ms", "swahili": "sw", "latin": "la",
}


def as_list(value) -> list:
    """Last.fm returns a bare object instead of a one-item list when there's a single result."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def iso_time(value: str) -> datetime:
    """Parse API timestamps like '2011-01-25T18:44:36Z' / '...123Z' into an aware UTC datetime."""
    value = value.strip().replace("Z", "+00:00")
    if "." in value:  # trim fractional seconds Python 3.10 can't parse when they aren't 3/6 digits
        head, _, tail = value.partition(".")
        frac = "".join(ch for ch in tail if ch.isdigit())
        value = head + "." + frac[:6].ljust(6, "0") + tail[len(frac):]
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def lastfm_image(images) -> str | None:
    for img in reversed(as_list(images)):
        url = img.get("#text") if isinstance(img, dict) else None
        if url and LASTFM_NOIMAGE not in url:
            return url
    return None


class Integrations(commands.Cog):
    """Look things up across the web"""
    emoji = "🌐"

    def __init__(self, bot):
        self.bot = bot
        self.lastfm_key = settings.lastfm_api_key

    async def get_json(self, url: str, headers: dict | None = None, **params):
        try:
            async with self.bot.session.get(url, params=params or None, headers=headers or None,
                                            timeout=aiohttp.ClientTimeout(total=20)) as r:
                if r.status == 404:
                    raise UserError("Nothing found")
                if r.status == 429 or (r.status == 403 and "api.github.com" in url):
                    raise UserError("That service is rate-limiting me, try again in a few minutes")
                if r.status >= 400:
                    raise UserError(f"The service returned an error ({r.status})")
                return await r.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            raise UserError("That service didn't respond, try again in a moment") from None
        except ValueError:
            raise UserError("That service sent a response I couldn't read") from None

    # ================================================================ Last.fm
    async def lf(self, method: str, **params):
        if not self.lastfm_key:
            raise UserError("Last.fm isn't set up — the bot owner needs a free `LASTFM_API_KEY` "
                            "from https://www.last.fm/api/account/create")
        params = {"method": method, "api_key": self.lastfm_key, "format": "json", **params}
        try:
            async with self.bot.session.get(LASTFM, params=params, timeout=aiohttp.ClientTimeout(total=20)) as r:
                data = await r.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            raise UserError("Last.fm didn't respond, try again in a moment") from None
        if not isinstance(data, dict):
            raise UserError("Last.fm sent an unexpected response")
        if "error" in data:  # Last.fm puts the reason in the body, even on 4xx responses
            if data["error"] in (10, 26):
                raise UserError("The bot's Last.fm API key is invalid — the owner needs to update `LASTFM_API_KEY`")
            if data["error"] == 29:
                raise UserError("Last.fm is rate-limiting me, try again in a minute")
            raise UserError(f"Last.fm: {data.get('message', 'something went wrong')}")
        return data

    async def lf_user(self, ctx: Context, member: discord.abc.User | None) -> str:
        member = member or ctx.author
        row = await self.bot.db.fetchone("SELECT username FROM lastfm WHERE user_id=?", member.id)
        if not row:
            raise UserError(f"{'You haven' if member == ctx.author else f'{member.mention} hasn'}'t linked Last.fm — "
                            f"`{ctx.clean_prefix}lf set <username>`")
        return row["username"]

    @commands.group(aliases=["lf", "fm"], invoke_without_command=True)
    @requires_uperm()
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
    @requires_uperm()
    async def nowplaying(self, ctx: Context, member: discord.Member = None):
        """Your current/last Last.fm track"""
        user = await self.lf_user(ctx, member)
        data = await self.lf("user.getrecenttracks", user=user, limit=1, extended=1)
        tracks = as_list(data["recenttracks"].get("track"))
        if not tracks:
            raise UserError("No scrobbles yet")
        t = tracks[0]
        artist = t["artist"].get("name") or t["artist"].get("#text", "?")
        try:
            info = await self.lf("track.getinfo", artist=artist, track=t["name"], username=user)
            plays = info.get("track", {}).get("userplaycount", "0")
        except UserError:  # track isn't in Last.fm's catalogue yet
            plays = "?"
        total = data["recenttracks"].get("@attr", {}).get("total", 0)
        img = lastfm_image(t.get("image"))
        live = t.get("@attr", {}).get("nowplaying")
        e = discord.Embed(color=Colors.main)
        e.set_author(name=f"{'Now playing' if live else 'Last played'} • {user}",
                     url=f"https://last.fm/user/{user}", icon_url=(member or ctx.author).display_avatar.url)
        e.add_field(name="Track", value=f"[{t['name']}]({t['url']})")
        e.add_field(name="Artist", value=f"[{artist}](https://last.fm/music/{urllib.parse.quote(artist)})")
        if img:
            e.set_thumbnail(url=img)
        album = (t.get("album") or {}).get("#text")
        e.set_footer(text=f"{plays} plays of this track • {int(total):,} total scrobbles" + (f" • {album}" if album else ""))
        msg = await ctx.send(embed=e)
        settings = await self.bot.db.get_config(ctx.guild.id, "settings") if ctx.guild else {}
        for r in settings.get("np_reactions", ["🔥", "🗑️"]):
            try:
                await msg.add_reaction(r)
            except discord.HTTPException:
                pass

    @lastfm.command(name="reactions")
    @commands.guild_only()
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
        lines = [f"{'🎶 ' if t.get('@attr', {}).get('nowplaying') else ''}**{t['name']}** by {t['artist']['#text']}"
                 for t in as_list(data["recenttracks"].get("track"))]
        if not lines:
            raise UserError("No scrobbles yet")
        await ctx.paginate(chunk_embeds(f"{user}'s recent tracks", lines))

    async def _top(self, ctx, member, method, key, period, title):
        user = await self.lf_user(ctx, member)
        periods = {"7d": "7day", "1m": "1month", "3m": "3month", "6m": "6month", "1y": "12month", "all": "overall"}
        if period not in periods:
            raise UserError("Period must be one of: " + ", ".join(f"`{p}`" for p in periods))
        data = await self.lf(method, user=user, period=periods[period], limit=50)
        items = as_list(data.get(key, {}).get(key[3:-1]))
        if not items:
            raise UserError("Nothing scrobbled in that period")
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
        if img := lastfm_image(u.get("image")):
            e.set_thumbnail(url=img)
        await ctx.send(embed=e)

    @lastfm.command(name="whoknows", aliases=["wk"])
    @commands.guild_only()
    async def lf_whoknows(self, ctx: Context, *, artist: str = None):
        """Who in this server listens to an artist the most"""
        if not artist:
            user = await self.lf_user(ctx, None)
            data = await self.lf("user.getrecenttracks", user=user, limit=1)
            recent = as_list(data["recenttracks"].get("track"))
            if not recent:
                raise UserError("Give an artist name — you haven't scrobbled anything yet")
            artist = recent[0]["artist"]["#text"]
        rows = await self.bot.db.fetchall("SELECT * FROM lastfm")
        members = [(ctx.guild.get_member(r["user_id"]), r["username"]) for r in rows]
        members = [m for m in members if m[0]][:40]
        if not members:
            raise UserError(f"Nobody here has linked Last.fm yet — `{ctx.clean_prefix}lf set <username>`")
        sem = asyncio.Semaphore(5)

        async def plays_for(member, uname):
            async with sem:
                try:
                    info = await self.lf("artist.getinfo", artist=artist, username=uname)
                    return int(info["artist"]["stats"].get("userplaycount", 0)), member
                except (UserError, KeyError, ValueError):
                    return 0, member

        async with ctx.typing():
            results = [r for r in await asyncio.gather(*(plays_for(m, u) for m, u in members)) if r[0]]
        if not results:
            raise UserError(f"Nobody here has listened to **{shorten(artist, 100)}**")
        results.sort(key=lambda x: x[0], reverse=True)
        lines = [f"{'👑 ' if i == 0 else ''}{m.mention} — **{p:,}** plays" for i, (p, m) in enumerate(results)]
        await ctx.paginate(chunk_embeds(f"Who knows {artist} in {ctx.guild.name}", lines))

    # ================================================================ lookups
    @commands.command(aliases=["ud"])
    @requires_uperm()
    async def urban(self, ctx: Context, *, term: str):
        """Urban Dictionary definitions and slang"""
        data = await self.get_json("https://api.urbandictionary.com/v0/define", term=term)
        defs = data.get("list", [])
        if not defs:
            raise UserError(f"No Urban Dictionary definitions found for `{term}`.")
        pages = []
        for d in defs[:10]:
            clean_def = d["definition"].replace("[", "").replace("]", "").strip()
            e = discord.Embed(
                title=f"📚 {d['word']}",
                url=d["permalink"],
                color=Colors.main,
                description=shorten(clean_def, 2000)
            )
            if d.get("example"):
                clean_ex = d["example"].replace("[", "").replace("]", "").strip()
                e.add_field(name="Example", value=f"*{shorten(clean_ex, 1000)}*", inline=False)
            
            up = d.get("thumbs_up", 0)
            down = d.get("thumbs_down", 0)
            total = up + down
            ratio = f"{round((up / total) * 100)}%" if total > 0 else "—"
            author = d.get("author") or "Anonymous"
            e.set_footer(text=f"By {author} • 👍 {up:,} ({ratio}) • 👎 {down:,}")
            pages.append(e)
        await ctx.paginate(pages)

    @commands.command(aliases=["dictionary", "dict"])
    @requires_uperm()
    async def define(self, ctx: Context, *, word: str):
        """English dictionary definitions, phonetics, and usage examples: `define serendipity`"""
        word_clean = word.strip().lower()
        quoted = urllib.parse.quote(word_clean)
        try:
            data = await self.get_json(f"https://api.dictionaryapi.dev/api/v2/entries/en/{quoted}")
        except UserError:
            raise UserError(f"No dictionary definition found for `{word}`.")

        if not isinstance(data, list) or not data:
            raise UserError(f"No dictionary definition found for `{word}`.")

        entry = data[0]
        title_word = entry.get("word", word_clean).capitalize()
        phonetic = entry.get("phonetic") or ""
        if not phonetic:
            for p in entry.get("phonetics", []):
                if p.get("text"):
                    phonetic = p["text"]
                    break

        e = discord.Embed(
            title=f"📖 {title_word} {f'`{phonetic}`' if phonetic else ''}".strip(),
            color=Colors.main
        )

        meanings = entry.get("meanings", [])
        for m in meanings[:4]:
            part = m.get("partOfSpeech", "definition").capitalize()
            defs = m.get("definitions", [])
            lines = []
            for i, d in enumerate(defs[:2], 1):
                def_text = d.get("definition", "").strip()
                ex = d.get("example")
                if ex:
                    lines.append(f"**{i}.** {def_text}\n   ↳ *\"{ex}\"*")
                else:
                    lines.append(f"**{i}.** {def_text}")
            if lines:
                e.add_field(name=f"**{part}**", value="\n".join(lines), inline=False)

        synonyms = []
        for m in meanings:
            for s in m.get("synonyms", []):
                if s not in synonyms:
                    synonyms.append(s)
        if synonyms:
            e.add_field(name="Synonyms", value=", ".join(synonyms[:8]), inline=False)

        e.set_footer(text="Free English Dictionary API")
        await ctx.send(embed=e)

    @commands.command(aliases=["currency", "exchange"])
    @requires_uperm()
    async def convert(self, ctx: Context, amount: float, from_curr: str, to_curr: str):
        """Convert currencies with live European Central Bank exchange rates: `convert 100 USD EUR`"""
        from_c = from_curr.upper()
        to_c = to_curr.upper()
        if from_c == to_c:
            return await ctx.neutral(f"**{amount:,.2f} {from_c}** is equal to **{amount:,.2f} {to_c}**.")

        try:
            data = await self.get_json("https://api.frankfurter.app/latest", amount=amount, from_curr=from_c, to=to_c)
        except Exception:
            raise UserError(f"Could not convert `{from_c}` to `{to_c}`. Supported currencies: USD, EUR, GBP, JPY, CAD, AUD, CHF, etc.")

        rates = data.get("rates", {})
        if to_c not in rates:
            raise UserError(f"Unsupported target currency `{to_c}`.")

        converted = rates[to_c]
        rate = converted / amount if amount != 0 else 0
        inverse = 1 / rate if rate != 0 else 0

        e = discord.Embed(
            title=f"💱 Currency Conversion: {from_c} ➔ {to_c}",
            color=Colors.success
        )
        e.add_field(name=f"Amount ({from_c})", value=f"**{amount:,.2f} {from_c}**")
        e.add_field(name=f"Converted ({to_c})", value=f"**{converted:,.2f} {to_c}**")
        e.add_field(name="Exchange Rates", value=f"1 {from_c} = `{rate:.4f} {to_c}`\n1 {to_c} = `{inverse:.4f} {from_c}`", inline=False)
        e.set_footer(text=f"Official European Central Bank rates • {data.get('date', 'today')}")
        await ctx.send(embed=e)

    @commands.command(aliases=["ip", "geoip"])
    @requires_uperm()
    async def ipinfo(self, ctx: Context, query: str):
        """IP address and domain geolocation intelligence: `ipinfo 1.1.1.1`"""
        clean_target = query.strip().removeprefix("http://").removeprefix("https://").split("/")[0]
        data = await self.get_json(f"http://ip-api.com/json/{urllib.parse.quote(clean_target)}",
                                   fields="status,message,country,countryCode,regionName,city,zip,lat,lon,timezone,isp,org,as,query")
        if data.get("status") != "success":
            raise UserError(f"Failed to lookup IP `{clean_target}`: {data.get('message', 'invalid query')}")

        ip_addr = data.get("query", clean_target)
        country = data.get("country", "Unknown")
        code = data.get("countryCode", "").lower()
        flag = "".join(chr(127397 + ord(c)) for c in code.upper()) if len(code) == 2 else "🌐"

        e = discord.Embed(title=f"{flag} Network Intelligence: {ip_addr}", color=Colors.main)
        e.add_field(name="Location", value=f"{data.get('city', '—')}, {data.get('regionName', '—')}\n{country} ({code.upper()})")
        e.add_field(name="ISP / Provider", value=f"{data.get('isp', '—')}\n*{data.get('org', '—')}*")
        e.add_field(name="ASN", value=f"`{data.get('as', '—')}`")
        e.add_field(name="Timezone", value=f"`{data.get('timezone', '—')}`")
        e.add_field(name="ZIP / Postal", value=f"`{data.get('zip', '—')}`")
        e.add_field(name="Coordinates", value=f"`{data.get('lat', 0):.4f}, {data.get('lon', 0):.4f}`")
        e.set_footer(text="Veil Network Intelligence • ip-api.com")
        await ctx.send(embed=e)

    @commands.command(name="pypi", aliases=["pythonpkg", "pip"])
    @requires_uperm()
    async def pypi(self, ctx: Context, package: str):
        """Python package lookup on PyPI: `pypi discord.py`"""
        quoted = urllib.parse.quote(package.strip().lower())
        try:
            data = await self.get_json(f"https://pypi.org/pypi/{quoted}/json")
        except UserError:
            raise UserError(f"Package `{package}` was not found on PyPI.")

        info = data.get("info", {})
        name = info.get("name", package)
        version = info.get("version", "—")
        summary = info.get("summary") or "No description provided."
        author = info.get("author") or info.get("maintainer") or "Unknown"
        license_name = info.get("license") or "Not specified"
        url = info.get("project_url") or info.get("package_url") or f"https://pypi.org/project/{name}/"

        e = discord.Embed(title=f"🐍 PyPI: {name} v{version}", url=url, color=0x3776AB, description=shorten(summary, 2000))
        e.add_field(name="Installation", value=f"```bash\npip install {name}\n```", inline=False)
        e.add_field(name="Author", value=author)
        e.add_field(name="License", value=shorten(license_name, 50))
        if info.get("requires_python"):
            e.add_field(name="Python Req", value=f"`{info['requires_python']}`")
        e.set_footer(text="Official Python Package Index (PyPI)")
        await ctx.send(embed=e)

    @commands.command(name="npm", aliases=["nodejs", "npmpkg"])
    @requires_uperm()
    async def npm(self, ctx: Context, package: str):
        """Node.js package lookup on NPM: `npm express`"""
        quoted = urllib.parse.quote(package.strip().lower())
        try:
            data = await self.get_json(f"https://registry.npmjs.org/{quoted}")
        except UserError:
            raise UserError(f"Package `{package}` was not found on NPM registry.")

        dist_tags = data.get("dist-tags", {})
        latest_ver = dist_tags.get("latest") or "latest"
        versions = data.get("versions", {})
        ver_info = versions.get(latest_ver, {})

        name = data.get("name", package)
        desc = data.get("description") or ver_info.get("description") or "No description provided."
        author_data = data.get("author") or ver_info.get("author") or {}
        author_name = author_data.get("name") if isinstance(author_data, dict) else str(author_data) or "Unknown"
        license_name = ver_info.get("license") or data.get("license") or "Not specified"

        e = discord.Embed(title=f"📦 NPM: {name} v{latest_ver}", url=f"https://www.npmjs.com/package/{name}", color=0xCB3837, description=shorten(desc, 2000))
        e.add_field(name="Installation", value=f"```bash\nnpm install {name}\n```", inline=False)
        e.add_field(name="Author", value=author_name)
        e.add_field(name="License", value=str(license_name))
        e.set_footer(text="Official Node Package Manager (NPM)")
        await ctx.send(embed=e)

    @commands.command(name="qr", aliases=["qrcode"])
    @requires_uperm()
    async def qr(self, ctx: Context, *, text_or_url: str):
        """Generate a scannable QR code for text or a URL: `qr https://discord.gg`"""
        encoded = urllib.parse.quote(text_or_url)
        qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=350x350&data={encoded}&margin=10"
        e = discord.Embed(title="📱 QR Code Generator", color=Colors.main)
        e.set_image(url=qr_url)
        e.description = f"**Data:**\n`{shorten(text_or_url, 500)}`"
        e.set_footer(text="Scan with your phone's camera • Veil Utilities")
        await ctx.send(embed=e)

    @commands.command()
    @requires_uperm()
    async def weather(self, ctx: Context, *, location: str):
        """Current weather anywhere with full forecast metrics: `weather Tokyo`"""
        geo = await self.get_json("https://geocoding-api.open-meteo.com/v1/search", name=location, count=1)
        if not geo.get("results"):
            raise UserError(f"Location `{location}` not found.")
        g = geo["results"][0]
        w = await self.get_json(
            "https://api.open-meteo.com/v1/forecast",
            latitude=g["latitude"],
            longitude=g["longitude"],
            current="temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,wind_gusts_10m,weather_code,uv_index",
            daily="temperature_2m_max,temperature_2m_min,sunrise,sunset",
            timezone="auto",
            forecast_days=1
        )
        c = w["current"]
        cond = WEATHER_CODES.get(c.get("weather_code"), "🌡️ Unknown")
        tc = c["temperature_2m"]
        fl = c.get("apparent_temperature", tc)
        hi = w["daily"]["temperature_2m_max"][0]
        lo = w["daily"]["temperature_2m_min"][0]
        wind_kmh = c.get("wind_speed_10m", 0)
        wind_mph = wind_kmh / 1.60934
        gust_kmh = c.get("wind_gusts_10m", wind_kmh)
        gust_mph = gust_kmh / 1.60934
        uv = c.get("uv_index", "—")

        loc_title = f"{g['name']}, {g.get('admin1', '')} {g.get('country_code', '')}".strip()
        e = discord.Embed(color=Colors.main, title=f"🌦️ Weather: {loc_title}", description=f"### {cond}")
        e.add_field(name="Temperature", value=f"**{tc:.1f}°C** / **{tc * 9 / 5 + 32:.1f}°F**")
        e.add_field(name="Feels Like", value=f"{fl:.1f}°C / {fl * 9 / 5 + 32:.1f}°F")
        e.add_field(name="High / Low", value=f"{hi:.0f}° / {lo:.0f}°C ({hi * 9 / 5 + 32:.0f}° / {lo * 9 / 5 + 32:.0f}°F)")
        e.add_field(name="Humidity", value=f"{c['relative_humidity_2m']}%")
        e.add_field(name="Wind Speed", value=f"{wind_kmh:.0f} km/h ({wind_mph:.0f} mph)")
        e.add_field(name="Wind Gusts", value=f"{gust_kmh:.0f} km/h ({gust_mph:.0f} mph)")
        e.add_field(name="UV Index", value=f"{uv}")
        e.add_field(name="Coordinates", value=f"`{g['latitude']:.2f}, {g['longitude']:.2f}`")
        e.add_field(name="Timezone", value=f"`{w.get('timezone', 'UTC')}`")
        e.set_footer(text="Open-Meteo Global Forecasting")
        await ctx.send(embed=e)

    @commands.group(aliases=["gh"], invoke_without_command=True)
    @requires_uperm()
    async def github(self, ctx: Context, username: str):
        """GitHub profile lookup with full details: `github torvalds`"""
        u = await self.get_json(f"https://api.github.com/users/{urllib.parse.quote(username)}")
        e = discord.Embed(color=Colors.neutral, title=u.get("name") or u["login"], url=u["html_url"], description=u.get("bio") or "*No bio provided.*")
        e.set_thumbnail(url=u["avatar_url"])
        e.add_field(name="Repositories", value=f"**{u['public_repos']:,}** public")
        e.add_field(name="Followers", value=f"{u['followers']:,}")
        e.add_field(name="Following", value=f"{u['following']:,}")
        if u.get("company"):
            e.add_field(name="Company", value=u["company"])
        if u.get("location"):
            e.add_field(name="Location", value=u["location"])
        if u.get("blog"):
            e.add_field(name="Website", value=f"[{u['blog']}]({u['blog'] if u['blog'].startswith('http') else 'https://' + u['blog']})")
        if u.get("twitter_username"):
            e.add_field(name="X / Twitter", value=f"@{u['twitter_username']}")
        e.add_field(name="Joined", value=ts(iso_time(u["created_at"])))
        e.set_footer(text="Official GitHub API")
        await ctx.send(embed=e)

    @github.command(name="repo")
    @requires_uperm()
    async def gh_repo(self, ctx: Context, repo: str):
        """Repository lookup: `gh repo owner/name`"""
        if repo.count("/") != 1:
            raise UserError("Use the format `owner/name`, e.g. `gh repo discord/discord-api-docs`")
        r = await self.get_json(f"https://api.github.com/repos/{urllib.parse.quote(repo.strip('/'), safe='/')}")
        e = discord.Embed(color=Colors.neutral, title=r["full_name"], url=r["html_url"], description=r.get("description") or "*No description provided.*")
        e.add_field(name="⭐ Stars", value=f"{r['stargazers_count']:,}")
        e.add_field(name="🍴 Forks", value=f"{r['forks_count']:,}")
        e.add_field(name="Issues", value=f"{r['open_issues_count']:,}")
        e.add_field(name="Language", value=r.get("language") or "—")
        e.add_field(name="License", value=(r.get("license") or {}).get("spdx_id") or "—")
        e.add_field(name="Default Branch", value=f"`{r.get('default_branch', 'main')}`")
        e.set_thumbnail(url=r["owner"]["avatar_url"])
        e.set_footer(text="Official GitHub API")
        await ctx.send(embed=e)

    @commands.command(aliases=["price", "coin"])
    @requires_uperm()
    async def crypto(self, ctx: Context, coin: str = "bitcoin", currency: str = "usd"):
        """Crypto price, market cap, and 24h metrics: `crypto btc usd`"""
        currency = currency.lower()
        search = await self.get_json("https://api.coingecko.com/api/v3/search", query=coin)
        if not search.get("coins"):
            raise UserError(f"Cryptocurrency `{coin}` not found.")
        exact = [c for c in search["coins"] if c.get("symbol", "").lower() == coin.lower()]
        c = (exact or search["coins"])[0]
        data = await self.get_json("https://api.coingecko.com/api/v3/simple/price", ids=c["id"], vs_currencies=currency,
                                   include_24hr_change="true", include_24hr_vol="true", include_market_cap="true")
        d = data.get(c["id"], {})
        if currency not in d:
            raise UserError(f"Unsupported fiat/crypto currency `{currency}`.")
        change = d.get(f"{currency}_24h_change") or 0
        price = d[currency]
        vol = d.get(f"{currency}_24h_vol")
        mcap = d.get(f"{currency}_market_cap")
        rank = c.get("market_cap_rank")

        color = Colors.success if change >= 0 else Colors.error
        change_icon = "📈" if change >= 0 else "📉"
        e = discord.Embed(
            color=color,
            title=f"{c['name']} ({c['symbol'].upper()})",
            description=f"### **{price:,.8g} {currency.upper()}** ({change_icon} `{change:+.2f}%` 24h)"
        )
        if rank:
            e.add_field(name="Market Rank", value=f"#{rank}")
        if mcap:
            e.add_field(name="Market Cap", value=f"{mcap:,.0f} {currency.upper()}")
        if vol:
            e.add_field(name="24h Volume", value=f"{vol:,.0f} {currency.upper()}")
        if c.get("large"):
            e.set_thumbnail(url=c["large"])
        e.set_footer(text="CoinGecko Market Data")
        await ctx.send(embed=e)

    @commands.command(aliases=["mcserver", "mc_status"])
    @requires_uperm()
    async def minecraft(self, ctx: Context, address: str):
        """Minecraft server status (Java & Bedrock): `minecraft hypixel.net`"""
        address = address.strip().removeprefix("https://").removeprefix("http://").strip("/")
        quoted = urllib.parse.quote(address, safe=":")
        d = await self.get_json(f"https://api.mcsrvstat.us/3/{quoted}")
        if not d.get("online"):
            raise UserError(f"Minecraft server `{address}` is currently offline or unreachable.")
        motd_lines = d.get("motd", {}).get("clean", [])
        motd_text = "\n".join(motd_lines).strip() or "No MOTD provided."
        players_online = d.get("players", {}).get("online", 0)
        players_max = d.get("players", {}).get("max", 0)
        version = d.get("version", "Unknown")
        protocol = d.get("protocol", {}).get("version", "—")
        software = d.get("software", "Vanilla / Custom")

        e = discord.Embed(color=Colors.success, title=f"⛏️ Minecraft Server: {address}",
                          description=f"```\n{shorten(motd_text, 1000)}\n```")
        e.add_field(name="Status", value="🟢 Online")
        e.add_field(name="Players", value=f"**{players_online:,}** / {players_max:,}")
        e.add_field(name="Version", value=f"`{version}`")
        e.add_field(name="Software", value=f"`{software}`")
        e.add_field(name="Protocol", value=f"`{protocol}`")
        if d.get("ip"):
            e.add_field(name="Direct IP", value=f"`{d['ip']}:{d.get('port', 25565)}`")
        e.set_thumbnail(url=f"https://api.mcsrvstat.us/icon/{quoted}")
        e.set_footer(text="mcsrvstat.us Minecraft API")
        await ctx.send(embed=e)

    @commands.command(aliases=["rblx"])
    @requires_uperm()
    async def roblox(self, ctx: Context, username: str):
        """Roblox profile lookup"""
        try:
            async with self.bot.session.post("https://users.roblox.com/v1/usernames/users",
                                             json={"usernames": [username], "excludeBannedUsers": False},
                                             timeout=aiohttp.ClientTimeout(total=20)) as r:
                if r.status == 429:
                    raise UserError("Roblox is rate-limiting me, try again in a minute")
                if r.status >= 400:
                    raise UserError(f"Roblox returned an error ({r.status})")
                found = (await r.json(content_type=None)).get("data", [])
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            raise UserError("Roblox didn't respond, try again in a moment") from None
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
        e.add_field(name="Created", value=ts(iso_time(u["created"])))
        if u.get("isBanned"):
            e.add_field(name="Status", value="🚫 Banned")
        if thumb.get("data") and thumb["data"][0].get("imageUrl"):
            e.set_thumbnail(url=thumb["data"][0]["imageUrl"])
        await ctx.send(embed=e)

    @commands.command(aliases=["tr"])
    @requires_uperm()
    async def translate(self, ctx: Context, language: str, *, text: str = None):
        """Translate text (or reply to a message): `translate es hello`"""
        if text is None and ctx.message.reference and isinstance(ctx.message.reference.resolved, discord.Message):
            text = ctx.message.reference.resolved.content
        if not text:
            raise UserError("Give some text or reply to a message")
        language = LANGUAGE_NAMES.get(language.lower(), language)
        if not (2 <= len(language) <= 5 and language.replace("-", "").isalpha()):
            raise UserError("Give a language code or name first, e.g. `translate es hello` or `translate spanish hello`")
        text = text[:1800]
        async with ctx.typing():
            result = await self.translate_text(text, language)
        if result is None:
            raise UserError("Translation services are busy right now, try again in a minute")
        out, source = result
        e = discord.Embed(color=Colors.main, description=shorten(out, 4000))
        e.set_footer(text=f"{source or 'auto'} → {language}")
        await ctx.send(embed=e, allowed_mentions=discord.AllowedMentions.none())

    async def translate_text(self, text: str, target: str) -> tuple[str, str | None] | None:
        """Try several free translators in turn (Google's endpoints rate-limit hosting IPs quickly)."""
        timeout = aiohttp.ClientTimeout(total=15)
        session = self.bot.session

        async def gtx():
            async with session.get("https://translate.googleapis.com/translate_a/single", timeout=timeout,
                                   params={"client": "gtx", "sl": "auto", "tl": target, "dt": "t", "q": text}) as r:
                if r.status != 200:
                    return None
                data = await r.json(content_type=None)
                out = "".join(part[0] for part in (data[0] or []) if part and part[0])
                return (out, data[2]) if out else None

        async def chrome():
            async with session.get("https://clients5.google.com/translate_a/t", timeout=timeout,
                                   params={"client": "dict-chrome-ex", "sl": "auto", "tl": target, "q": text}) as r:
                if r.status != 200:
                    return None
                data = await r.json(content_type=None)
                first = data[0] if data else None
                if isinstance(first, list) and first and first[0]:
                    return first[0], (first[1] if len(first) > 1 else None)
                return (first, None) if isinstance(first, str) and first else None

        async def mymemory():
            async with session.get("https://api.mymemory.translated.net/get", timeout=timeout,
                                   params={"q": text[:500], "langpair": f"autodetect|{target}"}) as r:
                if r.status != 200:
                    return None
                data = await r.json(content_type=None)
                rd = data.get("responseData") or {}
                out = rd.get("translatedText")
                if not out or data.get("quotaFinished") or "MYMEMORY WARNING" in out or "INVALID" in out.upper()[:20]:
                    return None
                return out, rd.get("detectedLanguage")

        fallback = None
        for service in (gtx, chrome, mymemory):
            try:
                result = await service()
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, LookupError, TypeError):
                continue
            if not result:
                continue
            if result[0].strip().lower() == text.strip().lower():
                fallback = fallback or result  # unchanged: maybe already in that language, but try the next one
                continue
            return result
        return fallback

    @commands.command(aliases=["wiki"])
    @requires_uperm()
    async def wikipedia(self, ctx: Context, *, query: str):
        """Wikipedia summary"""
        s = await self.get_json("https://en.wikipedia.org/w/api.php", action="opensearch", search=query, limit=1, format="json")
        if not s[1]:
            raise UserError("No article found")
        title = s[1][0]
        d = await self.get_json(f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(title, safe='')}")
        e = discord.Embed(color=Colors.neutral, title=d["title"], url=d["content_urls"]["desktop"]["page"],
                          description=shorten(d.get("extract", ""), 1500))
        if d.get("thumbnail"):
            e.set_thumbnail(url=d["thumbnail"]["source"])
        await ctx.send(embed=e)


async def setup(bot):
    await bot.add_cog(Integrations(bot))
