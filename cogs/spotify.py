"""Spotify lookups: what someone's listening to (no key needed) + track/artist/album/playlist search (free API key)."""
from __future__ import annotations

import base64
import time

import discord
from discord.ext import commands

from core.config import settings
from core.utils import Context, UserError, shorten

API = "https://api.spotify.com/v1"
GREEN = 0x1DB954


def ms(n: int) -> str:
    s = n // 1000
    return f"{s // 60}:{s % 60:02}"


class SpotifyCog(commands.Cog, name="Spotify"):
    """Look up Spotify tracks, artists, albums and what people are listening to"""
    emoji = "🟢"

    def __init__(self, bot):
        self.bot = bot
        self.cid = settings.spotify_client_id
        self.secret = settings.spotify_client_secret
        self.token: str | None = None
        self.expires = 0.0

    async def auth(self) -> str:
        if not (self.cid and self.secret):
            raise UserError("Spotify search isn't configured — the bot owner needs `SPOTIFY_CLIENT_ID` and "
                            "`SPOTIFY_CLIENT_SECRET` (free at developer.spotify.com)")
        if self.token and time.time() < self.expires - 60:
            return self.token
        basic = base64.b64encode(f"{self.cid}:{self.secret}".encode()).decode()
        async with self.bot.session.post("https://accounts.spotify.com/api/token",
                                         data={"grant_type": "client_credentials"},
                                         headers={"Authorization": f"Basic {basic}"}) as r:
            data = await r.json(content_type=None)
        if "access_token" not in data:
            raise UserError("Spotify rejected the bot's credentials")
        self.token, self.expires = data["access_token"], time.time() + data.get("expires_in", 3600)
        return self.token

    async def api(self, path: str, **params):
        token = await self.auth()
        async with self.bot.session.get(f"{API}{path}", params=params or None,
                                        headers={"Authorization": f"Bearer {token}"}) as r:
            if r.status == 404:
                raise UserError("Nothing found on Spotify")
            if r.status >= 400:
                raise UserError(f"Spotify error ({r.status})")
            return await r.json(content_type=None)

    async def search(self, kind: str, query: str):
        data = await self.api("/search", q=query, type=kind, limit=1)
        items = data.get(f"{kind}s", {}).get("items", [])
        if not items or items[0] is None:
            raise UserError(f"No {kind} found for `{query}`")
        return items[0]

    @staticmethod
    def link_view(url: str, preview: str | None = None) -> discord.ui.View:
        v = discord.ui.View()
        v.add_item(discord.ui.Button(label="Open in Spotify", url=url))
        if preview:
            v.add_item(discord.ui.Button(label="30s preview", url=preview))
        return v

    # ------------------------------------------------------------ listening (presence, no key)
    @commands.group(aliases=["sp"], invoke_without_command=True)
    async def spotify(self, ctx: Context, *, member: discord.Member = None):
        """What someone is listening to on Spotify right now"""
        member = member or ctx.author
        act = next((a for a in member.activities if isinstance(a, discord.Spotify)), None)
        if not act:
            raise UserError(f"{member.mention} isn't listening to Spotify (or hides it in their status)")
        e = discord.Embed(color=GREEN, title=act.title, url=act.track_url,
                          description=f"by **{', '.join(act.artists)}**\non *{act.album}*")
        e.set_author(name=f"{member.display_name} is listening", icon_url=member.display_avatar.url)
        e.set_thumbnail(url=act.album_cover_url)
        elapsed = (discord.utils.utcnow() - act.start).total_seconds()
        total = act.duration.total_seconds()
        pct = max(0, min(1, elapsed / total)) if total else 0
        e.add_field(name="Progress", value=f"`{ms(int(max(0, elapsed) * 1000))}` "
                                           f"{'▬' * int(pct * 14)}🔘{'▬' * (14 - int(pct * 14))} `{ms(int(total * 1000))}`")
        await ctx.send(embed=e, view=self.link_view(act.track_url))

    # ------------------------------------------------------------ search (API key)
    @spotify.command(name="track", aliases=["song", "t"])
    async def sp_track(self, ctx: Context, *, query: str):
        """Look up a track"""
        t = await self.search("track", query)
        e = discord.Embed(color=GREEN, title=t["name"], url=t["external_urls"]["spotify"],
                          description=f"by **{', '.join(a['name'] for a in t['artists'])}**\non *{t['album']['name']}*")
        e.add_field(name="Length", value=ms(t["duration_ms"]))
        e.add_field(name="Released", value=t["album"].get("release_date", "?"))
        e.add_field(name="Popularity", value=f"{t.get('popularity', 0)}/100")
        if t.get("explicit"):
            e.add_field(name="Explicit", value="Yes")
        if t["album"]["images"]:
            e.set_thumbnail(url=t["album"]["images"][0]["url"])
        await ctx.send(embed=e, view=self.link_view(t["external_urls"]["spotify"], t.get("preview_url")))

    @spotify.command(name="artist", aliases=["a"])
    async def sp_artist(self, ctx: Context, *, query: str):
        """Look up an artist and their top tracks"""
        a = await self.search("artist", query)
        top = await self.api(f"/artists/{a['id']}/top-tracks", market="US")
        e = discord.Embed(color=GREEN, title=a["name"], url=a["external_urls"]["spotify"])
        e.add_field(name="Followers", value=f"{a['followers']['total']:,}")
        e.add_field(name="Popularity", value=f"{a.get('popularity', 0)}/100")
        if a.get("genres"):
            e.add_field(name="Genres", value=", ".join(a["genres"][:5]), inline=False)
        tracks = "\n".join(f"`{i}` [{t['name']}]({t['external_urls']['spotify']})" for i, t in enumerate(top["tracks"][:5], 1))
        if tracks:
            e.add_field(name="Top tracks", value=tracks, inline=False)
        if a["images"]:
            e.set_thumbnail(url=a["images"][0]["url"])
        await ctx.send(embed=e, view=self.link_view(a["external_urls"]["spotify"]))

    @spotify.command(name="album", aliases=["al"])
    async def sp_album(self, ctx: Context, *, query: str):
        """Look up an album and its tracklist"""
        al = await self.search("album", query)
        full = await self.api(f"/albums/{al['id']}")
        tracks = full["tracks"]["items"]
        listing = "\n".join(f"`{t['track_number']}` {shorten(t['name'], 50)} `{ms(t['duration_ms'])}`" for t in tracks[:15])
        if len(tracks) > 15:
            listing += f"\n…and {len(tracks) - 15} more"
        e = discord.Embed(color=GREEN, title=full["name"], url=full["external_urls"]["spotify"],
                          description=f"by **{', '.join(a['name'] for a in full['artists'])}**")
        e.add_field(name="Released", value=full.get("release_date", "?"))
        e.add_field(name="Tracks", value=str(full["total_tracks"]))
        e.add_field(name="Label", value=full.get("label") or "—")
        e.add_field(name="Tracklist", value=listing or "—", inline=False)
        if full["images"]:
            e.set_thumbnail(url=full["images"][0]["url"])
        await ctx.send(embed=e, view=self.link_view(full["external_urls"]["spotify"]))

    @spotify.command(name="playlist", aliases=["pl"])
    async def sp_playlist(self, ctx: Context, *, query: str):
        """Look up a public playlist"""
        p = await self.search("playlist", query)
        e = discord.Embed(color=GREEN, title=p["name"], url=p["external_urls"]["spotify"],
                          description=shorten(p.get("description") or "", 300) or None)
        e.add_field(name="By", value=p["owner"].get("display_name") or "?")
        e.add_field(name="Tracks", value=f"{p['tracks']['total']:,}")
        if p.get("images"):
            e.set_thumbnail(url=p["images"][0]["url"])
        await ctx.send(embed=e, view=self.link_view(p["external_urls"]["spotify"]))

    @spotify.command(name="cover", aliases=["art"])
    async def sp_cover(self, ctx: Context, *, query: str):
        """Full-size album cover for a track or album"""
        al = await self.search("album", query)
        if not al["images"]:
            raise UserError("No cover art")
        e = discord.Embed(color=GREEN, title=f"{al['name']} — {al['artists'][0]['name']}", url=al["external_urls"]["spotify"])
        e.set_image(url=al["images"][0]["url"])
        await ctx.send(embed=e)


async def setup(bot):
    await bot.add_cog(SpotifyCog(bot))
