"""Safe HTTP for user-supplied URLs.

Anything a user can point the bot at (image links, RSS feeds, embeds in replied messages) goes through
``bot.public_session``. It refuses to connect to private, loopback, link-local, multicast or reserved
addresses, so nobody can make the bot request ``http://127.0.0.1:2333`` (Lavalink), the host's control
panel, or cloud metadata endpoints such as ``http://169.254.169.254``.

Two layers:
* ``PublicResolver`` drops non-public addresses from every DNS answer. This runs on each connection,
  including after redirects, so DNS-rebinding tricks don't work.
* A trace hook rejects URLs whose host is a literal IP (aiohttp skips the resolver for those) and
  non-http(s) schemes, including on every redirect hop.
"""
from __future__ import annotations

import ipaddress
import socket

import aiohttp
from aiohttp.abc import AbstractResolver
from yarl import URL

from core.utils import UserError


class BlockedURL(UserError):
    """Raised when a URL points somewhere the bot must not connect to."""


def is_public_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def check_url(url: str | URL) -> URL:
    """Validate scheme and literal-IP hosts. Hostnames are checked at DNS time by PublicResolver."""
    u = url if isinstance(url, URL) else URL(str(url))
    if u.scheme not in ("http", "https"):
        raise BlockedURL("Only http(s) links are allowed")
    host = (u.host or "").strip("[]").rstrip(".").lower()
    if not host:
        raise BlockedURL("That link has no host")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise BlockedURL("That link points to a private address")
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return u  # a hostname
    if not is_public_ip(host):
        raise BlockedURL("That link points to a private address")
    return u


class PublicResolver(AbstractResolver):
    """Wrap another resolver and keep only publicly routable addresses."""

    def __init__(self, inner: AbstractResolver | None = None):
        self._inner = inner or aiohttp.ThreadedResolver()

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET):
        infos = await self._inner.resolve(host, port, family)
        safe = [i for i in infos if is_public_ip(i["host"])]
        if not safe:
            raise OSError(f"{host} does not resolve to a public address")
        return safe

    async def close(self) -> None:
        await self._inner.close()


async def _on_request_start(session, ctx, params):
    check_url(params.url)


async def _on_redirect(session, ctx, params):
    location = params.response.headers.get("Location")
    if location:
        check_url(params.url.join(URL(location)))


def make_public_session(**kwargs) -> aiohttp.ClientSession:
    trace = aiohttp.TraceConfig()
    trace.on_request_start.append(_on_request_start)
    trace.on_request_redirect.append(_on_redirect)
    connector = aiohttp.TCPConnector(resolver=PublicResolver(), limit=20, ttl_dns_cache=60)
    kwargs.setdefault("timeout", aiohttp.ClientTimeout(total=20))
    return aiohttp.ClientSession(connector=connector, trace_configs=[trace], **kwargs)
