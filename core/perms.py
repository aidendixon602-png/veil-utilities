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

# =====================================================================
# CODE-DEFINED PERMISSIONS (Editable directly in this file)
# =====================================================================
# You can assign permission levels directly here in the bot code!
# The bot merges these with any levels assigned via Discord commands.
#
# CODE_USERS: Map user IDs (int or str) to level keys (e.g. "supremeleader", "admin", "mod", "helper")
# Example:
#   CODE_USERS = {
#       123456789012345678: "supremeleader",
#   }
CODE_USERS: dict[int | str, str] = {}

# CODE_ROLES: Map role IDs (int or str) or exact role names (str) to level keys
# Example:
#   CODE_ROLES = {
#       "Admin Role": "admin",
#       "Moderator": "mod",
#       123456789012345678: "helper",
#   }
CODE_ROLES: dict[int | str, str] = {}

# key -> (display name, rank, granted bot-permissions). Assignable with `perm add`.
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

# shown in the level list but handed out automatically, never via `perm add`
RESERVED: dict[str, tuple[str, int]] = {
    "serverowner": ("Server Owner", OWNER_RANK),
    "botdev":      ("Bot Developer", DEV_RANK),
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


def role_key(cfg: dict, role_id: int) -> str | None:
    return (cfg.get("roles") or {}).get(str(role_id))


def member_level_info(cfg: dict, member: discord.Member, *, is_owner: bool = False, is_dev: bool = False) -> tuple[str | None, str | None, int]:
    """Resolve a member's effective permission level, source, and numeric rank.

    Returns:
        (level_name, granted_by, rank)
        e.g. ("Supreme Leader", "Permissions attached to user", 7)
             ("Admin", "@HR | Branch Management", 3)
             ("Bot Developer", "Built-in", 10000)
    """
    if is_dev:
        return "Bot Developer", "Built-in", DEV_RANK
    if is_owner:
        return "Server Owner", "Server Owner", OWNER_RANK

    levels = all_levels(cfg)

    # 1. User-specific assignment: Check CODE_USERS first, then cfg["users"]
    mid = getattr(member, "id", 0)
    ukey = CODE_USERS.get(mid) or CODE_USERS.get(str(mid)) or user_key(cfg, mid)
    if ukey and ukey in levels:
        info = levels[ukey]
        return info["name"], "Permissions attached to user", info["rank"]

    # 2. Check roles attached to member: Check CODE_ROLES and cfg["roles"]
    roles_cfg = cfg.get("roles") or {}
    best_info = None
    best_role = None
    best_rank = 0
    for r in getattr(member, "roles", []):
        r_name = getattr(r, "name", "")
        r_id = getattr(r, "id", 0)
        rkey = (
            CODE_ROLES.get(r_id)
            or CODE_ROLES.get(str(r_id))
            or CODE_ROLES.get(r_name)
            or CODE_ROLES.get(r_name.lower())
            or roles_cfg.get(str(r_id))
        )
        if rkey and rkey in levels:
            r_info = levels[rkey]
            if r_info["rank"] > best_rank:
                best_rank = r_info["rank"]
                best_info = r_info
                best_role = r

    if best_info and best_role:
        return best_info["name"], best_role.mention, best_rank

    # 3. Real Discord administrator fallback
    if getattr(member, "guild_permissions", None) and member.guild_permissions.administrator:
        admin_roles = [r for r in getattr(member, "roles", []) if r.permissions.administrator and not r.is_default()]
        source = admin_roles[-1].mention if admin_roles else "Administrator permission"
        return "Admin", source, ADMIN_RANK

    return None, None, 0


def level_perms(cfg: dict, member_or_id: discord.Member | int) -> set[str]:
    levels = all_levels(cfg)
    if isinstance(member_or_id, int):
        key = CODE_USERS.get(member_or_id) or CODE_USERS.get(str(member_or_id)) or user_key(cfg, member_or_id)
        lv = levels.get(key) if key else None
        return set(lv["perms"]) if lv else set()

    # discord.Member: check both user and role assignments
    name, source, rank = member_level_info(cfg, member_or_id)
    if not name:
        return set()
    for lv in levels.values():
        if lv["name"] == name:
            return set(lv["perms"])
    return set()


def rank_of(cfg: dict, member: discord.Member, *, is_owner: bool = False, is_dev: bool = False,
            has_admin: bool = False) -> int:
    name, source, rank = member_level_info(cfg, member, is_owner=is_owner, is_dev=is_dev)
    if rank > 0:
        return rank
    if has_admin:
        return ADMIN_RANK
    return 0


def label(cfg: dict, member: discord.Member, *, is_owner: bool = False, is_dev: bool = False) -> str | None:
    """A short title for the response footer, or None to omit."""
    name, source, rank = member_level_info(cfg, member, is_owner=is_owner, is_dev=is_dev)
    return name
