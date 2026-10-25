"""Per-user and per-role permission levels.

A level has a display name, a numeric rank (higher = more power) and a set of bot-permissions it grants.
Assigning a user or role a level lets them pass the bot's permission checks for those permissions — without
needing dangerous native Discord permissions. Config lives per-guild under the "permlevels" module:

    {"users": {"<user id>": "<level key>"}, "roles": {"<role id>": "<level key>"}, "overrides": {...}}

Server Owner and Bot Developer are automatic built-in ranks that cannot be manually assigned.
"""
from __future__ import annotations

import discord

DEV_RANK = 10_000
OWNER_RANK = 1_000
ADMIN_RANK = 900  # a real Discord administrator with no assigned level

# =====================================================================
# BOT DEVELOPERS (Editable directly in this file)
# =====================================================================
# User IDs listed here receive the Bot Developer permission level and
# are logged with "Permission: Bot Developer" and "Granted By: Built-in".
BOT_DEVELOPERS: list[int] = []


def is_bot_developer(user_or_id: int | discord.abc.User | discord.Member | None, bot: discord.Client | None = None) -> bool:
    """Check if a user is a recognized bot developer.

    Devs are automatically authorized for everything across the bot, including:
    - Built-in Bot Developer permission rank (rank 10,000)
    - Role Blacklist (BLED) immunity and authorization to hold/assign blacklisted roles
    - Immunity to quarantine, jail, mute, kick, ban, and moderation hierarchy
    - Antinuke, Antiraid, Antialt, and Automod whitelist bypass
    - Global bot blacklist bypass
    """
    if user_or_id is None:
        return False
    uid = getattr(user_or_id, "id", user_or_id)
    if not isinstance(uid, int):
        try:
            uid = int(uid)
        except (ValueError, TypeError):
            return False
    if uid in BOT_DEVELOPERS:
        return True
    try:
        from core.config import settings
        if settings and settings.owner_ids and uid in settings.owner_ids:
            return True
    except Exception:
        pass
    if bot is not None:
        if getattr(bot, "owner_ids", None) and uid in bot.owner_ids:
            return True
        if getattr(bot, "owner_id", None) and uid == bot.owner_id:
            return True
    return False

# =====================================================================
# ALL BOT PERMISSIONS
# =====================================================================
# Comprehensive list of every bot permission recognized by Discord and the bot:
ALL_BOT_PERMS: list[str] = [
    "administrator",
    "view_audit_log",
    "view_channel",
    "manage_guild",
    "manage_roles",
    "manage_channels",
    "manage_messages",
    "manage_nicknames",
    "manage_expressions",
    "manage_webhooks",
    "manage_events",
    "manage_threads",
    "kick_members",
    "ban_members",
    "moderate_members",
    "mention_everyone",
    "create_instant_invite",
    "change_nickname",
    "send_messages",
    "send_messages_in_threads",
    "create_public_threads",
    "create_private_threads",
    "embed_links",
    "attach_files",
    "read_message_history",
    "add_reactions",
    "use_external_emojis",
    "use_external_stickers",
    "connect",
    "speak",
    "stream",
    "mute_members",
    "deafen_members",
    "move_members",
    "use_voice_activation",
    "priority_speaker",
    "request_to_speak",
]

# =====================================================================
# CODE-DEFINED PERMISSIONS (Editable directly in this file)
# =====================================================================
# Permissions assigned here are built through the bot code and logged
# with "Granted By: Built-in".
#
# CODE_USERS: Map user IDs (int or str) to level keys
CODE_USERS: dict[int | str, str] = {
    1277709272041394187: "supremeleader",
}

# CODE_ROLES: Map role IDs (int or str) or exact role names (str) to level keys
CODE_ROLES: dict[int | str, str] = {
    "HR | Branch Management": "admin",
    "Office of Lilz": "helper",
}

