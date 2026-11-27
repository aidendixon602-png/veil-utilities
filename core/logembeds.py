"""Structured audit-log embeds for Veil Utilities.

Every log channel shares one consistent visual layout:
- Author line: clean username handle and avatar (or server name/icon for server-wide events)
- Title: concise event name in Title Case with no decorative emojis
- Fields: scannable inline metadata on the top row, full-width content/lists underneath
- Footer: resource IDs (and command timing when applicable), paired with Discord's native timestamp
"""
from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Iterable

import discord

from core import redact
from core.utils import Colors, fmt_duration, shorten, shorten_md, ts

SLATE = 0x4E5D6C  # restrained steel-slate for neutral edits and state updates
AUDIT_SETTLE = 0.6  # seconds to let Discord write the audit-log entry before we look for it
AUDIT_WINDOW = 15.0  # an entry older than this isn't about the event we just saw
QUOTE_LIMIT = 900
LIST_LIMIT = 950
NEW_ACCOUNT_DAYS = 7
GROUP_SHOWN = 25
DANGEROUS = ("administrator", "manage_guild", "manage_roles", "manage_channels", "manage_webhooks", "ban_members",
             "kick_members", "mention_everyone", "moderate_members", "manage_messages")

_LEAD_SYMBOL = re.compile(r"^(?:[^\w\s`<@#\[(\"'\-*_~|]+\s*)+")


# ---------------------------------------------------------------- building blocks

def name_of(user) -> str:
    """Clean username handle without appending duplicate parenthetical nicknames."""
    return getattr(user, "name", None) or getattr(user, "display_name", None) or "Unknown"


def avatar_of(user) -> str | None:
    asset = getattr(user, "display_avatar", None)
    return getattr(asset, "url", None)


def _icon(guild) -> str | None:
    icon = getattr(guild, "icon", None)
    return getattr(icon, "url", None)


def strip_lead_emoji(text: str) -> str:
    """Strip leading status emojis (e.g. `❌ 🔒 ...`) so structured fields stay clean."""
    cleaned = _LEAD_SYMBOL.sub("", (text or "").strip())
    return cleaned or (text or "").strip()


def quote(text: str | None, limit: int = QUOTE_LIMIT) -> str:
    """Markdown block quote, cut to `limit` characters."""
    text = shorten((text or "").replace("\r", ""), limit)
    return "\n".join(f"> {line}" if line else "> " for line in text.split("\n"))


def _dedup(items: Iterable) -> list:
    seen: set = set()
    out = []
    for item in items:
        key = getattr(item, "id", item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def role_pills(items: Iterable, limit: int = 1000) -> str:
    """Space-separated mention pills, deduplicated and cut safely at whole-mention boundaries."""
    unique = _dedup(items)
    if not unique:
        return ""
    shown: list[str] = []
    for item in unique:
        remaining = len(unique) - len(shown) - 1
        suffix = f"  ·  +{remaining} more" if remaining else ""
        candidate = (" ".join([*shown, item.mention]) + suffix) if shown else (item.mention + suffix)
        if len(candidate) > limit:
            break
        shown.append(item.mention)
    remaining = len(unique) - len(shown)
    suffix = f"  ·  +{remaining} more" if remaining else ""
    return (" ".join(shown) + suffix) if shown else f"+{remaining} more"


def mentions(items: Iterable, limit: int = 900) -> str:
    return role_pills(items, limit)


def mention_list(items: Iterable, limit: int = 1000) -> str:
    return role_pills(items, limit)


def footer(ids: Iterable[tuple[str, object]]) -> str:
    parts = [f"{label}: {value}" if label else str(value)
             for label, value in ids if value not in (None, "")]
    return " • ".join(parts)


def bullets(lines: list[str], limit: int = LIST_LIMIT) -> str:
    """One line each, cut with `+N more` instead of overflowing."""
    out, used = [], 0
    for i, line in enumerate(lines):
        if used + len(line) + 1 > limit - 16:
            out.append(f"+{len(lines) - i} more")
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)


def small(*parts) -> str:
    kept = [str(p) for p in parts if p]
    return "-# " + " · ".join(kept) if kept else ""


def by(actor, verb: str = "by") -> str:
    return f"{verb} {actor.mention}" if actor is not None else ""


def joined(*parts) -> str:
    return " • ".join(str(p) for p in parts if p)


def person(user) -> str:
    if user is None:
        return "Unknown"
    return f"{user.mention} (`{user.name}`)"


def with_id(user) -> str:
    if user is None:
        return ""
    return f"{user.mention} (`{user.id}`)"


def when(dt) -> str:
    return f"{ts(dt, 'D')} ({ts(dt, 'R')})"


def age_of(dt) -> str:
    return fmt_duration((discord.utils.utcnow() - dt).total_seconds())


def short_age(dt) -> str:
    return age_of(dt).split(", ")[0]


def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def hex_color(value: int) -> str:
    return f"#{value:06X}" if value else "Default"


