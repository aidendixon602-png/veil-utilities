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


# ---------------------------------------------------------------- messages

def _attachments(m) -> str:
    files = list(getattr(m, "attachments", None) or [])
    if not files:
        return ""
    shown = " · ".join(f"[{a.filename}]({a.url})" for a in files[:5])
    more = f" +{len(files) - 5} more" if len(files) > 5 else ""
    return f"📎 {shown}{more}"


def message_deleted(m: discord.Message, actor=None) -> discord.Embed:
    deleter = actor if actor is not None and actor.id != m.author.id else None
    text = quote(m.content) if m.content else "*no text (embed or attachments only)*"
    sent = getattr(m, "created_at", None)
    return log_card(Colors.error, f"Message deleted · {name_of(m.author)}", icon=avatar_of(m.author), lines=[
        joined(m.author.mention, f"in {m.channel.mention}", by(deleter, "deleted by")),
        text,
        small(f"sent {ts(sent, 'R')}" if sent else "", _attachments(m)),
    ], ids=[("User ID", m.author.id), ("Message ID", m.id)])


def message_edited(before: discord.Message, after: discord.Message) -> discord.Embed:
    sent = getattr(after, "created_at", None)
    return log_card(Colors.warn, f"Message edited · {name_of(after.author)}", icon=avatar_of(after.author), lines=[
        joined(after.author.mention, f"in {after.channel.mention}", f"[jump to it]({after.jump_url})"),
        "-# before", quote(before.content) if before.content else "*empty*",
        "-# after", quote(after.content) if after.content else "*empty*",
        small(f"sent {ts(sent, 'R')}" if sent else ""),
    ], ids=[("User ID", after.author.id), ("Message ID", after.id)])


def bulk_deleted(messages: list[discord.Message]) -> discord.Embed:
    channel = messages[0].channel
    counts: dict[int, list] = {}
    for m in messages:
        counts.setdefault(m.author.id, [m.author, 0])[1] += 1
    top = sorted(counts.values(), key=lambda pair: -pair[1])
    authors = ", ".join(f"{a.mention} ×{n}" for a, n in top[:8]) + (f" +{len(top) - 8} more" if len(top) > 8 else "")
    times = [m.created_at for m in messages if getattr(m, "created_at", None)]
    span = f"sent {ts(min(times), 'f')} – {ts(max(times), 't')}" if times else ""
    return log_card(Colors.error, f"{plural(len(messages), 'message')} deleted · #{getattr(channel, 'name', '?')}",
                    icon=_icon(getattr(channel, "guild", None)), lines=[
                        f"in {channel.mention} · from {plural(len(counts), 'person', 'people')}",
                        small(f"by {authors}"), small(span), small("the messages are in the attached file"),
                    ], ids=[("Channel ID", channel.id)])


def transcript(messages: list[discord.Message]) -> str:
    return "\n".join(f"[{m.created_at:%Y-%m-%d %H:%M}] {m.author}: {m.content}"
                     for m in sorted(messages, key=lambda x: x.id))


# ---------------------------------------------------------------- members

def _account(user) -> str:
    created = getattr(user, "created_at", None)
    if not created:
        return ""
    young = (discord.utils.utcnow() - created).days < NEW_ACCOUNT_DAYS
    return f"account made {when(created)}" + (" · ⚠️ **new account**" if young else "")


def member_joined(m: discord.Member) -> discord.Embed:
    count = getattr(m.guild, "member_count", None)
    return log_card(Colors.success, f"Member joined · {name_of(m)}", icon=avatar_of(m), lines=[
        joined(m.mention, f"member **#{count:,}**" if count else "", "🤖 bot" if getattr(m, "bot", False) else ""),
        small(_account(m)),
    ], ids=[("User ID", m.id)])


