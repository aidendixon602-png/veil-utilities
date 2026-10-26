"""Private developer channels the bot sets up for itself, so no channel ID ever has to be pasted into .env.

* ``errors``: a traceback for every command that crashed.
* ``auth``: alerts about servers that tried to add the bot (with the Authorize button).

ERROR_LOG_CHANNEL / AUTH_LOG_CHANNEL still win when they are set. Otherwise, once the bot is online it picks a home
server (one a developer owns or is in), makes a channel for each in it that only the bot and the developers can see,
remembers the IDs, and makes a channel again if one is deleted or the bot moves to another home server.
"""
from __future__ import annotations

import logging

import discord

from core.config import settings

log = logging.getLogger("veil.devchannels")

CHANNELS: dict[str, tuple[str, str]] = {
    "errors": ("veil-errors", "Tracebacks for commands that crashed"),
    "auth": ("veil-auth", "Servers that tried to add the bot"),
}


def _override(key: str) -> int | None:
    return {"errors": settings.error_log_channel, "auth": settings.auth_log_channel}.get(key)


def channel_for(bot, key: str) -> discord.abc.Messageable | None:
    """The channel to post in: the one from .env if set, otherwise the one the bot made."""
    channel_id = _override(key) or getattr(bot, "dev_channels", {}).get(key)
    return bot.get_channel(channel_id) if channel_id else None


def _developers(bot) -> frozenset[int]:
    return frozenset(getattr(bot, "owner_ids", None) or ())


def _can_set_up(guild: discord.Guild) -> bool:
    me = guild.me
    perms = me.guild_permissions if me else None
    return bool(perms and (perms.administrator or (perms.manage_channels and perms.manage_roles)))


def pick_home_guild(bot) -> discord.Guild | None:
    """A server where a developer is, preferring one a developer owns, then an authorized one, then the oldest."""
    devs = _developers(bot)
    candidates = [g for g in bot.guilds
                  if _can_set_up(g) and (g.owner_id in devs or any(g.get_member(d) for d in devs))]
    candidates.sort(key=lambda g: (g.owner_id not in devs, not bot.is_authorized(g.id), g.id))
    return candidates[0] if candidates else None


async def _create(bot, guild: discord.Guild, key: str) -> discord.TextChannel | None:
    name, topic = CHANNELS[key]
    overwrites: dict = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True,
                                              read_message_history=True),
    }
    for uid in _developers(bot):
        member = guild.get_member(uid)
        if member:
            overwrites[member] = discord.PermissionOverwrite(view_channel=True, read_message_history=True)
    try:
        return await guild.create_text_channel(name, overwrites=overwrites, topic=topic,
                                               reason="veil utilities: private developer channel")
    except discord.HTTPException as e:
        log.warning("Couldn't create #%s in %s (%s)", name, guild.name, e)
        return None


async def ensure(bot) -> dict[str, int]:
    """Make whichever developer channels are missing and remember them. Safe to call on every start."""
    stored = dict(getattr(bot, "dev_channels", {}) or {})
    missing = [k for k in CHANNELS if not _override(k) and not (stored.get(k) and bot.get_channel(stored[k]))]
    if not missing:
        return stored

    home = bot.get_guild(stored["guild"]) if stored.get("guild") else None
    if home is None or not _can_set_up(home):
        home = pick_home_guild(bot)
    if home is None:
        log.info("Developer channels: no server with a developer in it where I can manage channels yet")
        return stored
    if stored.get("guild") != home.id:  # a new home: the old IDs point somewhere else
        stored = {"guild": home.id}

    for key in missing:
        channel = await _create(bot, home, key)
        if channel:
            stored[key] = channel.id
            log.info("Developer channels: created #%s in %s", channel.name, home.name)
    bot.dev_channels = stored
    g = await bot.db.get_config(0, "global")
    g["dev_channels"] = stored
    await bot.db.set_config(0, "global", g)
    return stored