def dangerous(role) -> list[str]:
    perms = getattr(role, "permissions", None)
    if perms is None:
        return []
    if getattr(perms, "administrator", False):
        return ["administrator"]
    return [p.replace("_", " ") for p in DANGEROUS if getattr(perms, p, False)]


FIELD_ROW = 78
FIELD_MIN = 10
FIELD_GAP = 3


def field_card(color: int, title: str, *, subject=None, icon: str | None = None, lead: str | None = None,
               fields: Iterable[tuple[str, object, bool]] = (), details: Iterable[tuple[str, object]] = (),
               ids: Iterable[tuple] = (), header: str | None = None) -> discord.Embed:
    """Standard structured audit embed: author handle + avatar, bold title, clean fields, ID footer."""
    embed = discord.Embed(color=color, title=shorten(title, 256), timestamp=discord.utils.utcnow())
    if lead:
        embed.description = shorten_md(lead, 4096)
    name = header or (name_of(subject) if subject is not None else None)
    if name:
        embed.set_author(name=shorten(name, 256), icon_url=icon or (avatar_of(subject) if subject is not None else None))
    room = 0
    for label, value, inline in fields:
        if value in (None, ""):
            continue
        text = shorten_md(str(value), 1024)
        if not text:
            continue
        if inline and room >= FIELD_MIN:
            room -= len(str(label)) + len(text) + FIELD_GAP
        else:
            room = FIELD_ROW - len(str(label)) - len(text) if inline else -1
        embed.add_field(name=shorten(str(label), 256), value=text, inline=inline)
    for label, value in details:
        if value not in (None, ""):
            embed.add_field(name=shorten(str(label), 256), value=shorten_md(str(value), 1024), inline=False)
    footer_text = footer(ids)
    if footer_text:
        embed.set_footer(text=shorten(footer_text, 2048))
    return embed


def log_card(color: int, header: str, *, icon: str | None = None, lines: Iterable[str] = (),
             ids: Iterable[tuple] = ()) -> discord.Embed:
    body = "\n".join(line for line in lines if line)
    return field_card(color, header, icon=icon, lead=body or None, ids=ids)


def card(color: int, subject=None, text: str = "", *, ids: Iterable[tuple] = (), header: str | None = None
         ) -> discord.Embed:
    return field_card(color, header or "Log Test", subject=subject, lead=text or None, ids=ids)


def entry_card(color: int, sentence: str, *, subject=None, icon: str | None = None,
               details: Iterable[tuple[str, object]] = (), quoted: str | None = None,
               ids: Iterable[tuple] = (), header: str | None = None) -> discord.Embed:
    """Backwards-compatible card builder using the clean structured layout."""
    lead = f"{sentence}\n\n{redact.scrub(quoted)}" if quoted else sentence
    return field_card(color, header or "Audit Entry", subject=subject, icon=icon,
                      lead=lead, details=details, ids=ids)


def feed_line(emoji: str, text: str) -> str:
    prefix = f"{emoji} " if emoji else ""
    return shorten_md(f"{ts(discord.utils.utcnow(), 't')} {prefix}{text}", 1900)


_ON_BEHALF = re.compile(r"^(?:(?P<what>.+?) by )?(?P<name>.+?) \((?P<id>\d{15,21})\)(?:: (?P<reason>.*))?$", re.S)


def on_behalf(guild, actor, reason: str | None):
    """Credit the staff member who invoked a bot moderation command rather than the bot itself."""
    me = getattr(guild, "me", None)
    if actor is None or me is None or getattr(actor, "id", None) != me.id or not reason:
        return actor, reason
    match = _ON_BEHALF.match(reason)
    member = guild.get_member(int(match["id"])) if match and hasattr(guild, "get_member") else None
    if member is None:
        return actor, reason
    said = (match["reason"] or "").strip()
    said = "" if said == "None" else said
    what = (match["what"] or "").strip()
    return member, (f"{what}: {said}" if what and said else what or said or None)


async def audit_actor(guild, action: discord.AuditLogAction, target_id: int, *,
                      check: Callable | None = None) -> tuple[discord.abc.User | None, str | None]:
    """Resolve `(actor, reason)` from Discord's audit log when unambiguous."""
    me = getattr(guild, "me", None)
    if not me or not getattr(me.guild_permissions, "view_audit_log", False):
        return None, None
    if AUDIT_SETTLE:
        await asyncio.sleep(AUDIT_SETTLE)
    matches = []
    try:
        async for entry in guild.audit_logs(limit=6, action=action):
            if getattr(entry.target, "id", None) != target_id:
                continue
            age = (discord.utils.utcnow() - entry.created_at).total_seconds()
            if age < 0 or age > AUDIT_WINDOW:
                continue
            if check is not None and not check(entry):
                continue
            matches.append(entry)
            if len(matches) > 1:
                return None, None
    except Exception:
        return None, None
    if len(matches) == 1:
        entry = matches[0]
        return on_behalf(guild, entry.user, entry.reason)
    return None, None


# ---------------------------------------------------------------- messages

