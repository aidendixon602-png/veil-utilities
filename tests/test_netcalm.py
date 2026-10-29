"""Host network blips: reconnects never wait more than about a minute, and each failure is one log line."""
import logging
import socket

import aiohttp

from core import netcalm


def test_reconnect_waits_are_capped():
    b = netcalm.CappedBackoff()
    waits = [b.delay() for _ in range(50)]
    assert max(waits) <= 2 ** netcalm.MAX_EXP


def _record(exc):
    return logging.LogRecord("discord.shard", logging.ERROR, __file__, 1,
                             "Attempting a reconnect for shard ID %s in %.2fs", (0, 845.55),
                             (type(exc), exc, None))


def _dns_error():
    try:
        try:
            raise socket.gaierror(-3, "Temporary failure in name resolution")
        except socket.gaierror as inner:
            raise aiohttp.ClientConnectionError("Cannot connect to host gateway.discord.gg:443") from inner
    except aiohttp.ClientConnectionError as e:
        return e


def test_a_dns_failure_is_one_line():
    rec = _record(_dns_error())
    assert netcalm.QuietNetworkErrors().filter(rec)
    assert rec.exc_info is None and rec.levelno == logging.WARNING
    assert rec.getMessage() == ("Attempting a reconnect for shard ID 0 in 845.55s "
                                "(the host's DNS isn't answering, a network problem on the hosting side)")


def test_other_errors_keep_their_traceback():
    rec = _record(ValueError("a real bug"))
    netcalm.QuietNetworkErrors().filter(rec)
    assert rec.exc_info is not None and rec.levelno == logging.ERROR


def test_install_patches_discord_and_is_idempotent():
    import discord.shard
    netcalm.install()
    netcalm.install()
    assert discord.shard.ExponentialBackoff is netcalm.CappedBackoff
    assert sum(isinstance(f, netcalm.QuietNetworkErrors) for f in logging.getLogger("discord.shard").filters) == 1
