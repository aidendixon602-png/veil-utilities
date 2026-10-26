"""Log embeds: a titled card with the person's avatar, labelled fields, IDs in the footer and one timestamp.

Everything here is a plain function that returns a discord.Embed, so the look of the logs lives in one place and
can be tested without Discord. The cog (cogs/logging.py) decides where each embed goes.

Every card answers the same questions: who it is about (name, @mention, ID), who caused it (from the audit log,
or "Unknown" when Discord doesn't say), what exactly changed, and enough context (account age, join date, roles,
jump links) to judge it without opening anything else.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable

import discord

from core import redact
from core.utils import Colors, fmt_duration, shorten, ts

AUDIT_SETTLE = 0.6  # seconds to let Discord write the audit-log entry before we look for it
AUDIT_WINDOW = 15.0  # an entry older than this isn't about the event we just saw
QUOTE_LIMIT = 900
LIST_LIMIT = 950  # characters of a field kept for a list before "+N more"
NEW_ACCOUNT_DAYS = 7
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


def quote(text: str | None, limit: int = QUOTE_LIMIT) -> str:
    """Markdown block quote, cut to `limit` characters."""
    text = shorten((text or "").replace("\r", ""), limit)
    return "\n".join(f"> {line}" if line else ">" for line in text.split("\n"))


def mentions(items: Iterable, limit: int = 900) -> str:
    return shorten(", ".join(i.mention for i in items), limit)


def footer(ids: Iterable[tuple[str, object]]) -> str:
    return " · ".join(f"{label}: {value}" for label, value in ids)


def bullets(lines: list[str], limit: int = LIST_LIMIT) -> str:
    """One line each, cut with "+N more" instead of overflowing a field."""
    out, used = [], 0
    for i, line in enumerate(lines):
        if used + len(line) + 1 > limit - 16:
            out.append(f"*+{len(lines) - i} more*")
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)


def person(user) -> str:
    """`@mention`, then `username` · `ID` on the next line. A missing user (no audit entry) says so."""
    if user is None:
        return "*Unknown*\n-# the audit log didn't say"
    bot = " 🤖" if getattr(user, "bot", False) else ""
    return f"{user.mention}{bot}\n`{user.name}` · `{user.id}`"


def when(dt) -> str:
    """`Oct 26, 2026` and how long ago, e.g. for an account's creation date."""
    return f"{ts(dt, 'D')}\n{ts(dt, 'R')}"


def age_of(dt) -> str:
    return fmt_duration((discord.utils.utcnow() - dt).total_seconds())


def card(color: int, subject=None, text: str = "", *, fields: Iterable[tuple] = (), ids: Iterable[tuple] = (),
         icon: str | None = None) -> discord.Embed:
    """A one-line card: a name line on top and one sentence. Used where a short note is enough (mod-log cases)."""
    embed = discord.Embed(color=color, description=shorten(text, 4000) if text else None,
                          timestamp=discord.utils.utcnow())
    if subject is not None:
        embed.set_author(name=shorten(name_of(subject), 256), icon_url=avatar_of(subject))
    for name, value, *rest in fields:
        embed.add_field(name=name, value=shorten(value, 1024), inline=bool(rest and rest[0]))
    ids = list(ids)
    if ids:
        embed.set_footer(text=shorten(footer(ids), 2048), icon_url=icon)
    return embed


def detail(color: int, title: str, *, subject=None, thumb: str | None = None, text: str = "",
           fields: Iterable[tuple] = (), ids: Iterable[tuple] = (), icon: str | None = None) -> discord.Embed:
    """The shape of every event log. `fields` are (name, value[, inline]); empty values are left out."""
    embed = discord.Embed(color=color, title=shorten(title, 256), description=shorten(text, 4000) if text else None,
                          timestamp=discord.utils.utcnow())
    image = thumb or (avatar_of(subject) if subject is not None else None)
    if image:
        embed.set_thumbnail(url=image)
    for name, value, *rest in fields:
        if value in (None, ""):
            continue
        embed.add_field(name=name, value=shorten(str(value), 1024), inline=bool(rest and rest[0]))
    ids = list(ids)
    if ids:
        embed.set_footer(text=shorten(footer(ids), 2048), icon_url=icon)
    return embed