def _attachments(m) -> str:
    files = list(getattr(m, "attachments", None) or [])
    if not files:
        return ""
    shown = " • ".join(f"[{a.filename}]({a.url})" for a in files[:5])
    return shown + (f" (+{len(files) - 5} more)" if len(files) > 5 else "")


def _message_content(text: str | None, limit: int = 950) -> str:
    """Clean scrubbed message content without nested `>` blockquotes that break user formatting."""
    cleaned = redact.scrub((text or "").replace("\r", "")).strip()
    if not cleaned:
        return "*Empty*"
    return shorten_md(cleaned, limit)


def message_deleted(m: discord.Message, actor=None) -> discord.Embed:
    deleter = actor if actor is not None and actor.id != m.author.id else None
    sent = getattr(m, "created_at", None)
    attachments = _attachments(m)
    has_text = bool((m.content or "").strip())
    top_row: list[tuple[str, object, bool]] = [
        ("Author", m.author.mention, True),
        ("Channel", m.channel.mention, True),
    ]
    if deleter is not None:
        top_row.append(("Deleted By", deleter.mention, True))
    elif sent:
        top_row.append(("Sent", ts(sent, "R"), True))
    extra_fields: list[tuple[str, object, bool]] = []
    if has_text or not attachments:
        extra_fields.append(("Content", _message_content(m.content), False))
    if attachments:
        extra_fields.append(("Attachments", attachments, False))
    if deleter is not None and sent:
        extra_fields.append(("Sent", ts(sent, "R"), False))
    return field_card(Colors.error, "Message Deleted", subject=m.author,
                      fields=[*top_row, *extra_fields],
                      ids=[("User ID", m.author.id), ("Message ID", m.id)])


def message_edited(before: discord.Message, after: discord.Message) -> discord.Embed:
    sent = getattr(after, "created_at", None)
    jump = getattr(after, "jump_url", None)
    channel_val = f"{after.channel.mention} ([Jump]({jump}))" if jump else after.channel.mention
    return field_card(SLATE, "Message Edited", subject=after.author, fields=[
        ("Author", after.author.mention, True),
        ("Channel", channel_val, True),
        ("Sent", ts(sent, "R") if sent else None, True),
        ("Before", _message_content(before.content), False),
        ("After", _message_content(after.content), False),
    ], ids=[("User ID", after.author.id), ("Message ID", after.id)])


def bulk_deleted(messages: list[discord.Message]) -> discord.Embed:
    channel = messages[0].channel
    guild = getattr(channel, "guild", None)
    counts: dict[int, list] = {}
    for m in messages:
        counts.setdefault(m.author.id, [m.author, 0])[1] += 1
    top = sorted(counts.values(), key=lambda pair: -pair[1])
    authors = ", ".join(f"{a.mention} ({n})" for a, n in top[:8]) + (f", +{len(top) - 8} more" if len(top) > 8 else "")
    times = [m.created_at for m in messages if getattr(m, "created_at", None)]
    header = getattr(guild, "name", None) or f"#{getattr(channel, 'name', 'channel')}"
    return field_card(Colors.error, "Messages Purged", header=header, icon=_icon(guild), fields=[
        ("Channel", channel.mention, True),
        ("Deleted", plural(len(messages), "message"), True),
        ("Time Span", f"{ts(min(times), 't')} – {ts(max(times), 't')}" if times else None, True),
        ("Authors", authors, False),
    ], ids=[("Channel ID", channel.id), ("", "Transcript attached")])


def transcript(messages: list[discord.Message]) -> str:
    return "\n".join(f"[{m.created_at:%Y-%m-%d %H:%M}] {m.author}: {redact.scrub(m.content)}"
                     for m in sorted(messages, key=lambda x: x.id))


# ---------------------------------------------------------------- members & moderation

def member_joined(m: discord.Member) -> discord.Embed:
    count = getattr(m.guild, "member_count", None)
    created = getattr(m, "created_at", None)
    young = bool(created and (discord.utils.utcnow() - created).days < NEW_ACCOUNT_DAYS)
    is_bot = bool(getattr(m, "bot", False))
    return field_card(Colors.success, "Bot Added" if is_bot else "Member Joined", subject=m, fields=[
        ("Member", m.mention, True),
        ("Account Created", f"{ts(created, 'D')} ({ts(created, 'R')})" if created else None, True),
        ("Member Count", f"#{count:,}" if count else None, True),
        ("Flag", f"New account ({short_age(created)} old)" if young and created else None, False),
    ], ids=[("User ID", m.id)])