def member_left(m: discord.Member, kicked_by=None, reason: str | None = None) -> discord.Embed:
    kicked = kicked_by is not None
    joined_at = getattr(m, "joined_at", None)
    stayed = f"was here {short_age(joined_at)}" if joined_at else ""
    count = getattr(m.guild, "member_count", None)
    roles = [r for r in getattr(m, "roles", []) if not r.is_default()]
    return log_card(Colors.warn if kicked else Colors.error,
                    f"{'Member kicked' if kicked else 'Member left'} · {name_of(m)}", icon=avatar_of(m), lines=[
                        joined(m.mention, by(kicked_by, "kicked by")),
                        quote(reason, 300) if reason else "",
                        small(stayed, f"{count:,} members now" if count and not kicked else ""),
                        small(f"had {mentions(reversed(roles), 700)}" if roles else ""),
                    ], ids=[("User ID", m.id)])


def _ban_card(color: int, word: str, user, mod, reason: str | None, clicked: bool) -> discord.Embed:
    lines = [joined(user.mention, by(mod, f"{word} by")),
             quote(reason, 300) if reason else "",
             small("with the button on the ban log" if clicked else "", "" if reason or clicked else "no reason given"),
             small(_account(user))]
    return log_card(color, f"Member {word} · {name_of(user)}", icon=avatar_of(user), lines=lines,
                    ids=[("User ID", user.id)])


def user_banned(user, mod=None, reason: str | None = None, *, clicked: bool = False) -> discord.Embed:
    return _ban_card(Colors.error, "banned", user, mod, reason, clicked)


def user_unbanned(user, mod=None, reason: str | None = None, *, clicked: bool = False) -> discord.Embed:
    return _ban_card(Colors.success, "unbanned", user, mod, reason, clicked)


def nickname_changed(m: discord.Member, old: str | None, new: str | None, actor=None) -> discord.Embed:
    show = lambda nick: f"`{nick}`" if nick else "*none*"  # noqa: E731
    other = actor if actor is not None and actor.id != m.id else None
    return log_card(Colors.blurple, f"Nickname changed · {name_of(m)}", icon=avatar_of(m), lines=[
        joined(m.mention, by(other, "changed by")),
        f"{show(old)} → {show(new)}",
    ], ids=[("User ID", m.id)])


def _role_warnings(roles) -> str:
    warns = [f"{r.mention} has {', '.join(dangerous(r))}" for r in roles if dangerous(r)]
    return f"⚠️ {'; '.join(warns)}" if warns else ""


def roles_changed(m: discord.Member, added: list, removed: list, actor=None, reason: str | None = None) -> discord.Embed:
    if added and not removed:
        word, color = "Roles given", Colors.success
    elif removed and not added:
        word, color = "Roles taken", Colors.error
    else:
        word, color = "Roles changed", Colors.blurple
    return log_card(color, f"{word} · {name_of(m)}", icon=avatar_of(m), lines=[
        joined(m.mention, by(actor)),
        f"**+** {mentions(added)}" if added else "",
        f"**−** {mentions(removed)}" if removed else "",
        quote(reason, 300) if reason else "",
        small(_role_warnings(added)),
    ], ids=[("User ID", m.id)])


def timed_out(m: discord.Member, until, actor=None, reason: str | None = None) -> discord.Embed:
    left = (until - discord.utils.utcnow()).total_seconds()
    return log_card(Colors.warn, f"Member timed out · {name_of(m)}", icon=avatar_of(m), lines=[
        joined(m.mention, by(actor), f"for **{fmt_duration(round(left / 60) * 60 or left)}**" if left > 0 else ""),
        quote(reason, 300) if reason else "",
        small(f"ends {ts(until, 'f')} ({ts(until, 'R')})", "" if reason else "no reason given"),
    ], ids=[("User ID", m.id)])


def timeout_removed(m: discord.Member, actor=None) -> discord.Embed:
    return log_card(Colors.success, f"Timeout removed · {name_of(m)}", icon=avatar_of(m),
                    lines=[joined(m.mention, by(actor))], ids=[("User ID", m.id)])


# ---------------------------------------------------------------- roles

def _role_facts(r, *more) -> str:
    color = getattr(getattr(r, "color", None), "value", 0)
    return small(f"colour {hex_color(color)}" if color else "",
                 f"position {r.position}" if getattr(r, "position", None) is not None else "",
                 "shown separately" if getattr(r, "hoist", False) else "",
                 "mentionable" if getattr(r, "mentionable", False) else "", *more)


def _danger_line(r) -> str:
    warn = dangerous(r)
    return f"⚠️ has **{', '.join(warn)}**" if warn else ""