def titled(label: str, subject) -> str:
    return f"{label} — {name_of(subject)}"


def by(actor) -> str:
    if not actor:
        return ""
    return f" · by {'🤖 ' if getattr(actor, 'bot', False) else ''}{actor.mention}"


def why(reason: str | None) -> str:
    return f"\n{quote(reason, 300)}" if reason else ""


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


def role_line(role) -> str:
    warn = dangerous(role)
    flag = f" ⚠️ `{'`, `'.join(warn)}`" if warn else ""
    return f"• {role.mention} · `{role.name}` · `{role.id}`{flag}"


def member_context(m) -> list[tuple]:
    """Inline fields that say who this is: join date, account age, role count."""
    out = []
    joined = getattr(m, "joined_at", None)
    if joined:
        out.append(("Joined", ts(joined, "R"), True))
    created = getattr(m, "created_at", None)
    if created:
        young = (discord.utils.utcnow() - created).days < NEW_ACCOUNT_DAYS
        out.append(("Account Created", f"{ts(created, 'R')}{' ⚠️ new' if young else ''}", True))
    roles = [r for r in getattr(m, "roles", []) if not r.is_default()]
    if hasattr(m, "roles"):
        out.append(("Roles", str(len(roles)), True))
    return out


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
            return entry.user, entry.reason
    except Exception:  # missing access, rate limit, library quirks: the log is still worth sending
        pass
    return None, None


# ---------------------------------------------------------------- messages

def message_deleted(m: discord.Message, actor=None) -> discord.Embed:
    fields = [("Author", person(m.author), True),
              ("Channel", f"{m.channel.mention}\n`#{getattr(m.channel, 'name', '?')}`", True)]
    if actor and actor.id != m.author.id:
        fields.append(("Deleted By", person(actor), True))
    sent = getattr(m, "created_at", None)
    if sent:
        fields.append(("Sent", ts(sent, "R"), True))
    if m.attachments:
        fields.append((f"Attachments ({len(m.attachments)})",
                       bullets([f"📎 [{a.filename}]({a.url})" for a in m.attachments[:10]]), False))
    text = quote(m.content) if m.content else "*No text (embed or attachment only)*"
    return detail(Colors.error, titled("Message Deleted", m.author), subject=m.author, text=text, fields=fields,
                  ids=[("User ID", m.author.id), ("Message ID", m.id)])


def message_edited(before: discord.Message, after: discord.Message) -> discord.Embed:
    fields = [("Author", person(after.author), True),
              ("Channel", f"{after.channel.mention}\n[Jump to message]({after.jump_url})", True)]
    sent = getattr(after, "created_at", None)
    if sent:
        fields.append(("Sent", ts(sent, "R"), True))
    fields += [("Before", quote(before.content) if before.content else "*Empty*", False),
               ("After", quote(after.content) if after.content else "*Empty*", False)]
    return detail(Colors.warn, titled("Message Edited", after.author), subject=after.author, fields=fields,
                  ids=[("User ID", after.author.id), ("Message ID", after.id)])


def bulk_deleted(messages: list[discord.Message]) -> discord.Embed:
    channel = messages[0].channel
    counts: dict[int, list] = {}
    for m in messages:
        counts.setdefault(m.author.id, [m.author, 0])[1] += 1
    top = sorted(counts.values(), key=lambda pair: -pair[1])
    authors = bullets([f"• {a.mention} · {n}" for a, n in top[:10]] + ([f"*+{len(top) - 10} more people*"] if len(top) > 10 else []))
    times = [m.created_at for m in messages if getattr(m, "created_at", None)]
    span = f"{ts(min(times), 'f')} → {ts(max(times), 'f')}" if times else None
    fields = [("Channel", f"{channel.mention}\n`#{getattr(channel, 'name', '?')}`", True),
              ("Messages", str(len(messages)), True), ("People", str(len(counts)), True),
              ("Authors", authors, False), ("Sent between", span, False)]
    return detail(Colors.error, "Messages Bulk Deleted", text="A transcript of the messages is attached.", fields=fields,
                  ids=[("Channel ID", channel.id)])


