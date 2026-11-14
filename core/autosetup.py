"""Owner-confirmed protection setup, preserving existing module settings.

No protection is enabled on join or startup. The owner can review the proposed defaults and explicitly
confirm them in their server. Discord's optional AutoMod slur rule is separate from saved Veil modules.
"""
from __future__ import annotations

import asyncio
import logging

import discord

log = logging.getLogger("veil.autosetup")

AUTOMOD_DEFAULTS = {"spam": [6, 5], "massmention": 6, "zalgo": True, "invites": True,
                    "escalate": {"hits": 3, "within": 600, "timeout": 600}}
WARN_DEFAULTS = {"3": "timeout:3600", "5": "kick"}
RAID_DEFAULTS = {"massjoin": {"joins": 10, "seconds": 10}}


SETUP_PREVIEW = (
    "**Proposed defaults** (only for Veil modules without saved settings):\n"
    "• Antinuke Default preset can ban, kick or strip roles; newly added bots are quarantined until approved\n"
    "• Raid detection: 10 joins in 10 seconds can kick joiners and temporarily pause invites and member DMs\n"
    "• Automod deletes messages for 6 messages in 5 seconds, 6 mentions, zalgo and invites; "
    "3 hits in 10 minutes = 10-minute timeout\n"
    "• Warnings: 3 = 1-hour timeout; 5 = kick\n\n"
    "Discord's slur AutoMod rule may also be created if Veil has Manage Server and no keyword preset rule exists; "
    "this check is separate from saved Veil settings. Staff levels are not assigned. "
    "Existing saved Veil settings are left unchanged. Actions require the bot's Discord permissions."
)


async def prompt_owner(bot, guild) -> None:
    """Offer the owner an opt-in; never change protection settings or send on startup."""
    owner = getattr(guild, "owner", None)
    if owner is None and getattr(guild, "owner_id", None) and callable(getattr(guild, "fetch_member", None)):
        try:
            owner = await asyncio.wait_for(guild.fetch_member(guild.owner_id), timeout=5)
        except (discord.HTTPException, asyncio.TimeoutError):
            return
    if owner is None:
        return
    try:
        settings = await bot.db.get_config(guild.id, "settings")
        prefix = settings.get("prefix", getattr(bot, "default_prefix", ",")) or ","
        prefix = str(prefix).replace("`", "")
        embed = discord.Embed(title="Set up server protection?", color=0x5865F2,
                              description=("No protection was changed by this message. Some protections may already "
                                           "be active.\n\n" + SETUP_PREVIEW +
                                           f"\n\nTo review and confirm, run `{prefix}autosetup` in this server. "
                                           "This setup will not enable anything unless you confirm. Ignore this message to do nothing."))
        embed.set_author(name=guild.name)
        await owner.send(embed=embed)
    except (discord.HTTPException, AttributeError):
        log.info("Could not send protection setup prompt for guild %s", guild.id)


async def _saved(db, guild_id: int, module: str) -> bool:
    """Whether the server has ever saved settings for this module (if so, it's theirs: hands off)."""
    try:
        return await db.fetchone("SELECT 1 FROM config WHERE guild_id=? AND module=?", guild_id, module) is not None
    except Exception:
        return True  # can't tell: never overwrite


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
            enabled=True, reason="Veil owner-confirmed protection setup")
        return "Discord's AutoMod slur filter"
    except (discord.HTTPException, AttributeError, TypeError):
        return None


async def run(bot, guild) -> list[str]:
    """Set up whatever this server hasn't set up itself. Returns what was switched on (empty: nothing to do)."""
    db = bot.db
    done: list[str] = []

    antinuke = bot.get_cog("Antinuke")
    if antinuke is not None and not await _saved(db, guild.id, "antinuke"):
        cfg = await antinuke.cfg(guild.id)
        antinuke.enable_rules(cfg)
        from cogs.antinuke import PRESETS
        antinuke.apply_preset(cfg, PRESETS["default"])
        await antinuke.save(guild.id, cfg)
        done.append("antinuke on (Default preset), new bots quarantined until approved · `,an`")

    if not await _saved(db, guild.id, "antiraid"):
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

    return done


async def announce(bot, guild, done: list[str], *, dm_owner: bool = False, prefix: str = ",") -> None:
    """Post the confirmed changes to the antinuke log and optionally DM the owner."""
    if not done:
        return
    e = discord.Embed(title="🛡️ Protection setup completed", color=0x5865F2, description=(
        "The owner confirmed setup. These changes were applied; saved Veil module settings were left unchanged.\n\n"
        + "\n".join(f"• {line.replace('`,', '`' + prefix)}" for line in done)
        + f"\n\n-# staff access is assigned separately with `{prefix}perm`"))
    from core.perms import ghost_guards
    if ghost_guards(guild) and any("antinuke" in line or "raid detection" in line for line in done):
        e.description += ("\n\n⚠️ Ghost Vanguard is also present. Disable overlapping protection in one bot "
                          "to avoid duplicate actions.")
    name = getattr(guild, "name", None)
    if name:  # at the top, so a DM says which server it's about before anything else
        icon = getattr(getattr(guild, "icon", None), "url", None)
        e.set_author(name=name, icon_url=icon) if icon else e.set_author(name=name)
    antinuke = bot.get_cog("Antinuke")
    channel = None
    if antinuke:
        try:
            # Reading the raw configuration avoids running Antinuke migrations just to announce setup.
            cfg = await bot.db.get_config(guild.id, "antinuke") or {}
            channel = await antinuke._log_channel(guild, cfg)
        except Exception:
            log.exception("Could not find setup log channel for guild %s", guild.id)
    for dest in (channel, getattr(guild, "owner", None) if dm_owner else None):
        if dest is None:
            continue
        try:
            await dest.send(embed=e)
        except (discord.HTTPException, AttributeError):
            pass


async def run_once(bot, guild, *, force: bool = False, dm_owner: bool = False, prefix: str = ",") -> list[str]:
    """Apply a confirmed setup once (recorded), unless the owner explicitly requests another run."""
    marker = await bot.db.get_config(guild.id, "autosetup")
    if marker.get("done") and not force:
        return []
    try:
        done = await run(bot, guild)
    except Exception:
        log.exception("Protection setup failed in %s (%s)", getattr(guild, "name", "?"), guild.id)
        raise  # A partial setup is possible; the caller must not report success.
    marker["done"] = True
    await bot.db.set_config(guild.id, "autosetup", marker)
    await announce(bot, guild, done, dm_owner=dm_owner, prefix=prefix)
    return done
