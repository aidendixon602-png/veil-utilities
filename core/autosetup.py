"""Automatic protection: what a server gets without anyone running a setup command.

When the bot joins a server (and once for servers it was already in), it switches on whatever that server hasn't
set up itself: anything already configured is left exactly as it is. It then reports what it did in the antinuke
log channel, with the command to change each part. Only a server the bot has just joined also DMs its owner, so a
restart never sends an owner one DM per server.

  * antinuke on with the Default preset, raid detection (a join wave of 10 in 10s), new bots quarantined
  * automod: spam (6 messages in 5s), mass mentions (6+), zalgo text and invite links, plus Discord's own
    AutoMod slur filter (Discord keeps that word list up to date; the bot only switches it on)
  * escalation: 3 warnings = 1 hour timeout, 5 = kick; repeated automod hits get a short timeout
  * staff levels from role names: roles called Helper/Trial Mod, Moderator/Mod, Admin, Server Manager/Head Admin
    get the matching permission level. Never Co-Owner or Owner: those the server owner gives on purpose, and a
    role name alone (which anyone with Manage Roles can change) must not hand out that much.
"""
from __future__ import annotations

import logging
import re

import discord

log = logging.getLogger("veil.autosetup")

AUTOMOD_DEFAULTS = {"spam": [6, 5], "massmention": 6, "zalgo": True, "invites": True,
                    "escalate": {"hits": 3, "within": 600, "timeout": 600}}
WARN_DEFAULTS = {"3": "timeout:3600", "5": "kick"}
RAID_DEFAULTS = {"massjoin": {"joins": 10, "seconds": 10}}
# role names (lower case, letters only) -> the level they get. Nothing above Server Manager, on purpose.
STAFF_NAMES = {
    "helper": "helper", "helpers": "helper", "trialmod": "helper", "trialmoderator": "helper",
    "juniormod": "helper", "juniormoderator": "helper",
    "mod": "moderator", "mods": "moderator", "moderator": "moderator", "moderators": "moderator",
    "admin": "admin", "admins": "admin", "administrator": "admin", "administrators": "admin",
    "headadmin": "headadmin", "servermanager": "headadmin", "senioradmin": "headadmin", "headmod": "headadmin", "headmoderator": "headadmin",
}


async def _saved(db, guild_id: int, module: str) -> bool:
    """Whether the server has ever saved settings for this module (if so, it's theirs: hands off)."""
    try:
        return await db.fetchone("SELECT 1 FROM config WHERE guild_id=? AND module=?", guild_id, module) is not None
    except Exception:
        return True  # can't tell: never overwrite


def staff_roles(guild) -> dict[int, str]:
    """Role ID -> level key for roles whose name says what they are."""
    out = {}
    for role in getattr(guild, "roles", []):
        if role.is_default() or getattr(role, "managed", False):
            continue
        key = STAFF_NAMES.get(re.sub(r"[^a-z]", "", role.name.lower()))
        if key:
            out[role.id] = key
    return out


async def _slur_filter(guild) -> str | None:
    """Switch on Discord's own AutoMod slur list (one preset rule per server; skipped if they have one)."""
    me = getattr(guild, "me", None)
    if not me or not me.guild_permissions.manage_guild:
        return None
    try:
        rules = await guild.fetch_automod_rules()
        if any(r.trigger.type == discord.AutoModRuleTriggerType.keyword_preset for r in rules):
            return None
        await guild.create_automod_rule(
            name="Veil: block slurs", event_type=discord.AutoModRuleEventType.message_send,
            trigger=discord.AutoModTrigger(type=discord.AutoModRuleTriggerType.keyword_preset,
                                           presets=discord.AutoModPresets(slurs=True)),
            actions=[discord.AutoModRuleAction(type=discord.AutoModRuleActionType.block_message,
                                               custom_message="That word isn't allowed here.")],
            enabled=True, reason="Veil automatic setup")
        return "Discord's AutoMod slur filter"
    except (discord.HTTPException, AttributeError, TypeError):
        return None


