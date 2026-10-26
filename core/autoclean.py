"""Keep channels clean: once a command has run, delete the message that ran it, and the bot's replies a little later.

* The command message goes as soon as the command is done (not before, so commands can still read attachments
  and replies).
* Plain replies go after the server's delay (15 seconds by default).
* A menu (a reply with buttons that time out, like `,help` or `,an`) goes when the menu closes.
* Things that are the point of the command stay: messages with lasting buttons (giveaways, button roles, ticket
  panels) and what `poll` / `createembed` post.

Servers can turn it off or change the delay with `,autoclean`. Deleted command messages are remembered for a few
minutes so the message log, snipe and ghost-ping detection don't treat them as someone deleting a message.
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict

import discord

DEFAULT_DELAY = 15
KEEP_COMMANDS = {"poll", "createembed"}  # their reply is the thing the person asked for
_CLEANED: OrderedDict[int, float] = OrderedDict()  # message id -> when we deleted it
_REMEMBER_FOR = 300


def was_cleaned(message_id: int) -> bool:
    """True if autoclean deleted this message (so it isn't a user deleting something)."""
    now = time.monotonic()
    while _CLEANED and now - next(iter(_CLEANED.values())) > _REMEMBER_FOR:
        _CLEANED.popitem(last=False)
    return message_id in _CLEANED


def remember(message_id: int) -> None:
    """Note that the bot is about to delete this message as clean-up (not a user deleting it)."""
    _mark(message_id)


def _mark(message_id: int) -> None:
    _CLEANED[message_id] = time.monotonic()
    while len(_CLEANED) > 5000:
        _CLEANED.popitem(last=False)


def track(ctx, message, view) -> None:
    """Remember a reply sent for this command (called by Context.send)."""
    if message is None:
        return
    replies = getattr(ctx, "_autoclean_replies", None)
    if replies is None:
        replies = []
        try:
            ctx._autoclean_replies = replies
        except AttributeError:
            return
    replies.append((message, view))


async def settings_for(bot, guild) -> tuple[bool, int]:
    if guild is None or getattr(bot, "db", None) is None:
        return False, DEFAULT_DELAY
    cfg = await bot.db.get_config(guild.id, "settings")
    clean = cfg.get("autoclean") or {}
    return bool(clean.get("on", True)), int(clean.get("delay", DEFAULT_DELAY))


def _keeps(view) -> bool:
    """A reply whose buttons never expire is content (giveaway, button roles, a panel): it stays."""
    return view is not None and getattr(view, "timeout", 0) is None


async def _delete(message) -> None:
    try:
        await message.delete()
    except (discord.HTTPException, AttributeError):
        pass


async def _delete_later(message, view, delay: int) -> None:
    if view is not None and not view.is_finished():
        await view.wait()  # a menu: gone when it closes (a menu may already have deleted itself)
    else:
        await asyncio.sleep(delay)
    await _delete(message)


async def after_command(ctx, *, ran: bool) -> None:
    """Called once a command has finished (`ran`) or failed. Cleans up if the server wants it."""
    if getattr(ctx, "_autoclean_done", False) or ctx.guild is None or ctx.command is None:
        return
    ctx._autoclean_done = True
    replies = getattr(ctx, "_autoclean_replies", [])
    if not ran and not replies:
        return  # nothing happened that the person saw (ignored, silent checks): leave their message alone
    on, delay = await settings_for(ctx.bot, ctx.guild)
    if not on:
        return
    message = getattr(ctx, "message", None)
    if message is not None and getattr(message, "id", None):
        _mark(message.id)
        await _delete(message)
    if (ctx.command.root_parent or ctx.command).qualified_name in KEEP_COMMANDS:
        return
    for reply, view in replies:
        if _keeps(view):
            continue
        asyncio.create_task(_delete_later(reply, view, delay))
