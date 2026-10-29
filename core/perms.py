"""Per-user and per-role permission levels.

A level has a display name, a numeric rank (higher = more power) and a set of bot-permissions it grants.
Assigning a user or role a level lets them pass the bot's permission checks for those permissions — without
needing dangerous native Discord permissions. Config lives per-guild under the "permlevels" module:

    {"users": {"<user id>": "<level key>"}, "roles": {"<role id>": "<level key>"}, "overrides": {...}}

The hierarchy, highest first: Developer, Server Owner, Owner, Co-Owner, Server Manager, Admin, Moderator,
Helper. Developer (the bot's developers) and Server Owner (whoever owns the server) are automatic and can't be
assigned.

The level keys saved in the database never changed, only the names shown: `supremeleader` is Owner, `overseer` is
Co-Owner and `headadmin` is Server Manager. Older names still work wherever a level is typed: Supreme Leader,
Overseer, Head Admin, Mod, Bot Manager.
"""
from __future__ import annotations

import discord

DEV_RANK = 10_000
OWNER_RANK = 1_000
ADMIN_RANK = 900  # a real Discord administrator with no assigned level
COOWNER_RANK = 7  # the top assignable level, Owner (key `supremeleader`, Administrator)
DEV_NAME = "Developer"
OWNER_NAME = "Server Owner"

# =====================================================================
# BOT DEVELOPERS (Editable directly in this file)
# =====================================================================
# User IDs listed here receive the Bot Developer permission level and
# are logged with "Permission: Bot Developer" and "Granted By: Built-in".
BOT_DEVELOPERS: list[int] = []

# Filled at startup with the owner (or team members) of the bot's Discord application, so developers are
# recognised everywhere without listing their IDs in OWNER_IDS (see Veil.discover_developers).
DISCOVERED_DEVELOPERS: set[int] = set()

# =====================================================================
# TRUSTED BOTS (Editable directly in this file)
# =====================================================================
# Bots that are whitelisted in every server, built in: the antinuke never counts or punishes what they do, adding
# them is never treated as an unauthorized bot add (nobody is punished and the bot isn't banned), they aren't
# quarantined when they join, and they may hold blacklisted roles. Only list bots you run yourself: a trusted bot
# whose token leaks could act in any server without the antinuke stopping it.
TRUSTED_BOTS: dict[int, str] = {
    1555002091162837022: "Ghost Vanguard",
}


# =====================================================================
# WHITELISTED PEOPLE (Editable directly in this file)
# =====================================================================
# People the bot never takes any action against, in any server: no moderation command can target them (not even a
# developer's), and the antinuke, automod, anti-raid, role blacklist and network bans all leave them alone.
WHITELISTED_PEOPLE: dict[int, str] = {
    843909457616044092: "thesimisear",
}


def is_whitelisted_person(user_or_id) -> bool:
    uid = getattr(user_or_id, "id", user_or_id)
    try:
        return int(uid) in WHITELISTED_PEOPLE
    except (TypeError, ValueError):
        return False


GHOST_VANGUARD_ID = next(bid for bid, name in TRUSTED_BOTS.items() if name == "Ghost Vanguard")


def ghost_guards(guild) -> bool:
    """Whether Ghost Vanguard is in this server. It is the guard there: Veil's own antinuke watching and
    anti-raid step aside so the two never act on the same thing (Veil still limits what's done through its own
    commands, which Ghost Vanguard trusts and so doesn't watch)."""
    get_member = getattr(guild, "get_member", None)
    return get_member is not None and get_member(GHOST_VANGUARD_ID) is not None


def is_trusted_bot(user_or_id) -> bool:
    """Whether this account is one of the built-in trusted bots."""
    uid = getattr(user_or_id, "id", user_or_id)
    try:
        return int(uid) in TRUSTED_BOTS
    except (TypeError, ValueError):
        return False


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
    if uid in BOT_DEVELOPERS or uid in DISCOVERED_DEVELOPERS:
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
CODE_USERS: dict[int | str, str] = {}

# CODE_ROLES: Map role IDs (int or str) or exact role names (str) to level keys
CODE_ROLES: dict[int | str, str] = {}

# =====================================================================
# BOT PERMISSION RANKS
# =====================================================================
# key -> (display name, rank, granted bot-permissions). Assignable with `perm add` / `uperm add`.
# Ranks only order the levels (higher = more power); servers can change them with `permlevels rank`.
PRESETS: dict[str, tuple[str, int, list[str]]] = {
    "helper":        ("Helper", 1, ["manage_messages"]),
    "moderator":     ("Moderator", 2, ["manage_messages", "kick_members", "moderate_members", "manage_nicknames"]),
    "admin":         ("Admin", 3, ["manage_messages", "kick_members", "ban_members", "moderate_members",
                                    "manage_nicknames", "manage_roles", "manage_channels"]),
    "headadmin":     ("Server Manager", 4, ["manage_messages", "kick_members", "ban_members", "moderate_members",
                                        "manage_nicknames", "manage_roles", "manage_channels", "manage_guild",
                                        "manage_webhooks", "manage_expressions", "mention_everyone"]),
    "overseer":      ("Co-Owner", 6, [p for p in ALL_BOT_PERMS if p != "administrator"]),
    "supremeleader": ("Owner", COOWNER_RANK, ["administrator"] + ALL_BOT_PERMS),
}

