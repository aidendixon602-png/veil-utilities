"""Per-user permission levels.

A level has a display name, a numeric rank (higher = more power) and a set of bot-permissions it grants.
Assigning a user a level lets them pass the bot's permission checks for those permissions — without needing
the real (dangerous) Discord permissions. Config lives per-guild under the "permlevels" module:

    {"users": {"<user id>": "<level key>"}, "overrides": {"<key>": {"name", "rank", "perms"}}}
"""
from __future__ import annotations

import discord

# key -> (display name, rank, granted bot-permissions)
PRESETS: dict[str, tuple[str, int, list[str]]] = {
    "helper":        ("Helper", 1, ["manage_messages"]),
    "moderator":     ("Moderator", 2, ["manage_messages", "kick_members", "moderate_members", "manage_nicknames"]),
    "botmanager":    ("Bot Manager", 3, ["manage_messages", "moderate_members", "manage_guild"]),
    "admin":         ("Administrator", 4, ["manage_messages", "kick_members", "ban_members", "moderate_members",
                                           "manage_nicknames", "manage_roles", "manage_channels", "manage_guild"]),
    "overseer":      ("Overseer", 5, ["administrator"]),
    "supremeleader": ("Supreme Leader", 6, ["administrator"]),
}

DEV_RANK = 10_000
OWNER_RANK = 1_000
ADMIN_RANK = 900  # a real Discord administrator outranks every bot level


def all_levels(cfg: dict) -> dict[str, dict]:
    """Merge presets with any per-guild overrides."""
    merged = {k: {"name": n, "rank": r, "perms": list(p)} for k, (n, r, p) in PRESETS.items()}
    for key, data in (cfg.get("overrides") or {}).items():
        base = merged.get(key, {"name": key.replace("_", " ").title(), "rank": 0, "perms": []})
        merged[key] = {
            "name": data.get("name", base["name"]),
            "rank": data.get("rank", base["rank"]),
            "perms": data.get("perms", base["perms"]),
        }
    return merged


def resolve_key(cfg: dict, text: str) -> str | None:
    """Accept a level key or display name, in any spacing/case."""
    flat = text.lower().replace(" ", "").replace("_", "").replace("-", "")
    for key, data in all_levels(cfg).items():
        if flat == key or flat == data["name"].lower().replace(" ", ""):
            return key
    return None


def user_key(cfg: dict, user_id: int) -> str | None:
    return (cfg.get("users") or {}).get(str(user_id))


def level_perms(cfg: dict, user_id: int) -> set[str]:
    key = user_key(cfg, user_id)
    lv = all_levels(cfg).get(key) if key else None
    return set(lv["perms"]) if lv else set()


def rank_of(cfg: dict, member: discord.Member, *, is_owner: bool = False, is_dev: bool = False,
            has_admin: bool = False) -> int:
    if is_dev:
        return DEV_RANK
    if is_owner:
        return OWNER_RANK
    if has_admin:
        return ADMIN_RANK
    key = user_key(cfg, member.id)
    lv = all_levels(cfg).get(key) if key else None
    return lv["rank"] if lv else 0


def label(cfg: dict, member: discord.Member, *, is_owner: bool = False, is_dev: bool = False) -> str | None:
    """A short title for the response footer, or None to omit."""
    if is_dev:
        return "Developer"
    key = user_key(cfg, member.id)
    lv = all_levels(cfg).get(key) if key else None
    if lv:
        return lv["name"]
    if is_owner:
        return "Server Owner"
    if getattr(member, "guild_permissions", None) and member.guild_permissions.administrator:
        return "Administrator"
    return None
