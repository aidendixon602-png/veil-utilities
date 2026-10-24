"""Music lookups: what someone's listening to on Spotify, plus track/artist/album/playlist search.

Works with no setup: searches use Deezer's free public API and link to the same search on Spotify. With
SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET set, searches come straight from Spotify instead. (Since February 2026
Spotify only issues working API keys to developers with a Premium subscription, and removed fields like
followers, popularity, labels and artist top tracks, so the code below treats all of those as optional.)
"""
from __future__ import annotations

import asyncio
import base64
import time
import urllib.parse

import aiohttp
import discord
from discord.ext import commands

from core.config import settings
from core.utils import Context, UserError, shorten

API = "https://api.spotify.com/v1"
DEEZER = "https://api.deezer.com"
GREEN = 0x1DB954


def ms(n: int) -> str:
    s = int(n) // 1000
    return f"{s // 60}:{s % 60:02}"


def spotify_search_url(query: str) -> str:
    return f"https://open.spotify.com/search/{urllib.parse.quote(query)}"


class SpotifyUnavailable(Exception):
    """Spotify credentials missing or refused: use the free fallback."""


class SpotifyCog(commands.Cog, name="Spotify"):
    """Look up songs, artists, albums and what people are listening to on Spotify"""
    emoji = "🟢"

    def __init__(self, bot):
        self.bot = bot
        self.cid = settings.spotify_client_id
        self.secret = settings.spotify_client_secret
        self.token: str | None = None
        self.expires = 0.0
        self.disabled_reason: str | None = None  # set when Spotify refuses the credentials

    # ------------------------------------------------------------ HTTP
    async def _json(self, method: str, url: str, **kw):
        try:
            async with self.bot.session.request(method, url, timeout=aiohttp.ClientTimeout(total=20), **kw) as r:
                try:
                    data = await r.json(content_type=None)
                except ValueError:
                    data = None
                return r.status, data
        except (aiohttp.ClientError, asyncio.TimeoutError):
            raise UserError("The music service didn't respond, try again in a moment") from None

    async def auth(self) -> str:
        if not (self.cid and self.secret) or self.disabled_reason:
            raise SpotifyUnavailable
        if self.token and time.time() < self.expires - 60:
            return self.token
        basic = base64.b64encode(f"{self.cid}:{self.secret}".encode()).decode()
        status, data = await self._json("POST", "https://accounts.spotify.com/api/token",
                                        data={"grant_type": "client_credentials"},
                                        headers={"Authorization": f"Basic {basic}"})
        if not isinstance(data, dict) or "access_token" not in data:
            self.disabled_reason = "Spotify rejected SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET"
            raise SpotifyUnavailable
        self.token, self.expires = data["access_token"], time.time() + data.get("expires_in", 3600)
        return self.token

    async def api(self, path: str, **params):
        token = await self.auth()
        status, data = await self._json("GET", f"{API}{path}", params=params or None,
                                        headers={"Authorization": f"Bearer {token}"})
        if status == 403:  # app owner has no Premium / endpoint not available to Development Mode apps
            self.disabled_reason = "Spotify refused the app (its owner needs Spotify Premium)"
            raise SpotifyUnavailable
        if status == 404:
            raise UserError("Nothing found on Spotify")
        if status == 429:
            raise UserError("Spotify is rate-limiting me, try again in a minute")
        if status >= 400 or not isinstance(data, dict):
            raise UserError(f"Spotify error ({status})")
        return data

    async def spotify_search(self, kind: str, query: str) -> dict:
        data = await self.api("/search", q=query, type=kind, limit=1)
        items = [i for i in data.get(f"{kind}s", {}).get("items", []) if i]
        if not items:
            raise UserError(f"No {kind} found for `{query}`")
        return items[0]

    async def deezer_search(self, kind: str, query: str) -> dict:
        status, data = await self._json("GET", f"{DEEZER}/search/{kind}", params={"q": query, "limit": 1})
        if status >= 400 or not isinstance(data, dict) or "error" in data:
            raise UserError("The music search is busy, try again in a minute")
        items = data.get("data") or []
        if not items:
            raise UserError(f"No {kind} found for `{query}`")
        return items[0]

    @staticmethod
    def link_view(url: str, preview: str | None = None, label: str = "Open in Spotify") -> discord.ui.View:
        v = discord.ui.View()
        v.add_item(discord.ui.Button(label=label, url=url))
        if preview:
            v.add_item(discord.ui.Button(label="30s preview", url=preview))
        return v

    @staticmethod
    def footer(e: discord.Embed) -> None:
        e.set_footer(text="Info from Deezer • button opens the same search on Spotify")

    # ------------------------------------------------------------ listening (presence, no key)
    @commands.group(aliases=["sp"], invoke_without_command=True)
    @commands.guild_only()
    async def spotify(self, ctx: Context, *, member: discord.Member = None):
        """What someone is listening to on Spotify right now"""
        member = member or ctx.author
        member = ctx.guild.get_member(member.id) or member  # the cached member carries presence data
        act = next((a for a in member.activities if isinstance(a, discord.Spotify)), None)
        if not act:
            hint = "" if self.bot.intents.presences else " (the bot's Presence Intent is off)"
            raise UserError(f"{member.mention} isn't listening to Spotify, or hides it in their status{hint}")
        e = discord.Embed(color=GREEN, title=act.title, url=act.track_url,
                          description=f"by **{', '.join(act.artists)}**\non *{act.album}*")
        e.set_author(name=f"{member.display_name} is listening", icon_url=member.display_avatar.url)
        e.set_thumbnail(url=act.album_cover_url)
        elapsed = (discord.utils.utcnow() - act.start).total_seconds()
        total = act.duration.total_seconds()
        pct = max(0, min(1, elapsed / total)) if total else 0
        e.add_field(name="Progress", value=f"`{ms(int(max(0, min(elapsed, total)) * 1000))}` "
                                           f"{'▬' * int(pct * 14)}🔘{'▬' * (14 - int(pct * 14))} `{ms(int(total * 1000))}`")
        await ctx.send(embed=e, view=self.link_view(act.track_url))

    # ------------------------------------------------------------ search
    @spotify.command(name="track", aliases=["song", "t"])
    async def sp_track(self, ctx: Context, *, query: str):
        """Look up a track"""
        try:
            t = await self.spotify_search("track", query)
        except SpotifyUnavailable:
            d = await self.deezer_search("track", query)
            e = discord.Embed(color=GREEN, title=d["title"], url=spotify_search_url(f"{d['title']} {d['artist']['name']}"),
                              description=f"by **{d['artist']['name']}**\non *{d['album']['title']}*")
            e.add_field(name="Length", value=ms(d.get("duration", 0) * 1000))
            if d.get("explicit_lyrics"):
                e.add_field(name="Explicit", value="Yes")
            if d["album"].get("cover_xl"):
                e.set_thumbnail(url=d["album"]["cover_xl"])
            self.footer(e)
            return await ctx.send(embed=e, view=self.link_view(e.url, d.get("preview")))
        album = t.get("album") or {}
        e = discord.Embed(color=GREEN, title=t["name"], url=t["external_urls"]["spotify"],
                          description=f"by **{', '.join(a['name'] for a in t.get('artists', []))}**\non *{album.get('name', '?')}*")
        e.add_field(name="Length", value=ms(t.get("duration_ms", 0)))
        e.add_field(name="Released", value=album.get("release_date", "?"))
        if t.get("popularity") is not None:
            e.add_field(name="Popularity", value=f"{t['popularity']}/100")
        if t.get("explicit"):
            e.add_field(name="Explicit", value="Yes")
        if album.get("images"):
            e.set_thumbnail(url=album["images"][0]["url"])
        await ctx.send(embed=e, view=self.link_view(t["external_urls"]["spotify"], t.get("preview_url")))

    @spotify.command(name="artist", aliases=["a"])
    async def sp_artist(self, ctx: Context, *, query: str):
        """Look up an artist and their top tracks"""
        try:
            a = await self.spotify_search("artist", query)
        except SpotifyUnavailable:
            d = await self.deezer_search("artist", query)
            _, top = await self._json("GET", f"{DEEZER}/artist/{d['id']}/top", params={"limit": 5})
            e = discord.Embed(color=GREEN, title=d["name"], url=spotify_search_url(d["name"]))
            e.add_field(name="Fans", value=f"{d.get('nb_fan', 0):,}")
            e.add_field(name="Albums", value=f"{d.get('nb_album', 0):,}")
            tracks = "\n".join(f"`{i}` {shorten(t['title'], 60)}"
                               for i, t in enumerate((top or {}).get("data", [])[:5], 1))
            if tracks:
                e.add_field(name="Top tracks", value=tracks, inline=False)
            if d.get("picture_xl"):
                e.set_thumbnail(url=d["picture_xl"])
            self.footer(e)
            return await ctx.send(embed=e, view=self.link_view(e.url))
        e = discord.Embed(color=GREEN, title=a["name"], url=a["external_urls"]["spotify"])
        if (a.get("followers") or {}).get("total") is not None:
            e.add_field(name="Followers", value=f"{a['followers']['total']:,}")
        if a.get("popularity") is not None:
            e.add_field(name="Popularity", value=f"{a['popularity']}/100")
        if a.get("genres"):
            e.add_field(name="Genres", value=", ".join(a["genres"][:5]), inline=False)
        try:  # top-tracks was removed for new Development Mode apps: fall back to a track search
            top = (await self.api(f"/artists/{a['id']}/top-tracks", market="US")).get("tracks", [])
        except (SpotifyUnavailable, UserError):
            self.disabled_reason = None
            try:
                found = await self.api("/search", q=f'artist:"{a["name"]}"', type="track", limit=5)
                top = [t for t in found.get("tracks", {}).get("items", []) if t]
            except (SpotifyUnavailable, UserError):
                top = []
        tracks = "\n".join(f"`{i}` [{shorten(t['name'], 60)}]({t['external_urls']['spotify']})"
                           for i, t in enumerate(top[:5], 1))
        if tracks:
            e.add_field(name="Top tracks", value=tracks, inline=False)
        if a.get("images"):
            e.set_thumbnail(url=a["images"][0]["url"])
        await ctx.send(embed=e, view=self.link_view(a["external_urls"]["spotify"]))

    @spotify.command(name="album", aliases=["al"])
    async def sp_album(self, ctx: Context, *, query: str):
        """Look up an album and its tracklist"""
        try:
            al = await self.spotify_search("album", query)
            full = await self.api(f"/albums/{al['id']}")
        except SpotifyUnavailable:
            d = await self.deezer_search("album", query)
            _, full = await self._json("GET", f"{DEEZER}/album/{d['id']}")
            full = full if isinstance(full, dict) and "error" not in full else d
            tracks = (full.get("tracks") or {}).get("data", [])
            listing = "\n".join(f"`{i}` {shorten(t['title'], 50)} `{ms(t.get('duration', 0) * 1000)}`"
                                for i, t in enumerate(tracks[:15], 1))
            if len(tracks) > 15:
                listing += f"\n…and {len(tracks) - 15} more"
            e = discord.Embed(color=GREEN, title=d["title"], url=spotify_search_url(f"{d['title']} {d['artist']['name']}"),
                              description=f"by **{d['artist']['name']}**")
            e.add_field(name="Released", value=full.get("release_date", "?"))
            e.add_field(name="Tracks", value=str(full.get("nb_tracks", len(tracks))))
            if full.get("label"):
                e.add_field(name="Label", value=full["label"])
            e.add_field(name="Tracklist", value=listing or "—", inline=False)
            if d.get("cover_xl"):
                e.set_thumbnail(url=d["cover_xl"])
            self.footer(e)
            return await ctx.send(embed=e, view=self.link_view(e.url))
        tracks = (full.get("tracks") or {}).get("items", [])
        listing = "\n".join(f"`{t.get('track_number', i)}` {shorten(t['name'], 50)} `{ms(t.get('duration_ms', 0))}`"
                            for i, t in enumerate(tracks[:15], 1))
        if len(tracks) > 15:
            listing += f"\n…and {len(tracks) - 15} more"
        e = discord.Embed(color=GREEN, title=full["name"], url=full["external_urls"]["spotify"],
                          description=f"by **{', '.join(a['name'] for a in full.get('artists', []))}**")
        e.add_field(name="Released", value=full.get("release_date", "?"))
        e.add_field(name="Tracks", value=str(full.get("total_tracks", len(tracks))))
        if full.get("label"):
            e.add_field(name="Label", value=full["label"])
        e.add_field(name="Tracklist", value=listing or "—", inline=False)
        if full.get("images"):
            e.set_thumbnail(url=full["images"][0]["url"])
        await ctx.send(embed=e, view=self.link_view(full["external_urls"]["spotify"]))

    @spotify.command(name="playlist", aliases=["pl"])
    async def sp_playlist(self, ctx: Context, *, query: str):
        """Look up a public playlist"""
        try:
            p = await self.spotify_search("playlist", query)
        except SpotifyUnavailable:
            d = await self.deezer_search("playlist", query)
            e = discord.Embed(color=GREEN, title=d["title"], url=d["link"])
            e.add_field(name="By", value=(d.get("user") or {}).get("name") or "?")
            e.add_field(name="Tracks", value=f"{d.get('nb_tracks', 0):,}")
            if d.get("picture_xl"):
                e.set_thumbnail(url=d["picture_xl"])
            e.set_footer(text="Playlist from Deezer (Spotify search isn't set up on this bot)")
            view = self.link_view(d["link"], label="Open on Deezer")
            view.add_item(discord.ui.Button(label="Search on Spotify", url=spotify_search_url(query)))
            return await ctx.send(embed=e, view=view)
        e = discord.Embed(color=GREEN, title=p["name"], url=p["external_urls"]["spotify"],
                          description=shorten(p.get("description") or "", 300) or None)
        e.add_field(name="By", value=(p.get("owner") or {}).get("display_name") or "?")
        total = (p.get("items") or p.get("tracks") or {}).get("total")  # renamed tracks -> items in 2026
        if total is not None:
            e.add_field(name="Tracks", value=f"{total:,}")
        if p.get("images"):
            e.set_thumbnail(url=p["images"][0]["url"])
        await ctx.send(embed=e, view=self.link_view(p["external_urls"]["spotify"]))

    @spotify.command(name="cover", aliases=["art"])
    async def sp_cover(self, ctx: Context, *, query: str):
        """Full-size album cover for a track or album"""
        try:
            al = await self.spotify_search("album", query)
            if not al.get("images"):
                raise UserError("No cover art")
            title = f"{al['name']} — {al['artists'][0]['name']}" if al.get("artists") else al["name"]
            url, image = al["external_urls"]["spotify"], al["images"][0]["url"]
        except SpotifyUnavailable:
            d = await self.deezer_search("album", query)
            if not d.get("cover_xl"):
                raise UserError("No cover art") from None
            title = f"{d['title']} — {d['artist']['name']}"
            url, image = spotify_search_url(title.replace(" — ", " ")), d["cover_xl"]
        e = discord.Embed(color=GREEN, title=shorten(title, 256), url=url)
        e.set_image(url=image)
        await ctx.send(embed=e)


async def setup(bot):
    await bot.add_cog(SpotifyCog(bot))
