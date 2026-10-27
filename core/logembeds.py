"""Log cards: one clean shape for every event, built in one place so the logs look alike and can be tested.

Every card reads the same way, top to bottom:

    [avatar]  Role deleted · testnuke-role-1        <- header: what happened, and to what/whom
    by @Veil Utilities                              <- who, where (mentions, so they're clickable)
    > Antinuke test (,testnuke)                     <- the reason or the message text, quoted, when there is one
    -# position 1 · was shown separately           <- small grey details
    Role ID: 1564424937685393469 · Today at 4:46 PM <- IDs live in the footer only, next to Discord's own time

No field grids, no thumbnails, no IDs in code blocks: the header says what it is, the body says who and why. The
header's icon is the face that matters: the member a member event is about, or whoever did it for roles, channels
and the server. When the audit log doesn't say who did something, the card just doesn't name anyone.

The cog (cogs/logging.py) decides where each card goes; everything here returns a discord.Embed.
"""
from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Iterable

import discord

from core import redact
from core.utils import Colors, fmt_duration, shorten, shorten_md, ts

AUDIT_SETTLE = 0.6  # seconds to let Discord write the audit-log entry before we look for it
AUDIT_WINDOW = 15.0  # an entry older than this isn't about the event we just saw
QUOTE_LIMIT = 900
LIST_LIMIT = 950  # characters kept for a list before "+N more"
NEW_ACCOUNT_DAYS = 7
GROUP_SHOWN = 25  # names listed on a grouped card before "+N more"
# permissions worth a warning when a role that has them is handed out
DANGEROUS = ("administrator", "manage_guild", "manage_roles", "manage_channels", "manage_webhooks", "ban_members",
             "kick_members", "mention_everyone", "moderate_members", "manage_messages")


# ---------------------------------------------------------------- building blocks

def name_of(user) -> str:
    """`Jacob (ham6600y)` when the display name differs from the username, else just the username."""
    display = getattr(user, "display_name", None) or user.name
    return display if display == user.name else f"{display} ({user.name})"


def avatar_of(user) -> str | None:
    asset = getattr(user, "display_avatar", None)
    return getattr(asset, "url", None)


def _icon(guild) -> str | None:
    icon = getattr(guild, "icon", None)
    return getattr(icon, "url", None)


def quote(text: str | None, limit: int = QUOTE_LIMIT) -> str:
    """Markdown block quote, cut to `limit` characters."""
    text = shorten((text or "").replace("\r", ""), limit)
    return "\n".join(f"> {line}" if line else ">" for line in text.split("\n"))


def mentions(items: Iterable, limit: int = 900) -> str:
    return shorten_md(", ".join(i.mention for i in items), limit)


def footer(ids: Iterable[tuple[str, object]]) -> str:
    return " · ".join(f"{label}: {value}" for label, value in ids)


def bullets(lines: list[str], limit: int = LIST_LIMIT) -> str:
    """One line each, cut with "+N more" instead of overflowing."""
    out, used = [], 0
    for i, line in enumerate(lines):
        if used + len(line) + 1 > limit - 16:
            out.append(f"*+{len(lines) - i} more*")
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)


def small(*parts) -> str:
    """A grey detail line (`-# a · b · c`); empty parts are dropped, nothing left means no line."""
    kept = [str(p) for p in parts if p]
    return "-# " + " · ".join(kept) if kept else ""


def by(actor, verb: str = "by") -> str:
    """`by @mod`, or nothing when the audit log didn't say who."""
    return f"{verb} {actor.mention}" if actor is not None else ""


def joined(*parts) -> str:
    """Parts of one line, joined with ` · `, empty ones left out."""
    return " · ".join(str(p) for p in parts if p)


def person(user) -> str:
    """`@mention` and the username, for places where a mention alone might not resolve."""
    if user is None:
        return "*unknown*"
    return f"{user.mention} (`{user.name}`)"


def when(dt) -> str:
    """`Oct 26, 2026 (3 days ago)`."""
    return f"{ts(dt, 'D')} ({ts(dt, 'R')})"


def age_of(dt) -> str:
    return fmt_duration((discord.utils.utcnow() - dt).total_seconds())