def transcript(messages: list[discord.Message]) -> str:
    return "\n".join(f"[{m.created_at:%Y-%m-%d %H:%M}] {m.author}: {m.content}"
                     for m in sorted(messages, key=lambda x: x.id))


# ---------------------------------------------------------------- members

def member_joined(m: discord.Member) -> discord.Embed:
    young = (discord.utils.utcnow() - m.created_at).days < NEW_ACCOUNT_DAYS
    count = getattr(m.guild, "member_count", None)
    fields = [("User", person(m), True),
              ("Account Created", f"{when(m.created_at)}{chr(10) + '⚠️ New account' if young else ''}", True),
              ("Account Age", age_of(m.created_at), True)]
    if count:
        fields.append(("Member", f"#{count:,}", True))
    if getattr(m, "bot", False):
        fields.append(("Type", "🤖 Bot account", True))
    return detail(Colors.success, titled("Member Joined", m), subject=m, fields=fields, ids=[("User ID", m.id)])


def member_left(m: discord.Member, kicked_by=None, reason: str | None = None) -> discord.Embed:
    kicked = bool(kicked_by)
    fields = [("User", person(m), True)]
    if kicked:
        fields.append(("Kicked By", person(kicked_by), True))
    joined = getattr(m, "joined_at", None)
    if joined:
        fields += [("Joined", ts(joined, "R"), True), ("Time in Server", age_of(joined), True)]
    created = getattr(m, "created_at", None)
    if created:
        fields.append(("Account Created", ts(created, "R"), True))
    roles = [r for r in getattr(m, "roles", []) if not r.is_default()]
    if roles:
        fields.append((f"Roles ({len(roles)})", mentions(reversed(roles)), False))
    if reason:
        fields.append(("Reason", quote(reason, 300), False))
    count = getattr(m.guild, "member_count", None)
    return detail(Colors.warn if kicked else Colors.error, titled("Member Kicked" if kicked else "Member Left", m),
                  subject=m, fields=fields, ids=[("User ID", m.id)] + ([("Members", count)] if count else []))


def _banned_fields(user, mod, reason) -> list[tuple]:
    fields = [("User", person(user), True), ("Moderator", person(mod), True)]
    created = getattr(user, "created_at", None)
    if created:
        fields.append(("Account Created", ts(created, "R"), True))
    joined = getattr(user, "joined_at", None)
    if joined:
        fields.append(("Joined", ts(joined, "R"), True))
    fields.append(("Reason", quote(reason, 300) if reason else "*No reason given*", False))
    return fields


def user_banned(user, mod=None, reason: str | None = None) -> discord.Embed:
    return detail(Colors.error, titled("Member Banned", user), subject=user, fields=_banned_fields(user, mod, reason),
                  ids=[("User ID", user.id)])


def user_unbanned(user, mod=None, reason: str | None = None) -> discord.Embed:
    return detail(Colors.success, titled("Member Unbanned", user), subject=user,
                  fields=_banned_fields(user, mod, reason), ids=[("User ID", user.id)])


def nickname_changed(m: discord.Member, old: str | None, new: str | None, actor=None) -> discord.Embed:
    show = lambda nick: f"`{nick}`" if nick else "*none (uses the username)*"  # noqa: E731
    fields = [("User", person(m), True),
              ("Changed By", "Themselves" if (actor is None or actor.id == m.id) else person(actor), True),
              ("Before", show(old), False), ("After", show(new), False)]
    return detail(Colors.blurple, titled("Nickname Changed", m), subject=m, fields=fields, ids=[("User ID", m.id)])


def roles_changed(m: discord.Member, added: list, removed: list, actor=None, reason: str | None = None) -> discord.Embed:
    if added and not removed:
        word, color = "Added", Colors.success
    elif removed and not added:
        word, color = "Removed", Colors.error
    else:
        word, color = "Updated", Colors.blurple
    fields = [("User", person(m), True), ("Responsible Actor", person(actor), True)]
    if added:
        fields.append((f"➕ Added ({len(added)})", bullets([role_line(r) for r in added]), False))
    if removed:
        fields.append((f"➖ Removed ({len(removed)})", bullets([role_line(r) for r in removed]), False))
    if reason:
        fields.append(("Reason", quote(reason, 300), False))
    fields += member_context(m)
    count = len([r for r in getattr(m, "roles", []) if not r.is_default()])
    return detail(color, titled(f"Roles {word}", m), subject=m, fields=fields,
                  ids=[("User ID", m.id), ("Roles now", count)])


