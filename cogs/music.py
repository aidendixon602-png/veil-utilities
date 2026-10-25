"""Music via Lavalink (wavelink 3).

Works out of the box on free public Lavalink nodes; set LAVALINK_URI for your own server (core/lavalink_nodes.py).
"""
from __future__ import annotations

import asyncio
import logging
from typing import cast

import discord
from discord.ext import commands, tasks

from core.config import settings
from core.lavalink_nodes import resolve_nodes
from core.utils import Colors, Context, UserError, chunk_embeds, has_perms, missing_perms, shorten

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


def parse_time(text: str) -> int:
    """'90', '1:30' or '1:02:03' -> seconds."""
    try:
        parts = [int(p) for p in text.strip().split(":")]
    except ValueError:
        raise UserError("Use a time like `1:30` or `90`") from None
    if not 1 <= len(parts) <= 3 or any(p < 0 for p in parts):
        raise UserError("Use a time like `1:30` or `90`")
    return sum(p * 60 ** i for i, p in enumerate(reversed(parts)))


MAX_QUEUE = 500
ALONE_TIMEOUT = 120  # seconds the bot stays in an empty voice channel


def listeners(player) -> list[discord.Member]:
    """Humans in the player's voice channel (not deafened)."""
    ch = getattr(player, "channel", None)
    if ch is None:
        return []
    return [m for m in ch.members if not m.bot and not (m.voice and (m.voice.deaf or m.voice.self_deaf))]