def member_left(m: discord.Member, kicked_by=None, reason: str | None = None) -> discord.Embed:
    kicked = kicked_by is not None
    joined_at = getattr(m, "joined_at", None)
    roles = _dedup([r for r in reversed(list(getattr(m, "roles", []))) if not r.is_default()])
    count = getattr(m.guild, "member_count", None)
    if kicked:
        reason_text = shorten(reason, 300) if reason else "None"
        return field_card(Colors.warn, "Member Kicked", subject=m, fields=[
            ("Target", m.mention, True),
            ("Moderator", kicked_by.mention, True),
            ("Reason", reason_text, len(reason_text) <= 42),
            (f"Roles ({len(roles)})", role_pills(roles) if roles else None, False),
        ], ids=[("User ID", m.id)])
    return field_card(Colors.error, "Member Left", subject=m, fields=[
        ("Member", m.mention, True),
        ("Joined", ts(joined_at, "R") if joined_at else None, True),
        ("Members", f"{count:,}" if count else None, True),
        (f"Roles ({len(roles)})", role_pills(roles) if roles else None, False),
    ], ids=[("User ID", m.id)])


def _ban_card(color: int, title: str, user, mod, reason: str | None, clicked: bool) -> discord.Embed:
    reason_text = shorten(reason, 300) if reason else ("Log button" if clicked else "None")
    return field_card(color, title, subject=user, fields=[
        ("Target", user.mention, True),
        ("Moderator", mod.mention if mod else "Unknown", True),
        ("Reason", reason_text, len(reason_text) <= 42),
    ], ids=[("User ID", user.id)])


def user_banned(user, mod=None, reason: str | None = None, *, clicked: bool = False) -> discord.Embed:
    return _ban_card(Colors.error, "Member Banned", user, mod, reason, clicked)


def user_unbanned(user, mod=None, reason: str | None = None, *, clicked: bool = False) -> discord.Embed:
    return _ban_card(Colors.success, "Member Unbanned", user, mod, reason, clicked)


def nickname_changed(m: discord.Member, old: str | None, new: str | None, actor=None) -> discord.Embed:
    fmt_nick = lambda nick: f"`{nick}`" if nick else "None"  # noqa: E731
    other = actor if actor is not None and actor.id != m.id else None
    return field_card(SLATE, "Nickname Changed", subject=m, fields=[
        ("Member", m.mention, True),
        ("Changed By", other.mention if other else None, True),
        ("Nickname", f"{fmt_nick(old)} → {fmt_nick(new)}", False),
    ], ids=[("User ID", m.id)])


def _role_warnings(roles) -> str:
    warns = [f"{r.mention}: {', '.join(p.title() for p in dangerous(r))}" for r in _dedup(roles) if dangerous(r)]
    return "\n".join(warns)


def roles_changed(m: discord.Member, added: list, removed: list, actor=None, reason: str | None = None) -> discord.Embed:
    return roles_updated(m, added, removed, actor, reason)


def roles_updated(m: discord.Member, added: list, removed: list, actor=None, reason: str | None = None,
                  *, total: int | None = None) -> discord.Embed:
    added = _dedup(added)
    removed = _dedup(removed)
    strip = bool(removed) and not added and bool(total) and len(removed) >= max(3, total - 1)
    if strip:
        color, title = Colors.orange, "Member Stripped"
    elif added and not removed:
        color, title = Colors.success, "Roles Updated"
    elif removed and not added:
        color, title = Colors.error, "Roles Updated"
    else:
        color, title = SLATE, "Roles Updated"
    lead = None
    if strip and total is not None:
        left = max(0, total - len(removed))
        lead = (f"Removed **{plural(len(removed), 'role')}** from {m.mention} ({plural(left, 'role')} remaining)."
                if left else f"Removed all **{plural(len(removed), 'role')}** from {m.mention}.")
    return field_card(color, title, subject=m, lead=lead, fields=[
        ("Member", m.mention, True),
        ("Changed By", actor.mention if actor else None, True),
        (f"Added ({len(added)})", role_pills(added) if added else None, False),
        (f"Removed ({len(removed)})", role_pills(removed) if removed else None, False),
        ("Elevated Permissions", _role_warnings(added) or None, False),
        ("Reason", reason, False),
    ], ids=[("User ID", m.id)])


def member_stripped(m, removed: Iterable = (), actor=None, *, hint: str | None = None) -> discord.Embed:
    removed = _dedup(removed)
    return field_card(Colors.orange, "Member Stripped", subject=m, fields=[
        ("Member", m.mention, True),
        ("Moderator", actor.mention if actor else None, True),
        (f"Roles Removed ({len(removed)})" if removed else "Roles Removed", role_pills(removed) if removed else None, False),
        ("Recovery", hint, False),
    ], ids=[("User ID", m.id)])


def timed_out(m: discord.Member, until, actor=None, reason: str | None = None) -> discord.Embed:
    left = (until - discord.utils.utcnow()).total_seconds()
    dur = fmt_duration(round(left / 60) * 60 or left) if left > 0 else ""
    reason_text = shorten(reason, 300) if reason else "None"
    return field_card(Colors.warn, "Member Timed Out", subject=m, fields=[
        ("Target", m.mention, True),
        ("Moderator", actor.mention if actor else "Unknown", True),
        ("Duration", f"{dur} ({ts(until, 'R')})" if dur else ts(until, "R"), True),
        ("Reason", reason_text, False),
    ], ids=[("User ID", m.id)])


