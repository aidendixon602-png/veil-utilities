"""Antinuke (audit-log driven, threshold based) and Antiraid (join filters)."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import timedelta

import discord
from discord.ext import commands

from core.config import settings
from core.utils import Colors, Context, UserError, antinuke_admin, has_perms, is_dangerous

# verbs for the "that would … at once" message
VERBS = {"ban": "ban", "kick": "kick", "channeldelete": "delete channels for", "roledelete": "delete roles for",
         "rolecreate": "create roles for", "permissions": "grant staff permissions to", "timeout": "time out",
         "unban": "unban"}

A = discord.AuditLogAction
MODULES = {
    "ban": "Mass banning members", "kick": "Mass kicking members",
    "channeldelete": "Deleting channels", "channelcreate": "Creating channels",
    "roledelete": "Deleting roles", "rolecreate": "Creating roles",
    "webhook": "Creating webhooks", "emoji": "Deleting emojis/stickers",
    "botadd": "Adding bots", "vanity": "Changing the vanity URL",
    "permissions": "Granting dangerous permissions to roles/members",
    "prune": "Pruning members",
    "timeout": "Mass timing out members", "unban": "Mass unbanning users",
}
# modules that default to a higher threshold than 3 when turned on with `antinuke enable`
DEFAULT_THRESHOLDS = {"timeout": 5}

MIDTOWN_RULES = [
    {"key": "ban", "name": "Ban", "threshold": 3, "window": 60, "punishment": "ban", "tags": "[revert]"},
    {"key": "botadd", "name": "Bot Add", "threshold": 1, "window": 60, "punishment": "ban", "tags": "[revert]"},
    {"key": "channelcreate", "name": "Channel Create", "threshold": 2, "window": 120, "punishment": "kick", "tags": "[revert]"},
    {"key": "channeldelete", "name": "Channel Delete", "threshold": 2, "window": 120, "punishment": "ban", "tags": None},
    {"key": "kick", "name": "Kick", "threshold": 3, "window": 120, "punishment": "ban", "tags": None},
    {"key": "permissions", "name": "Permission Grant", "extra": " (administrator, ban_members, kick_members, manage_channels, manage_guild, manage_webhooks)", "threshold": 1, "window": 60, "punishment": "strip", "tags": "[revert, user]"},
    {"key": "rolecreate", "name": "Role Create", "threshold": 5, "window": 120, "punishment": "strip", "tags": "[revert]"},
    {"key": "roledelete", "name": "Role Delete", "threshold": 5, "window": 120, "punishment": "strip", "tags": None},
    {"key": "roleinvite", "name": "Role Invite", "threshold": 1, "window": 60, "punishment": "strip", "tags": "[revert]"},
    {"key": "serverupdate", "name": "Server Update", "threshold": 1, "window": 60, "punishment": "strip", "tags": None},
    {"key": "timeout", "name": "Timeout", "threshold": 5, "window": 120, "punishment": "strip", "tags": "[revert]"},
    {"key": "webhook", "name": "Webhook", "threshold": 1, "window": 60, "punishment": "kick", "tags": "[revert]"},
]
RULE_LOOKUP = {r["key"]: r for r in MIDTOWN_RULES}

PRESETS = {
    "default": {
        "ban": {"threshold": 3, "window": 60, "punishment": "ban"},
        "botadd": {"threshold": 1, "window": 60, "punishment": "ban"},
        "channelcreate": {"threshold": 2, "window": 120, "punishment": "kick"},
        "channeldelete": {"threshold": 2, "window": 120, "punishment": "ban"},
        "kick": {"threshold": 3, "window": 120, "punishment": "ban"},
        "permissions": {"threshold": 1, "window": 60, "punishment": "strip"},
        "rolecreate": {"threshold": 5, "window": 120, "punishment": "strip"},
        "roledelete": {"threshold": 5, "window": 120, "punishment": "strip"},
        "roleinvite": {"threshold": 1, "window": 60, "punishment": "strip"},
        "serverupdate": {"threshold": 1, "window": 60, "punishment": "strip"},
        "timeout": {"threshold": 5, "window": 120, "punishment": "strip"},
        "webhook": {"threshold": 1, "window": 60, "punishment": "kick"},
    },
    "strict": {
        "ban": {"threshold": 1, "window": 60, "punishment": "ban"},
        "botadd": {"threshold": 1, "window": 60, "punishment": "ban"},
        "channelcreate": {"threshold": 1, "window": 60, "punishment": "ban"},
        "channeldelete": {"threshold": 1, "window": 60, "punishment": "ban"},
        "kick": {"threshold": 1, "window": 60, "punishment": "ban"},
        "permissions": {"threshold": 1, "window": 60, "punishment": "strip"},
        "rolecreate": {"threshold": 1, "window": 60, "punishment": "strip"},
        "roledelete": {"threshold": 1, "window": 60, "punishment": "strip"},
        "roleinvite": {"threshold": 1, "window": 60, "punishment": "strip"},
        "serverupdate": {"threshold": 1, "window": 60, "punishment": "strip"},
        "timeout": {"threshold": 2, "window": 60, "punishment": "strip"},
        "webhook": {"threshold": 1, "window": 60, "punishment": "ban"},
    },
    "relaxed": {
        "ban": {"threshold": 5, "window": 120, "punishment": "ban"},
        "botadd": {"threshold": 2, "window": 60, "punishment": "ban"},
        "channelcreate": {"threshold": 5, "window": 180, "punishment": "kick"},
        "channeldelete": {"threshold": 3, "window": 180, "punishment": "ban"},
        "kick": {"threshold": 5, "window": 180, "punishment": "ban"},
        "permissions": {"threshold": 2, "window": 60, "punishment": "strip"},
        "rolecreate": {"threshold": 10, "window": 180, "punishment": "strip"},
        "roledelete": {"threshold": 10, "window": 180, "punishment": "strip"},
        "roleinvite": {"threshold": 2, "window": 60, "punishment": "strip"},
        "serverupdate": {"threshold": 2, "window": 60, "punishment": "strip"},
        "timeout": {"threshold": 10, "window": 180, "punishment": "strip"},
        "webhook": {"threshold": 2, "window": 60, "punishment": "kick"},
    },
}


def parse_timeframe(tf: str | int) -> int:
    if isinstance(tf, int):
        return max(5, min(tf, 86400))
    s = str(tf).strip().lower()
    if s.endswith("s"):
        s = s[:-1]
    elif s.endswith("m"):
        return max(5, min(int(float(s[:-1]) * 60), 86400))
    elif s.endswith("h"):
        return max(5, min(int(float(s[:-1]) * 3600), 86400))
    try:
        val = int(float(s))
        return max(5, min(val, 86400))
    except (ValueError, TypeError):
        raise UserError(f"Invalid timeframe `{tf}`. Example: `60s`, `120s`, `2m`")


# channel-overwrite permissions that are staff powers inside a channel (administrator can't be set there)
OVERWRITE_DANGEROUS = discord.Permissions(manage_roles=True, manage_channels=True, manage_webhooks=True,
                                          mention_everyone=True, manage_messages=True)
ACTION_MAP = {
    A.ban: "ban", A.kick: "kick", A.member_prune: "prune",
    A.channel_delete: "channeldelete", A.channel_create: "channelcreate",
    A.role_delete: "roledelete", A.role_create: "rolecreate",
    A.webhook_create: "webhook", A.emoji_delete: "emoji", A.sticker_delete: "emoji",
    A.bot_add: "botadd", A.guild_update: "serverupdate",
    A.role_update: "permissions", A.member_role_update: "permissions",
    A.overwrite_create: "permissions", A.overwrite_update: "permissions",
    A.member_update: "timeout", A.unban: "unban",
    A.invite_create: "roleinvite",
}
INSTANT = {"botadd", "vanity", "permissions", "prune", "serverupdate", "roleinvite"}  # threshold of 1
LABELS = {
    "ban": "Mass Ban", "kick": "Mass Kick", "prune": "Member Prune",
    "channeldelete": "Channel Delete", "channelcreate": "Channel Create",
    "roledelete": "Role Delete", "rolecreate": "Role Create", "webhook": "Webhook Create",
    "emoji": "Emoji Delete", "botadd": "Bot Add", "vanity": "Vanity Change",
    "serverupdate": "Server Update", "permissions": "Permission Grant",
    "timeout": "Mass Timeout", "unban": "Mass Unban", "roleinvite": "Role Invite",
}


def gained_dangerous(before: discord.Permissions | None, after: discord.Permissions | None) -> bool:
    """True if `after` has a dangerous permission that `before` didn't (bit-wise, not by total value:
    swapping Manage Server for Administrator lowers the number but is a big escalation)."""
    if after is None:
        return False
    added = discord.Permissions(after.value & ~(before.value if before is not None else 0))
    return is_dangerous(added)


def overwrite_gained(before, after) -> bool:
    """For channel overwrite audit entries: did the allow set gain a staff power?"""
    allow_after = getattr(after, "allow", None)
    if allow_after is None:
        return False
    allow_before = getattr(before, "allow", None)
    added = allow_after.value & ~(allow_before.value if allow_before is not None else 0)
    return bool(added & OVERWRITE_DANGEROUS.value)


def alert_embed(*, guild, user, actor_id, module, threshold, window, punishment, highest, roles,
                reverted, failed, footer, via=None) -> discord.Embed:
    """The alert card sent to the log channel, server owner and developers."""
    e = discord.Embed(title="Antinuke Triggered", color=Colors.error, timestamp=discord.utils.utcnow())
    if user:
        av_url = getattr(getattr(user, "display_avatar", None), "url", None)
        e.set_author(name=getattr(user, "name", str(actor_id)), icon_url=av_url)
    e.add_field(name="Guild", value=f"**{guild.name}**\n`{guild.id}`")
    e.add_field(name="User", value=f"{user.name if user else 'Unknown'}\n(`{actor_id}`)")
    e.add_field(name="Bot", value="Yes" if user and user.bot else "No")
    e.add_field(name="Triggered Action", value=LABELS.get(module, module) + (f"\nvia bot command `{via}`" if via else ""))
    e.add_field(name="Threshold", value=f"{threshold} in {window}s")
    e.add_field(name="Punishment", value=punishment + (" (failed)" if failed else ""))
    e.add_field(name="Highest Role", value=highest or "None", inline=False)
    held = ", ".join(roles)
    e.add_field(name="Roles Held", value=(held[:1020] + "…") if len(held) > 1024 else (held or "None"), inline=False)
    if reverted:
        e.add_field(name="Response", value="\n".join(reverted)[:1024], inline=False)
    if failed:
        e.add_field(name="Problems", value="\n".join(failed)[:1024], inline=False)
    e.set_footer(text=footer)
    return e
DEFAULT = {"enabled": False, "punishment": "ban", "window": 60, "whitelist": [], "admins": [],
           "modules": {}, "presets": {}, "log": None}


class Antinuke(commands.Cog):
    """Stop rogue admins and raids before they wreck your server"""
    emoji = "🛡️"

    def __init__(self, bot):
        self.bot = bot
        self.hits: dict[tuple[int, int, str], deque] = defaultdict(deque)
        self.joins: dict[int, deque] = defaultdict(deque)
        self.raid_until: dict[int, float] = {}

    async def cfg(self, gid):
        return await self.bot.db.get_config(gid, "antinuke", DEFAULT)

    async def save(self, gid, data):
        await self.bot.db.set_config(gid, "antinuke", data)

    # ================================================================ detection
    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry: discord.AuditLogEntry):
        module = ACTION_MAP.get(entry.action)
        if not module:
            return
        guild = entry.guild
        cfg = await self.cfg(guild.id)
        if not cfg.get("enabled"):
            return
        mod = cfg["modules"].get(module)
        if not mod or not mod.get("on"):
            return
        actor_id = entry.user_id
        if not actor_id or await self._trusted(guild, cfg, actor_id):
            return

        # filter the noisy events down to the dangerous ones
        if module in ("vanity", "serverupdate"):
            if getattr(entry.before, "vanity_url_code", None) == getattr(entry.after, "vanity_url_code", None):
                return
        if module == "timeout":
            # member_update covers nick changes etc. — only count a timeout being applied
            if getattr(entry.after, "timed_out_until", None) is None:
                return
        if module == "permissions":
            if entry.action == A.role_update:
                if not gained_dangerous(getattr(entry.before, "permissions", None),
                                        getattr(entry.after, "permissions", None)):
                    return
            elif entry.action in (A.overwrite_create, A.overwrite_update):
                if not overwrite_gained(entry.before, entry.after):
                    return
            else:
                added = getattr(entry.after, "roles", []) or []
                if not any(is_dangerous(r.permissions) for r in added if isinstance(r, discord.Role)):
                    return

        rule_def = RULE_LOOKUP.get(module)
        def_th = rule_def["threshold"] if rule_def else 3
        def_win = rule_def["window"] if rule_def else 60
        threshold = 1 if module in INSTANT else max(1, int(mod.get("threshold", def_th)))
        window = int(mod.get("window", cfg.get("window", def_win)))
        key = (guild.id, actor_id, module)
        q = self.hits[key]
        t = time.monotonic()
        q.append(t)
        while q and t - q[0] > window:
            q.popleft()
        if len(q) < threshold:
            return
        del self.hits[key]  # don't keep an empty deque per (guild, user, module) forever
        await self.respond(guild, cfg, actor_id, module, entry, threshold)

    # ================================================================ bot-command guard
    async def _trusted(self, guild: discord.Guild, cfg: dict, user_id: int) -> bool:
        from core import perms as _perms
        return (user_id in (guild.owner_id, self.bot.user.id) or user_id in cfg.get("whitelist", [])
                or user_id in cfg.get("admins", []) or _perms.is_bot_developer(user_id, self.bot))

    async def guard(self, ctx: Context, module: str, amount: int = 1) -> None:
        """Count a destructive action done *through the bot* against the member who ran the command.

        Discord's audit log credits those actions to the bot, which the audit-log listener ignores, so without
        this anyone with bot permissions (a permission level or fake perms) could ban/delete freely while
        antinuke watched nothing. Uses the same modules, thresholds, window and counter as direct actions,
        but *blocks* the action that would hit the threshold instead of reacting after it happened.
        """
        guild = ctx.guild
        cfg = await self.cfg(guild.id)
        mod = cfg["modules"].get(module)
        if not cfg.get("enabled") or not mod or not mod.get("on"):
            return
        if await self._trusted(guild, cfg, ctx.author.id):
            return
        rule_def = RULE_LOOKUP.get(module)
        def_th = rule_def["threshold"] if rule_def else 3
        def_win = rule_def["window"] if rule_def else 60
        threshold = 1 if module in INSTANT else max(1, int(mod.get("threshold", def_th)))
        window = int(mod.get("window", cfg.get("window", def_win)))
        key = (guild.id, ctx.author.id, module)
        q = self.hits[key]
        t = time.monotonic()
        while q and t - q[0] > window:
            q.popleft()
        if amount > 1 and amount >= threshold:
            # one bulk request (e.g. massban) over the limit: refuse, but don't treat it as an attack
            if not q:
                self.hits.pop(key, None)
            raise UserError(f"🛡️ That would {VERBS.get(module, module)} **{amount}** at once — over this server's "
                            f"antinuke limit ({threshold} per {window}s). The server owner or an antinuke admin "
                            f"has to do this.")
        if len(q) + amount >= threshold:
            del self.hits[key]
            await self.respond(guild, cfg, ctx.author.id, module, None, threshold, via=ctx.command.qualified_name)
            raise UserError(f"🛡️ Antinuke blocked this: **{LABELS.get(module, module)}** limit reached "
                            f"({threshold} per {window}s). The server owner has been alerted.")
        q.extend([t] * amount)

    async def _revoke_bot_perms(self, guild: discord.Guild, user_id: int) -> list[str]:
        """Take away powers granted *through the bot*: a permission level and roles carrying fake perms."""
        done = []
        levels = await self.bot.db.get_config(guild.id, "permlevels")
        if (levels.get("users") or {}).pop(str(user_id), None) is not None:
            await self.bot.db.set_config(guild.id, "permlevels", levels)
            done.append("revoked their bot permission level")
        member = guild.get_member(user_id)
        if member:
            fake = await self.bot.db.get_config(guild.id, "fakeperms")
            roles = [r for r in member.roles if fake.get(str(r.id)) and not r.managed and r < guild.me.top_role]
            if roles:
                try:
                    await member.remove_roles(*roles, reason="Antinuke: revoke fake permissions")
                    done.append(f"removed {len(roles)} fake-permission role(s)")
                except discord.HTTPException:
                    done.append("⚠️ couldn't remove their fake-permission roles")
        return done

    async def respond(self, guild: discord.Guild, cfg: dict, actor_id: int, module: str, entry, threshold: int = 1,
                      via: str | None = None):
        actor = guild.get_member(actor_id)
        # snapshot before punishment strips/removes anything
        user = actor or self.bot.get_user(actor_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(actor_id)
            except discord.HTTPException:
                user = None
        roles_held = [r.name for r in reversed(actor.roles[1:])] if actor else []
        highest = actor.top_role.name if actor and len(actor.roles) > 1 else None
        mod = cfg.get("modules", {}).get(module, {})
        rule_def = RULE_LOOKUP.get(module)
        default_pun = rule_def["punishment"] if rule_def else "ban"
        default_win = rule_def["window"] if rule_def else 60
        if module == "permissions":
            # Giving administrator role or dangerous permissions must strictly strip the role, never default to ban!
            punishment = mod.get("punishment") or "strip"
        else:
            punishment = mod.get("punishment") or cfg.get("punishment", default_pun)
        window = mod.get("window") or cfg.get("window", default_win)
        reason = f"Antinuke: {MODULES.get(module, module)}"
        done = [f"blocked `{via}`"] if via else []
        # strip bot-granted powers first, so a kick (or a failed punishment) doesn't leave them armed
        done += await self._revoke_bot_perms(guild, actor_id)
        # revert what we safely can (nothing to revert when the guard blocked the action up front)
        try:
            if entry is None:
                pass
            elif module == "botadd" and isinstance(entry.target, (discord.Member, discord.User, discord.Object)):
                await guild.ban(entry.target, reason="Antinuke: unauthorized bot")
                done.append("banned the added bot")
            elif module == "permissions" and entry.action == A.role_update and isinstance(entry.target, discord.Role):
                before = getattr(entry.before, "permissions", None)
                if before is not None:
                    await entry.target.edit(permissions=before, reason=reason)
                    done.append(f"reverted permissions on {entry.target.name}")
            elif module == "permissions" and entry.action in (A.overwrite_create, A.overwrite_update) \
                    and isinstance(entry.target, discord.abc.GuildChannel):
                ow_target = getattr(entry.extra, "id", None) and (
                    guild.get_role(entry.extra.id) or guild.get_member(entry.extra.id))
                if ow_target is not None:
                    ow = entry.target.overwrites_for(ow_target)
                    for perm, value in OVERWRITE_DANGEROUS:
                        if value and getattr(ow, perm) is True:
                            setattr(ow, perm, None)
                    await entry.target.set_permissions(ow_target, overwrite=ow, reason=reason)
                    done.append(f"removed staff overwrites in #{entry.target.name}")
            elif module == "timeout" and isinstance(entry.target, discord.Member):
                await entry.target.timeout(None, reason=reason)
                done.append(f"removed the timeout from {entry.target}")
            elif module == "permissions":
                # Strip dangerous roles from target member
                target_m = getattr(entry, "target", None)
                def _can_strip_role(r):
                    if not is_dangerous(getattr(r, "permissions", None)):
                        return False
                    if getattr(r, "managed", False):
                        return False
                    try:
                        return r < guild.me.top_role
                    except Exception:
                        return getattr(r, "position", 0) < getattr(guild.me.top_role, "position", 999999)

                if isinstance(target_m, discord.Member):
                    added = [r for r in getattr(entry.after, "roles", []) if _can_strip_role(r)]
                    if not added:
                        added = [r for r in getattr(target_m, "roles", []) if _can_strip_role(r)]
                    if added:
                        await target_m.remove_roles(*added, reason=reason)
                        done.append(f"removed dangerous roles from {target_m}")
                # Also strip dangerous roles from actor if they gave it to themselves or have dangerous perms
                actor_can_be_stripped = False
                if actor and actor.id != guild.owner_id:
                    try:
                        actor_can_be_stripped = actor.top_role < guild.me.top_role
                    except Exception:
                        actor_can_be_stripped = getattr(actor.top_role, "position", 0) < getattr(guild.me.top_role, "position", 999999)

                if actor_can_be_stripped:
                    actor_dangerous = [r for r in getattr(actor, "roles", []) if _can_strip_role(r)]
                    if actor_dangerous:
                        await actor.remove_roles(*actor_dangerous, reason=reason)
                        if not any("removed dangerous roles" in d for d in done):
                            done.append(f"removed dangerous roles from {actor}")
            elif module in ("vanity", "serverupdate") and getattr(entry.before, "vanity_url_code", None):
                await guild.edit(vanity_code=entry.before.vanity_url_code, reason=reason)
                done.append("restored vanity")
        except discord.HTTPException:
            pass
        # punish
        try:
            actor_above_me = False
            if actor and getattr(guild, "me", None):
                try:
                    actor_above_me = (actor.top_role >= guild.me.top_role)
                except Exception:
                    actor_above_me = getattr(actor.top_role, "position", 0) >= getattr(guild.me.top_role, "position", 0)

            if actor is None:
                await guild.ban(discord.Object(actor_id), reason=reason)
                done.append("banned the attacker")
            elif actor_above_me:
                done.append("⚠️ could not punish — attacker is above my role")
            elif punishment == "kick":
                await actor.kick(reason=reason); done.append("kicked the attacker")
            elif punishment == "strip":
                roles = [r for r in getattr(actor, "roles", []) if is_dangerous(getattr(r, "permissions", None)) and not getattr(r, "managed", False)]
                if roles:
                    await actor.remove_roles(*roles, reason=reason)
                done.append("stripped the attacker's staff roles")
            else:
                await actor.ban(reason=reason); done.append("banned the attacker")
        except discord.HTTPException as e:
            done.append(f"⚠️ punishment failed ({e.status})")

        outcome = {"ban": "Ban", "kick": "Kick", "strip": "Strip"}.get(punishment, punishment.title())
        if actor is None:
            outcome = "Ban (not in server)"
        reverted = [d for d in done if not d.endswith("attacker") and not d.startswith("⚠️")
                    and "staff roles" not in d]
        info = dict(guild=guild, user=user, actor_id=actor_id, module=module, via=via, threshold=threshold,
                    window=window, punishment=outcome, highest=highest, roles=roles_held,
                    reverted=reverted, failed=[d for d in done if d.startswith("⚠️")])

        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Jump to Server", emoji="↗️",
                                        url=f"https://discord.com/channels/{guild.id}"))

        # 1. DM the user (target member or actor)
        target_obj = getattr(entry, "target", None)
        user_to_dm = target_obj if isinstance(target_obj, (discord.Member, discord.User)) else actor
        if user_to_dm and not getattr(user_to_dm, "bot", False):
            try:
                user_embed = discord.Embed(
                    title="🛡️ Veil Antinuke Security Notice",
                    description=f"Your administrative/dangerous role in **{guild.name}** was automatically stripped.\n\n"
                                f"**Trigger**: Unauthorized permission grant (administrator / dangerous permissions).\n"
                                f"**Action**: Roles granting dangerous permissions were safely removed by Veil Antinuke to secure the server.",
                    color=Colors.error,
                )
                user_embed.set_footer(text="Veil Zero-Trust Security • Midtown Events Department")
                await user_to_dm.send(embed=user_embed)
            except (discord.HTTPException, discord.Forbidden):
                pass

        # 2. Server copies: log channel + server owner
        log_channel = guild.get_channel(cfg.get("log") or 0)
        if not log_channel:
            text_chs = list(getattr(guild, "text_channels", []))
            for name in ("antinuke-logs", "antinuke-log", "security-logs", "veil-utilities-logging"):
                log_channel = discord.utils.get(text_chs, name=name)
                if log_channel:
                    break

        destinations = [log_channel]
        if guild.owner:
            destinations.append(guild.owner)

        for d in destinations:
            if d:
                try:
                    await d.send(embed=alert_embed(**info, footer="Antinuke alert"), view=view)
                except discord.HTTPException:
                    pass

        # 3. Global copy: every bot developer (resolved user instances)
        if settings.antinuke_dev_alerts:
            from cogs.authorization import developers
            dev_ids = await developers(self.bot)
            for uid in dev_ids:
                if guild.owner_id == uid:
                    continue  # already got the owner copy
                try:
                    dev_user = self.bot.get_user(uid) or await self.bot.fetch_user(uid)
                    if dev_user:
                        await dev_user.send(embed=alert_embed(**info, footer="Global antinuke alert • Veil Developers"), view=view)
                except (discord.HTTPException, discord.Forbidden):
                    pass

    # ================================================================ status & rules
    async def build_status_embed(self, guild_id: int) -> discord.Embed:
        cfg = await self.cfg(guild_id)
        is_enabled = bool(cfg.get("enabled", False))
        state_str = "enabled" if is_enabled else "disabled"
        lines = []
        for r in MIDTOWN_RULES:
            k = r["key"]
            mod = cfg.get("modules", {}).get(k, {})
            name = r["name"]
            extra = r.get("extra", "")
            th = mod.get("threshold", r["threshold"])
            win = mod.get("window", r["window"])
            pun = mod.get("punishment", r["punishment"])
            tags = mod.get("tags", r.get("tags"))
            tag_part = f" `{tags}`" if tags else ""
            lines.append(f"`{name}`{extra} - **{th} in {win}s → {pun}**{tag_part}")

        footer_cmd = (
            ",an on/off · ,an <action> <count> <punishment> <timeframe> · "
            ",an whitelist/unwhitelist <user> · ,an reset <action> · ,an preset save/load <name>"
        )
        desc = (
            f"Antinuke is currently **{state_str}**.\n\n"
            f"**Rules**\n"
            + "\n".join(lines)
            + f"\n\n**{footer_cmd}**"
        )
        return discord.Embed(
            title="Antinuke Status",
            description=desc,
            color=Colors.mint,
        )

    def resolve_action(self, name: str) -> str | None:
        s = name.lower().replace("_", "").replace(" ", "").replace("-", "")
        alias_map = {
            "ban": "ban", "bans": "ban",
            "botadd": "botadd", "bot": "botadd", "bots": "botadd",
            "channelcreate": "channelcreate", "createchannel": "channelcreate",
            "channeldelete": "channeldelete", "deletechannel": "channeldelete",
            "kick": "kick", "kicks": "kick",
            "permissions": "permissions", "permission": "permissions", "perm": "permissions", "perms": "permissions", "grant": "permissions",
            "rolecreate": "rolecreate", "createrole": "rolecreate",
            "roledelete": "roledelete", "deleterole": "roledelete",
            "roleinvite": "roleinvite", "invite": "roleinvite", "invites": "roleinvite",
            "serverupdate": "serverupdate", "guild": "serverupdate", "vanity": "serverupdate", "server": "serverupdate",
            "timeout": "timeout", "mute": "timeout", "timeouts": "timeout",
            "webhook": "webhook", "webhooks": "webhook",
            "emoji": "emoji", "prune": "prune", "unban": "unban",
        }
        return alias_map.get(s)

    async def _update_rule(self, ctx: Context, action: str, count: int, punishment: str, timeframe: str | int):
        await antinuke_admin(ctx)
        action_key = self.resolve_action(action)
        if not action_key:
            valid = ", ".join(f"`{r['key']}`" for r in MIDTOWN_RULES)
            raise UserError(f"Unknown action `{action}`. Valid actions:\n{valid}")
        punishment = punishment.lower()
        if punishment not in ("ban", "kick", "strip"):
            raise UserError("Punishment must be `ban`, `kick`, or `strip`")
        seconds = parse_timeframe(timeframe)
        if count < 1 or count > 100:
            raise UserError("Count must be between 1 and 100")

        cfg = await self.cfg(ctx.guild.id)
        mod = cfg["modules"].setdefault(action_key, {"on": True})
        mod["on"] = True
        mod["threshold"] = count
        mod["punishment"] = punishment
        mod["window"] = seconds
        await self.save(ctx.guild.id, cfg)

        rule_def = RULE_LOOKUP.get(action_key)
        name = rule_def["name"] if rule_def else action_key
        await ctx.approve(f"Antinuke rule for **{name}** set to **{count} in {seconds}s → {punishment}**")

    async def _show_rule(self, ctx: Context, action: str):
        cfg = await self.cfg(ctx.guild.id)
        mod = cfg.get("modules", {}).get(action, {})
        rule = RULE_LOOKUP.get(action)
        if not rule:
            raise UserError(f"Unknown action `{action}`")
        name = rule["name"]
        th = mod.get("threshold", rule["threshold"])
        win = mod.get("window", rule["window"])
        pun = mod.get("punishment", rule["punishment"])
        tags = mod.get("tags", rule.get("tags"))
        tag_str = f" `{tags}`" if tags else ""
        await ctx.neutral(f"Antinuke rule for **{name}**: **{th} in {win}s → {pun}**{tag_str}")

    # ================================================================ commands
    @commands.group(aliases=["an"], invoke_without_command=True)
    @commands.guild_only()
    async def antinuke(self, ctx: Context):
        """View antinuke rules and status overview"""
        embed = await self.build_status_embed(ctx.guild.id)
        await ctx.send(embed=embed)

    @antinuke.command(name="status", aliases=["config", "settings"])
    async def an_status(self, ctx: Context):
        """View antinuke rules and status overview"""
        embed = await self.build_status_embed(ctx.guild.id)
        await ctx.send(embed=embed)

    @antinuke.command(name="enable", aliases=["on"])
    async def an_enable(self, ctx: Context):
        """Turn antinuke on"""
        await antinuke_admin(ctx)
        cfg = await self.cfg(ctx.guild.id)
        cfg["enabled"] = True
        for r in MIDTOWN_RULES:
            k = r["key"]
            cfg["modules"].setdefault(k, {
                "on": True,
                "threshold": r["threshold"],
                "window": r["window"],
                "punishment": r["punishment"],
            })
        for m in MODULES:
            cfg["modules"].setdefault(m, {"on": True, "threshold": DEFAULT_THRESHOLDS.get(m, 3)})
        await self.save(ctx.guild.id, cfg)
        await ctx.approve("Antinuke **enabled**.")

    @antinuke.command(name="disable", aliases=["off"])
    async def an_disable(self, ctx: Context):
        """Turn antinuke off"""
        await antinuke_admin(ctx)
        cfg = await self.cfg(ctx.guild.id)
        cfg["enabled"] = False
        await self.save(ctx.guild.id, cfg)
        await ctx.approve("Antinuke **disabled**.")

    @antinuke.command(name="toggle", aliases=["on/off"])
    async def an_toggle(self, ctx: Context):
        """Toggle antinuke on or off"""
        await antinuke_admin(ctx)
        cfg = await self.cfg(ctx.guild.id)
        cfg["enabled"] = not cfg.get("enabled", False)
        if cfg["enabled"]:
            for r in MIDTOWN_RULES:
                k = r["key"]
                cfg["modules"].setdefault(k, {
                    "on": True,
                    "threshold": r["threshold"],
                    "window": r["window"],
                    "punishment": r["punishment"],
                })
        await self.save(ctx.guild.id, cfg)
        state_text = "enabled" if cfg["enabled"] else "disabled"
        await ctx.approve(f"Antinuke is now **{state_text}**.")

    @antinuke.command(name="set", aliases=["rule", "configure"])
    async def an_set(self, ctx: Context, action: str, arg2: str, arg3: str = None, arg4: str = None):
        """Configure an antinuke rule: `,an set ban 3 ban 60s` or `,an set ban on 3`"""
        await antinuke_admin(ctx)
        if arg2.lower() in ("on", "enable", "true", "yes", "off", "disable", "false", "no"):
            state = arg2.lower()
            threshold = int(arg3) if arg3 and arg3.isdigit() else 3
            k = self.resolve_action(action) or action.lower()
            cfg = await self.cfg(ctx.guild.id)
            cfg["modules"][k] = {"on": state in ("on", "enable", "true", "yes"), "threshold": threshold}
            await self.save(ctx.guild.id, cfg)
            return await ctx.approve(f"`{k}` is now **{'on' if cfg['modules'][k]['on'] else 'off'}** (threshold **{threshold}**)")
        if arg3 is None or arg4 is None:
            raise UserError("Usage: `,an set <action> <count> <punishment> <timeframe>` (e.g. `,an set ban 3 ban 60s`)")
        try:
            count = int(arg2)
        except ValueError:
            raise UserError(f"Count must be a number, got `{arg2}`")
        await self._update_rule(ctx, action, count, arg3, arg4)

    @antinuke.command(name="module")
    async def an_module(self, ctx: Context, module: str, state: str, threshold: int = 3):
        """Toggle an individual module: `antinuke module ban on 3`"""
        await antinuke_admin(ctx)
        k = self.resolve_action(module) or module.lower()
        if k not in MODULES and k not in RULE_LOOKUP:
            raise UserError(f"Unknown module. Options: {', '.join(f'`{m}`' for m in MODULES)}")
        cfg = await self.cfg(ctx.guild.id)
        state = state.lower()
        if state not in ("on", "enable", "true", "yes", "off", "disable", "false", "no"):
            raise UserError("State must be `on` or `off`")
        threshold = max(1, min(threshold, 100))
        cfg["modules"][k] = {"on": state in ("on", "enable", "true", "yes"), "threshold": threshold}
        await self.save(ctx.guild.id, cfg)
        await ctx.approve(f"`{k}` is now **{'on' if cfg['modules'][k]['on'] else 'off'}**"
                          + ("" if k in INSTANT else f" (threshold **{threshold}**)"))

    # Direct action subcommands matching Midtown Utilities rules
    @antinuke.command(name="ban")
    async def an_cmd_ban(self, ctx: Context, count: int = None, punishment: str = None, timeframe: str = None):
        """Configure the ban rule: `,an ban 3 ban 60s`"""
        if count is None:
            return await self._show_rule(ctx, "ban")
        if punishment is None or timeframe is None:
            raise UserError("Usage: `,an ban <count> <punishment> <timeframe>` (e.g. `,an ban 3 ban 60s`)")
        await self._update_rule(ctx, "ban", count, punishment, timeframe)

    @antinuke.command(name="botadd", aliases=["bot", "bots"])
    async def an_cmd_botadd(self, ctx: Context, count: int = None, punishment: str = None, timeframe: str = None):
        """Configure the bot add rule: `,an botadd 1 ban 60s`"""
        if count is None:
            return await self._show_rule(ctx, "botadd")
        if punishment is None or timeframe is None:
            raise UserError("Usage: `,an botadd <count> <punishment> <timeframe>`")
        await self._update_rule(ctx, "botadd", count, punishment, timeframe)

    @antinuke.command(name="channelcreate")
    async def an_cmd_channelcreate(self, ctx: Context, count: int = None, punishment: str = None, timeframe: str = None):
        """Configure the channel create rule: `,an channelcreate 2 kick 120s`"""
        if count is None:
            return await self._show_rule(ctx, "channelcreate")
        if punishment is None or timeframe is None:
            raise UserError("Usage: `,an channelcreate <count> <punishment> <timeframe>`")
        await self._update_rule(ctx, "channelcreate", count, punishment, timeframe)

    @antinuke.command(name="channeldelete")
    async def an_cmd_channeldelete(self, ctx: Context, count: int = None, punishment: str = None, timeframe: str = None):
        """Configure the channel delete rule: `,an channeldelete 2 ban 120s`"""
        if count is None:
            return await self._show_rule(ctx, "channeldelete")
        if punishment is None or timeframe is None:
            raise UserError("Usage: `,an channeldelete <count> <punishment> <timeframe>`")
        await self._update_rule(ctx, "channeldelete", count, punishment, timeframe)

    @antinuke.command(name="kick")
    async def an_cmd_kick(self, ctx: Context, count: int = None, punishment: str = None, timeframe: str = None):
        """Configure the kick rule: `,an kick 3 ban 120s`"""
        if count is None:
            return await self._show_rule(ctx, "kick")
        if punishment is None or timeframe is None:
            raise UserError("Usage: `,an kick <count> <punishment> <timeframe>`")
        await self._update_rule(ctx, "kick", count, punishment, timeframe)

    @antinuke.command(name="permissions", aliases=["permission", "perms"])
    async def an_cmd_permissions(self, ctx: Context, count: int = None, punishment: str = None, timeframe: str = None):
        """Configure the permission grant rule: `,an permissions 1 strip 60s`"""
        if count is None:
            return await self._show_rule(ctx, "permissions")
        if punishment is None or timeframe is None:
            raise UserError("Usage: `,an permissions <count> <punishment> <timeframe>`")
        await self._update_rule(ctx, "permissions", count, punishment, timeframe)

    @antinuke.command(name="rolecreate")
    async def an_cmd_rolecreate(self, ctx: Context, count: int = None, punishment: str = None, timeframe: str = None):
        """Configure the role create rule: `,an rolecreate 5 strip 120s`"""
        if count is None:
            return await self._show_rule(ctx, "rolecreate")
        if punishment is None or timeframe is None:
            raise UserError("Usage: `,an rolecreate <count> <punishment> <timeframe>`")
        await self._update_rule(ctx, "rolecreate", count, punishment, timeframe)

    @antinuke.command(name="roledelete")
    async def an_cmd_roledelete(self, ctx: Context, count: int = None, punishment: str = None, timeframe: str = None):
        """Configure the role delete rule: `,an roledelete 5 strip 120s`"""
        if count is None:
            return await self._show_rule(ctx, "roledelete")
        if punishment is None or timeframe is None:
            raise UserError("Usage: `,an roledelete <count> <punishment> <timeframe>`")
        await self._update_rule(ctx, "roledelete", count, punishment, timeframe)

    @antinuke.command(name="roleinvite", aliases=["invite"])
    async def an_cmd_roleinvite(self, ctx: Context, count: int = None, punishment: str = None, timeframe: str = None):
        """Configure the role invite rule: `,an roleinvite 1 strip 60s`"""
        if count is None:
            return await self._show_rule(ctx, "roleinvite")
        if punishment is None or timeframe is None:
            raise UserError("Usage: `,an roleinvite <count> <punishment> <timeframe>`")
        await self._update_rule(ctx, "roleinvite", count, punishment, timeframe)

    @antinuke.command(name="serverupdate", aliases=["server"])
    async def an_cmd_serverupdate(self, ctx: Context, count: int = None, punishment: str = None, timeframe: str = None):
        """Configure the server update rule: `,an serverupdate 1 strip 60s`"""
        if count is None:
            return await self._show_rule(ctx, "serverupdate")
        if punishment is None or timeframe is None:
            raise UserError("Usage: `,an serverupdate <count> <punishment> <timeframe>`")
        await self._update_rule(ctx, "serverupdate", count, punishment, timeframe)

    @antinuke.command(name="timeout", aliases=["mute"])
    async def an_cmd_timeout(self, ctx: Context, count: int = None, punishment: str = None, timeframe: str = None):
        """Configure the timeout rule: `,an timeout 5 strip 120s`"""
        if count is None:
            return await self._show_rule(ctx, "timeout")
        if punishment is None or timeframe is None:
            raise UserError("Usage: `,an timeout <count> <punishment> <timeframe>`")
        await self._update_rule(ctx, "timeout", count, punishment, timeframe)

    @antinuke.command(name="webhook", aliases=["webhooks"])
    async def an_cmd_webhook(self, ctx: Context, count: int = None, punishment: str = None, timeframe: str = None):
        """Configure the webhook rule: `,an webhook 1 kick 60s`"""
        if count is None:
            return await self._show_rule(ctx, "webhook")
        if punishment is None or timeframe is None:
            raise UserError("Usage: `,an webhook <count> <punishment> <timeframe>`")
        await self._update_rule(ctx, "webhook", count, punishment, timeframe)

    @antinuke.command(name="whitelist", aliases=["wl", "trust"])
    async def an_whitelist(self, ctx: Context, user: discord.Member | discord.User):
        """Add a user or bot to the antinuke whitelist"""
        await antinuke_admin(ctx)
        cfg = await self.cfg(ctx.guild.id)
        if user.id in cfg["whitelist"]:
            return await ctx.neutral(f"**{user}** is already in the whitelist")
        cfg["whitelist"].append(user.id)
        await self.save(ctx.guild.id, cfg)
        await ctx.approve(f"Whitelisted **{user}**")

    @antinuke.command(name="unwhitelist", aliases=["unwl", "untrust"])
    async def an_unwhitelist(self, ctx: Context, user: discord.Member | discord.User):
        """Remove a user or bot from the antinuke whitelist"""
        await antinuke_admin(ctx)
        cfg = await self.cfg(ctx.guild.id)
        if user.id in cfg["whitelist"]:
            cfg["whitelist"].remove(user.id)
            await self.save(ctx.guild.id, cfg)
            await ctx.approve(f"Removed **{user}** from the whitelist")
        else:
            await ctx.neutral(f"**{user}** is not in the whitelist")

    @antinuke.command(name="reset")
    async def an_reset(self, ctx: Context, action: str = None):
        """Reset an action rule or all rules to defaults: `,an reset ban` or `,an reset`"""
        await antinuke_admin(ctx)
        cfg = await self.cfg(ctx.guild.id)
        if action and action.lower() != "all":
            k = self.resolve_action(action)
            if not k or k not in RULE_LOOKUP:
                raise UserError(f"Unknown action `{action}` to reset")
            default_r = RULE_LOOKUP[k]
            cfg["modules"][k] = {
                "on": True,
                "threshold": default_r["threshold"],
                "window": default_r["window"],
                "punishment": default_r["punishment"],
            }
            await self.save(ctx.guild.id, cfg)
            await ctx.approve(f"Reset **{default_r['name']}** rule to default settings.")
        else:
            for r in MIDTOWN_RULES:
                k = r["key"]
                cfg["modules"][k] = {
                    "on": True,
                    "threshold": r["threshold"],
                    "window": r["window"],
                    "punishment": r["punishment"],
                }
            await self.save(ctx.guild.id, cfg)
            await ctx.approve("Reset all antinuke rules to Midtown defaults.")

    @antinuke.group(name="preset", invoke_without_command=True)
    async def an_preset(self, ctx: Context):
        """Manage rule presets: `,an preset save/load/list`"""
        await ctx.neutral("Preset commands:\n"
                          "• `,an preset save <name>` — save current rules to a preset\n"
                          "• `,an preset load <name>` — load a preset (default, strict, relaxed, or custom)\n"
                          "• `,an preset list` — list all presets")

    @an_preset.command(name="save")
    async def an_preset_save(self, ctx: Context, name: str):
        """Save current antinuke rules as a named preset"""
        await antinuke_admin(ctx)
        name = name.lower().strip()
        if len(name) > 30:
            raise UserError("Preset name is too long (max 30 chars)")
        cfg = await self.cfg(ctx.guild.id)
        presets = cfg.setdefault("presets", {})
        presets[name] = {k: v for k, v in cfg["modules"].items() if k in RULE_LOOKUP}
        await self.save(ctx.guild.id, cfg)
        await ctx.approve(f"Saved current rules as preset **{name}**.")

    @an_preset.command(name="load")
    async def an_preset_load(self, ctx: Context, name: str):
        """Load an antinuke preset (default, strict, relaxed, or saved custom)"""
        await antinuke_admin(ctx)
        name = name.lower().strip()
        cfg = await self.cfg(ctx.guild.id)
        target = None
        if name in PRESETS:
            target = PRESETS[name]
        elif name in cfg.get("presets", {}):
            target = cfg["presets"][name]
        else:
            available = list(PRESETS.keys()) + list(cfg.get("presets", {}).keys())
            raise UserError(f"Preset `{name}` not found. Available presets: {', '.join(f'`{p}`' for p in available)}")

        for k, v in target.items():
            rule_def = RULE_LOOKUP.get(k)
            cfg["modules"][k] = {
                "on": True,
                "threshold": v.get("threshold", rule_def["threshold"] if rule_def else 3),
                "window": v.get("window", rule_def["window"] if rule_def else 60),
                "punishment": v.get("punishment", rule_def["punishment"] if rule_def else "ban"),
            }
        await self.save(ctx.guild.id, cfg)
        await ctx.approve(f"Loaded preset **{name}**.")

    @an_preset.command(name="list")
    async def an_preset_list(self, ctx: Context):
        """List available antinuke presets"""
        cfg = await self.cfg(ctx.guild.id)
        builtins = ", ".join(f"`{p}`" for p in PRESETS.keys())
        custom = ", ".join(f"`{p}`" for p in cfg.get("presets", {}).keys()) or "none"
        embed = discord.Embed(title="Antinuke Presets", color=Colors.mint)
        embed.add_field(name="Built-in Presets", value=builtins, inline=False)
        embed.add_field(name="Custom Server Presets", value=custom, inline=False)
        await ctx.send(embed=embed)

    @antinuke.command(name="admins", aliases=["adminlist", "details"])
    async def an_admins_list(self, ctx: Context):
        """List antinuke admins and whitelisted accounts"""
        await antinuke_admin(ctx)
        cfg = await self.cfg(ctx.guild.id)
        embed = discord.Embed(title="Antinuke Trusted Accounts", color=Colors.mint)
        embed.add_field(name="Admins", value=", ".join(f"<@{u}>" for u in cfg["admins"]) or "none", inline=False)
        embed.add_field(name="Whitelisted", value=", ".join(f"<@{u}>" for u in cfg["whitelist"]) or "none", inline=False)
        await ctx.send(embed=embed)

    @antinuke.command(name="punishment")
    async def an_punishment(self, ctx: Context, punishment: str):
        """Set the default punishment: ban, kick or strip"""
        await antinuke_admin(ctx)
        if punishment.lower() not in ("ban", "kick", "strip"):
            raise UserError("Punishment must be `ban`, `kick` or `strip`")
        cfg = await self.cfg(ctx.guild.id)
        cfg["punishment"] = punishment.lower()
        await self.save(ctx.guild.id, cfg)
        await ctx.approve(f"Default antinuke punishment set to **{punishment.lower()}**")

    @antinuke.command(name="window")
    async def an_window(self, ctx: Context, seconds: int):
        """Set the default time window thresholds are counted over"""
        await antinuke_admin(ctx)
        cfg = await self.cfg(ctx.guild.id)
        cfg["window"] = max(5, min(seconds, 3600))
        await self.save(ctx.guild.id, cfg)
        await ctx.approve(f"Default threshold window set to **{cfg['window']}s**")

    @antinuke.command(name="admin")
    async def an_admin(self, ctx: Context, member: discord.Member):
        """Toggle an antinuke admin (server owner only) — admins can configure antinuke and are trusted"""
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        if ctx.author.id != ctx.guild.owner_id:
            raise UserError("Only the server owner can manage antinuke admins")
        cfg = await self.cfg(ctx.guild.id)
        if member.id in cfg["admins"]:
            cfg["admins"].remove(member.id); msg = f"**{member}** is no longer an antinuke admin"
        else:
            cfg["admins"].append(member.id); msg = f"**{member}** is now an antinuke admin"
        await self.save(ctx.guild.id, cfg)
        await ctx.approve(msg)

    @antinuke.command(name="log")
    async def an_log(self, ctx: Context, channel: discord.TextChannel = None):
        """Set where antinuke alerts go (owner always gets a DM)"""
        await antinuke_admin(ctx)
        cfg = await self.cfg(ctx.guild.id)
        cfg["log"] = channel.id if channel else None
        await self.save(ctx.guild.id, cfg)
        await ctx.approve(f"Antinuke alerts → {channel.mention}" if channel else "Antinuke log channel cleared")

    # ================================================================ antiraid
    @commands.group(aliases=["raid"], invoke_without_command=True)
    @has_perms(administrator=True)
    async def antiraid(self, ctx: Context):
        """Filter suspicious joins: mass joins, new accounts, no avatar"""
        cfg = await self.bot.db.get_config(ctx.guild.id, "antiraid")
        embed = discord.Embed(title="Antiraid", color=Colors.main)
        mj = cfg.get("massjoin")
        embed.add_field(name="Mass join", value=f"{mj['joins']} joins / {mj['seconds']}s" if mj else "off")
        embed.add_field(name="Account age", value=f"< {cfg['age']} days" if cfg.get("age") else "off")
        embed.add_field(name="No avatar", value="on" if cfg.get("avatar") else "off")
        embed.add_field(name="Punishment", value=cfg.get("punishment", "kick"))
        embed.add_field(name="Whitelist", value=str(len(cfg.get("whitelist", []))) + " users")
        await ctx.send(embed=embed)

    async def _ar_set(self, ctx, key, value, msg):
        cfg = await self.bot.db.get_config(ctx.guild.id, "antiraid")
        if value is None:
            cfg.pop(key, None)
        else:
            cfg[key] = value
        await self.bot.db.set_config(ctx.guild.id, "antiraid", cfg)
        await ctx.approve(msg)

    @antiraid.command(name="massjoin")
    @has_perms(administrator=True)
    async def ar_massjoin(self, ctx: Context, joins: int = 0, seconds: int = 10):
        """Punish everyone in a join wave: `antiraid massjoin 10 10` (0 to disable)"""
        if joins and not (2 <= joins <= 100 and 1 <= seconds <= 300):
            raise UserError("Use 2–100 joins within 1–300 seconds, e.g. `antiraid massjoin 10 10`")
        await self._ar_set(ctx, "massjoin", {"joins": joins, "seconds": seconds} if joins else None,
                           f"Mass join filter: {joins} joins / {seconds}s" if joins else "Mass join filter off")

    @antiraid.command(name="age")
    @has_perms(administrator=True)
    async def ar_age(self, ctx: Context, days: int = 0):
        """Punish accounts younger than N days (0 to disable)"""
        await self._ar_set(ctx, "age", days or None, f"Account age filter: < {days} days" if days else "Age filter off")

    @antiraid.command(name="avatar")
    @has_perms(administrator=True)
    async def ar_avatar(self, ctx: Context, state: str):
        """Punish accounts with no avatar (on/off)"""
        on = state.lower() in ("on", "true", "enable")
        await self._ar_set(ctx, "avatar", on or None, f"Avatar filter {'on' if on else 'off'}")

    @antiraid.command(name="punishment")
    @has_perms(administrator=True)
    async def ar_punishment(self, ctx: Context, punishment: str):
        """kick, ban or timeout"""
        if punishment not in ("kick", "ban", "timeout"):
            raise UserError("Use `kick`, `ban` or `timeout`")
        await self._ar_set(ctx, "punishment", punishment, f"Antiraid punishment: **{punishment}**")

    @antiraid.command(name="whitelist")
    @has_perms(administrator=True)
    async def ar_whitelist(self, ctx: Context, user: discord.User):
        """Let a specific user bypass antiraid"""
        cfg = await self.bot.db.get_config(ctx.guild.id, "antiraid")
        wl = cfg.setdefault("whitelist", [])
        wl.remove(user.id) if user.id in wl else wl.append(user.id)
        await self.bot.db.set_config(ctx.guild.id, "antiraid", cfg)
        await ctx.approve(f"Toggled antiraid whitelist for **{user}**")

    # ================================================================ antialt
    @commands.group(name="antialt", aliases=["alt", "antialts"], invoke_without_command=True)
    @has_perms(administrator=True)
    async def antialt(self, ctx: Context):
        """Configure Anti-Alt security to screen newly created burner accounts"""
        cfg = await self.bot.db.get_config(ctx.guild.id, "antiraid")
        embed = discord.Embed(title="🛡️ Anti-Alt Account Protection", color=Colors.main)
        age = cfg.get("age")
        embed.add_field(name="Minimum Account Age", value=f"`< {age} day(s)`" if age else "`Off`", inline=True)
        embed.add_field(name="Require Avatar", value="`Enabled`" if cfg.get("avatar") else "`Disabled`", inline=True)
        embed.add_field(name="Enforcement Action", value=f"`{cfg.get('punishment', 'kick').title()}`", inline=True)
        embed.add_field(name="Whitelisted Users", value=f"`{len(cfg.get('whitelist', []))} account(s)`", inline=True)
        embed.set_footer(text=f"Configure with {ctx.clean_prefix}antialt minage <days> or {ctx.clean_prefix}antialt action <kick|ban|timeout>")
        await ctx.send(embed=embed)

    @antialt.command(name="minage", aliases=["age", "days"])
    @has_perms(administrator=True)
    async def aa_minage(self, ctx: Context, days: int = 0):
        """Set minimum account age in days before joining is allowed (0 to disable)"""
        await self._ar_set(ctx, "age", days or None, f"Anti-Alt minimum account age: **<{days} days**" if days else "Anti-Alt age filter disabled")

    @antialt.command(name="action", aliases=["punish", "punishment"])
    @has_perms(administrator=True)
    async def aa_action(self, ctx: Context, action: str):
        """Set action for alt accounts: `kick`, `ban` or `timeout`"""
        action = action.lower()
        if action not in ("kick", "ban", "timeout"):
            raise UserError("Action must be `kick`, `ban` or `timeout`")
        await self._ar_set(ctx, "punishment", action, f"Anti-Alt action set to **{action}**")

    @antialt.command(name="avatar")
    @has_perms(administrator=True)
    async def aa_avatar(self, ctx: Context, state: str):
        """Toggle blocking accounts without custom avatars: `antialt avatar on/off`"""
        on = state.lower() in ("on", "true", "enable", "yes")
        await self._ar_set(ctx, "avatar", on or None, f"Anti-Alt avatar requirement **{'enabled' if on else 'disabled'}**")

    @antialt.command(name="whitelist", aliases=["allow", "trust"])
    @has_perms(administrator=True)
    async def aa_whitelist(self, ctx: Context, user: discord.User):
        """Whitelist an alt account so it can join without being blocked"""
        cfg = await self.bot.db.get_config(ctx.guild.id, "antiraid")
        wl = cfg.setdefault("whitelist", [])
        if user.id in wl:
            wl.remove(user.id)
            msg = f"Removed **{user}** from Anti-Alt whitelist"
        else:
            wl.append(user.id)
            msg = f"Whitelisted **{user}** from Anti-Alt checks"
        await self.bot.db.set_config(ctx.guild.id, "antiraid", cfg)
        await ctx.approve(msg)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        from core import perms as _perms
        if _perms.is_bot_developer(member.id, self.bot):
            return
        cfg = await self.bot.db.get_config(member.guild.id, "antiraid")
        if not cfg or member.id in cfg.get("whitelist", []):
            return
        reason = None
        if cfg.get("age") and (discord.utils.utcnow() - member.created_at).days < cfg["age"]:
            reason = f"account younger than {cfg['age']} days"
        elif cfg.get("avatar") and member.avatar is None:
            reason = "no avatar"
        mj = cfg.get("massjoin")
        wave: list[int] = []
        if mj and not reason:
            gid = member.guild.id
            q = self.joins[gid]
            t = time.monotonic()
            q.append((t, member.id))
            while q and t - q[0][0] > mj["seconds"]:
                q.popleft()
            if len(q) >= mj["joins"]:
                if self.raid_until.get(gid, 0) <= t:
                    # raid mode just started: the earlier joiners in this wave are part of it too
                    wave = [uid for _, uid in q if uid != member.id]
                    self.bot.loop.create_task(self._raid_alert(member.guild, len(q), mj["seconds"]))
                self.raid_until[gid] = t + 300  # raid mode for 5 minutes (extended while joins continue)
            if self.raid_until.get(gid, 0) > t:
                reason = "mass join (raid mode)"
            elif not q:
                self.joins.pop(gid, None)
        if wave:
            for uid in wave:
                m = member.guild.get_member(uid)
                if m and uid not in cfg.get("whitelist", []):
                    await self._raid_punish(m, cfg, "mass join (raid mode)")
        if not reason:
            return
        await self._raid_punish(member, cfg, reason)

    async def _raid_punish(self, member: discord.Member, cfg: dict, reason: str) -> None:
        p = cfg.get("punishment", "kick")
        try:
            if p == "ban":
                await member.ban(reason=f"Antiraid: {reason}", delete_message_seconds=3600)
            elif p == "timeout":
                await member.timeout(timedelta(days=1), reason=f"Antiraid: {reason}")
            else:
                await member.kick(reason=f"Antiraid: {reason}")
        except discord.HTTPException:
            pass

    async def _raid_alert(self, guild: discord.Guild, joins: int, seconds: int) -> None:
        """Tell the antinuke log channel (or the owner) that raid mode switched on."""
        cfg = await self.cfg(guild.id)
        dest = guild.get_channel(cfg.get("log") or 0) or guild.owner
        if dest is None:
            return
        e = discord.Embed(title="🚨 Raid mode on", color=Colors.error, timestamp=discord.utils.utcnow(),
                          description=f"**{joins}** members joined **{guild.name}** within {seconds}s. "
                                      "New joiners will be punished for the next 5 minutes.")
        try:
            await dest.send(embed=e)
        except discord.HTTPException:
            pass

async def setup(bot):
    await bot.add_cog(Antinuke(bot))