def _who(actor, guild) -> str | None:
    """The header icon for server-object events: whoever did it, else the server's icon."""
    return avatar_of(actor) if actor is not None else _icon(guild)


def role_created(r: discord.Role, actor=None, reason: str | None = None) -> discord.Embed:
    return log_card(r.color.value or Colors.success, f"Role created · {r.name}", icon=_who(actor, r.guild), lines=[
        joined(r.mention, by(actor)), quote(reason, 300) if reason else "", _danger_line(r), _role_facts(r),
    ], ids=[("Role ID", r.id)])


def role_deleted(r: discord.Role, actor=None, reason: str | None = None) -> discord.Embed:
    members = getattr(r, "members", None)
    return log_card(Colors.error, f"Role deleted · {r.name}", icon=_who(actor, r.guild), lines=[
        by(actor), quote(reason, 300) if reason else "", _danger_line(r),
        _role_facts(r, f"{plural(len(members), 'member')} had it" if members else ""),
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


def role_updated(after: discord.Role, changes: list[str], actor=None) -> discord.Embed:
    members = getattr(after, "members", None)
    return log_card(after.color.value or Colors.blurple, f"Role updated · {after.name}", icon=_who(actor, after.guild),
                    lines=[joined(after.mention, by(actor)), bullets([f"• {c}" for c in changes]),
                           _danger_line(after),
                           small(f"{plural(len(members), 'member')} {'has' if len(members) == 1 else 'have'} it"
                                 if members is not None else "")],
                    ids=[("Role ID", after.id)])


# ---------------------------------------------------------------- channels and server

_CHANNEL_KINDS = ((discord.CategoryChannel, "Category"), (discord.ForumChannel, "Forum"),
                  (discord.StageChannel, "Stage channel"), (discord.VoiceChannel, "Voice channel"),
                  (discord.TextChannel, "Text channel"))


def channel_kind(c) -> str:
    return next((label for cls, label in _CHANNEL_KINDS if isinstance(c, cls)), "Channel")


def _channel_facts(c) -> str:
    category = getattr(c, "category", None)
    slow = getattr(c, "slowmode_delay", 0) or 0
    return small(f"in **{category.name}**" if category else "no category",
                 f"position {c.position}" if getattr(c, "position", None) is not None else "",
                 f"slowmode {slow}s" if slow else "", "age-restricted" if getattr(c, "nsfw", False) else "")


def _hash(c) -> str:
    return "" if isinstance(c, discord.CategoryChannel) else "#"


def channel_created(c, actor=None, reason: str | None = None) -> discord.Embed:
    topic = getattr(c, "topic", None)
    return log_card(Colors.success, f"{channel_kind(c)} created · {_hash(c)}{c.name}", icon=_who(actor, c.guild),
                    lines=[joined(c.mention, by(actor)), quote(reason, 300) if reason else "",
                           f"-# topic: {shorten(topic, 200)}" if topic else "", _channel_facts(c)],
                    ids=[("Channel ID", c.id)])


def channel_deleted(c, actor=None, reason: str | None = None) -> discord.Embed:
    return log_card(Colors.error, f"{channel_kind(c)} deleted · {_hash(c)}{c.name}", icon=_who(actor, c.guild),
                    lines=[by(actor), quote(reason, 300) if reason else "", _channel_facts(c)],
                    ids=[("Channel ID", c.id)])


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
    return log_card(Colors.blurple, f"{channel_kind(after)} updated · {_hash(after)}{after.name}",
                    icon=_who(actor, after.guild),
                    lines=[joined(after.mention, by(actor)), bullets([f"• {c}" for c in changes])],
                    ids=[("Channel ID", after.id)])


def server_updated(after: discord.Guild, changes: list[str], actor=None) -> discord.Embed:
    return log_card(Colors.blurple, f"Server updated · {after.name}", icon=_who(actor, after),
                    lines=[by(actor), bullets([f"• {c}" for c in changes])], ids=[("Server ID", after.id)])


def server_changes(before: discord.Guild, after: discord.Guild) -> list[str]:
    out = []
    if before.name != after.name:
        out.append(f"Name: `{before.name}` → `{after.name}`")
    if before.vanity_url_code != after.vanity_url_code:
        out.append(f"Vanity URL: `{before.vanity_url_code or 'none'}` → `{after.vanity_url_code or 'none'}`")
    if before.icon != after.icon:
        out.append("Icon changed")
    if before.banner != after.banner:
        out.append("Banner changed")
    if before.verification_level != after.verification_level:
        out.append(f"Verification level: `{before.verification_level.name}` → `{after.verification_level.name}`")
    return out


def _invite_limits(inv) -> tuple[str, str]:
    uses = plural(inv.max_uses, "use") if inv.max_uses else "unlimited uses"
    if not inv.max_age:
        return uses, "never expires"
    hours = inv.max_age // 3600
    return uses, f"expires in {hours}h" if hours else f"expires in {max(1, inv.max_age // 60)}m"


def invite_created(inv: discord.Invite) -> discord.Embed:
    uses, expires = _invite_limits(inv)
    inviter = getattr(inv, "inviter", None)
    where = f"for {inv.channel.mention}" if getattr(inv, "channel", None) else ""
    return log_card(Colors.success, f"Invite created · {inv.code}", icon=_who(inviter, inv.guild), lines=[
        joined(f"discord.gg/{inv.code}", where, by(inviter)),
        small(uses, expires, "temporary membership" if getattr(inv, "temporary", False) else ""),
    ], ids=[("Code", inv.code)])


def invite_deleted(inv: discord.Invite) -> discord.Embed:
    inviter = getattr(inv, "inviter", None)
    uses = getattr(inv, "uses", None)
    where = f"for {inv.channel.mention}" if getattr(inv, "channel", None) else ""
    return log_card(Colors.error, f"Invite deleted · {inv.code}", icon=_who(inviter, inv.guild), lines=[
        joined(where, by(inviter, "made by")),
        small(f"used {plural(uses, 'time')}" if uses is not None else ""),
    ], ids=[("Code", inv.code)])


def emojis_changed(guild: discord.Guild, added: list, removed: list) -> discord.Embed:
    lines = []
    if added:
        lines.append("**+** " + shorten_md(" ".join(f"{e} `:{e.name}:`" for e in added[:20]), 1500))
    if removed:
        lines.append("**−** " + shorten_md(" ".join(f"`:{e.name}:`" for e in removed[:20]), 1500))
    return log_card(Colors.blurple, f"Emojis updated · {guild.name}", icon=_icon(guild), lines=lines,
                    ids=[("Server ID", guild.id)])


# ---------------------------------------------------------------- bursts (many of the same thing at once)

_GROUP_WORDS = {
    "role_create": ("role", "created", Colors.success), "role_delete": ("role", "deleted", Colors.error),
    "channel_create": ("channel", "created", Colors.success), "channel_delete": ("channel", "deleted", Colors.error),
}


def grouped(kind: str, items: list, actor=None, reason: str | None = None) -> discord.Embed:
    """One card for a burst: `6 roles deleted` with the names listed, instead of six near-identical cards."""
    noun, verb, color = _GROUP_WORDS[kind]
    alive = verb == "created"

    def label(obj) -> str:
        if alive:
            return obj.mention
        return re.sub(r"([*_`~|\\>])", r"\\\1", f"{_hash(obj) if noun == 'channel' else '@'}{obj.name}")

    names = [label(o) for o in items[:GROUP_SHOWN]]
    more = f" +{len(items) - GROUP_SHOWN} more" if len(items) > GROUP_SHOWN else ""
    guild = getattr(items[0], "guild", None)
    return log_card(color, f"{plural(len(items), noun)} {verb}", icon=_who(actor, guild), lines=[
        by(actor), quote(reason, 300) if reason else "", shorten_md(", ".join(names), 3000) + more,
    ])


# ---------------------------------------------------------------- voice and commands

def voice_changed(m: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> discord.Embed | None:
    def room(channel) -> str:
        return channel.mention

    def crowd(channel) -> str:
        count = len(getattr(channel, "members", []) or [])
        return f"{plural(count, 'person', 'people')} in there now" if count else ""

    if before.channel != after.channel:
        if not before.channel:
            header, color, line, extra = "Joined voice", Colors.success, f"joined {room(after.channel)}", crowd(after.channel)
        elif not after.channel:
            header, color, line, extra = "Left voice", Colors.error, f"left {room(before.channel)}", crowd(before.channel)
        else:
            header, color = "Moved voice", Colors.blurple
            line, extra = f"{room(before.channel)} → {room(after.channel)}", crowd(after.channel)
        return log_card(color, f"{header} · {name_of(m)}", icon=avatar_of(m),
                        lines=[joined(m.mention, line), small(extra)], ids=[("User ID", m.id)])
    for attr, on, off in (("mute", "Server muted", "Server unmuted"), ("deaf", "Server deafened", "Server undeafened")):
        if getattr(before, attr) != getattr(after, attr):
            now_on = getattr(after, attr)
            where = f"in {room(after.channel)}" if after.channel else ""
            return log_card(Colors.warn if now_on else Colors.success, f"{on if now_on else off} · {name_of(m)}",
                            icon=avatar_of(m), lines=[joined(m.mention, where)], ids=[("User ID", m.id)])
    return None


def _took(ms: float | None) -> str:
    if ms is None:
        return ""
    return f"{ms / 1000:.1f}s" if ms >= 1000 else f"{ms:.0f} ms"


def command_used(ctx, permission: str, granted_by: str, *, rank: int | None = None, ms: float | None = None,
                 error: str | None = None) -> discord.Embed:
    """One card per command run: who ran what where, how it ended, and what let them run it."""
    command = getattr(ctx, "command", None)
    hidden = command is not None and (getattr(command, "hidden", False) or getattr(command, "cog_name", None) == "Owner")
    if hidden:
        # developer commands can carry code, queries or secrets, and these cards are readable by server admins
        content = f"{getattr(ctx, 'prefix', '') or ''}{command.qualified_name}"
        shown = f"`{content}`"
    else:
        content = redact.scrub(ctx.message.content.strip())
        shown = f"```{shorten(content, 900)}```" if ("\n" in content or len(content) > 100) else f"`{shorten(content, 900)}`"
    failed = error is not None
    jump = getattr(ctx.message, "jump_url", None)
    # show the level, and which role gave it when one did
    authority = {"None": "Everyone", "-": "Everyone"}.get(permission, permission)
    if granted_by and granted_by not in ("Built-in", "-"):
        authority += f" (via {granted_by})"
    alias = ""
    if command is not None:
        invoked = (getattr(ctx, "invoked_with", None) or "").lower()
        if invoked and invoked != command.name.lower() and invoked in [a.lower() for a in getattr(command, "aliases", [])]:
            alias = f"alias `{invoked}`"
    result = ("❌ failed" if failed else "✅ done") + (f" in {_took(ms)}" if ms is not None else "")
    lines = [joined(ctx.author.mention, f"in {ctx.channel.mention}", f"[jump]({jump})" if jump else ""),
             shown,
             quote(redact.scrub(error), 300) if failed else "",
             small(result, authority, getattr(command, "cog_name", None) if command is not None else "", alias,
                   "arguments hidden" if hidden else "")]
    ids = [("User ID", ctx.author.id)] + ([("Message ID", ctx.message.id)] if hasattr(ctx.message, "id") else [])
    return log_card(Colors.error if failed else Colors.blurple,
                    f"{'Command failed' if failed else 'Command used'} · {name_of(ctx.author)}",
                    icon=avatar_of(ctx.author), lines=lines, ids=ids)


# ---------------------------------------------------------------- moderation cases

def case_card(num: int, action: str, target, moderator, reason: str | None) -> discord.Embed:
    """The mod-log card for a case (`,ban`, `,warn`...)."""
    named = target if hasattr(target, "mention") and hasattr(target, "name") else None
    who = f"{named.mention} (`{named.name}`)" if named else f"<@{target.id}>"
    return log_card(Colors.main, f"Case #{num} · {action.title()}" + (f" · {name_of(named)}" if named else ""),
                    icon=avatar_of(named) if named else None, lines=[
                        joined(who, by(moderator)),
                        quote(reason, 600) if reason else small("no reason given"),
                    ], ids=[("User ID", target.id), ("Case", num)])
