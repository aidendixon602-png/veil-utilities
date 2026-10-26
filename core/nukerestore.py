"""Putting a server back after a nuke: what was deleted is remembered for a while, then rebuilt on demand.

Discord forgets a channel's or role's settings the moment it is deleted, so the antinuke keeps a short-lived copy
of everything that gets deleted (name, type, position, category, permission overwrites, topic, slowmode, colour,
permissions, who had the role...). When an attacker is caught, those copies are turned back into real channels and
roles: roles first, so channel overwrites that pointed at a deleted role point at its replacement.

Messages in a deleted channel are gone for good; that can't be undone by anyone.
"""
from __future__ import annotations

import asyncio
import re
import time

import discord

KEEP_FOR = 900  # seconds a deleted channel or role is remembered
MAX_MEMBERS_RESTORED = 250  # members given a restored role back (each is one request)


def snapshot_channel(channel) -> dict:
    """Everything needed to rebuild a channel, as plain data (the channel object goes stale once deleted)."""
    overwrites = []
    for ow in getattr(channel, "_overwrites", None) or []:
        overwrites.append((ow.id, int(getattr(ow, "type", 0)), int(ow.allow), int(ow.deny)))
    ctype = getattr(channel, "type", discord.ChannelType.text)
    return {
        "id": channel.id, "name": channel.name, "type": getattr(ctype, "value", ctype),
        "category_id": getattr(channel, "category_id", None), "position": getattr(channel, "position", 0),
        "overwrites": overwrites, "topic": getattr(channel, "topic", None), "nsfw": bool(getattr(channel, "nsfw", False)),
        "slowmode": int(getattr(channel, "slowmode_delay", 0) or 0), "bitrate": getattr(channel, "bitrate", None),
        "user_limit": getattr(channel, "user_limit", None), "at": time.monotonic(),
    }


def snapshot_role(role) -> dict:
    """A deleted role's settings, who had it, and the channel overwrites that mentioned it."""
    guild = role.guild
    holders = [m.id for m in getattr(role, "members", [])]
    overwrites = []
    for channel in getattr(guild, "channels", []):
        for ow in getattr(channel, "_overwrites", None) or []:
            if ow.id == role.id:
                overwrites.append((channel.id, int(ow.allow), int(ow.deny)))
    colour = getattr(role, "colour", None) or getattr(role, "color", None)
    return {
        "id": role.id, "name": role.name, "permissions": int(getattr(role.permissions, "value", 0)),
        "colour": int(getattr(colour, "value", colour or 0) or 0), "hoist": bool(getattr(role, "hoist", False)),
        "mentionable": bool(getattr(role, "mentionable", False)), "position": getattr(role, "position", 1),
        "holders": holders, "overwrites": overwrites, "at": time.monotonic(),
    }


class Snapshots:
    """Recently deleted channels and roles, per id, forgotten after KEEP_FOR seconds."""

    def __init__(self):
        self.channels: dict[int, dict] = {}
        self.roles: dict[int, dict] = {}

    def _prune(self) -> None:
        now = time.monotonic()
        for store in (self.channels, self.roles):
            for key in [k for k, v in store.items() if now - v["at"] > KEEP_FOR]:
                del store[key]

    def channel_deleted(self, channel) -> None:
        self._prune()
        self.channels[channel.id] = snapshot_channel(channel)

    def role_deleted(self, role) -> None:
        self._prune()
        self.roles[role.id] = snapshot_role(role)

    async def wait_for(self, kind: str, object_id: int, timeout: float = 3.0) -> dict | None:
        """The audit log entry can arrive a moment before Discord's delete event: wait briefly for the copy."""
        store = self.channels if kind == "channel" else self.roles
        deadline = time.monotonic() + timeout
        while object_id not in store and time.monotonic() < deadline:
            await asyncio.sleep(0.25)
        return store.pop(object_id, None)


def _overwrite_map(guild, overwrites, role_map: dict[int, object]) -> dict:
    """(id, type, allow, deny) tuples -> {Role/Member: PermissionOverwrite}, following roles that were rebuilt."""
    out = {}
    for target_id, kind, allow, deny in overwrites:
        target = role_map.get(target_id) or guild.get_role(target_id) or (guild.get_member(target_id) if kind else None)
        if target is None and target_id == guild.id:
            target = guild.default_role
        if target is not None:
            out[target] = discord.PermissionOverwrite.from_pair(discord.Permissions(allow), discord.Permissions(deny))
    return out


async def restore_roles(guild, snaps: list[dict], reason: str) -> tuple[dict[int, object], list[str]]:
    """Rebuild deleted roles (settings, position, members, overwrites). Returns old id -> new role, and lines."""
    role_map: dict[int, object] = {}
    lines: list[str] = []
    for snap in sorted(snaps, key=lambda s: -s["position"]):  # highest first keeps their order
        try:
            role = await guild.create_role(name=snap["name"], permissions=discord.Permissions(snap["permissions"]),
                                           colour=discord.Colour(snap["colour"]), hoist=snap["hoist"],
                                           mentionable=snap["mentionable"], reason=reason)
        except discord.HTTPException as e:
            lines.append(f"⚠️ couldn't restore the role {snap['name']} (Discord said {e.status})")
            continue
        role_map[snap["id"]] = role
        top = getattr(getattr(guild.me, "top_role", None), "position", 0)
        if snap["position"] and top:
            try:
                await role.edit(position=max(1, min(snap["position"], top - 1)), reason=reason)
            except (discord.HTTPException, TypeError, ValueError):
                pass
        given = 0
        for member_id in snap["holders"][:MAX_MEMBERS_RESTORED]:
            member = guild.get_member(member_id)
            if member is None:
                continue
            try:
                await member.add_roles(role, reason=reason)
                given += 1
            except discord.HTTPException:
                pass
        for channel_id, allow, deny in snap["overwrites"]:
            channel = guild.get_channel(channel_id)
            if channel is None:
                continue
            try:
                await channel.set_permissions(role, overwrite=discord.PermissionOverwrite.from_pair(
                    discord.Permissions(allow), discord.Permissions(deny)), reason=reason)
            except discord.HTTPException:
                pass
        extra = f" (given back to {given} member{'s' if given != 1 else ''})" if given else ""
        lines.append(f"restored @{snap['name']}{extra}")
    return role_map, lines