def timeout_removed(m: discord.Member, actor=None) -> discord.Embed:
    return field_card(Colors.success, "Timeout Removed", subject=m, fields=[
        ("Target", m.mention, True),
        ("Moderator", actor.mention if actor else None, True),
    ], ids=[("User ID", m.id)])


# ---------------------------------------------------------------- roles

def _role_facts(r, *more) -> str:
    color = getattr(getattr(r, "color", None), "value", 0)
    return " • ".join(str(x) for x in (
        hex_color(color) if color else "",
        f"Position {r.position}" if getattr(r, "position", None) is not None else "",
        "Hoisted" if getattr(r, "hoist", False) else "",
        "Mentionable" if getattr(r, "mentionable", False) else "",
        *more,
    ) if x)


def _server_header(actor, guild) -> tuple[str | None, str | None]:
    if actor is not None:
        return name_of(actor), avatar_of(actor)
    return getattr(guild, "name", None), _icon(guild)


def role_created(r: discord.Role, actor=None, reason: str | None = None) -> discord.Embed:
    warn = dangerous(r)
    header, icon = _server_header(actor, getattr(r, "guild", None))
    return field_card(r.color.value or Colors.success, "Role Created", header=header, icon=icon, fields=[
        ("Role", r.mention, True),
        ("Created By", actor.mention if actor else None, True),
        ("Properties", _role_facts(r) or None, True),
        ("Elevated Permissions", ", ".join(p.title() for p in warn) if warn else None, False),
        ("Reason", reason, False),
    ], ids=[("Role ID", r.id)])


def role_deleted(r: discord.Role, actor=None, reason: str | None = None) -> discord.Embed:
    members = getattr(r, "members", None)
    warn = dangerous(r)
    header, icon = _server_header(actor, getattr(r, "guild", None))
    member_note = plural(len(members), "member") if members else ""
    return field_card(Colors.error, "Role Deleted", header=header, icon=icon, fields=[
        ("Role", f"`@{r.name}`", True),
        ("Deleted By", actor.mention if actor else None, True),
        ("Properties", _role_facts(r, member_note) or None, True),
        ("Elevated Permissions", ", ".join(p.title() for p in warn) if warn else None, False),
        ("Reason", reason, False),
    ], ids=[("Role ID", r.id)])


def role_changes(before: discord.Role, after: discord.Role) -> list[str]:
    out = []
    if before.name != after.name:
        out.append(f"Name: `{before.name}` → `{after.name}`")
    if before.color != after.color:
        out.append(f"Color: `{hex_color(before.color.value)}` → `{hex_color(after.color.value)}`")
    if before.hoist != after.hoist:
        out.append(f"Hoisted: {'Yes' if after.hoist else 'No'}")
    if before.mentionable != after.mentionable:
        out.append(f"Mentionable: {'Yes' if after.mentionable else 'No'}")
    if before.permissions != after.permissions:
        gained = [p.replace("_", " ").title() for p, v in after.permissions if v and not getattr(before.permissions, p)]
        lost = [p.replace("_", " ").title() for p, v in before.permissions if v and not getattr(after.permissions, p)]
        if gained:
            out.append("Permissions Added: " + ", ".join(gained))
        if lost:
            out.append("Permissions Removed: " + ", ".join(lost))
    return out


def _changes(changes: list[str]) -> list[tuple[str, str, bool]]:
    out: list[tuple[str, str, bool]] = []
    for change in changes:
        label, sep, rest = change.partition(": ")
        out.append((label, rest, False) if sep else ("Change", change, False))
    return out


def role_updated(after: discord.Role, changes: list[str], actor=None) -> discord.Embed:
    header, icon = _server_header(actor, getattr(after, "guild", None))
    return field_card(after.color.value or SLATE, "Role Updated", header=header, icon=icon, fields=[
        ("Role", after.mention, True),
        ("Updated By", actor.mention if actor else None, True),
        *_changes(changes),
    ], ids=[("Role ID", after.id)])


# ---------------------------------------------------------------- channels and server

_CHANNEL_KINDS = ((discord.CategoryChannel, "Category"), (discord.ForumChannel, "Forum"),
                  (discord.StageChannel, "Stage Channel"), (discord.VoiceChannel, "Voice Channel"),
                  (discord.TextChannel, "Text Channel"))


def channel_kind(c) -> str:
    return next((label for cls, label in _CHANNEL_KINDS if isinstance(c, cls)), "Channel")


def _hash(c) -> str:
    return "" if isinstance(c, discord.CategoryChannel) else "#"


def _gone(name: str) -> str:
    return f"`{name.replace('`', chr(0x2018))}`"