def short_age(dt) -> str:
    """The biggest unit only: `3 days`, `2 hours`, `40 seconds`."""
    return age_of(dt).split(", ")[0]


def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def hex_color(value: int) -> str:
    return f"#{value:06X}" if value else "default"


def dangerous(role) -> list[str]:
    """Permission names on a role that deserve a warning (just `administrator` when it has that)."""
    perms = getattr(role, "permissions", None)
    if perms is None:
        return []
    if getattr(perms, "administrator", False):
        return ["administrator"]
    return [p.replace("_", " ") for p in DANGEROUS if getattr(perms, p, False)]


def log_card(color: int, header: str, *, icon: str | None = None, lines: Iterable[str] = (),
             ids: Iterable[tuple] = ()) -> discord.Embed:
    """The one shape every log uses: header (with an icon), body lines, IDs in the footer, Discord's timestamp."""
    body = "\n".join(line for line in lines if line)
    embed = discord.Embed(color=color, description=shorten(body, 4000) if body else None,
                          timestamp=discord.utils.utcnow())
    embed.set_author(name=shorten(header, 256), icon_url=icon)
    ids = list(ids)
    if ids:
        embed.set_footer(text=shorten(footer(ids), 2048))
    return embed


def card(color: int, subject=None, text: str = "", *, ids: Iterable[tuple] = (), header: str | None = None
         ) -> discord.Embed:
    """A one-line note (e.g. `,logs test`), in the same shape as the logs."""
    title = header or (name_of(subject) if subject is not None else "Veil Utilities")
    return log_card(color, title, icon=avatar_of(subject) if subject is not None else None, lines=[text], ids=ids)


_ON_BEHALF = re.compile(r"^(?:(?P<what>.+?) by )?(?P<name>.+?) \((?P<id>\d{15,21})\)(?:: (?P<reason>.*))?$", re.S)


def on_behalf(guild, actor, reason: str | None):
    """The bot did it because someone ran a command (`,timeout`, `,ban`...): credit that person, and keep only what
    they wrote as the reason. The audit reason says who (see core.utils.audit_reason); anything else is left alone."""
    me = getattr(guild, "me", None)
    if actor is None or me is None or getattr(actor, "id", None) != me.id or not reason:
        return actor, reason
    match = _ON_BEHALF.match(reason)
    member = guild.get_member(int(match["id"])) if match and hasattr(guild, "get_member") else None
    if member is None:
        return actor, reason
    said = (match["reason"] or "").strip()
    said = "" if said == "None" else said  # older commands wrote "name: None" when no reason was given
    what = (match["what"] or "").strip()
    return member, (f"{what}: {said}" if what and said else what or said or None)


async def audit_actor(guild, action: discord.AuditLogAction, target_id: int, *,
                      check: Callable | None = None) -> tuple[discord.abc.User | None, str | None]:
    """Who caused an event, from the audit log: `(user, reason)`, or `(None, None)` when we can't tell."""
    me = getattr(guild, "me", None)
    if not me or not getattr(me.guild_permissions, "view_audit_log", False):
        return None, None
    if AUDIT_SETTLE:
        await asyncio.sleep(AUDIT_SETTLE)
    try:
        async for entry in guild.audit_logs(limit=6, action=action):
            if getattr(entry.target, "id", None) != target_id:
                continue
            if (discord.utils.utcnow() - entry.created_at).total_seconds() > AUDIT_WINDOW:
                continue
            if check is not None and not check(entry):
                continue
            return on_behalf(guild, entry.user, entry.reason)
    except Exception:  # missing access, rate limit, library quirks: the log is still worth sending
        pass
    return None, None


# ---------------------------------------------------------------- the two shapes
#
# A card (bans, joins, roles, channels...): the person's avatar and name on top, one bold sentence that says what
# happened with clickable mentions, then labelled lines (**Reason:** ...), IDs in the footer and Discord's own time.
# A line (commands, edits, voice): one short text line in the log channel, like Discord's own audit log; the cog
# gathers lines that arrive close together into one message.