# Old SAVED level keys -> the level that replaced them (saved assignments are migrated on startup).
# These are about what's stored, not what's typed: a short-lived version saved Supreme Leader as "coowner" and
# Overseer + Bot Manager as "manager". What a typed name means is TYPED_NAMES below.
LEGACY_KEYS: dict[str, str] = {
    "mod": "moderator",
    "botmanager": "headadmin",  # Bot Manager was removed: its holders became Head Admins (nobody gains access)
    "coowner": "supremeleader",
    "manager": "overseer",
}

# Old names people may still type -> the level they mean now. A typed name that's ambiguous goes to the LOWER
# level ("manager" could be Server Manager or the old Manager, so it's Server Manager), so a typo never hands out
# more than intended. The current display names (Owner, Co-Owner, Server Manager) are matched before these.
TYPED_NAMES: dict[str, str] = {
    "mod": "moderator",
    "botmanager": "headadmin",
    "manager": "headadmin",
    "headadmin": "headadmin",
    "overseer": "overseer",
    "supremeleader": "supremeleader",
}

# Shown in the level list but handed out automatically, never via `perm add` / `uperm add`
RESERVED: dict[str, tuple[str, int]] = {
    "owner":     (OWNER_NAME, OWNER_RANK),
    "developer": (DEV_NAME, DEV_RANK),
}
RESERVED_ALIASES: dict[str, str] = {"serverowner": "owner", "guildowner": "owner", "botdev": "developer",
                                    "botdeveloper": "developer", "dev": "developer"}

# Permission hierarchy display order (highest to lowest)
HIERARCHY_ORDER: list[str] = [
    DEV_NAME,
    OWNER_NAME,
    "Owner",
    "Co-Owner",
    "Server Manager",
    "Admin",
    "Moderator",
    "Helper",
]

# Permission hierarchy keys (highest to lowest)
HIERARCHY_KEYS: list[str] = [
    "developer",
    "owner",
    "supremeleader",
    "overseer",
    "headadmin",
    "admin",
    "moderator",
    "helper",
]


def canonical(key: str | None) -> str | None:
    """A level key as it's called now (`mod` -> `moderator`); other keys come back unchanged."""
    if key is None:
        return None
    return LEGACY_KEYS.get(key, key)


def migrate(cfg: dict) -> bool:
    """Rewrite old level keys in a server's saved assignments and overrides. True when anything changed."""
    changed = False
    for bucket in ("users", "roles"):
        entries = cfg.get(bucket) or {}
        for target, key in list(entries.items()):
            new = canonical(key)
            if new != key:
                entries[target] = new
                changed = True
    overrides = cfg.get("overrides") or {}
    for old, new in LEGACY_KEYS.items():
        if old in overrides:
            data = overrides.pop(old)
            if new not in overrides:  # two old levels became one: the first one found keeps its settings
                data.pop("name", None)  # an old custom name would hide the new one
                if data:
                    overrides[new] = data
            changed = True
    return changed


def all_levels(cfg: dict) -> dict[str, dict]:
    """Assignable levels merged with any per-guild overrides."""
    merged = {k: {"name": n, "rank": r, "perms": list(p)} for k, (n, r, p) in PRESETS.items()}
    for key, data in (cfg.get("overrides") or {}).items():
        key = canonical(key)
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
    if flat in TYPED_NAMES:  # "supreme leader", "overseer", "head admin", "bot manager", "mod"
        return TYPED_NAMES[flat]
    return None


def is_reserved(text: str) -> str | None:
    flat = _flat(text)
    flat = RESERVED_ALIASES.get(flat, flat)
    for key, (name, _) in RESERVED.items():
        if flat == key or flat == _flat(name):
            return name
    return None


def user_key(cfg: dict, user_id: int) -> str | None:
    return canonical((cfg.get("users") or {}).get(str(user_id)))


def role_key(cfg: dict, role_id: int) -> str | None:
    return canonical((cfg.get("roles") or {}).get(str(role_id)))


def member_level_info(cfg: dict, member: discord.Member, *, is_owner: bool = False, is_dev: bool = False) -> tuple[str | None, str | None, int]:
    """Resolve a member's effective permission level, source, and numeric rank.

    Built-in permissions (bot developers, server owners, or bot code assignments) are logged
    with "Granted By: Built-in".
    uperm permissions assigned via roles in Discord are logged with "Granted By: @Role".
    uperm permissions assigned directly to a user in Discord are logged with "Permissions attached to user".

    Returns:
        (level_name, granted_by, rank)
        e.g. ("Owner", "Permissions attached to user", 7)
             ("Admin", "@HR | Branch Management", 3)
             ("Helper", "@Office of Lilz", 1)
             ("Developer", "Built-in", 10000)
             ("Server Owner", "Built-in", 1000)
    """
    mid = getattr(member, "id", 0)
    if is_dev or is_bot_developer(member):
        return DEV_NAME, "Built-in", DEV_RANK
    if is_owner:
        return OWNER_NAME, "Built-in", OWNER_RANK

    levels = all_levels(cfg)

    # 1. Bot Code Assignments (built through the bot code -> "Built-in")
    ukey_code = canonical(CODE_USERS.get(mid) or CODE_USERS.get(str(mid)))
    if ukey_code and ukey_code in levels:
        info = levels[ukey_code]
        return info["name"], "Built-in", info["rank"]

    for r in getattr(member, "roles", []):
        r_name = getattr(r, "name", "")
        r_id = getattr(r, "id", 0)
        rkey_code = canonical(
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
        rkey = canonical(roles_cfg.get(str(r.id)))
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
        key = canonical(CODE_USERS.get(member_or_id) or CODE_USERS.get(str(member_or_id))) or user_key(cfg, member_or_id)
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
