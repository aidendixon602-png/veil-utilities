"""Read-only security summaries using the bot's existing routing and protection rules."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy

from core.dashboard import _esc


def antinuke_health(bot, guild, raw: dict) -> str:
    from cogs.antinuke import Antinuke, MODULES, RULE_LOOKUP, OVERLAP_NOTE
    from core.perms import ghost_guards

    cfg = deepcopy(raw)
    Antinuke.migrate(cfg)
    modules = cfg.get("modules") or {}
    keys = set(MODULES) | set(RULE_LOOKUP)
    keys.discard("vanity")  # legacy alias, not an independently handled audit event
    active = sum(bool(modules.get(key, {}).get("on")) for key in keys if isinstance(modules.get(key), dict))
    enabled = bool(cfg.get("enabled"))
    status = "Enabled" if enabled else "Disabled"
    warning = "Rules do not run while protection is disabled." if not enabled else (
        "No rules are enabled." if not active else "Enabled rules are configured; this is not a guarantee of protection.")
    me = getattr(guild, "me", None)
    perms = getattr(me, "guild_permissions", None)
    checks = (("view_audit_log", "View Audit Log"), ("manage_roles", "Manage Roles"),
              ("manage_channels", "Manage Channels"), ("ban_members", "Ban Members"),
              ("kick_members", "Kick Members"), ("moderate_members", "Moderate Members"))
    if perms is None:
        permission_summary = "Bot permissions could not be checked."
    else:
        missing = [label for key, label in checks if not getattr(perms, key, False)]
        permission_summary = "Missing permissions: " + ", ".join(missing) if missing else "Core audit and response permissions are present."
    intents = getattr(bot, "intents", None)
    intent_summary = ("Server Members intent could not be checked." if intents is None else
                      "Server Members intent is on." if getattr(intents, "members", False) else
                      "Server Members intent is off; member checks and restoration may be incomplete.")
    roles = getattr(guild, "roles", None)
    top = getattr(me, "top_role", None)
    position = getattr(top, "position", None)
    if roles is None or not isinstance(position, int):
        hierarchy = "Bot role hierarchy could not be checked."
    else:
        above = sum(1 for role in roles if isinstance(getattr(role, "position", None), int) and role.position > position)
        hierarchy = (f"{above} server role(s) are above Veil's highest role. Veil cannot manage roles above it; "
                     "the target's hierarchy also matters.")
    overlap = f'<p class="hint">{_esc(OVERLAP_NOTE)}</p>' if ghost_guards(guild) else ""
    return (f'<section class="card" id="health"><div class="card-head"><span class="section-icon" aria-hidden="true">✓</span>'
            '<div><h2>Protection readiness</h2><p>A read-only snapshot of settings and permissions.</p></div></div>'
            f'<div class="health-grid"><div><span>PROTECTION</span><strong>{status}</strong></div>'
            f'<div><span>RULES ENABLED</span><strong>{active}</strong></div></div>'
            f'<p class="hint">{_esc(warning)}</p><p class="hint">{_esc(permission_summary)}</p>'
            f'<p class="hint">{_esc(intent_summary)}</p><p class="hint">{_esc(hierarchy)}</p>{overlap}'
            '<p class="hint">Role hierarchy, channel permissions, audit events and Discord availability can affect enforcement. '
            'These checks do not guarantee protection.</p></section>')


def log_routes(guild, cfg: dict) -> str:
    from cogs.logging import KINDS, OFF, _channel_for, _shared_home

    destinations = {}
    for key in KINDS:
        direct = _channel_for(guild, cfg, key)
        # Timeout events use the moderation/member stream when their own route is unavailable,
        # including when the direct timeout route is off (see Logging.timeout_channel).
        fallback = _shared_home(guild, cfg, key) if key == "timeouts" and not direct else None
        destinations[key] = (direct, fallback)
    counts = Counter(ch.id for direct, fallback in destinations.values() if (ch := direct or fallback))
    rows = []
    for key, kind in KINDS.items():
        direct, fallback = destinations[key]
        ch = direct or fallback
        setting = cfg.get(key)
        if fallback:
            mode = "Event fallback" + ("; direct route off" if setting == OFF else "")
        elif setting == OFF:
            mode = "Off"
        elif direct is None:
            mode = "No route found"
        elif setting == direct.id:
            mode = "Selected"
        else:
            mode = "Automatic"
        if isinstance(setting, int) and not isinstance(setting, bool) and setting > 0 and (not direct or direct.id != setting):
            mode += " · selected channel unavailable or excluded"
        if ch:
            label = f"#{ch.name}"
            if counts[ch.id] > 1:
                mode += " · shared"
        else:
            label = "No destination"
        rows.append(f'<div class="route-row"><span>{_esc(kind.label)}</span><strong>{_esc(label)}</strong><small>{_esc(mode)}</small></div>')
    return ('<div class="routes-preview"><h3>Current destinations</h3>'
            '<p class="hint">Based on saved settings and channels currently visible to Veil. '
            'Exclusions, event-specific fallbacks and channel permissions can affect delivery. '
            'Changing a selector below updates this preview after saving and refreshing.</p>'
            f'<div class="route-grid">{"".join(rows)}</div></div>')