async def run(bot, guild) -> list[str]:
    """Set up whatever this server hasn't set up itself. Returns what was switched on (empty: nothing to do)."""
    db = bot.db
    done: list[str] = []

    from core.perms import ghost_guards
    guarded = ghost_guards(guild)  # Ghost Vanguard is this server's guard: Veil doesn't switch on its own
    antinuke = bot.get_cog("Antinuke")
    if antinuke is not None and not guarded and not await _saved(db, guild.id, "antinuke"):
        cfg = await antinuke.cfg(guild.id)
        antinuke.enable_rules(cfg)
        from cogs.antinuke import PRESETS
        antinuke.apply_preset(cfg, PRESETS["default"])
        await antinuke.save(guild.id, cfg)
        done.append("antinuke on (Default preset), new bots quarantined until approved · `,an`")

    if not guarded and not await _saved(db, guild.id, "antiraid"):
        await db.set_config(guild.id, "antiraid", dict(RAID_DEFAULTS))
        done.append("raid detection: a wave of 10 joins in 10s · `,antiraid`")

    if not await _saved(db, guild.id, "filters"):
        cfg = await db.get_config(guild.id, "filters", {"exempt_roles": [], "exempt_channels": [],
                                                        "action": "delete", "timeout": 60})
        cfg.update({k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v)
                    for k, v in AUTOMOD_DEFAULTS.items()})
        await db.set_config(guild.id, "filters", cfg)
        done.append("automod: spam, mass mentions, zalgo and invite links; 3 hits in 10 min = 10 min timeout "
                    "· `,filter`")
    slur = await _slur_filter(guild)
    if slur:
        done.append(slur)

    if not await _saved(db, guild.id, "warnpunish"):
        await db.set_config(guild.id, "warnpunish", dict(WARN_DEFAULTS))
        done.append("warnings: 3 = 1 hour timeout, 5 = kick · `,warnpunish`")

    levels = await db.get_config(guild.id, "permlevels")
    if not levels.get("users") and not levels.get("roles"):
        mapped = staff_roles(guild)
        if mapped:
            levels["roles"] = {str(rid): key for rid, key in mapped.items()}
            await db.set_config(guild.id, "permlevels", levels)
            from core.perms import PRESETS
            names = ", ".join(f"<@&{rid}> → {PRESETS[key][0]}" for rid, key in mapped.items())
            done.append(f"staff levels from role names: {names} · `,perm`")
    return done


async def announce(bot, guild, done: list[str], *, dm_owner: bool = False) -> None:
    """Post what was switched on where antinuke alerts go; DM the owner too only when `dm_owner` (a new server)."""
    if not done:
        return
    e = discord.Embed(title="🛡️ Protection set up automatically", color=0x5865F2, description=(
        "These were off, so I switched them on. Anything you had set up yourself is unchanged.\n\n"
        + "\n".join(f"• {line}" for line in done)
        + "\n\n-# staff levels don't skip the antinuke: `,an whitelist @Role` for a staff role that should"))
    name = getattr(guild, "name", None)
    if name:  # at the top, so a DM says which server it's about before anything else
        icon = getattr(getattr(guild, "icon", None), "url", None)
        e.set_author(name=name, icon_url=icon) if icon else e.set_author(name=name)
    antinuke = bot.get_cog("Antinuke")
    channel = await antinuke._log_channel(guild, await antinuke.cfg(guild.id)) if antinuke else None
    for dest in (channel, getattr(guild, "owner", None) if dm_owner else None):
        if dest is None:
            continue
        try:
            await dest.send(embed=e)
        except (discord.HTTPException, AttributeError):
            pass


async def run_once(bot, guild, *, force: bool = False, dm_owner: bool = False) -> list[str]:
    """Run the automatic setup for a server once (recorded), unless `force`. `dm_owner` for a server just joined."""
    marker = await bot.db.get_config(guild.id, "autosetup")
    if marker.get("done") and not force:
        return []
    try:
        done = await run(bot, guild)
    except Exception:
        log.exception("Automatic setup failed in %s (%s)", getattr(guild, "name", "?"), guild.id)
        return []
    marker["done"] = True
    await bot.db.set_config(guild.id, "autosetup", marker)
    await announce(bot, guild, done, dm_owner=dm_owner)
    return done
