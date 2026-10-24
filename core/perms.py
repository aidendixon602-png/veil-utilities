"""Per-user permission levels.

A level has a display name, a numeric rank (higher = more power) and a set of bot-permissions it grants.
Assigning a user a level lets them pass the bot's permission checks for those permissions — without needing
the real (dangerous) Discord permissions. Config lives per-guild under the "permlevels" module:

    {"users": {"<user id>": "<level key>"}, "overrides": {"<key>": {"name", "rank", "perms"}}}

Server Owner and Bot Dev are not assignable — they come from being the guild owner / a bot developer.
"""
from __future__ import annotations

import discord

DEV_RANK = 10_000
OWNER_RANK = 1_000
ADMIN_RANK = 900  # a real Discord administrator with no assigned level

# key -> (display name, rank, granted bot-permissions). Assignable with `uperm add`.
PRESETS: dict[str, tuple[str, int, list[str]]] = {
    "helper":        ("Helper", 1, ["manage_messages"]),
    "mod":           ("Mod", 2, ["manage_messages", "kick_members", "moderate_members", "manage_nicknames"]),
    "admin":         ("Admin", 3, ["manage_messages", "kick_members", "ban_members", "moderate_members",
                                    "manage_nicknames", "manage_roles", "manage_channels"]),
    "headadmin":     ("Head Admin", 4, ["manage_messages", "kick_members", "ban_members", "moderate_members",
                                        "manage_nicknames", "manage_roles", "manage_channels", "manage_guild",
                                        "manage_webhooks", "manage_expressions", "mention_everyone"]),
    "botmanager":    ("Bot Manager", 5, ["administrator"]),
    "overseer":      ("Overseer", 6, ["administrator"]),
    "supremeleader": ("Supreme Leader", 7, ["administrator"]),
}

# shown in the level list but handed out automatically, never via `uperm add`
RESERVED: dict[str, tuple[str, int]] = {
    "serverowner": ("Server Owner", OWNER_RANK),
    "botdev":      ("Bot Dev", DEV_RANK),
}


def all_levels(cfg: dict) -> dict[str, dict]:
    """Assignable levels merged with any per-guild overrides."""
    merged = {k: {"name": n, "rank": r, "perms": list(p)} for k, (n, r, p) in PRESETS.items()}
    for key, data in (cfg.get("overrides") or {}).items():
        base = merged.get(key, {"name": key.replace("_", " ").title(), "rank": 0, "perms": []})
        merged[key] = {
            "name": data.get("name", base["name"]),
            "rank": data.get("rank", base["rank"]),
            "perms": data.get("perms", base["perms"]),
        }
    return merged


def _flat(text: str) -> str:
    return text.lower().replace(" ", "").replace("_", "").replace("-", "")


def resolve_key(cfg: dict, text: str) -> str | None:
    """Accept an assignable level's key or display name, in any spacing/case."""
    flat = _flat(text)
    for key, data in all_levels(cfg).items():
        if flat == key or flat == _flat(data["name"]):
            return key
    return None


def is_reserved(text: str) -> str | None:
    flat = _flat(text)
    for key, (name, _) in RESERVED.items():
        if flat == key or flat == _flat(name):
            return name
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
        return "Bot Dev"
    if is_owner:
        return "Server Owner"
    key = user_key(cfg, member.id)
    lv = all_levels(cfg).get(key) if key else None
    if lv:
        return lv["name"]
    if getattr(member, "guild_permissions", None) and member.guild_permissions.administrator:
        return "Server Admin"
    return None