async def restore_channels(guild, snaps: list[dict], role_map: dict[int, object], reason: str) -> list[str]:
    """Rebuild deleted channels: categories first, then the channels inside them, in their old order."""
    lines: list[str] = []
    category_map: dict[int, object] = {}
    is_category = discord.ChannelType.category.value
    ordered = sorted(snaps, key=lambda s: (s["type"] != is_category, s["position"]))
    for snap in ordered:
        overwrites = _overwrite_map(guild, snap["overwrites"], role_map)
        parent = category_map.get(snap["category_id"]) or (guild.get_channel(snap["category_id"])
                                                           if snap["category_id"] else None)
        kind = snap["type"]
        try:
            if kind == is_category:
                channel = await guild.create_category(snap["name"], overwrites=overwrites, position=snap["position"],
                                                      reason=reason)
                category_map[snap["id"]] = channel
            elif kind in (discord.ChannelType.voice.value, discord.ChannelType.stage_voice.value):
                extra = {k: snap[k] for k in ("bitrate", "user_limit") if snap.get(k)}
                maker = guild.create_stage_channel if kind == discord.ChannelType.stage_voice.value \
                    else guild.create_voice_channel
                channel = await maker(snap["name"], category=parent, overwrites=overwrites, position=snap["position"],
                                      reason=reason, **extra)
            else:
                channel = await guild.create_text_channel(
                    snap["name"], category=parent, overwrites=overwrites, position=snap["position"],
                    topic=snap["topic"], nsfw=snap["nsfw"], slowmode_delay=snap["slowmode"], reason=reason)
        except discord.HTTPException as e:
            lines.append(f"⚠️ couldn't restore #{snap['name']} (Discord said {e.status})")
            continue
        lines.append(f"restored #{getattr(channel, 'name', snap['name'])}")
    return lines


# how undo lines are grouped in the alert: (pattern, singular, plural, prefix shown before each name)
_GROUPS = [
    (re.compile(r"^deleted @(.+)$"), "deleted {n} role they made", "deleted {n} roles they made", "@"),
    (re.compile(r"^deleted #(.+)$"), "deleted {n} channel they made", "deleted {n} channels they made", "#"),
    (re.compile(r"^deleted the webhook (.+)$"), "deleted {n} webhook they made", "deleted {n} webhooks they made", ""),
    (re.compile(r"^deleted the invite (.+)$"), "deleted {n} invite they made", "deleted {n} invites they made", ""),
    (re.compile(r"^unbanned (.+)$"), "unbanned {n} person they banned", "unbanned {n} people they banned", ""),
    (re.compile(r"^banned again (.+)$"), "banned {n} person they unbanned again",
     "banned {n} people they unbanned again", ""),
    (re.compile(r"^restored #(.+)$"), "rebuilt {n} deleted channel", "rebuilt {n} deleted channels", "#"),
    (re.compile(r"^restored @(.+)$"), "rebuilt {n} deleted role", "rebuilt {n} deleted roles", "@"),
    (re.compile(r"^banned the bot (.+)$"), "banned {n} bot they added", "banned {n} bots they added", ""),
]


def _natural(name: str):
    """testnuke-role-2 before testnuke-role-10."""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name)]


def collapse(lines: list[str], limit: int = 5) -> list[str]:
    """Group undo lines into one line per kind, with a count and the names in order:
    'deleted 6 roles they made: @a, @b, @c, @d, @e +1 more'. Lines of other kinds are kept as they are."""
    groups: dict[int, list[str]] = {}
    out: list = []
    for line in lines:
        for index, (pattern, *_rest) in enumerate(_GROUPS):
            match = pattern.match(line)
            if match:
                if index not in groups:
                    groups[index] = []
                    out.append(index)
                if match.group(1) not in groups[index]:
                    groups[index].append(match.group(1))
                break
        else:
            if line not in out:
                out.append(line)
    result = []
    for item in out:
        if isinstance(item, str):
            result.append(item)
            continue
        _, singular, plural, prefix = _GROUPS[item]
        names = sorted(groups[item], key=_natural)
        shown = ", ".join(prefix + name for name in names[:limit])
        more = f" +{len(names) - limit} more" if len(names) > limit else ""
        result.append((singular if len(names) == 1 else plural).format(n=len(names)) + f": {shown}{more}")
    return result


def display(obj, fallback: str = "someone") -> str:
    """A safe short name for a user/role/channel in alert text."""
    name = getattr(obj, "name", None) or str(getattr(obj, "id", "") or fallback)
    return re.sub(r"[*_`~|>]", "", str(name))[:60]