def entry_card(color: int, sentence: str, *, subject=None, icon: str | None = None,
               details: Iterable[tuple[str, object]] = (), quoted: str | None = None,
               ids: Iterable[tuple] = (), header: str | None = None) -> discord.Embed:
    """`sentence` in bold, the quoted text (a message, a reason) under it, then `**Label:** value` lines."""
    lines = [f"**{sentence}**"]
    if quoted:
        lines.append(quote(quoted))
    lines += [f"**{label}:** {value}" for label, value in details if value not in (None, "")]
    embed = discord.Embed(color=color, description=shorten_md("\n".join(lines), 4000),
                          timestamp=discord.utils.utcnow())
    name = header or (name_of(subject) if subject is not None else None)
    if name:
        embed.set_author(name=shorten(name, 256), icon_url=icon or (avatar_of(subject) if subject is not None else None))
    ids = list(ids)
    if ids:
        embed.set_footer(text=shorten(footer(ids), 2048))
    return embed


def feed_line(emoji: str, text: str) -> str:
    """One audit-feed entry: `<t:..:t> ⌨️ @x ran ,ban in #general`. Sent with mentions switched off."""
    return shorten_md(f"{ts(discord.utils.utcnow(), 't')} {emoji} {text}", 1900)


def _by_text(actor) -> str:
    return f" by {actor.mention}" if actor is not None else ""


def _age(dt) -> str:
    return f"{ts(dt, 'R')} ({ts(dt, 'D')})" if dt else ""


def _account(user) -> str:
    created = getattr(user, "created_at", None)
    if not created:
        return ""
    young = (discord.utils.utcnow() - created).days < NEW_ACCOUNT_DAYS
    return f"created {_age(created)}" + (" ⚠️ **new account**" if young else "")


# ---------------------------------------------------------------- messages

def _attachments(m) -> str:
    files = list(getattr(m, "attachments", None) or [])
    if not files:
        return ""
    shown = " · ".join(f"[{a.filename}]({a.url})" for a in files[:5])
    return shown + (f" +{len(files) - 5} more" if len(files) > 5 else "")


def message_deleted(m: discord.Message, actor=None) -> discord.Embed:
    deleter = actor if actor is not None and actor.id != m.author.id else None
    sent = getattr(m, "created_at", None)
    return entry_card(Colors.error, f"Message sent by {m.author.mention} deleted in {m.channel.mention}",
                      subject=m.author, quoted=m.content or "[no text: embed or attachments only]", details=[
                          ("Deleted by", deleter.mention if deleter else ""),
                          ("Sent", ts(sent, "R") if sent else ""),
                          ("Attachments", _attachments(m)),
                      ], ids=[("User ID", m.author.id), ("Message ID", m.id)])


def message_edited(before: discord.Message, after: discord.Message) -> str:
    """A feed line: who edited what where, before → after (short), and a jump link."""
    def cut(text):
        text = (text or "").replace("\n", " ").replace("`", "'")
        return f"`{shorten(text, 80)}`" if text else "*empty*"
    return feed_line("✏️", f"{after.author.mention} edited a message in {after.channel.mention}: "
                           f"{cut(before.content)} → {cut(after.content)} · [jump]({after.jump_url})")


def bulk_deleted(messages: list[discord.Message]) -> discord.Embed:
    channel = messages[0].channel
    counts: dict[int, list] = {}
    for m in messages:
        counts.setdefault(m.author.id, [m.author, 0])[1] += 1
    top = sorted(counts.values(), key=lambda pair: -pair[1])
    authors = ", ".join(f"{a.mention} ×{n}" for a, n in top[:8]) + (f" +{len(top) - 8} more" if len(top) > 8 else "")
    times = [m.created_at for m in messages if getattr(m, "created_at", None)]
    return entry_card(Colors.error, f"{plural(len(messages), 'message')} deleted in {channel.mention}",
                      header=f"#{getattr(channel, 'name', '?')}", icon=_icon(getattr(channel, "guild", None)),
                      details=[("From", authors),
                               ("Sent", f"{ts(min(times), 'f')} – {ts(max(times), 't')}" if times else ""),
                               ("Transcript", "attached")],
                      ids=[("Channel ID", channel.id)])


