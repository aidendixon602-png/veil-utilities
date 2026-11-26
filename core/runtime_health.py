"""Bounded, read-only runtime checks shared by operator surfaces."""
from __future__ import annotations

import asyncio


async def runtime_health(bot) -> dict[str, str]:
    """Return safe health labels without leaking node URLs, credentials, or config contents."""
    health = {
        "database": "unavailable",
        "music": "disabled",
        "security": "status unavailable",
        "dashboard": "disabled",
    }

    db = getattr(bot, "db", None)
    fetchone = getattr(db, "fetchone", None)
    if callable(fetchone):
        try:
            await asyncio.wait_for(fetchone("SELECT 1"), timeout=2.0)
            health["database"] = "online"
        except Exception:
            health["database"] = "degraded"

    get_cog = getattr(bot, "get_cog", None)
    if callable(get_cog):
        antinuke = "loaded" if get_cog("Antinuke") is not None else "not loaded"
        auth_gate = "on" if getattr(bot, "auth_required", None) is True else (
            "off" if getattr(bot, "auth_required", None) is False else "unknown")
        health["security"] = f"auth gate {auth_gate} · antinuke {antinuke}"
        music = get_cog("Music")
        if music is not None:
            try:
                nodes = list(getattr(music, "nodes", ()) or ())
                online = list(music.online_nodes())
                health["music"] = f"{len(online)}/{len(nodes)} nodes online" if nodes else "no nodes configured"
            except Exception:
                health["music"] = "status unavailable"

    dashboard = getattr(bot, "dashboard", None)
    if dashboard is not None:
        health["dashboard"] = "running" if getattr(dashboard, "runner", None) else "configured"
    return health
