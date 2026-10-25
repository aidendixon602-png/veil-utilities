"""Lavalink nodes for music.

Without LAVALINK_URI the bot uses the free public Lavalink v4 nodes below, so music works with zero setup. Public
nodes are run by volunteers: they can be slow, full or go offline, so the bot connects to all of them and uses
whichever is healthy (failover). For reliable music, host your own Lavalink (see lavalink/ and HOSTING.md) and set:

    LAVALINK_URI=http://localhost:2333
    LAVALINK_PASSWORD=youshallnotpass

Several of your own nodes: comma-separate both values (one password is reused for every node if only one is
given). Set MUSIC_ENABLED=false to turn music off completely.

All public nodes below were checked on 2026-10-24 (Lavalink 4.2+, which supports Discord's DAVE voice encryption).
"""
from __future__ import annotations

PUBLIC_NODES: tuple[tuple[str, str], ...] = (
    ("https://lavalink.nazha.online:443", "nazhafreelava"),
    ("https://lava-v4.millohost.my.id:443", "https://discord.gg/mjS5J2K3ep"),
    ("https://lava4.millohost.my.id:443", "https://discord.gg/mjS5J2K3ep"),
)


def resolve_nodes(uri: str | None, password: str | None, enabled: bool = True) -> tuple[list[tuple[str, str]], bool]:
    """Return ([(uri, password), ...], using_public_nodes)."""
    if not enabled:
        return [], False
    if not uri:
        return list(PUBLIC_NODES), True
    uris = [u.strip().rstrip("/") for u in uri.split(",") if u.strip()]
    password = password or "youshallnotpass"
    if len(uris) == 1:  # a single node's password may itself contain commas
        return [(uris[0], password)], False
    passwords = [p.strip() for p in password.split(",")]
    if len(passwords) != len(uris):
        passwords = [passwords[0]] * len(uris)
    return list(zip(uris, passwords)), False