def channel_created(c, actor=None, reason: str | None = None) -> discord.Embed:
    category = getattr(c, "category", None)
    slow = getattr(c, "slowmode_delay", 0) or 0
    topic = getattr(c, "topic", None)
    header, icon = _server_header(actor, getattr(c, "guild", None))
    top_row: list[tuple[str, object, bool]] = [("Channel", c.mention, True)]
    if actor is not None:
        top_row.append(("Created By", actor.mention, True))
    else:
        top_row.append(("Type", channel_kind(c), True))
    top_row.append(("Category", category.name if category else "None", True))
    extra: list[tuple[str, object, bool]] = []
    if actor is not None:
        extra.append(("Type", channel_kind(c) + (f" • {slow}s slowmode" if slow else ""), False))
    elif slow:
        extra.append(("Slowmode", f"{slow}s", False))
    if getattr(c, "nsfw", False):
        extra.append(("Age-Restricted", "Yes", False))
    if topic:
        extra.append(("Topic", shorten(topic, 200), False))
    if reason:
        extra.append(("Reason", reason, False))
    return field_card(Colors.success, "Channel Created", header=header, icon=icon,
                      fields=[*top_row, *extra], ids=[("Channel ID", c.id)])


def channel_deleted(c, actor=None, reason: str | None = None) -> discord.Embed:
    category = getattr(c, "category", None)
    header, icon = _server_header(actor, getattr(c, "guild", None))
    top_row: list[tuple[str, object, bool]] = [("Channel", _gone(f"{_hash(c)}{c.name}"), True)]
    if actor is not None:
        top_row.append(("Deleted By", actor.mention, True))
    else:
        top_row.append(("Type", channel_kind(c), True))
    top_row.append(("Category", category.name if category else "None", True))
    return field_card(Colors.error, "Channel Deleted", header=header, icon=icon,
                      fields=[*top_row, ("Reason", reason, False)], ids=[("Channel ID", c.id)])


def channel_changes(before, after) -> list[str]:
    out = []
    if before.name != after.name:
        out.append(f"Name: `{_hash(before)}{before.name}` → `{_hash(after)}{after.name}`")
    if getattr(before, "category", None) != getattr(after, "category", None):
        old, new = getattr(before, "category", None), getattr(after, "category", None)
        out.append(f"Category: {old.name if old else 'None'} → {new.name if new else 'None'}")
    if getattr(before, "topic", None) != getattr(after, "topic", None):
        out.append(f"Topic: `{shorten(before.topic or 'None', 80)}` → `{shorten(after.topic or 'None', 80)}`")
    if getattr(before, "slowmode_delay", None) != getattr(after, "slowmode_delay", None):
        out.append(f"Slowmode: `{before.slowmode_delay}s` → `{after.slowmode_delay}s`")
    if getattr(before, "nsfw", None) != getattr(after, "nsfw", None):
        out.append(f"Age-Restricted: {'Yes' if after.nsfw else 'No'}")
    if getattr(before, "user_limit", None) != getattr(after, "user_limit", None):
        out.append(f"User Limit: `{before.user_limit or 'Unlimited'}` → `{after.user_limit or 'Unlimited'}`")
    overwrites_before, overwrites_after = before.overwrites, after.overwrites
    touched = [t for t in {*overwrites_before, *overwrites_after} if overwrites_before.get(t) != overwrites_after.get(t)]
    if touched:
        out.append("Permission Overwrites: " + role_pills(touched, 450))
    return out


def channel_updated(after, changes: list[str], actor=None) -> discord.Embed:
    header, icon = _server_header(actor, getattr(after, "guild", None))
    return field_card(SLATE, "Channel Updated", header=header, icon=icon, fields=[
        ("Channel", after.mention, True),
        ("Updated By", actor.mention if actor else None, True),
        *_changes(changes),
    ], ids=[("Channel ID", after.id)])


def server_updated(after: discord.Guild, changes: list[str], actor=None) -> discord.Embed:
    header, icon = _server_header(actor, after)
    return field_card(SLATE, "Server Updated", header=header, icon=icon, fields=[
        ("Updated By", actor.mention if actor else None, True),
        *_changes(changes),
    ], ids=[("Server ID", after.id)])


def server_changes(before: discord.Guild, after: discord.Guild) -> list[str]:
    out = []
    if before.name != after.name:
        out.append(f"Name: `{before.name}` → `{after.name}`")
    if before.vanity_url_code != after.vanity_url_code:
        out.append(f"Vanity URL: `{before.vanity_url_code or 'None'}` → `{after.vanity_url_code or 'None'}`")
    if before.icon != after.icon:
        out.append("Icon: Updated")
    if before.banner != after.banner:
        out.append("Banner: Updated")
    if before.verification_level != after.verification_level:
        out.append(f"Verification: `{before.verification_level.name.title()}` → `{after.verification_level.name.title()}`")
    return out


def _invite_limits(inv) -> tuple[str, str]:
    uses = str(inv.max_uses) if inv.max_uses else "Unlimited"
    if not inv.max_age:
        return uses, "Never"
    return uses, fmt_duration(inv.max_age)