class Music(commands.Cog):
    """Play music in voice channels (Spotify/YouTube/SoundCloud via Lavalink)"""
    emoji = "🎵"

    def __init__(self, bot):
        self.bot = bot
        self._skip_votes: dict[int, set[int]] = {}      # guild id -> member ids voting to skip the current track
        self._alone_tasks: dict[int, asyncio.Task] = {}  # guild id -> pending "leave empty channel" task

    async def cog_load(self):
        self.nodes, self.public = resolve_nodes(settings.lavalink_uri, settings.lavalink_password,
                                                settings.music_enabled)
        if wavelink is None or not self.nodes:
            log.info("Music disabled (%s)", "wavelink isn't installed" if wavelink is None else "MUSIC_ENABLED=false")
            self.nodes = []
            return
        log.info("Music: connecting to %d %s Lavalink node(s)", len(self.nodes), "public" if self.public else "")
        self.node_watchdog.start()

    def _make_node(self, index: int) -> "wavelink.Node":
        uri, password = self.nodes[index]
        # limited retries so one dead node never blocks the others; the watchdog brings it back later
        return wavelink.Node(identifier=f"node-{index + 1}", uri=uri, password=password,
                             inactive_player_timeout=300, retries=3)

    async def _connect_one(self, index: int, first: bool) -> None:
        try:
            await wavelink.Pool.connect(nodes=[self._make_node(index)], client=self.bot,
                                        cache_capacity=100 if first else None)
        except Exception:
            log.warning("Could not connect to Lavalink node %s", self.nodes[index][0], exc_info=True)

    @tasks.loop(minutes=5)
    async def node_watchdog(self):
        """Connect every node in parallel, and re-add nodes that dropped and used up their retries."""
        try:
            await self._check_nodes(self.node_watchdog.current_loop == 0)
        except Exception:  # an unhandled error would stop the watchdog for good
            log.exception("Lavalink node watchdog failed")

    async def _check_nodes(self, first: bool):
        jobs = []
        for i in range(len(self.nodes)):
            try:
                node = wavelink.Pool.get_node(f"node-{i + 1}")
            except Exception:  # not in the pool yet
                node = None
            if node is not None:
                if node.status is not wavelink.NodeStatus.DISCONNECTED:
                    continue
                try:
                    if hasattr(node, "_session") and node._session and not node._session.closed:
                        await node._session.close()
                    await node.close(eject=True)
                except Exception:
                    pass
            jobs.append(self._connect_one(i, first))
        if jobs:
            await asyncio.gather(*jobs)
        if first:
            up = sum(1 for n in wavelink.Pool.nodes.values() if n.status is wavelink.NodeStatus.CONNECTED)
            log.info("Music: %d/%d Lavalink node(s) online", up, len(self.nodes))

    @node_watchdog.before_loop
    async def _before_watchdog(self):
        await self.bot.wait_until_ready()

    def online_nodes(self) -> list:
        if wavelink is None:
            return []
        return [n for n in wavelink.Pool.nodes.values() if n.status is wavelink.NodeStatus.CONNECTED]

    async def search(self, query: str, prefer=None):
        """Search with failover: try each online node (and YouTube Music -> YouTube -> SoundCloud for plain
        searches) until something returns results. Raises UserError when nothing works."""
        nodes = sorted(self.online_nodes(), key=lambda n: (n is not prefer, len(n.players)))
        if not nodes:
            raise UserError("The music server is offline right now, try again in a minute")
        is_url = query.startswith(("http://", "https://"))
        sources = [None] if is_url else ["scsearch", "ytmsearch", "ytsearch"]
        for node in nodes[:3]:
            for source in sources:
                try:
                    if source:
                        found = await wavelink.Playable.search(query, source=source, node=node)
                    else:
                        found = await wavelink.Playable.search(query, node=node)
                except Exception as e:  # LavalinkLoadException, node offline, unsupported source...
                    log.debug("search %r on %s/%s failed: %r", query, node.identifier, source, e)
                    continue
                if found:
                    return found
        return None

    async def cog_check(self, ctx: Context) -> bool:
        if wavelink is None or not self.nodes:
            raise UserError("Music is turned off on this bot")
        if not ctx.guild:
            raise commands.NoPrivateMessage()
        return True

    # ------------------------------------------------------------ helpers
    async def player(self, ctx: Context, connect: bool = False, listen_only: bool = False) -> "wavelink.Player":
        """Get the guild's player. Controlling it requires being in the same voice channel;
        `listen_only` commands (queue, song) just need a player to exist."""
        player = cast(wavelink.Player, ctx.voice_client)
        if player:
            if listen_only:
                return player
            if ctx.author.voice is None or ctx.author.voice.channel != player.channel:
                raise UserError(f"You need to be in {player.channel.mention}")
            return player
        if not connect:
            raise UserError("I'm not playing anything")
        if not ctx.author.voice or not ctx.author.voice.channel:
            raise UserError("Join a voice channel first")
        channel = ctx.author.voice.channel
        me = channel.permissions_for(ctx.guild.me)
        if not (me.connect and me.speak):
            raise UserError(f"I need **Connect** and **Speak** in {channel.mention}")
        if channel.user_limit and len(channel.members) >= channel.user_limit and not me.move_members:
            raise UserError(f"{channel.mention} is full")
        try:
            player = await channel.connect(cls=wavelink.Player, self_deaf=False, timeout=15)
        except (asyncio.TimeoutError, discord.ClientException):
            raise UserError("I couldn't join your voice channel, try again") from None
        except Exception as e:  # wavelink.InvalidNodeException etc. when Lavalink is down
            log.warning("Voice connect failed: %r", e)
            raise UserError("The music server is offline right now, try again later") from None
        player.autoplay = wavelink.AutoPlayMode.disabled
        player.home = ctx.channel  # type: ignore[attr-defined]
        return player

    async def is_dj(self, ctx: Context, player=None) -> bool:
        """DJ = bot owner, Manage Channels / Move Members (incl. fake perms & perm levels),
        the assigned DJ role, or being alone with the bot."""
        if await self.bot.is_owner(ctx.author):
            return True
        perms = ctx.author.guild_permissions
        if perms.administrator or perms.manage_channels or perms.move_members or ctx.author.id == ctx.guild.owner_id:
            return True
        cfg = await self.bot.db.get_config(ctx.guild.id, "music")
        role_id = cfg.get("dj_role")
        if role_id and ctx.guild.get_role(role_id) is not None:
            if any(r.id == role_id for r in ctx.author.roles):
                return True
        elif not await missing_perms(ctx, move_members=True):
            return True
        from core import perms as _perms
        if _perms.rank_of(await self.bot.db.get_config(ctx.guild.id, "permlevels"), ctx.author) >= 1:
            return True
        if player is not None and listeners(player) == [ctx.author]:
            return True
        return False

    async def require_dj(self, ctx: Context, player=None, *, requester_ok=None) -> None:
        if requester_ok is not None and getattr(requester_ok.extras, "requester_id", None) == ctx.author.id:
            return
        if not await self.is_dj(ctx, player):
            cfg = await self.bot.db.get_config(ctx.guild.id, "music")
            role = ctx.guild.get_role(cfg.get("dj_role") or 0)
            raise UserError(f"You need the {role.mention if role else 'DJ'} role for that")

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
        if payload.player and payload.player.guild:
            self._skip_votes.pop(payload.player.guild.id, None)
        home = getattr(payload.player, "home", None)
        if home:
            try:
                await home.send(embed=self.track_embed(payload.track))
            except discord.HTTPException:
                pass

    @commands.Cog.listener()
    async def on_wavelink_track_exception(self, payload):
        log.warning("Lavalink track exception on %s: %s", getattr(payload.player, "guild", None), getattr(payload, "exception", None))
        player = getattr(payload, "player", None)
        track = getattr(payload, "track", None)
        home = getattr(player, "home", None) if player else None

        # Automatic fallback to SoundCloud if YouTube blocks playback
        if player and track and getattr(track, "title", None) and not getattr(track, "_fallback_tried", False):
            title = getattr(track, "title", "")
            author = getattr(track, "author", "").replace(" - Topic", "").strip()
            query = f"{title} {author}".strip()
            try:
                fb_tracks = await wavelink.Playable.search(query, source="scsearch", node=getattr(player, "node", None))
                if fb_tracks:
                    fb = fb_tracks[0]
                    fb.extras = getattr(track, "extras", None)
                    fb._fallback_tried = True
                    if home:
                        await home.send(embed=discord.Embed(
                            color=Colors.mint,
                            description=f"🔄 YouTube restricted playback for **{title}**. Automatically streaming from **SoundCloud**: [{fb.title}]({fb.uri})"
                        ))
                    return await player.play(fb)
            except Exception as e:
                log.debug("Auto fallback to SoundCloud failed: %r", e)

        if home:
            try:
                await home.send(embed=discord.Embed(
                    color=Colors.warn,
                    description="⚠️ Playback failed on this track (YouTube restricted on this node). Try playing by song title or using a SoundCloud link."
                ))
            except discord.HTTPException:
                pass

    @commands.Cog.listener()
    async def on_wavelink_inactive_player(self, player):
        await player.disconnect()

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before, after):
        """Leave a voice channel after it has been empty for ALONE_TIMEOUT seconds."""
        player = member.guild.voice_client
        if player is None or wavelink is None or not isinstance(player, wavelink.Player):
            return
        gid = member.guild.id
        if member.id == self.bot.user.id and after.channel is None:
            self._skip_votes.pop(gid, None)
            if t := self._alone_tasks.pop(gid, None):
                t.cancel()
            return
        if any(not m.bot for m in player.channel.members):
            if t := self._alone_tasks.pop(gid, None):
                t.cancel()
        elif gid not in self._alone_tasks:
            self._alone_tasks[gid] = asyncio.create_task(self._leave_if_alone(player, gid))

    async def _leave_if_alone(self, player, gid: int):
        try:
            await asyncio.sleep(ALONE_TIMEOUT)
            if player.channel and not any(not m.bot for m in player.channel.members):
                home = getattr(player, "home", None)
                await player.disconnect()
                if home:
                    try:
                        await home.send(embed=discord.Embed(color=Colors.main,
                                                            description="👋 Left the voice channel because it was empty"))
                    except discord.HTTPException:
                        pass
        except asyncio.CancelledError:
            pass
        finally:
            if self._alone_tasks.get(gid) is asyncio.current_task():
                self._alone_tasks.pop(gid, None)

    async def cog_unload(self):
        if self.node_watchdog.is_running():
            self.node_watchdog.cancel()
        for t in self._alone_tasks.values():
            t.cancel()
        if wavelink is not None:
            for node in list(wavelink.Pool.nodes.values()):
                try:
                    if hasattr(node, "_session") and node._session and not node._session.closed:
                        await node._session.close()
                    await node.close(eject=True)
                except Exception:
                    pass
            try:
                await wavelink.Pool.close()
            except Exception:
                pass

    # ------------------------------------------------------------ commands
    @commands.command(aliases=["p"])
    async def play(self, ctx: Context, *, query: str):
        """Play a song or playlist (URL or search)"""
        if not self.online_nodes():
            raise UserError("The music server is offline right now, try again in a minute")
        player = await self.player(ctx, connect=True)
        async with ctx.typing():
            tracks = await self.search(query.strip("<>"), prefer=getattr(player, "node", None))
        if not tracks:
            if not player.playing and player.queue.is_empty:
                await player.disconnect()
            raise UserError("No results — try another link or search")
        room = MAX_QUEUE - player.queue.count
        if room <= 0:
            raise UserError(f"The queue is full ({MAX_QUEUE} tracks)")
        extras = {"requester": str(ctx.author), "requester_id": ctx.author.id}
        if isinstance(tracks, wavelink.Playlist):
            added = 0
            for t in tracks.tracks[:room]:
                t.extras = extras
                await player.queue.put_wait(t)
                added += 1
            more = f" (queue limit reached, skipped {len(tracks.tracks) - added})" if added < len(tracks.tracks) else ""
            await ctx.approve(f"Queued **{added}** tracks from **{tracks.name}**{more}")
        else:
            track = tracks[0]
            track.extras = extras
            await player.queue.put_wait(track)
            if player.playing:
                await ctx.send(embed=self.track_embed(track, f"Queued (#{player.queue.count})"))
        if not player.playing and not player.paused:
            await player.play(player.queue.get(), volume=getattr(player, "_vol", 60))

    @commands.command(aliases=["next", "voteskip", "vs"])
    async def skip(self, ctx: Context):
        """Skip the current song (DJs and the requester skip instantly, others vote)"""
        player = await self.player(ctx)
        if not player.current:
            raise UserError("Nothing is playing")
        mine = getattr(player.current.extras, "requester_id", None) == ctx.author.id
        if not mine and not await self.is_dj(ctx, player):
            votes = self._skip_votes.setdefault(ctx.guild.id, set())
            votes.add(ctx.author.id)
            humans = {m.id for m in listeners(player)}
            votes &= humans
            needed = max(1, (len(humans) + 1) // 2)
            if len(votes) < needed:
                return await ctx.neutral(f"⏭️ Vote to skip: **{len(votes)}/{needed}**")
        self._skip_votes.pop(ctx.guild.id, None)
        await player.skip(force=True)
        await ctx.message.add_reaction("⏭️")

    @commands.command()
    async def pause(self, ctx: Context):
        """Pause"""
        player = await self.player(ctx)
        await self.require_dj(ctx, player)
        await player.pause(True)
        await ctx.message.add_reaction("⏸️")

    @commands.command()
    async def resume(self, ctx: Context):
        """Resume"""
        player = await self.player(ctx)
        await self.require_dj(ctx, player)
        await player.pause(False)
        await ctx.message.add_reaction("▶️")

    @commands.command(aliases=["dc", "disconnect"])
    async def stop(self, ctx: Context):
        """Stop and leave"""
        player = await self.player(ctx)
        await self.require_dj(ctx, player)
        await player.disconnect()
        await ctx.message.add_reaction("👋")

    @commands.command(aliases=["q"])
    async def queue(self, ctx: Context):
        """Show the queue"""
        player = await self.player(ctx, listen_only=True)
        lines = [f"[{shorten(t.title, 60)}]({t.uri}) `{fmt_ms(t.length)}`" for t in player.queue]
        head = f"**Now:** [{player.current.title}]({player.current.uri})\n\n" if player.current else ""
        pages = chunk_embeds(f"Queue ({player.queue.count})", lines) or [discord.Embed(color=Colors.main, title="Queue", description="Empty")]
        pages[0].description = head + (pages[0].description or "")
        await ctx.paginate(pages)

    @commands.command(aliases=["playing", "current"])
    async def song(self, ctx: Context):
        """What's playing"""
        player = await self.player(ctx, listen_only=True)
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
        await self.require_dj(ctx, player)
        volume = max(0, min(volume, 150))
        await player.set_volume(volume)
        player._vol = volume  # type: ignore[attr-defined]
        await ctx.approve(f"Volume **{volume}%**")

    @commands.command()
    async def loop(self, ctx: Context, mode: str = "track"):
        """Loop mode: track, queue or off"""
        player = await self.player(ctx)
        await self.require_dj(ctx, player)
        modes = {"track": wavelink.QueueMode.loop, "queue": wavelink.QueueMode.loop_all, "off": wavelink.QueueMode.normal}
        if mode not in modes:
            raise UserError("Use `track`, `queue` or `off`")
        player.queue.mode = modes[mode]
        await ctx.approve(f"Loop: **{mode}**")

    @commands.command()
    async def shuffle(self, ctx: Context):
        """Shuffle the queue"""
        player = await self.player(ctx)
        await self.require_dj(ctx, player)
        player.queue.shuffle()
        await ctx.message.add_reaction("🔀")

    @commands.command()
    async def remove(self, ctx: Context, position: int):
        """Remove a track from the queue"""
        player = await self.player(ctx)
        if not 1 <= position <= player.queue.count:
            raise UserError("Invalid position")
        track = player.queue[position - 1]
        await self.require_dj(ctx, player, requester_ok=track)
        player.queue.delete(position - 1)
        await ctx.approve(f"Removed **{track.title}**")

    @commands.command()
    async def clearqueue(self, ctx: Context):
        """Clear the queue"""
        player = await self.player(ctx)
        await self.require_dj(ctx, player)
        player.queue.clear()
        await ctx.approve("Cleared the queue")

    @commands.command()
    async def seek(self, ctx: Context, position: str):
        """Seek to a time: `seek 1:30`"""
        player = await self.player(ctx)
        await self.require_dj(ctx, player)
        if not player.current:
            raise UserError("Nothing is playing")
        secs = parse_time(position)
        if player.current.is_stream or secs * 1000 > player.current.length:
            raise UserError("You can't seek there")
        await player.seek(secs * 1000)
        await ctx.approve(f"Seeked to **{position}**")

    @commands.command()
    async def autoplay(self, ctx: Context, state: bool):
        """Keep playing recommended songs when the queue ends"""
        player = await self.player(ctx)
        await self.require_dj(ctx, player)
        player.autoplay = wavelink.AutoPlayMode.enabled if state else wavelink.AutoPlayMode.partial
        await ctx.approve(f"Autoplay **{'on' if state else 'off'}**")

    @commands.command(name="audiofilter", aliases=["preset", "fx"])
    async def audiofilter(self, ctx: Context, preset: str = None):
        """Audio presets: nightcore, vaporwave, bassboost, 8d, slowed, karaoke, clear"""
        player = await self.player(ctx)
        await self.require_dj(ctx, player)
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

    @commands.command()
    @has_perms(manage_guild=True)
    async def djrole(self, ctx: Context, *, role: discord.Role | None = None):
        """Set the DJ role (needed to pause/stop/volume/etc.), or no role to let everyone control music"""
        cfg = await self.bot.db.get_config(ctx.guild.id, "music")
        if role is None:
            cfg.pop("dj_role", None)
            await self.bot.db.set_config(ctx.guild.id, "music", cfg)
            return await ctx.approve("DJ role removed: everyone in the voice channel can control music")
        if role.is_default():
            raise UserError("Pick a real role, not @everyone")
        cfg["dj_role"] = role.id
        await self.bot.db.set_config(ctx.guild.id, "music", cfg)
        await ctx.approve(f"DJ role set to {role.mention}. Others can still `play`, vote to `skip`, "
                          "and remove their own tracks")

    @commands.command(aliases=["deaf", "undeafen"])
    async def deafen(self, ctx: Context):
        """Toggle whether the bot is deafened in the voice channel"""
        player = await self.player(ctx)
        is_deaf = getattr(ctx.guild.me.voice, "self_deaf", False) if ctx.guild.me.voice else False
        new_state = not is_deaf
        try:
            await ctx.guild.change_voice_state(channel=player.channel, self_deaf=new_state)
            await ctx.approve(f"Bot is now **{'deafened' if new_state else 'undeafened'}** in voice.")
        except Exception as e:
            await ctx.warn(f"Could not change deafen state: {e}")


async def setup(bot):
    await bot.add_cog(Music(bot))