def timed_out(m: discord.Member, until, actor=None, reason: str | None = None) -> discord.Embed:
    left = (until - discord.utils.utcnow()).total_seconds()
    fields = [("User", person(m), True), ("Moderator", person(actor), True),
              ("Until", f"{ts(until, 'f')}\n{ts(until, 'R')}", True),
              ("Duration", fmt_duration(left) if left > 0 else "—", True),
              ("Reason", quote(reason, 300) if reason else "*No reason given*", False)] + member_context(m)
    return detail(Colors.warn, titled("Member Timed Out", m), subject=m, fields=fields, ids=[("User ID", m.id)])


def timeout_removed(m: discord.Member, actor=None) -> discord.Embed:
    return detail(Colors.success, titled("Timeout Removed", m), subject=m,
                  fields=[("User", person(m), True), ("Removed By", person(actor), True)] + member_context(m),
                  ids=[("User ID", m.id)])


# ---------------------------------------------------------------- roles

def _role_facts(r: discord.Role) -> list[tuple]:
    facts = [("Colour", f"`{hex_color(r.color.value)}`", True),
             ("Shown separately", "Yes" if getattr(r, "hoist", False) else "No", True),
             ("Mentionable", "Yes" if getattr(r, "mentionable", False) else "No", True)]
    position = getattr(r, "position", None)
    if position is not None:
        facts.append(("Position", str(position), True))
    warn = dangerous(r)
    if warn:
        facts.append(("⚠️ Dangerous permissions", ", ".join(f"`{p}`" for p in warn), False))
    return facts


def role_created(r: discord.Role, actor=None) -> discord.Embed:
    fields = [("Role", f"{r.mention}\n`{r.name}` · `{r.id}`", True), ("Created By", person(actor), True)] + _role_facts(r)
    return detail(r.color.value or Colors.success, f"Role Created — {r.name}", fields=fields, ids=[("Role ID", r.id)],
                  thumb=_icon(r.guild), icon=_icon(r.guild))


def role_deleted(r: discord.Role, actor=None) -> discord.Embed:
    fields = [("Role", f"`@{r.name}` · `{r.id}`", True), ("Deleted By", person(actor), True)] + _role_facts(r)
    return detail(Colors.error, f"Role Deleted — {r.name}", fields=fields, ids=[("Role ID", r.id)],
                  thumb=_icon(r.guild), icon=_icon(r.guild))


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
    fields = [("Role", f"{after.mention}\n`{after.name}` · `{after.id}`", True), ("Updated By", person(actor), True)]
    members = getattr(after, "members", None)
    if members is not None:
        fields.append(("Members", str(len(members)), True))
    fields.append(("Changes", bullets([f"• {c}" for c in changes]), False))
    warn = dangerous(after)
    if warn:
        fields.append(("⚠️ Dangerous permissions now", ", ".join(f"`{p}`" for p in warn), False))
    return detail(after.color.value or Colors.blurple, f"Role Updated — {after.name}", fields=fields,
                  ids=[("Role ID", after.id)], thumb=_icon(after.guild), icon=_icon(after.guild))


# ---------------------------------------------------------------- channels and server

_CHANNEL_KINDS = ((discord.CategoryChannel, "Category"), (discord.ForumChannel, "Forum"),
                  (discord.StageChannel, "Stage channel"), (discord.VoiceChannel, "Voice channel"),
                  (discord.TextChannel, "Text channel"))


def channel_kind(c) -> str:
    return next((label for cls, label in _CHANNEL_KINDS if isinstance(c, cls)), "Channel")


def _where(c) -> str:
    category = getattr(c, "category", None)
    return f" in **{category.name}**" if category else ""


