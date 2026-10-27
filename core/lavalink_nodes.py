"""Lavalink nodes for music.

Without LAVALINK_URI the bot uses the free public Lavalink v4 nodes below, so music works with zero setup. Public
nodes are run by volunteers: they can be slow, full or go offline, so the bot connects to all of them and uses
whichever is healthy (failover). For reliable music, host your own Lavalink (see docs/lavalink/ and docs/HOSTING.md) and set:

    LAVALINK_URI=http://localhost:2333
    LAVALINK_PASSWORD=youshallnotpass

Several of your own nodes: comma-separate both values (one password is reused for every node if only one is
given). Your nodes come first and the public ones stay connected behind them as backups, so music keeps working
when yours is down; LAVALINK_BACKUPS=false uses only yours. Set MUSIC_ENABLED=false to turn music off completely.

Public Lavalink v4 nodes with SSL, from the community list at lavalink.darrennathanael.com (checked 2026-10-27).
Discord's DAVE voice encryption needs Lavalink 4.2+, so nodes listed on an older version are left out.
"""
from __future__ import annotations

PUBLIC_NODES: tuple[tuple[str, str], ...] = (
    ("https://lavalink.nazha.online:443", "nazhafreelava"),
    ("https://lava-v4.millohost.my.id:443", "https://discord.gg/mjS5J2K3ep"),
    ("https://lava4.millohost.my.id:443", "https://discord.gg/mjS5J2K3ep"),
    ("https://lavalinkv4.serenetia.com:443", "https://seretia.link/discord"),
    ("https://lavalink-v4.triniumhost.com:443", "free"),
)


def resolve_nodes(uri: str | None, password: str | None, enabled: bool = True, *,
                  backups: bool = False) -> tuple[list[tuple[str, str]], bool]:
    """Return ([(uri, password), ...], using_public_nodes). With `backups`, your own nodes come first and the public
    ones follow as fallbacks (using_public_nodes stays False: the first nodes are yours)."""
    nodes, public = _own_nodes(uri, password, enabled)
    if backups and nodes and not public:
        mine = {u.lower() for u, _ in nodes}
        nodes += [n for n in PUBLIC_NODES if n[0].lower() not in mine]
    return nodes, public


def _own_nodes(uri: str | None, password: str | None, enabled: bool) -> tuple[list[tuple[str, str]], bool]:
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
