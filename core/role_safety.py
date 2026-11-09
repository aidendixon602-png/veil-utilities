"""Fail-closed checks for roles distributed automatically or through self-service."""
from __future__ import annotations

from core.utils import UserError, is_dangerous, role_protection_ids

# Discord staff capabilities that are not necessarily included in the generic dangerous-role check.
STAFF_PERMISSIONS = {"kick_members", "ban_members", "moderate_members", "mute_members", "deafen_members",
                     "move_members", "view_audit_log", "view_guild_insights"}


def _has_staff_permissions(permissions) -> bool:
    return is_dangerous(permissions) or any(
        enabled and (name.startswith("manage_") or name in STAFF_PERMISSIONS)
        for name, enabled in permissions
    )


async def unsafe_automatic_roles(db, guild, roles) -> dict[int, str]:
    """Return role IDs that must not be automatically assigned, with a user-facing reason.

    Read every policy before returning: an unavailable or malformed policy must stop a grant,
    not silently become an empty policy. Call immediately before a role grant as well as at setup.
    """
    if db is None or guild is None or getattr(guild, "id", None) is None:
        raise RuntimeError("Role policy is unavailable")
    blacklist = await db.get_config(guild.id, "roleblacklist")
    levels = await db.get_config(guild.id, "permlevels")
    fake = await db.get_config(guild.id, "fakeperms")
    antinuke = await db.get_config(guild.id, "antinuke")
    if not all(isinstance(c, dict) for c in (blacklist, levels, fake, antinuke)):
        raise ValueError("Invalid role policy")
    level_roles = levels.get("roles", {})
    if not isinstance(level_roles, dict):
        raise ValueError("Invalid role-level policy")
    if any(not isinstance(blacklist.get(key, []), list) for key in ("roles", "hard_roles", "authorized")):
        raise ValueError("Invalid role-blacklist policy")
    bypass = blacklist.get("soft_bypass", {})
    trusted = antinuke.get("whitelist_roles", [])
    if not isinstance(bypass, dict) or not isinstance(bypass.get("roles", []), list) or not isinstance(trusted, list):
        raise ValueError("Invalid role-bypass policy")
    protected = role_protection_ids(blacklist)
    sensitive = set(blacklist.get("authorized", [])) | set(bypass.get("roles", [])) | set(trusted)
    result = {}
    for role in roles:
        rid = role.id
        if rid in protected or str(rid) in protected:
            result[rid] = "is blacklisted (bled) and cannot be assigned"
        elif str(rid) in level_roles or rid in level_roles:
            result[rid] = "has a Veil permission level and cannot be given automatically"
        elif fake.get(str(rid)) or fake.get(rid):
            result[rid] = "has Veil fake permissions and cannot be given automatically"
        elif rid in sensitive or str(rid) in sensitive:
            result[rid] = "has a security bypass and cannot be given automatically"
        elif role.is_default() or role.managed or role >= guild.me.top_role or _has_staff_permissions(role.permissions):
            result[rid] = "is not safe to give automatically"
    return result


async def require_safe_automatic_roles(db, guild, roles) -> None:
    denied = await unsafe_automatic_roles(db, guild, roles)
    for role in roles:
        if role.id in denied:
            raise UserError(f"{role.mention} {denied[role.id]}")