def _channel_facts(c) -> list[tuple]:
    facts = [("Type", channel_kind(c), True)]
    category = getattr(c, "category", None)
    facts.append(("Category", f"**{category.name}**" if category else "*none*", True))
    position = getattr(c, "position", None)
    if position is not None:
        facts.append(("Position", str(position), True))
    topic = getattr(c, "topic", None)
    if topic:
        facts.append(("Topic", quote(topic, 300), False))
    if getattr(c, "nsfw", False):
        facts.append(("Age-restricted", "Yes", True))
    if getattr(c, "slowmode_delay", 0):
        facts.append(("Slowmode", f"{c.slowmode_delay}s", True))
    return facts


def channel_created(c, actor=None) -> discord.Embed:
    fields = [("Channel", f"{c.mention}\n`#{c.name}` · `{c.id}`", True), ("Created By", person(actor), True)] + _channel_facts(c)
    return detail(Colors.success, f"{channel_kind(c)} Created — {c.name}", fields=fields, ids=[("Channel ID", c.id)],
                  thumb=_icon(c.guild), icon=_icon(c.guild))


def channel_deleted(c, actor=None) -> discord.Embed:
    fields = [("Channel", f"`#{c.name}` · `{c.id}`", True), ("Deleted By", person(actor), True)] + _channel_facts(c)
    return detail(Colors.error, f"{channel_kind(c)} Deleted — {c.name}", fields=fields, ids=[("Channel ID", c.id)],
                  thumb=_icon(c.guild), icon=_icon(c.guild))


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
    fields = [("Channel", f"{after.mention}\n`#{after.name}` · `{after.id}`", True), ("Updated By", person(actor), True),
              ("Type", channel_kind(after), True), ("Changes", bullets([f"• {c}" for c in changes]), False)]
    return detail(Colors.blurple, f"{channel_kind(after)} Updated — {after.name}", fields=fields,
                  ids=[("Channel ID", after.id)], thumb=_icon(after.guild), icon=_icon(after.guild))


def server_updated(after: discord.Guild, changes: list[str], actor=None) -> discord.Embed:
    fields = [("Server", f"**{after.name}**\n`{after.id}`", True), ("Updated By", person(actor), True),
              ("Changes", bullets([f"• {c}" for c in changes]), False)]
    return detail(Colors.blurple, f"Server Updated — {after.name}", fields=fields, ids=[("Server ID", after.id)],
                  thumb=_icon(after), icon=_icon(after))


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
    uses = f"{inv.max_uses} uses" if inv.max_uses else "unlimited uses"
    if not inv.max_age:
        return uses, "never"
    hours = inv.max_age // 3600
    return uses, f"in {hours}h" if hours else f"in {max(1, inv.max_age // 60)}m"


def invite_created(inv: discord.Invite) -> discord.Embed:
    uses, expires = _invite_limits(inv)
    fields = [("Invite", f"https://discord.gg/{inv.code}\n`{inv.code}`", True),
              ("Created By", person(inv.inviter), True),
              ("Channel", inv.channel.mention if inv.channel else "*a channel*", True),
              ("Max Uses", uses, True), ("Expires", expires, True)]
    if getattr(inv, "temporary", False):
        fields.append(("Temporary", "Members are kicked when they go offline", False))
    return detail(Colors.success, "Invite Created", fields=fields, ids=[("Code", inv.code)],
                  thumb=_icon(inv.guild), icon=_icon(inv.guild))


def invite_deleted(inv: discord.Invite) -> discord.Embed:
    fields = [("Invite", f"`{inv.code}`", True), ("Created By", person(getattr(inv, "inviter", None)), True),
              ("Channel", inv.channel.mention if getattr(inv, "channel", None) else None, True)]
    uses = getattr(inv, "uses", None)
    if uses is not None:
        fields.append(("Times Used", str(uses), True))
    return detail(Colors.error, "Invite Deleted", fields=fields, ids=[("Code", inv.code)],
                  thumb=_icon(inv.guild), icon=_icon(inv.guild))


def emojis_changed(guild: discord.Guild, added: list, removed: list) -> discord.Embed:
    fields = []
    if added:
        fields.append((f"➕ Added ({len(added)})", bullets([f"• {e} `:{e.name}:`" for e in added[:20]]), False))
    if removed:
        fields.append((f"➖ Removed ({len(removed)})", bullets([f"• `:{e.name}:`" for e in removed[:20]]), False))
    return detail(Colors.blurple, "Emojis Updated", fields=fields, ids=[("Server ID", guild.id)],
                  thumb=_icon(guild), icon=_icon(guild))