def transcript(messages: list[discord.Message]) -> str:
    return "\n".join(f"[{m.created_at:%Y-%m-%d %H:%M}] {m.author}: {m.content}"
                     for m in sorted(messages, key=lambda x: x.id))


# ---------------------------------------------------------------- members

def member_joined(m: discord.Member) -> discord.Embed:
    count = getattr(m.guild, "member_count", None)
    return entry_card(Colors.success, f"{m.mention} joined the server", subject=m, details=[
        ("Member", f"#{count:,}" if count else ""), ("Account", _account(m)),
        ("Bot", "yes" if getattr(m, "bot", False) else ""),
    ], ids=[("User ID", m.id)])


def member_left(m: discord.Member, kicked_by=None, reason: str | None = None) -> discord.Embed:
    kicked = kicked_by is not None
    joined_at = getattr(m, "joined_at", None)
    roles = [r for r in getattr(m, "roles", []) if not r.is_default()]
    count = getattr(m.guild, "member_count", None)
    sentence = f"{m.mention} was kicked{_by_text(kicked_by)}" if kicked else f"{m.mention} left the server"
    return entry_card(Colors.warn if kicked else Colors.error, sentence, subject=m, details=[
        ("Reason", reason if kicked and reason else ""),
        ("Joined", f"{ts(joined_at, 'R')} (was here {short_age(joined_at)})" if joined_at else ""),
        ("Roles", mentions(reversed(roles), 700) if roles else ""),
        ("Members now", f"{count:,}" if count and not kicked else ""),
    ], ids=[("User ID", m.id)])


def _ban_card(color: int, word: str, user, mod, reason: str | None, clicked: bool) -> discord.Embed:
    return entry_card(color, f"{user.mention} was {word}{_by_text(mod)}", subject=user, details=[
        ("Reason", reason or ("" if clicked else "no reason given")),
        ("How", "the button on the ban log" if clicked else ""),
        ("Account", _account(user)),
    ], ids=[("User ID", user.id)])


def user_banned(user, mod=None, reason: str | None = None, *, clicked: bool = False) -> discord.Embed:
    return _ban_card(Colors.error, "banned", user, mod, reason, clicked)


def user_unbanned(user, mod=None, reason: str | None = None, *, clicked: bool = False) -> discord.Embed:
    return _ban_card(Colors.success, "unbanned", user, mod, reason, clicked)


def nickname_changed(m: discord.Member, old: str | None, new: str | None, actor=None) -> discord.Embed:
    show = lambda nick: f"`{nick}`" if nick else "*none*"  # noqa: E731
    other = actor if actor is not None and actor.id != m.id else None
    return entry_card(Colors.blurple, f"{m.mention}'s nickname was changed{_by_text(other)}", subject=m,
                      details=[("Before", show(old)), ("After", show(new))], ids=[("User ID", m.id)])


def _role_warnings(roles) -> str:
    warns = [f"{r.mention} has {', '.join(dangerous(r))}" for r in roles if dangerous(r)]
    return "; ".join(warns)


def roles_changed(m: discord.Member, added: list, removed: list, actor=None, reason: str | None = None) -> discord.Embed:
    if added and not removed:
        color, verb = Colors.success, "was given roles"
    elif removed and not added:
        color, verb = Colors.error, "had roles taken"
    else:
        color, verb = Colors.blurple, "had their roles changed"
    return entry_card(color, f"{m.mention} {verb}{_by_text(actor)}", subject=m, details=[
        ("Added", mentions(added) if added else ""), ("Removed", mentions(removed) if removed else ""),
        ("Reason", reason or ""), ("⚠️ Dangerous", _role_warnings(added)),
    ], ids=[("User ID", m.id)])


def timed_out(m: discord.Member, until, actor=None, reason: str | None = None) -> discord.Embed:
    left = (until - discord.utils.utcnow()).total_seconds()
    return entry_card(Colors.warn, f"{m.mention} was timed out{_by_text(actor)}", subject=m, details=[
        ("Duration", fmt_duration(round(left / 60) * 60 or left) if left > 0 else ""),
        ("Ends", f"{ts(until, 'f')} ({ts(until, 'R')})"), ("Reason", reason or "no reason given"),
    ], ids=[("User ID", m.id)])


