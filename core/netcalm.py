"""Riding out network hiccups on the host (DNS down, connection reset) without a long outage or a wall of text.

discord.py waits longer after every failed reconnect, up to about 17 minutes between attempts, so after a long
network blip the bot could stay offline for a quarter of an hour after the network was already back. Here the
wait is capped at about a minute. Each failed attempt is also logged as one line saying what went wrong, instead of
a full traceback every time; any other error keeps its traceback.
"""
from __future__ import annotations

import asyncio
import logging
import socket

import aiohttp
import discord
from discord import backoff as _backoff

MAX_EXP = 6  # at most 2**6 = 64 seconds between reconnect attempts
NETWORK_ERRORS = (OSError, asyncio.TimeoutError, aiohttp.ClientError, discord.GatewayNotFound)
LOGGERS = ("discord.shard", "discord.client", "discord.gateway")


class CappedBackoff(_backoff.ExponentialBackoff):
    """discord.py's reconnect backoff, never waiting more than 2**MAX_EXP seconds."""

    def __init__(self, base: int = 1, *, integral=False):
        super().__init__(base, integral=integral)
        self._max = min(self._max, MAX_EXP)


def _chain(exc: BaseException | None):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def describe(exc: BaseException) -> str:
    """One short line about a network failure."""
    for e in _chain(exc):
        if isinstance(e, socket.gaierror) or "name resolution" in str(e).lower():
            return "the host's DNS isn't answering, a network problem on the hosting side"
    for e in _chain(exc):
        if isinstance(e, asyncio.TimeoutError):
            return "the connection timed out"
    text = str(exc).strip() or type(exc).__name__
    return f"{type(exc).__name__}: {text[:160]}"


class QuietNetworkErrors(logging.Filter):
    """Turn a reconnect-failure traceback into a single warning line. Anything that isn't a network error passes
    through untouched."""

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        if exc is not None and isinstance(exc, NETWORK_ERRORS):
            record.msg = f"{record.getMessage()} ({describe(exc)})"
            record.args = None
            record.exc_info = record.exc_text = None
            record.levelno, record.levelname = logging.WARNING, "WARNING"
        return True


def install() -> None:
    import discord.client
    import discord.shard
    discord.shard.ExponentialBackoff = CappedBackoff
    discord.client.ExponentialBackoff = CappedBackoff
    quiet = QuietNetworkErrors()
    for name in LOGGERS:
        logger = logging.getLogger(name)
        if not any(isinstance(f, QuietNetworkErrors) for f in logger.filters):
            logger.addFilter(quiet)