def invite_created(inv: discord.Invite) -> discord.Embed:
    uses, expires = _invite_limits(inv)
    inviter = getattr(inv, "inviter", None)
    channel = getattr(inv, "channel", None)
    header, icon = _server_header(inviter, getattr(inv, "guild", None))
    limits = f"{uses} uses • Expires in {expires}" if expires != "Never" else f"{uses} uses • Never expires"
    if uses == "Unlimited":
        limits = f"Unlimited uses • Expires in {expires}" if expires != "Never" else "Unlimited uses • Never expires"
    if getattr(inv, "temporary", False):
        limits += " • Temporary"
    return field_card(Colors.success, "Invite Created", header=header, icon=icon, fields=[
        ("Code", f"`{inv.code}`", True),
        ("Channel", channel.mention if channel else None, True),
        ("Created By", inviter.mention if inviter else None, True),
        ("Limits", limits, False),
    ], ids=[("Invite Code", inv.code)])


def invite_deleted(inv: discord.Invite) -> discord.Embed:
    inviter = getattr(inv, "inviter", None)
    uses = getattr(inv, "uses", None)
    channel = getattr(inv, "channel", None)
    header, icon = _server_header(inviter, getattr(inv, "guild", None))
    return field_card(Colors.error, "Invite Deleted", header=header, icon=icon, fields=[
        ("Code", f"`{inv.code}`", True),
        ("Channel", channel.mention if channel else None, True),
        ("Uses", str(uses) if uses is not None else None, True),
        ("Created By", inviter.mention if inviter else None, False),
    ], ids=[("Invite Code", inv.code)])


def emojis_changed(guild: discord.Guild, added: list, removed: list) -> discord.Embed:
    return field_card(SLATE, "Emojis Updated", header=guild.name, icon=_icon(guild), fields=[
        (f"Added ({len(added)})", shorten_md(" ".join(f"{e} `:{e.name}:`" for e in added[:20]), 1000) if added else None, False),
        (f"Removed ({len(removed)})", shorten_md(" ".join(f"`:{e.name}:`" for e in removed[:20]), 1000) if removed else None, False),
    ], ids=[("Server ID", guild.id)])


# ---------------------------------------------------------------- bursts (many of the same thing at once)

_GROUP_WORDS = {
    "role_create": ("Role", "Roles Created", "Created By", Colors.success, True),
    "role_delete": ("Role", "Roles Deleted", "Deleted By", Colors.error, False),
    "channel_create": ("Channel", "Channels Created", "Created By", Colors.success, True),
    "channel_delete": ("Channel", "Channels Deleted", "Deleted By", Colors.error, False),
}


def grouped(kind: str, items: list, actor=None, reason: str | None = None) -> discord.Embed:
    noun, title, by_label, color, alive = _GROUP_WORDS[kind]
    guild = getattr(items[0], "guild", None)
    header, icon = _server_header(actor, guild)
    if alive:
        listing = role_pills(items, 1000)
    else:
        shown = [f"`{_hash(o) if noun == 'Channel' else '@'}{o.name}`" for o in items[:GROUP_SHOWN]]
        more = f"  ·  +{len(items) - GROUP_SHOWN} more" if len(items) > GROUP_SHOWN else ""
        listing = shorten_md(", ".join(shown) + more, 1000)
    return field_card(color, title, header=header, icon=icon, fields=[
        (by_label, actor.mention if actor else None, True),
        ("Count", str(len(items)), True),
        (f"{noun}s ({len(items)})", listing, False),
        ("Reason", reason, False),
    ])


# ---------------------------------------------------------------- voice and commands