def timeout_removed(m: discord.Member, actor=None) -> discord.Embed:
    return entry_card(Colors.success, f"{m.mention}'s timeout was removed{_by_text(actor)}", subject=m,
                      ids=[("User ID", m.id)])


# ---------------------------------------------------------------- roles

def _role_facts(r, *more) -> str:
    color = getattr(getattr(r, "color", None), "value", 0)
    return " · ".join(str(x) for x in (
        f"colour {hex_color(color)}" if color else "",
        f"position {r.position}" if getattr(r, "position", None) is not None else "",
        "shown separately" if getattr(r, "hoist", False) else "",
        "mentionable" if getattr(r, "mentionable", False) else "", *more) if x)


def _who(actor, guild) -> str | None:
    """The header icon for server-object events: whoever did it, else the server's icon."""
    return avatar_of(actor) if actor is not None else _icon(guild)


def _actor_header(actor) -> str | None:
    return name_of(actor) if actor is not None else None


def role_created(r: discord.Role, actor=None, reason: str | None = None) -> discord.Embed:
    warn = dangerous(r)
    return entry_card(r.color.value or Colors.success, f"The role {r.mention} was created{_by_text(actor)}",
                      header=_actor_header(actor) or r.name, icon=_who(actor, r.guild), details=[
                          ("Details", _role_facts(r)), ("⚠️ Dangerous", ", ".join(warn) if warn else ""),
                          ("Reason", reason or ""),
                      ], ids=[("Role ID", r.id)])


def role_deleted(r: discord.Role, actor=None, reason: str | None = None) -> discord.Embed:
    members = getattr(r, "members", None)
    warn = dangerous(r)
    return entry_card(Colors.error, f"The role {_gone('@' + r.name)} was deleted{_by_text(actor)}",
                      header=_actor_header(actor) or r.name, icon=_who(actor, r.guild), details=[
                          ("Details", _role_facts(r, f"{plural(len(members), 'member')} had it" if members else "")),
                          ("⚠️ Dangerous", ", ".join(warn) if warn else ""), ("Reason", reason or ""),
                      ], ids=[("Role ID", r.id)])


def role_changes(before: discord.Role, after: discord.Role) -> list[str]:
    out = []
    if before.name != after.name:
        out.append(f"Name: `{before.name}` → `{after.name}`")
    if before.color != after.color:
        out.append(f"Colour: `{hex_color(before.color.value)}` → `{hex_color(after.color.value)}`")
    if before.hoist != after.hoist:
        out.append(f"Shown separately: `{'yes' if after.hoist else 'no'}`")
    if before.mentionable != after.mentionable:
        out.append(f"Mentionable: `{'yes' if after.mentionable else 'no'}`")
    if before.permissions != after.permissions:
        gained = [p.replace("_", " ") for p, v in after.permissions if v and not getattr(before.permissions, p)]
        lost = [p.replace("_", " ") for p, v in before.permissions if v and not getattr(after.permissions, p)]
        if gained:
            out.append("Permissions added: " + ", ".join(f"`{p}`" for p in gained))
        if lost:
            out.append("Permissions removed: " + ", ".join(f"`{p}`" for p in lost))
    return out


def _changes(changes: list[str]) -> list[tuple[str, str]]:
    """`Name: a → b` lines become labelled lines (`**Name:** a → b`)."""
    out = []
    for change in changes:
        label, sep, rest = change.partition(": ")
        out.append((label, rest) if sep else ("Change", change))
    return out


def role_updated(after: discord.Role, changes: list[str], actor=None) -> discord.Embed:
    warn = dangerous(after)
    return entry_card(after.color.value or Colors.blurple, f"The role {after.mention} was updated{_by_text(actor)}",
                      header=_actor_header(actor) or after.name, icon=_who(actor, after.guild),
                      details=_changes(changes) + [("⚠️ Dangerous now", ", ".join(warn) if warn else "")],
                      ids=[("Role ID", after.id)])


# ---------------------------------------------------------------- channels and server

