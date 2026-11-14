"""Owner/developer dashboard controls backed by the existing bot configuration.

Each section saves independently and checks a signed snapshot to avoid overwriting
changes made since the page was opened. No Discord actions or channel deletion happen here.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import hmac
import json
import time

import discord
from aiohttp import web

from core.dashboard import _esc, _parse_ids


FILTER_DEFAULT = {"exempt_roles": [], "exempt_channels": [], "action": "delete", "timeout": 60}
TICKET_DEFAULT = {"support": [], "max": 1}


def _digest(cfg: dict) -> str:
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def _revision(server, guild_id: int, user_id: int, module: str, cfg: dict) -> str:
    return server._sign({"t": "feature_revision", "gid": guild_id, "uid": user_id, "module": module,
                         "digest": _digest(cfg), "exp": int(time.time()) + 3600})


def _selected(value, current) -> str:
    return " selected" if str(value) == str(current) else ""


def _select(name: str, label: str, current, choices, hint: str = "") -> str:
    options = "".join(f'<option value="{_esc(value)}"{_selected(value, current)}>{_esc(title)}</option>'
                      for value, title in choices)
    return (f'<div class="form-group"><label for="{_esc(name)}">{_esc(label)}</label>'
            f'<select id="{_esc(name)}" name="{_esc(name)}">{options}</select>'
            f'<span class="hint">{_esc(hint)}</span></div>')


def _number(name: str, label: str, value, low: int, high: int, hint: str = "") -> str:
    return (f'<div class="form-group"><label for="{_esc(name)}">{_esc(label)}</label>'
            f'<input id="{_esc(name)}" name="{_esc(name)}" type="number" min="{low}" max="{high}" '
            f'value="{_esc(value)}" required><span class="hint">{_esc(hint)}</span></div>')


def _optional_number(name: str, label: str, value, low: int, high: int) -> str:
    return (f'<div class="form-group"><label for="{_esc(name)}">{_esc(label)}</label>'
            f'<input id="{_esc(name)}" name="{_esc(name)}" type="number" min="{low}" max="{high}" '
            f'value="{_esc(value if value is not None else "")}" placeholder="Default"></div>')


def _form_start(server, guild, user, feature: str, module: str, cfg: dict) -> str:
    revision = _revision(server, guild.id, int(user["uid"]), module, cfg)
    return (f'<form method="post" action="/servers/{guild.id}/features/{feature}">'
            f'<input type="hidden" name="csrf" value="{_esc(user["csrf"])}">'
            f'<input type="hidden" name="revision" value="{_esc(revision)}">')


def _channel_choices(guild, kind, current, default_label):
    channels = getattr(guild, "categories" if kind == "category" else "text_channels", [])
    choices = [("", default_label)] + [(str(c.id), c.name if kind == "category" else f"#{c.name}")
                                      for c in sorted(channels, key=lambda c: (c.position, c.id))]
    if current and str(current) not in {v for v, _ in choices}:
        choices.append((str(current), f"Unavailable channel ({current})"))
    return choices


def _on_off(value):
    return "on" if value else "off"


def _section(title: str, icon: str, description: str, body: str, anchor: str) -> str:
    return (f'<section class="card" id="{_esc(anchor)}"><div class="card-head">'
            f'<span class="section-icon" aria-hidden="true">{_esc(icon)}</span><div>'
            f'<h2>{_esc(title)}</h2><p>{_esc(description)}</p></div></div>{body}</section>')


async def features_page(server, request: web.Request) -> web.Response:
    user = server._user(request)
    if user is None:
        return server._redirect("/login")
    guild = await server._guild_for(request, user)
    if guild is None:
        return server._message("Server not found", "Veil isn't in that server, or you cannot manage it.", 404, user)
    from cogs.antinuke import Antinuke, DEFAULT as AN_DEFAULT, INSTANT, LABELS, MODULES, RULE_LOOKUP
    from cogs.levels import DEFAULT as LEVEL_DEFAULT

    filters = await server.bot.db.get_config(guild.id, "filters", FILTER_DEFAULT)
    levels = await server.bot.db.get_config(guild.id, "levels", LEVEL_DEFAULT)
    tickets = await server.bot.db.get_config(guild.id, "tickets", TICKET_DEFAULT)
    antinuke_raw = await server.bot.db.get_config(guild.id, "antinuke", AN_DEFAULT)
    # Display the same migrated rules the cog will use, without writing on a page view.
    antinuke = deepcopy(antinuke_raw)
    Antinuke.migrate(antinuke)
    on_off = (("off", "Off"), ("on", "On"))

    form = _form_start(server, guild, user, "automod", "filters", filters)
    form += '<div class="note">These filters can delete messages and apply timeouts. Members with Manage Messages are exempt.</div><div class="form-grid">'
    for key, label in (("invites", "Block invites"), ("links", "Block links"), ("zalgo", "Block zalgo text"),
                       ("antighostping", "Anti-ghostping")):
        form += _select(key, label, _on_off(filters.get(key)), on_off)
    for key, label, low, high in (("caps", "Capital letters (%)", 30, 100), ("massmention", "Mentions", 3, 50),
                                  ("emojis", "Emojis", 2, 50), ("newlines", "Line breaks", 3, 50)):
        form += _number(key, label, filters.get(key) or 0, 0, high, f"0 turns this filter off; otherwise {low}–{high}.")
    spam = filters.get("spam") or [0, 5]
    form += _number("spam_messages", "Spam message limit", spam[0], 0, 30, "0 turns spam filtering off; otherwise 2–30.")
    form += _number("spam_seconds", "Spam interval (seconds)", spam[1], 2, 120)
    form += _select("action", "Filter response", filters.get("action", "delete"), (("delete", "Delete message"), ("timeout", "Delete and timeout")))
    form += _number("timeout", "Timeout (seconds)", filters.get("timeout", 60), 10, 2419200, "10 seconds to 28 days.")
    form += '</div><p class="hint">Word lists, regex, exemptions and allowed domains are preserved; manage them in Discord.</p><button class="btn" type="submit">Save automod</button></form>'
    automod_section = _section("Automod", "01", "Control message filters and their response.", form, "automod")

    form = _form_start(server, guild, user, "levels", "levels", levels) + '<div class="form-grid">'
    form += _select("levels_enabled", "Leveling", _on_off(levels.get("enabled")), on_off,
                    "Turning this off does not erase XP.").replace('name="levels_enabled"', 'name="enabled"')
    form += _select("voice", "Voice XP", _on_off(levels.get("voice", True)), on_off)
    form += _select("stack", "Reward roles", _on_off(levels.get("stack", True)), (("on", "Keep previous rewards"), ("off", "Keep only highest reward")))
    form += _select("channel", "Level-up announcements", levels.get("channel") or "", _channel_choices(guild, "text", levels.get("channel"), "Same channel"))
    form += '</div><div class="form-group"><label for="level_message">Level-up message</label>'
    form += f'<textarea id="level_message" name="message" rows="3" maxlength="2000">{_esc(levels.get("message") or "")}</textarea>'
    form += '<span class="hint">Leave empty to disable announcements. Supports {level} and the bot’s usual message variables.</span></div>'
    form += '<p class="hint">XP, role rewards, multipliers and ignored channels are not changed here.</p><button class="btn" type="submit">Save leveling</button></form>'
    levels_section = _section("Leveling", "02", "Control XP and level-up announcements.", form, "leveling")

    form = _form_start(server, guild, user, "tickets", "tickets", tickets) + '<div class="note">Support roles can view private tickets. Only add roles that should have access.</div><div class="form-grid">'
    form += _select("category", "New ticket category", tickets.get("category") or "", _channel_choices(guild, "category", tickets.get("category"), "No category"))
    form += _select("log", "Transcript channel", tickets.get("log") or "", _channel_choices(guild, "text", tickets.get("log"), "DM only"))
    form += _select("ping", "Ping support roles", _on_off(tickets.get("ping")), on_off)
    form += _number("max", "Open tickets per user", tickets.get("max", 1), 1, 10)
    ids = ", ".join(str(v) for v in tickets.get("support", []))
    form += f'<div class="form-group"><label for="support">Support role IDs</label><input id="support" name="support" value="{_esc(ids)}" placeholder="Role IDs, separated by commas"><span class="hint">Roles must belong to this server. @everyone is not allowed.</span></div>'
    form += '</div><div class="form-group"><label for="ticket_message">Message inside new tickets</label>'
    form += f'<textarea id="ticket_message" name="message" rows="3" maxlength="2000">{_esc(tickets.get("message") or "")}</textarea>'
    form += '<span class="hint">Leave empty for the default message. Existing tickets are not changed.</span></div>'
    form += '<p class="hint">Use the Discord ticket command to send a panel or close a ticket.</p><button class="btn" type="submit">Save tickets</button></form>'
    tickets_section = _section("Tickets", "03", "Configure new tickets and transcripts.", form, "tickets")

    form = _form_start(server, guild, user, "protection", "antinuke", antinuke_raw)
    form += '<div class="note">Turning protection off stops antinuke enforcement. Turning it on fills missing rules with defaults; existing rules remain unchanged.</div>'
    form += _select("protection_enabled", "Antinuke protection", _on_off(antinuke.get("enabled")), on_off).replace(
        'name="protection_enabled"', 'name="enabled"')
    form += '<label class="confirm-choice"><input type="checkbox" name="confirm_disable" value="yes"> I understand disabling antinuke stops protection.</label>'
    form += '<button class="btn" type="submit">Save protection</button></form>'
    rules = antinuke.get("modules") or {}
    form += ('<div class="rules-heading"><h3>Individual rules</h3><p class="hint">Each rule saves separately. '
             'Turning a rule off weakens protection; turning a rule on does not enable the overall protection. '
             'Blank thresholds use rule defaults; blank windows and inherited punishments follow the server defaults.</p></div>')
    # The legacy "vanity" alias has no separate audit event; serverupdate handles it.
    for key in (k for k in dict.fromkeys([*RULE_LOOKUP, *MODULES]) if k != "vanity"):
        mod = rules.get(key) or {}
        title = LABELS.get(key, RULE_LOOKUP.get(key, {}).get("name", MODULES.get(key, key)))
        state = _on_off(mod.get("on"))
        form += _form_start(server, guild, user, "rule", "antinuke", antinuke_raw)
        form += f'<div class="rule-row"><div><strong>{_esc(title)}</strong><span class="hint">{_esc(key)}{ " · instant" if key in INSTANT else ""}</span></div>'
        form += f'<input type="hidden" name="rule" value="{_esc(key)}">'
        form += _select(f"rule_{key}", "Rule state", state, on_off).replace(f'name="rule_{key}"', 'name="state"')
        if key in INSTANT:
            form += '<div class="form-group"><label>Threshold</label><span class="hint">1 (fixed)</span></div>'
        else:
            form += _optional_number(f"threshold_{key}", "Threshold", mod.get("threshold"), 1, 100).replace(
                f'name="threshold_{key}"', 'name="threshold"')
        form += _optional_number(f"window_{key}", "Window (s)", mod.get("window"), 5, 86400).replace(
            f'name="window_{key}"', 'name="window"')
        if key == "permissions":
            form += '<div class="form-group"><label>Punishment</label><span class="hint">Strip (fixed)</span></div>'
        else:
            form += _select(f"punishment_{key}", "Punishment", mod.get("punishment") or "",
                            (("", "Server default"), ("ban", "Ban"), ("kick", "Kick"), ("strip", "Strip roles"))).replace(
                                f'name="punishment_{key}"', 'name="punishment"')
        form += '<button class="btn btn-plain" type="submit">Save rule</button></div></form>'
    antinuke_section = _section("Antinuke rules", "04", "Enable protection and control individual rules.", form, "rules")

    saved = ('<div class="note ok" role="status">Settings saved.</div>' if request.query.get("saved") else "")
    sections = {"automod": ("Automod", automod_section), "leveling": ("Leveling", levels_section),
                "tickets": ("Tickets", tickets_section), "rules": ("Antinuke", antinuke_section)}
    selected = request.query.get("section", "")
    if selected and selected not in sections:
        return server._message("Unknown feature", "Choose a feature from the server overview.", 404, user)
    if selected:
        label, content = sections[selected]
        # A fixed, allowlisted return hint keeps a save on the selected page.
        content = content.replace('<input type="hidden" name="csrf"',
                                  f'<input type="hidden" name="return_section" value="{selected}"><input type="hidden" name="csrf"')
        body = (f'<div class="page-heading"><div><p class="eyebrow">{_esc(guild.name)} / Features</p>'
                f'<h1>{_esc(label)}</h1><p>Review your settings and save changes when ready.</p></div>'
                f'<a class="btn btn-plain" href="/servers/{guild.id}/overview">← Server overview</a></div>{saved}'
                f'<div class="feature-content">{content}</div>')
        return server._page(label, body, user, guild=guild, active=selected)
    body = (f'<div class="page-heading"><div><p class="eyebrow">Servers / Feature controls</p><h1>{_esc(guild.name)}</h1>'
            '<p>Manage selected security and community features. Each section saves separately.</p></div>'
            f'<a class="btn btn-plain" href="/servers/{guild.id}">← Server settings</a></div>{saved}'
            '<div class="settings-layout"><nav class="section-nav" aria-label="Feature sections"><span>ON THIS PAGE</span>'
            '<a href="#automod">Automod</a><a href="#leveling">Leveling</a><a href="#tickets">Tickets</a>'
            '<a href="#rules">Antinuke rules</a></nav><div>'
            f'{automod_section}{levels_section}{tickets_section}{antinuke_section}</div></div>')
    return server._page("Feature controls", body, user, guild=guild)


class InvalidSetting(ValueError):
    pass


def _choice(data, field, values):
    value = str(data.get(field, ""))
    if value not in values:
        raise InvalidSetting(f"Invalid {field} selection")
    return value


def _int(data, field, low, high, *, zero=False):
    raw = str(data.get(field, ""))
    if not raw.isascii() or not raw.isdigit() or len(raw) > 8:
        raise InvalidSetting(f"Invalid {field}")
    value = int(raw)
    if not low <= value <= high and not (zero and value == 0):
        raise InvalidSetting(f"{field} must be {low}–{high}" + (" or 0 to turn it off" if zero else ""))
    return value


def _channel(guild, data, key, cls, old):
    value = str(data.get(key, ""))
    if not value:
        return None
    if not value.isascii() or not value.isdigit() or len(value) > 20 or not 0 < int(value) < 2**64:
        raise InvalidSetting(f"Invalid {key} channel")
    value = int(value)
    get_channel = getattr(guild, "get_channel", None)
    channel = get_channel(value) if callable(get_channel) else None
    if value != old and not isinstance(channel, cls):
        raise InvalidSetting(f"{key} must be a channel in this server")
    return value


async def save_feature(server, request: web.Request) -> web.Response:
    user = server._user(request)
    if user is None:
        return server._message("Login required", "Please sign in and try again.", 401)
    guild = await server._guild_for(request, user)
    if guild is None:
        return server._message("Server not found", "Veil isn't in that server, or you cannot manage it.", 404, user)
    data = await request.post()
    if not server._csrf_ok(user, data.get("csrf")):
        return server._message("Session expired", "Please refresh the page and try again.", 403, user)
    feature = request.match_info["feature"]
    from cogs.antinuke import Antinuke, DEFAULT as AN_DEFAULT, INSTANT, MODULES, RULE_LOOKUP
    from cogs.levels import DEFAULT as LEVEL_DEFAULT

    modules = {"automod": ("filters", FILTER_DEFAULT), "levels": ("levels", LEVEL_DEFAULT),
               "tickets": ("tickets", TICKET_DEFAULT), "protection": ("antinuke", AN_DEFAULT),
               "rule": ("antinuke", AN_DEFAULT)}
    if feature not in modules:
        return server._message("Unknown feature", "Nothing was saved.", 404, user)
    module, default = modules[feature]
    old = await server.bot.db.get_config(guild.id, module, default)
    revision = server._unsign(data.get("revision"), "feature_revision")
    if (not revision or revision.get("gid") != guild.id or revision.get("uid") != int(user["uid"])
            or revision.get("module") != module or not hmac.compare_digest(str(revision.get("digest", "")), _digest(old))):
        return server._message("Settings changed", "Refresh the page and review the latest settings. Nothing was saved.", 409, user)
    cfg = deepcopy(old)
    if module == "antinuke":
        # Match the cog's normal read-time migrations before applying the selected edit.
        Antinuke.migrate(cfg)
    notice = None
    try:
        if feature == "automod":
            for key in ("invites", "links", "zalgo", "antighostping"):
                cfg[key] = _choice(data, key, ("on", "off")) == "on"
            for key, low, high in (("caps", 30, 100), ("massmention", 3, 50), ("emojis", 2, 50), ("newlines", 3, 50)):
                cfg[key] = _int(data, key, low, high, zero=True)
            messages = _int(data, "spam_messages", 2, 30, zero=True)
            seconds = _int(data, "spam_seconds", 2, 120)
            cfg["spam"] = [messages, seconds] if messages else None
            cfg["action"] = _choice(data, "action", ("delete", "timeout"))
            cfg["timeout"] = _int(data, "timeout", 10, 2419200)
        elif feature == "levels":
            for key in ("enabled", "voice", "stack"):
                cfg[key] = _choice(data, key, ("on", "off")) == "on"
            cfg["channel"] = _channel(guild, data, "channel", discord.TextChannel, cfg.get("channel"))
            message = str(data.get("message", ""))
            if len(message) > 2000:
                raise InvalidSetting("Level-up message is too long")
            cfg["message"] = message or None
        elif feature == "tickets":
            cfg["category"] = _channel(guild, data, "category", discord.CategoryChannel, cfg.get("category"))
            cfg["log"] = _channel(guild, data, "log", discord.TextChannel, cfg.get("log"))
            cfg["ping"] = _choice(data, "ping", ("on", "off")) == "on"
            cfg["max"] = _int(data, "max", 1, 10)
            try:
                roles = _parse_ids(str(data.get("support", "")))
            except ValueError:
                raise InvalidSetting("Invalid support role IDs") from None
            for rid in roles:
                if rid == guild.id:
                    raise InvalidSetting("@everyone cannot be a support role")
                get_role = getattr(guild, "get_role", None)
                if rid not in cfg.get("support", []) and (not callable(get_role) or get_role(rid) is None):
                    raise InvalidSetting("Support roles must belong to this server")
            cfg["support"] = roles
            message = str(data.get("message", ""))
            if len(message) > 2000:
                raise InvalidSetting("Ticket message is too long")
            cfg["message"] = message or None
        elif feature == "protection":
            enabled = _choice(data, "enabled", ("on", "off")) == "on"
            if not enabled and cfg.get("enabled") and data.get("confirm_disable") != "yes":
                raise InvalidSetting("Confirm that you understand protection will be disabled")
            if enabled and not cfg.get("enabled"):
                Antinuke.enable_rules(cfg)
            elif not enabled:
                if cfg.get("enabled"):
                    notice = "turned the antinuke off via the dashboard"
                cfg["enabled"] = False
        else:
            rule = str(data.get("rule", ""))
            if rule == "vanity" or (rule not in RULE_LOOKUP and rule not in MODULES):
                raise InvalidSetting("Unknown antinuke rule")
            state = _choice(data, "state", ("on", "off")) == "on"
            mod = cfg.setdefault("modules", {}).setdefault(rule, {})
            mod["on"] = state
            for field, low, high in (("threshold", 1, 100), ("window", 5, 86400)):
                if field not in data:
                    continue
                if field == "threshold" and rule in INSTANT:
                    raise InvalidSetting("This rule has a fixed threshold of 1")
                if str(data[field]) == "":
                    mod.pop(field, None)
                else:
                    mod[field] = _int(data, field, low, high)
            if "punishment" in data:
                if rule == "permissions":
                    raise InvalidSetting("Permission grants always use strip")
                punishment = _choice(data, "punishment", ("", "ban", "kick", "strip"))
                if punishment:
                    mod["punishment"] = punishment
                else:
                    mod.pop("punishment", None)
            notice = f"updated antinuke rule {rule} via the dashboard"
    except (InvalidSetting, TypeError, ValueError) as exc:
        return server._message("Invalid settings", f"{exc}. Nothing was saved.", 400, user)
    await server.bot.db.set_config_committed(guild.id, module, cfg)
    if notice:
        try:
            get_cog = getattr(server.bot, "get_cog", None)
            cog = get_cog("Antinuke") if callable(get_cog) else None
            if cog:
                await cog.security_notice(guild, discord.Object(id=int(user["uid"])), notice)
        except Exception:
            pass  # security notices are best effort, as in the Discord commands
    import logging
    logging.getLogger("veil.dashboard").info("Dashboard: user %s updated %s for guild %s", user["uid"], feature, guild.id)
    anchor = {"automod": "automod", "levels": "leveling", "tickets": "tickets", "protection": "rules", "rule": "rules"}[feature]
    section = f"&section={anchor}" if data.get("return_section") == anchor else ""
    return server._redirect(f"/servers/{guild.id}/features?saved=1{section}#{anchor}")
