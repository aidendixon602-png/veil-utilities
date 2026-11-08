"""Optional per-command role restrictions, shared by invocation and help."""
from __future__ import annotations


def roles_allow_command(command, role_ids: set[str], restrictions) -> bool:
    """Every configured command in the ancestry must match at least one member role.

    Invalid stored restrictions fail closed. An absent entry imposes no new restriction.
    """
    if not isinstance(restrictions, dict):
        return False
    for item in (command, *getattr(command, "parents", [])):
        name = item.qualified_name
        if name not in restrictions:
            continue
        allowed = restrictions[name]
        if (not isinstance(allowed, list) or not allowed or not all(str(role).isdigit() for role in allowed)
                or not any(str(role) in role_ids for role in allowed)):
            return False
    return True