_CHANNEL_KINDS = ((discord.CategoryChannel, "Category"), (discord.ForumChannel, "Forum"),
                  (discord.StageChannel, "Stage channel"), (discord.VoiceChannel, "Voice channel"),
                  (discord.TextChannel, "Text channel"))


def channel_kind(c) -> str:
    return next((label for cls, label in _CHANNEL_KINDS if isinstance(c, cls)), "Channel")


def _hash(c) -> str:
    return "" if isinstance(c, discord.CategoryChannel) else "#"


def _gone(name: str) -> str:
    """A deleted channel or role (no mention left to show) as `code`, which reads cleanly inside a bold sentence."""
    return f"`{name.replace('`', chr(0x2018))}`"


def _channel_details(c) -> list[tuple[str, str]]:
    category = getattr(c, "category", None)
    slow = getattr(c, "slowmode_delay", 0) or 0
    topic = getattr(c, "topic", None)
    return [("Type", channel_kind(c)), ("Category", category.name if category else "none"),
            ("Topic", shorten(topic, 200) if topic else ""), ("Slowmode", f"{slow}s" if slow else ""),
            ("Age-restricted", "yes" if getattr(c, "nsfw", False) else "")]


def channel_created(c, actor=None, reason: str | None = None) -> discord.Embed:
    return entry_card(Colors.success, f"The channel {c.mention} was created{_by_text(actor)}",
                      header=_actor_header(actor) or f"{_hash(c)}{c.name}", icon=_who(actor, c.guild),
                      details=_channel_details(c) + [("Reason", reason or "")], ids=[("Channel ID", c.id)])


def channel_deleted(c, actor=None, reason: str | None = None) -> discord.Embed:
    return entry_card(Colors.error, f"The channel {_gone(_hash(c) + c.name)} was deleted{_by_text(actor)}",
                      header=_actor_header(actor) or f"{_hash(c)}{c.name}", icon=_who(actor, c.guild),
                      details=_channel_details(c) + [("Reason", reason or "")], ids=[("Channel ID", c.id)])


def channel_changes(before, after) -> list[str]:
    out = []
    if before.name != after.name:
        out.append(f"Name: `#{before.name}` → `#{after.name}`")
    if getattr(before, "category", None) != getattr(after, "category", None):
        old, new = getattr(before, "category", None), getattr(after, "category", None)
        out.append(f"Category: `{old.name if old else 'none'}` → `{new.name if new else 'none'}`")
    if getattr(before, "topic", None) != getattr(after, "topic", None):
        out.append(f"Topic: `{shorten(before.topic or 'none', 80)}` → `{shorten(after.topic or 'none', 80)}`")
    if getattr(before, "slowmode_delay", None) != getattr(after, "slowmode_delay", None):
        out.append(f"Slowmode: `{before.slowmode_delay}s` → `{after.slowmode_delay}s`")
    if getattr(before, "nsfw", None) != getattr(after, "nsfw", None):
        out.append(f"Age-restricted: `{'yes' if after.nsfw else 'no'}`")
    if getattr(before, "user_limit", None) != getattr(after, "user_limit", None):
        out.append(f"User limit: `{before.user_limit or 'none'}` → `{after.user_limit or 'none'}`")
    overwrites_before, overwrites_after = before.overwrites, after.overwrites
    touched = [t for t in {*overwrites_before, *overwrites_after} if overwrites_before.get(t) != overwrites_after.get(t)]
    if touched:
        out.append("Permissions changed for " + mentions(touched, 300))
    return out


def channel_updated(after, changes: list[str], actor=None) -> discord.Embed:
    return entry_card(Colors.blurple, f"The channel {after.mention} was updated{_by_text(actor)}",
                      header=_actor_header(actor) or f"{_hash(after)}{after.name}", icon=_who(actor, after.guild),
                      details=_changes(changes), ids=[("Channel ID", after.id)])


def server_updated(after: discord.Guild, changes: list[str], actor=None) -> discord.Embed:
    return entry_card(Colors.blurple, f"The server was updated{_by_text(actor)}",
                      header=_actor_header(actor) or after.name, icon=_who(actor, after),
                      details=_changes(changes), ids=[("Server ID", after.id)])