def voice_embed(m: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> discord.Embed | None:
    """Structured voice state card matching the rest of the server log channels."""
    if before.channel != after.channel:
        if not before.channel:
            return field_card(Colors.success, "Voice Joined", subject=m, fields=[
                ("Member", m.mention, True),
                ("Channel", after.channel.mention, True),
            ], ids=[("User ID", m.id)])
        if not after.channel:
            return field_card(SLATE, "Voice Left", subject=m, fields=[
                ("Member", m.mention, True),
                ("Channel", before.channel.mention, True),
            ], ids=[("User ID", m.id)])
        return field_card(SLATE, "Voice Moved", subject=m, fields=[
            ("Member", m.mention, True),
            ("From", before.channel.mention, True),
            ("To", after.channel.mention, True),
        ], ids=[("User ID", m.id)])
    for attr, on_title, off_title in (("mute", "Voice Server Muted", "Voice Server Unmuted"),
                                      ("deaf", "Voice Server Deafened", "Voice Server Undeafened")):
        if getattr(before, attr) != getattr(after, attr):
            now_on = getattr(after, attr)
            where = after.channel or before.channel
            return field_card(Colors.warn if now_on else Colors.success,
                              on_title if now_on else off_title, subject=m, fields=[
                                  ("Member", m.mention, True),
                                  ("Channel", where.mention if where else None, True),
                              ], ids=[("User ID", m.id)])
    return None


def voice_changed(m: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> str | None:
    """Plain-text fallback line for voice events with no duplicate speaker emojis."""
    if before.channel != after.channel:
        if not before.channel:
            return feed_line("", f"{m.mention} joined {after.channel.mention}")
        if not after.channel:
            return feed_line("", f"{m.mention} left {before.channel.mention}")
        return feed_line("", f"{m.mention} moved {before.channel.mention} → {after.channel.mention}")
    for attr, on, off in (("mute", "was server muted", "was server unmuted"),
                          ("deaf", "was server deafened", "was server undeafened")):
        if getattr(before, attr) != getattr(after, attr):
            now_on = getattr(after, attr)
            where = f" in {after.channel.mention}" if after.channel else ""
            return feed_line("", f"{m.mention} {on if now_on else off}{where}")
    return None


def _took(ms: float | None) -> str:
    if ms is None:
        return ""
    return f"{ms / 1000:.1f}s" if ms >= 1000 else f"{ms:.0f} ms"


def _command_text(ctx) -> str:
    """Command string safe to display to server staff."""
    command = getattr(ctx, "command", None)
    prefix = getattr(ctx, "prefix", "") or ""
    action = getattr(ctx, "panel_action", None)
    if action:
        return f"{action} ({prefix}{getattr(ctx, 'panel_name', None) or 'panel'} panel)"
    if getattr(command, "qualified_name", None) == "gv":
        words = ctx.message.content.split()
        if len(words) > 1 and words[1].lower() in ("dev", "network"):
            return f"{prefix}gv {words[1].lower()} · arguments hidden"
    if command is not None and (getattr(command, "hidden", False) or getattr(command, "cog_name", None) == "Owner"):
        return f"{prefix}{command.qualified_name} · arguments hidden"
    return redact.scrub(ctx.message.content.strip()).replace("\n", " ").replace("`", "'")


def command_used(ctx, permission: str, granted_by: str, *, rank: int | None = None, ms: float | None = None,
                 error: str | None = None) -> discord.Embed:
    """Compact structured command audit card with zero emoji clutter."""
    jump = getattr(ctx, "panel_jump", None) if getattr(ctx, "panel_action", None) else None
    jump = jump or getattr(ctx.message, "jump_url", None)
    failed = error is not None
    warning = bool(getattr(ctx, "command_log_warning", False))
    command = shorten(_command_text(ctx).replace("`", "'"), 900)
    level = shorten(str(permission).replace("\n", " "), 120) if permission and permission != "None" else "None"
    raw_grant = redact.scrub(str(granted_by or "")).replace("\n", " ").strip()
    if raw_grant in ("", "-", "Not assigned"):
        grant = "—"
    elif raw_grant == "Permissions attached to user":
        grant = "Direct"
    else:
        grant = shorten(raw_grant, 160)
    channel = shorten(ctx.channel.mention, 100) + (f" ([Jump]({shorten(str(jump), 500)}))" if jump else "")
    took = _took(ms)

    detail_field: tuple[str, str | None, bool] = ("Outcome", None, False)
    if failed:
        cleaned_err = shorten_md(strip_lead_emoji(redact.scrub(error).replace("\n", " ")), 400)
        detail_field = ("Error", cleaned_err, False)
    elif getattr(ctx, "command_log_outcome", None):
        cleaned_out = shorten_md(strip_lead_emoji(redact.scrub(str(ctx.command_log_outcome)).replace("\n", " ")), 400)
        if took:
            cleaned_out = f"{cleaned_out} ({took})"
        detail_field = ("Outcome", cleaned_out, False)

    footer_ids: list[tuple[str, object]] = [("User ID", ctx.author.id)]
    if took and not failed and not getattr(ctx, "command_log_outcome", None):
        footer_ids.append(("", took))

    title = "Command Failed" if failed else "Command Warning" if warning else "Command Executed"
    color = Colors.deny if failed else Colors.caution if warning else Colors.approve
    return field_card(color, title, subject=ctx.author, fields=[
        ("Command", f"`{command}`", False),
        ("Channel", channel, True),
        ("Permission", level, True),
        ("Granted By", grant, True),
        detail_field,
    ], ids=footer_ids)


# ---------------------------------------------------------------- moderation cases

NO_CASE_NUMBER = {"ban", "unban", "softban"}


def case_card(num: int, action: str, target, moderator, reason: str | None) -> discord.Embed:
    named = target if hasattr(target, "mention") and hasattr(target, "name") else None
    who = named.mention if named else f"<@{target.id}>"
    reason_text = shorten(reason, 300) if reason else "None"
    label = action.title()
    if action.lower() in NO_CASE_NUMBER:
        return field_card(Colors.main, label, subject=named, fields=[
            ("Target", who, True),
            ("Moderator", moderator.mention if moderator else "Unknown", True),
            ("Reason", reason_text, len(reason_text) <= 42),
        ], ids=[("User ID", target.id)])
    return field_card(Colors.main, f"Case #{num} — {label}", subject=named, fields=[
        ("Target", who, True),
        ("Moderator", moderator.mention if moderator else "Unknown", True),
        ("Reason", reason_text, len(reason_text) <= 42),
    ], ids=[("User ID", target.id), ("Case", f"#{num}")])
