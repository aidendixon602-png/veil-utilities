"""Compact log embeds: a name line on top, one sentence of what happened, IDs in the footer.

Everything here is a plain function that returns a discord.Embed, so the look of the logs lives in one place and
can be tested without Discord. The cog (cogs/logging.py) decides where each embed goes.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable

import discord

from core.utils import Colors, shorten, ts

AUDIT_SETTLE = 0.6  # seconds to let Discord write the audit-log entry before we look for it
AUDIT_WINDOW = 15.0  # an entry older than this isn't about the event we just saw
QUOTE_LIMIT = 900
NEW_ACCOUNT_DAYS = 7


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


def card(color: int, subject=None, text: str = "", *, fields: Iterable[tuple] = (), ids: Iterable[tuple] = (),
         icon: str | None = None) -> discord.Embed:
    """The one shape every log shares. `subject` fills the name line; `fields` are (name, value[, inline])."""
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


def by(actor) -> str:
    if not actor:
        return ""
    return f" · by {'🤖 ' if getattr(actor, 'bot', False) else ''}{actor.mention}"


def why(reason: str | None) -> str:
    return f"\n{quote(reason, 300)}" if reason else ""


def hex_color(value: int) -> str:
    return f"#{value:06X}" if value else "default"


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
    lines = [f"**Message deleted** in {m.channel.mention}" + (by(actor) if actor and actor.id != m.author.id else ""),
             quote(m.content) if m.content else "*No text (embed or attachment only)*"]
    if m.attachments:
        lines.append("\n".join(f"📎 [{a.filename}]({a.url})" for a in m.attachments[:5]))
    return card(Colors.error, m.author, "\n".join(lines),
                ids=[("User ID", m.author.id), ("Message ID", m.id)])


def message_edited(before: discord.Message, after: discord.Message) -> discord.Embed:
    text = f"**Message edited** in {after.channel.mention} · [Jump to message]({after.jump_url})"
    return card(Colors.warn, after.author, text,
                fields=[("Before", quote(before.content) if before.content else "*Empty*"),
                        ("After", quote(after.content) if after.content else "*Empty*")],
                ids=[("User ID", after.author.id), ("Message ID", after.id)])


def bulk_deleted(messages: list[discord.Message]) -> discord.Embed:
    channel = messages[0].channel
    return card(Colors.error, None, f"**{len(messages)} messages deleted** in {channel.mention} · transcript attached",
                ids=[("Channel ID", channel.id)])


def transcript(messages: list[discord.Message]) -> str:
    return "\n".join(f"[{m.created_at:%Y-%m-%d %H:%M}] {m.author}: {m.content}"
                     for m in sorted(messages, key=lambda x: x.id))


# ---------------------------------------------------------------- members

def member_joined(m: discord.Member) -> discord.Embed:
    text = f"{m.mention} **joined** · account created {ts(m.created_at, 'R')}"
    if (discord.utils.utcnow() - m.created_at).days < NEW_ACCOUNT_DAYS:
        text += f"\n⚠️ New account (under {NEW_ACCOUNT_DAYS} days old)"
    count = getattr(m.guild, "member_count", None)
    ids = [("User ID", m.id)] + ([("Member", f"#{count}")] if count else [])
    return card(Colors.success, m, text, ids=ids)


def member_left(m: discord.Member, kicked_by=None, reason: str | None = None) -> discord.Embed:
    if kicked_by:
        text, color = f"{m.mention} **was kicked**{by(kicked_by)}{why(reason)}", Colors.warn
    else:
        text, color = f"{m.mention} **left**", Colors.error
    if getattr(m, "joined_at", None):
        text += f"\nJoined {ts(m.joined_at, 'R')}"
    roles = [r for r in getattr(m, "roles", []) if not r.is_default()]
    if roles:
        text += f"\nRoles: {mentions(reversed(roles))}"
    count = getattr(m.guild, "member_count", None)
    return card(color, m, text, ids=[("User ID", m.id)] + ([("Members", count)] if count else []))


def user_banned(user, mod=None, reason: str | None = None) -> discord.Embed:
    return card(Colors.error, user, f"{user.mention} **was banned**{by(mod)}{why(reason)}", ids=[("User ID", user.id)])


def user_unbanned(user, mod=None, reason: str | None = None) -> discord.Embed:
    return card(Colors.success, user, f"{user.mention} **was unbanned**{by(mod)}{why(reason)}",
                ids=[("User ID", user.id)])


def nickname_changed(m: discord.Member, old: str | None, new: str | None, actor=None) -> discord.Embed:
    show = lambda nick: f"`{nick}`" if nick else "*none*"  # noqa: E731
    text = f"{m.mention} **changed nickname**{by(actor) if actor and actor.id != m.id else ''}\n{show(old)} → {show(new)}"
    return card(Colors.blurple, m, text, ids=[("User ID", m.id)])


def roles_changed(m: discord.Member, added: list, removed: list, actor=None, reason: str | None = None) -> discord.Embed:
    if added and not removed:
        word, color = "added", Colors.success
    elif removed and not added:
        word, color = "removed", Colors.error
    else:
        word, color = "updated", Colors.blurple
    lines = [f"{m.mention} **roles {word}**{by(actor)}"]
    if added:
        lines.append(f"➕ {mentions(added)}")
    if removed:
        lines.append(f"➖ {mentions(removed)}")
    if reason:
        lines.append(quote(reason, 300))
    return card(color, m, "\n".join(lines), ids=[("User ID", m.id)])


def timed_out(m: discord.Member, until, actor=None, reason: str | None = None) -> discord.Embed:
    text = f"{m.mention} **was timed out** until {ts(until, 'f')} ({ts(until, 'R')}){by(actor)}{why(reason)}"
    return card(Colors.warn, m, text, ids=[("User ID", m.id)])


def timeout_removed(m: discord.Member, actor=None) -> discord.Embed:
    return card(Colors.success, m, f"{m.mention} **timeout removed**{by(actor)}", ids=[("User ID", m.id)])


# ---------------------------------------------------------------- roles

def role_created(r: discord.Role, actor=None) -> discord.Embed:
    text = f"**Role created** {r.mention} · colour `{hex_color(r.color.value)}`{by(actor)}"
    return card(r.color.value or Colors.success, None, text, ids=[("Role ID", r.id)], icon=_icon(r.guild))


def role_deleted(r: discord.Role, actor=None) -> discord.Embed:
    return card(Colors.error, None, f"**Role deleted** `@{r.name}`{by(actor)}", ids=[("Role ID", r.id)], icon=_icon(r.guild))


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
    text = f"**Role updated** {after.mention}{by(actor)}\n" + "\n".join(changes)
    return card(after.color.value or Colors.blurple, None, text, ids=[("Role ID", after.id)], icon=_icon(after.guild))


# ---------------------------------------------------------------- channels and server

_CHANNEL_KINDS = ((discord.CategoryChannel, "Category"), (discord.ForumChannel, "Forum"),
                  (discord.StageChannel, "Stage channel"), (discord.VoiceChannel, "Voice channel"),
                  (discord.TextChannel, "Text channel"))


def channel_kind(c) -> str:
    return next((label for cls, label in _CHANNEL_KINDS if isinstance(c, cls)), "Channel")


def _where(c) -> str:
    category = getattr(c, "category", None)
    return f" in **{category.name}**" if category else ""


def channel_created(c, actor=None) -> discord.Embed:
    return card(Colors.success, None, f"**{channel_kind(c)} created** {c.mention}{_where(c)}{by(actor)}",
                ids=[("Channel ID", c.id)], icon=_icon(c.guild))


def channel_deleted(c, actor=None) -> discord.Embed:
    return card(Colors.error, None, f"**{channel_kind(c)} deleted** `#{c.name}`{_where(c)}{by(actor)}",
                ids=[("Channel ID", c.id)], icon=_icon(c.guild))


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
    text = f"**{channel_kind(after)} updated** {after.mention}{by(actor)}\n" + "\n".join(changes)
    return card(Colors.blurple, None, text, ids=[("Channel ID", after.id)], icon=_icon(after.guild))


def server_updated(after: discord.Guild, changes: list[str], actor=None) -> discord.Embed:
    return card(Colors.blurple, None, f"**Server updated**{by(actor)}\n" + "\n".join(changes),
                ids=[("Server ID", after.id)], icon=_icon(after))


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


def invite_created(inv: discord.Invite) -> discord.Embed:
    limits = []
    if inv.max_uses:
        limits.append(f"{inv.max_uses} uses")
    if inv.max_age:
        limits.append(f"expires in {inv.max_age // 3600 or 1}h" if inv.max_age >= 3600 else f"expires in {inv.max_age // 60 or 1}m")
    text = f"**Invite created** `{inv.code}` for {inv.channel.mention if inv.channel else 'a channel'}"
    text += by(inv.inviter) + (f"\n{' · '.join(limits)}" if limits else "")
    return card(Colors.success, None, text, ids=[("Code", inv.code)], icon=_icon(inv.guild))


def invite_deleted(inv: discord.Invite) -> discord.Embed:
    return card(Colors.error, None, f"**Invite deleted** `{inv.code}`", ids=[("Code", inv.code)], icon=_icon(inv.guild))


def emojis_changed(guild: discord.Guild, added: list, removed: list) -> discord.Embed:
    lines = ["**Emojis updated**"]
    if added:
        lines.append("➕ " + " ".join(str(e) for e in added[:20]))
    if removed:
        lines.append("➖ " + ", ".join(f"`:{e.name}:`" for e in removed[:20]))
    return card(Colors.blurple, None, "\n".join(lines), ids=[("Server ID", guild.id)], icon=_icon(guild))


# ---------------------------------------------------------------- voice and commands

def voice_changed(m: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> discord.Embed | None:
    if before.channel != after.channel:
        if not before.channel:
            text, color = f"{m.mention} **joined** {after.channel.mention}", Colors.success
        elif not after.channel:
            text, color = f"{m.mention} **left** {before.channel.mention}", Colors.error
        else:
            text, color = f"{m.mention} **moved** {before.channel.mention} → {after.channel.mention}", Colors.blurple
        return card(color, m, text, ids=[("User ID", m.id)])
    for attr, on, off in (("mute", "server muted", "server unmuted"), ("deaf", "server deafened", "server undeafened")):
        if getattr(before, attr) != getattr(after, attr):
            now_on = getattr(after, attr)
            return card(Colors.warn if now_on else Colors.success, m,
                        f"{m.mention} was **{on if now_on else off}** in {after.channel.mention}",
                        ids=[("User ID", m.id)])
    return None


def command_used(ctx, permission: str, granted_by: str) -> discord.Embed:
    content = ctx.message.content.strip()
    shown = f"```{shorten(content, 500)}```" if ("\n" in content or len(content) > 100) else f"`{shorten(content, 500)}`"
    text = f"**Command used** in {ctx.channel.mention}\n{shown}\n-# Permission: {permission} · Granted by: {granted_by}"
    return card(Colors.blurple, ctx.author, text, ids=[("User ID", ctx.author.id)])


def _icon(guild) -> str | None:
    icon = getattr(guild, "icon", None)
    return getattr(icon, "url", None)