def server_changes(before: discord.Guild, after: discord.Guild) -> list[str]:
    out = []
    if before.name != after.name:
        out.append(f"Name: `{before.name}` → `{after.name}`")
    if before.vanity_url_code != after.vanity_url_code:
        out.append(f"Vanity URL: `{before.vanity_url_code or 'none'}` → `{after.vanity_url_code or 'none'}`")
    if before.icon != after.icon:
        out.append("Icon: changed")
    if before.banner != after.banner:
        out.append("Banner: changed")
    if before.verification_level != after.verification_level:
        out.append(f"Verification level: `{before.verification_level.name}` → `{after.verification_level.name}`")
    return out


def _invite_limits(inv) -> tuple[str, str]:
    uses = plural(inv.max_uses, "use") if inv.max_uses else "unlimited"
    if not inv.max_age:
        return uses, "never"
    hours = inv.max_age // 3600
    return uses, f"in {hours}h" if hours else f"in {max(1, inv.max_age // 60)}m"


def invite_created(inv: discord.Invite) -> discord.Embed:
    uses, expires = _invite_limits(inv)
    inviter = getattr(inv, "inviter", None)
    where = f" for {inv.channel.mention}" if getattr(inv, "channel", None) else ""
    return entry_card(Colors.success, f"An invite{where} was created{_by_text(inviter)}",
                      header=_actor_header(inviter) or inv.code, icon=_who(inviter, inv.guild), details=[
                          ("Link", f"discord.gg/{inv.code}"), ("Uses", uses), ("Expires", expires),
                          ("Temporary", "yes" if getattr(inv, "temporary", False) else ""),
                      ], ids=[("Code", inv.code)])


def invite_deleted(inv: discord.Invite) -> discord.Embed:
    inviter = getattr(inv, "inviter", None)
    uses = getattr(inv, "uses", None)
    where = f" for {inv.channel.mention}" if getattr(inv, "channel", None) else ""
    return entry_card(Colors.error, f"An invite{where} was deleted", header=_actor_header(inviter) or inv.code,
                      icon=_who(inviter, inv.guild), details=[
                          ("Link", f"discord.gg/{inv.code}"), ("Made by", inviter.mention if inviter else ""),
                          ("Used", plural(uses, "time") if uses is not None else ""),
                      ], ids=[("Code", inv.code)])


def emojis_changed(guild: discord.Guild, added: list, removed: list) -> discord.Embed:
    return entry_card(Colors.blurple, "The server's emojis changed", header=guild.name, icon=_icon(guild), details=[
        ("Added", shorten_md(" ".join(f"{e} `:{e.name}:`" for e in added[:20]), 1500) if added else ""),
        ("Removed", shorten_md(" ".join(f"`:{e.name}:`" for e in removed[:20]), 1500) if removed else ""),
    ], ids=[("Server ID", guild.id)])


# ---------------------------------------------------------------- bursts (many of the same thing at once)

_GROUP_WORDS = {
    "role_create": ("role", "created", Colors.success), "role_delete": ("role", "deleted", Colors.error),
    "channel_create": ("channel", "created", Colors.success), "channel_delete": ("channel", "deleted", Colors.error),
}


def grouped(kind: str, items: list, actor=None, reason: str | None = None) -> discord.Embed:
    """One card for a burst: "6 roles were deleted by @x" with the names listed, instead of six cards."""
    noun, verb, color = _GROUP_WORDS[kind]
    alive = verb == "created"

    def label(obj) -> str:
        if alive:
            return obj.mention
        return re.sub(r"([*_`~|\\>])", r"\\\1", f"{_hash(obj) if noun == 'channel' else '@'}{obj.name}")

    names = [label(o) for o in items[:GROUP_SHOWN]]
    more = f" +{len(items) - GROUP_SHOWN} more" if len(items) > GROUP_SHOWN else ""
    guild = getattr(items[0], "guild", None)
    return entry_card(color, f"{plural(len(items), noun)} were {verb}{_by_text(actor)}",
                      header=_actor_header(actor) or f"{plural(len(items), noun)} {verb}", icon=_who(actor, guild),
                      details=[(f"{noun.title()}s", shorten_md(", ".join(names), 3000) + more), ("Reason", reason or "")])