# ---------------------------------------------------------------- voice and commands

def voice_changed(m: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> discord.Embed | None:
    def room(channel) -> str:
        count = len(getattr(channel, "members", []) or [])
        return f"{channel.mention}\n`{getattr(channel, 'name', '?')}`" + (f" · {count} in the channel" if count else "")

    if before.channel != after.channel:
        if not before.channel:
            title, color, fields = "Joined Voice", Colors.success, [("Channel", room(after.channel), True)]
        elif not after.channel:
            title, color, fields = "Left Voice", Colors.error, [("Channel", room(before.channel), True)]
        else:
            title, color = "Moved Voice", Colors.blurple
            fields = [("From", room(before.channel), True), ("To", room(after.channel), True)]
        return detail(color, titled(title, m), subject=m, fields=[("User", person(m), True)] + fields,
                      ids=[("User ID", m.id)])
    for attr, on, off in (("mute", "Server Muted", "Server Unmuted"), ("deaf", "Server Deafened", "Server Undeafened")):
        if getattr(before, attr) != getattr(after, attr):
            now_on = getattr(after, attr)
            return detail(Colors.warn if now_on else Colors.success, titled(on if now_on else off, m), subject=m,
                          fields=[("User", person(m), True), ("Channel", room(after.channel), True)],
                          ids=[("User ID", m.id)])
    return None


def command_used(ctx, permission: str, granted_by: str, *, rank: int | None = None, ms: float | None = None,
                 error: str | None = None) -> discord.Embed:
    """One card per command run: the command, who ran it where, what allowed it, and how it ended.

    Laid out to read at a glance: the person is in the author line (no thumbnail, so the fields get the full width),
    the command is the card's text, and each field is one short line. IDs are in the footer only."""
    command = getattr(ctx, "command", None)
    if command is not None and (getattr(command, "hidden", False) or getattr(command, "cog_name", None) == "Owner"):
        # developer commands can carry code, queries or secrets, and these cards are readable by server admins
        content = f"{getattr(ctx, 'prefix', '') or ''}{command.qualified_name}"
        shown = f"`{content}` · arguments hidden"
    else:
        content = redact.scrub(ctx.message.content.strip())
        shown = f"```{shorten(content, 900)}```" if ("\n" in content or len(content) > 100) else f"`{shorten(content, 900)}`"
    failed = error is not None
    jump = getattr(ctx.message, "jump_url", None)
    where = ctx.channel.mention + (f" · [jump]({jump})" if jump else "")
    # "rank 10000 / granted by Built-in" were internal numbers: show the level, and which role gave it when one did
    authority = {"None": "Everyone", "-": "Everyone"}.get(permission, permission)
    if granted_by and granted_by not in ("Built-in", "-"):
        authority += f"\n-# via {granted_by}"
    result = ("❌ Failed" if failed else "✅ Completed") + (f" · {ms:,.0f} ms" if ms is not None else "")
    fields = [("User", ctx.author.mention, True), ("Channel", where, True), ("Permission", authority, True),
              ("Result", result, True)]
    if command is not None:
        cog = getattr(command, "cog_name", None)
        if cog:
            fields.append(("Module", cog, True))
        invoked = (getattr(ctx, "invoked_with", None) or "").lower()
        if invoked and invoked != command.name.lower() and invoked in [a.lower() for a in getattr(command, "aliases", [])]:
            fields.append(("Alias used", f"`{invoked}`", True))
    if failed:
        fields.append(("Error", quote(redact.scrub(error), 300), False))
    embed = detail(Colors.error if failed else Colors.blurple, "Command Failed" if failed else "Command Used",
                   thumb=None, text=shown, fields=fields,
                   ids=[("User ID", ctx.author.id)] + ([("Message ID", ctx.message.id)] if hasattr(ctx.message, "id") else []))
    embed.set_author(name=shorten(name_of(ctx.author), 256), icon_url=avatar_of(ctx.author))
    return embed


def _icon(guild) -> str | None:
    icon = getattr(guild, "icon", None)
    return getattr(icon, "url", None)
