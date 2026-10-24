"""Music via Lavalink (wavelink 3). Only active when LAVALINK_URI is set."""
from __future__ import annotations

import logging
from typing import cast

import discord
from discord.ext import commands

from core.config import settings
from core.utils import Colors, Context, UserError, chunk_embeds, shorten

try:
    import wavelink
except ImportError:  # pragma: no cover
    wavelink = None

log = logging.getLogger("veil.music")


def fmt_ms(ms: int) -> str:
    s = int(ms // 1000)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


class Music(commands.Cog):
    """Play music in voice channels (Spotify/YouTube/SoundCloud via Lavalink)"""
    emoji = "🎵"

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        uri = settings.lavalink_uri
        if not uri or wavelink is None:
            log.info("Music disabled (no LAVALINK_URI)")
            return
        node = wavelink.Node(uri=uri, password=settings.lavalink_password,
                             inactive_player_timeout=300)
        self.bot.loop.create_task(self._connect(node))

    async def _connect(self, node):
        await self.bot.wait_until_ready()
        try:
            await wavelink.Pool.connect(nodes=[node], client=self.bot, cache_capacity=100)
        except Exception:
            log.exception("Could not connect to Lavalink")

    async def cog_check(self, ctx: Context) -> bool:
        if wavelink is None or not settings.lavalink_uri:
            raise UserError("Music isn't configured on this bot (no Lavalink server)")
        if not ctx.guild:
            raise commands.NoPrivateMessage()
        return True

    # ------------------------------------------------------------ helpers
    async def player(self, ctx: Context, connect: bool = False) -> "wavelink.Player":
        player = cast(wavelink.Player, ctx.voice_client)
        if player:
            if ctx.author.voice is None or ctx.author.voice.channel != player.channel:
                raise UserError(f"You need to be in {player.channel.mention}")
            return player
        if not connect:
            raise UserError("I'm not playing anything")
        if not ctx.author.voice:
            raise UserError("Join a voice channel first")
        player = await ctx.author.voice.channel.connect(cls=wavelink.Player, self_deaf=True)
        player.autoplay = wavelink.AutoPlayMode.partial
        player.home = ctx.channel  # type: ignore[attr-defined]
        return player

    def track_embed(self, track, title="Now playing") -> discord.Embed:
        e = discord.Embed(color=Colors.main, title=title,
                          description=f"[{track.title}]({track.uri}) by **{track.author}**")
        e.set_footer(text=f"Length {fmt_ms(track.length)}" + (f" • requested by {track.extras.requester}"
                                                            if getattr(track.extras, "requester", None) else ""))
        if track.artwork:
            e.set_thumbnail(url=track.artwork)
        return e

    @commands.Cog.listener()
    async def on_wavelink_track_start(self, payload):
        home = getattr(payload.player, "home", None)
        if home:
            try:
                await home.send(embed=self.track_embed(payload.track))
            except discord.HTTPException:
                pass

    @commands.Cog.listener()
    async def on_wavelink_inactive_player(self, player):
        await player.disconnect()

    # ------------------------------------------------------------ commands
    @commands.command(aliases=["p"])
    async def play(self, ctx: Context, *, query: str):
        """Play a song or playlist (URL or search)"""
        player = await self.player(ctx, connect=True)
        tracks = await wavelink.Playable.search(query)
        if not tracks:
            raise UserError("No results")
        if isinstance(tracks, wavelink.Playlist):
            tracks.extras = {"requester": str(ctx.author)}
            added = await player.queue.put_wait(tracks)
            await ctx.approve(f"Queued **{added}** tracks from **{tracks.name}**")
        else:
            track = tracks[0]
            track.extras = {"requester": str(ctx.author)}
            await player.queue.put_wait(track)
            if player.playing:
                await ctx.send(embed=self.track_embed(track, f"Queued (#{player.queue.count})"))
        if not player.playing:
            await player.play(player.queue.get(), volume=getattr(player, "_vol", 60))

    @commands.command(aliases=["next"])
    async def skip(self, ctx: Context):
        """Skip the current song"""
        player = await self.player(ctx)
        await player.skip(force=True)
        await ctx.message.add_reaction("⏭️")

    @commands.command()
    async def pause(self, ctx: Context):
        """Pause"""
        player = await self.player(ctx)
        await player.pause(True)
        await ctx.message.add_reaction("⏸️")

    @commands.command()
    async def resume(self, ctx: Context):
        """Resume"""
        player = await self.player(ctx)
        await player.pause(False)
        await ctx.message.add_reaction("▶️")

    @commands.command(aliases=["dc", "disconnect"])
    async def stop(self, ctx: Context):
        """Stop and leave"""
        player = await self.player(ctx)
        await player.disconnect()
        await ctx.message.add_reaction("👋")

    @commands.command(aliases=["q"])
    async def queue(self, ctx: Context):
        """Show the queue"""
        player = await self.player(ctx)
        lines = [f"[{shorten(t.title, 60)}]({t.uri}) `{fmt_ms(t.length)}`" for t in player.queue]
        head = f"**Now:** [{player.current.title}]({player.current.uri})\n\n" if player.current else ""
        pages = chunk_embeds(f"Queue ({player.queue.count})", lines) or [discord.Embed(color=Colors.main, title="Queue", description="Empty")]
        pages[0].description = head + (pages[0].description or "")
        await ctx.paginate(pages)

    @commands.command(aliases=["playing", "current"])
    async def song(self, ctx: Context):
        """What's playing"""
        player = await self.player(ctx)
        if not player.current:
            raise UserError("Nothing is playing")
        t = player.current
        e = self.track_embed(t)
        pct = player.position / t.length if t.length else 0
        e.add_field(name="Progress", value=f"`{fmt_ms(player.position)}` {'▬' * int(pct * 15)}🔘{'▬' * (15 - int(pct * 15))} `{fmt_ms(t.length)}`")
        await ctx.send(embed=e)

    @commands.command(aliases=["vol", "v"])
    async def volume(self, ctx: Context, volume: int):
        """Set volume (0–150)"""
        player = await self.player(ctx)
        volume = max(0, min(volume, 150))
        await player.set_volume(volume)
        player._vol = volume  # type: ignore[attr-defined]
        await ctx.approve(f"Volume **{volume}%**")

    @commands.command()
    async def loop(self, ctx: Context, mode: str = "track"):
        """Loop mode: track, queue or off"""
        player = await self.player(ctx)
        modes = {"track": wavelink.QueueMode.loop, "queue": wavelink.QueueMode.loop_all, "off": wavelink.QueueMode.normal}
        if mode not in modes:
            raise UserError("Use `track`, `queue` or `off`")
        player.queue.mode = modes[mode]
        await ctx.approve(f"Loop: **{mode}**")

    @commands.command()
    async def shuffle(self, ctx: Context):
        """Shuffle the queue"""
        player = await self.player(ctx)
        player.queue.shuffle()
        await ctx.message.add_reaction("🔀")

    @commands.command()
    async def remove(self, ctx: Context, position: int):
        """Remove a track from the queue"""
        player = await self.player(ctx)
        if not 1 <= position <= player.queue.count:
            raise UserError("Invalid position")
        track = player.queue[position - 1]
        player.queue.delete(position - 1)
        await ctx.approve(f"Removed **{track.title}**")

    @commands.command()
    async def clearqueue(self, ctx: Context):
        """Clear the queue"""
        player = await self.player(ctx)
        player.queue.clear()
        await ctx.approve("Cleared the queue")

    @commands.command()
    async def seek(self, ctx: Context, position: str):
        """Seek to a time: `seek 1:30`"""
        player = await self.player(ctx)
        parts = [int(p) for p in position.split(":")]
        secs = sum(p * 60 ** i for i, p in enumerate(reversed(parts)))
        await player.seek(secs * 1000)
        await ctx.approve(f"Seeked to **{position}**")

    @commands.command()
    async def autoplay(self, ctx: Context, state: bool):
        """Keep playing recommended songs when the queue ends"""
        player = await self.player(ctx)
        player.autoplay = wavelink.AutoPlayMode.enabled if state else wavelink.AutoPlayMode.partial
        await ctx.approve(f"Autoplay **{'on' if state else 'off'}**")

    @commands.command(name="audiofilter", aliases=["preset", "fx"])
    async def audiofilter(self, ctx: Context, preset: str = None):
        """Audio presets: nightcore, vaporwave, bassboost, 8d, slowed, karaoke, clear"""
        player = await self.player(ctx)
        f: wavelink.Filters = player.filters
        presets = ("nightcore", "vaporwave", "bassboost", "8d", "slowed", "karaoke", "clear")
        if preset not in presets:
            raise UserError(f"Presets: {', '.join(f'`{p}`' for p in presets)}")
        f.reset()
        if preset == "nightcore":
            f.timescale.set(pitch=1.2, speed=1.2, rate=1)
        elif preset == "vaporwave":
            f.timescale.set(pitch=0.8, speed=0.85, rate=1)
        elif preset == "slowed":
            f.timescale.set(pitch=0.9, speed=0.8, rate=1)
        elif preset == "bassboost":
            f.equalizer.set(bands=[{"band": i, "gain": g} for i, g in enumerate([0.3, 0.25, 0.2, 0.1, 0.05])])
        elif preset == "8d":
            f.rotation.set(rotation_hz=0.2)
        elif preset == "karaoke":
            f.karaoke.set(level=1.0, mono_level=1.0, filter_band=220.0, filter_width=100.0)
        await player.set_filters(f, seek=True)
        await ctx.approve(f"Filter: **{preset}**")


async def setup(bot):
    await bot.add_cog(Music(bot))