# ---------------------------------------------------------------- voice and commands (feed lines)

def voice_changed(m: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> str | None:
    """A feed line for voice joins, leaves, moves and server mutes/deafens; None when nothing worth logging changed."""
    if before.channel != after.channel:
        if not before.channel:
            return feed_line("🔊", f"{m.mention} joined {after.channel.mention}")
        if not after.channel:
            return feed_line("🔇", f"{m.mention} left {before.channel.mention}")
        return feed_line("↪️", f"{m.mention} moved {before.channel.mention} → {after.channel.mention}")
    for attr, on, off in (("mute", "was server muted", "was server unmuted"),
                          ("deaf", "was server deafened", "was server undeafened")):
        if getattr(before, attr) != getattr(after, attr):
            now_on = getattr(after, attr)
            where = f" in {after.channel.mention}" if after.channel else ""
            return feed_line("🔕" if now_on else "🔔", f"{m.mention} {on if now_on else off}{where}")
    return None


def _took(ms: float | None) -> str:
    if ms is None:
        return ""
    return f"{ms / 1000:.1f}s" if ms >= 1000 else f"{ms:.0f} ms"


def command_used(ctx, permission: str, granted_by: str, *, rank: int | None = None, ms: float | None = None,
                 error: str | None = None) -> str:
    """One command on a person's command card: when, what, where, and how long it took or why it failed. The
    person (name, avatar, level, ID) is on the card itself, so a line never repeats them."""
    command = getattr(ctx, "command", None)
    prefix = getattr(ctx, "prefix", "") or ""
    hidden = command is not None and (getattr(command, "hidden", False) or getattr(command, "cog_name", None) == "Owner")
    action = getattr(ctx, "panel_action", None)
    jump = getattr(ctx, "panel_jump", None) if action else None
    jump = jump or getattr(ctx.message, "jump_url", None)
    if action:  # a button on the ,mod / ,admin panel: which button, on which panel
        shown = f"**{action}** on the `{prefix}{getattr(ctx, 'panel_name', None) or 'panel'}` panel"
    elif hidden:
        # developer commands can carry code, queries or secrets, and these cards are readable by server admins
        shown = f"`{prefix}{command.qualified_name}` · arguments hidden"
    else:
        content = redact.scrub(ctx.message.content.strip()).replace("\n", " ").replace("`", "'")
        shown = f"`{shorten(content, 200)}`"
    parts = [ts(discord.utils.utcnow(), "t"), shown, ctx.channel.mention]
    if error is not None:
        parts.append("**failed:** " + shorten(redact.scrub(error).replace("\n", " "), 200))
    else:
        took = _took(ms)
        if took:
            parts.append(took)
    if jump:
        parts.append(f"[jump]({jump})")
    return shorten_md(" · ".join(parts), 1000)


def command_card(user, lines: list[str], level: str | None) -> discord.Embed:
    """A person's recent commands on one card: their name and avatar on top, a line per command, their permission
    level and ID underneath. Red when the latest one failed."""
    failed = bool(lines) and "**failed:**" in lines[-1]
    embed = discord.Embed(color=Colors.deny if failed else Colors.main,
                          description=shorten_md("\n".join(lines), 4000))
    embed.set_author(name=shorten(name_of(user), 256), icon_url=avatar_of(user))
    who = [level] if level and level != "None" else []
    embed.set_footer(text=shorten(" · ".join(who + [f"User ID: {user.id}"]), 2048))
    return embed


# ---------------------------------------------------------------- moderation cases

def case_card(num: int, action: str, target, moderator, reason: str | None) -> discord.Embed:
    """The mod-log card for a case (`,ban`, `,warn`...)."""
    named = target if hasattr(target, "mention") and hasattr(target, "name") else None
    who = named.mention if named else f"<@{target.id}>"
    return entry_card(Colors.main, f"Case #{num} · {action.title()}: {who}{_by_text(moderator)}",
                      subject=named, details=[("Reason", reason or "no reason given")],
                      ids=[("User ID", target.id), ("Case", num)])