# =====================================================================
# BOT PERMISSION RANKS
# =====================================================================
# key -> (display name, rank, granted bot-permissions). Assignable with `perm add` / `uperm add`.
PRESETS: dict[str, tuple[str, int, list[str]]] = {
    "helper":        ("Helper", 1, ["manage_messages"]),
    "mod":           ("Mod", 2, ["manage_messages", "kick_members", "moderate_members", "manage_nicknames"]),
    "admin":         ("Admin", 3, ["manage_messages", "kick_members", "ban_members", "moderate_members",
                                    "manage_nicknames", "manage_roles", "manage_channels"]),
    "headadmin":     ("Head Admin", 4, ["manage_messages", "kick_members", "ban_members", "moderate_members",
                                        "manage_nicknames", "manage_roles", "manage_channels", "manage_guild",
                                        "manage_webhooks", "manage_expressions", "mention_everyone"]),
    "botmanager":    ("Bot Manager", 5, ["administrator"] + ALL_BOT_PERMS),
    "overseer":      ("Overseer", 6, ["administrator"] + ALL_BOT_PERMS),
    "supremeleader": ("Supreme Leader", 7, ["administrator"] + ALL_BOT_PERMS),
}

# Shown in the level list but handed out automatically, never via `perm add` / `uperm add`
RESERVED: dict[str, tuple[str, int]] = {
    "serverowner": ("Server Owner", OWNER_RANK),
    "botdev":      ("Bot Developer", DEV_RANK),
}

# Permission hierarchy display order (lowest to highest)
HIERARCHY_ORDER: list[str] = [
    "Bot Developer",
    "Server Owner",
    "Supreme Leader",
    "Bot Manager",
    "Overseer",
    "Head Admin",
    "Admin",
    "Mod",
    "Helper",
]


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

    Built-in permissions (bot developers, server owners, or bot code assignments) are logged
    with "Granted By: Built-in".
    uperm permissions assigned via roles in Discord are logged with "Granted By: @Role".
    uperm permissions assigned directly to a user in Discord are logged with "Permissions attached to user".

    Returns:
        (level_name, granted_by, rank)
        e.g. ("Supreme Leader", "Permissions attached to user", 7)
             ("Admin", "@HR | Branch Management", 3)
             ("Helper", "@Office of Lilz", 1)
             ("Bot Developer", "Built-in", 10000)
             ("Server Owner", "Built-in", 1000)
    """
    mid = getattr(member, "id", 0)
    if is_dev or is_bot_developer(member):
        return "Bot Developer", "Built-in", DEV_RANK
    if is_owner:
        return "Server Owner", "Built-in", OWNER_RANK

    levels = all_levels(cfg)

    # 1. Bot Code Assignments (built through the bot code -> "Built-in")
    ukey_code = CODE_USERS.get(mid) or CODE_USERS.get(str(mid))
    if ukey_code and ukey_code in levels:
        info = levels[ukey_code]
        return info["name"], "Built-in", info["rank"]

    for r in getattr(member, "roles", []):
        r_name = getattr(r, "name", "")
        r_id = getattr(r, "id", 0)
        rkey_code = (
            CODE_ROLES.get(r_id)
            or CODE_ROLES.get(str(r_id))
            or CODE_ROLES.get(r_name)
            or CODE_ROLES.get(r_name.lower())
        )
        if rkey_code and rkey_code in levels:
            info = levels[rkey_code]
            return info["name"], "Built-in", info["rank"]

    # 2. Database User-Specific Assignment (assigned via uperm in Discord)
    ukey_db = user_key(cfg, mid)
    if ukey_db and ukey_db in levels:
        info = levels[ukey_db]
        return info["name"], "Permissions attached to user", info["rank"]

    # 3. Database Role Assignment (uperm is granted by role -> @Role mention)
    roles_cfg = cfg.get("roles") or {}
    best_info = None
    best_role = None
    best_rank = 0
    for r in getattr(member, "roles", []):
        rkey = roles_cfg.get(str(r.id))
        if rkey and rkey in levels:
            r_info = levels[rkey]
            if r_info["rank"] > best_rank:
                best_rank = r_info["rank"]
                best_info = r_info
                best_role = r

    if best_info and best_role:
        return best_info["name"], best_role.mention, best_rank

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
