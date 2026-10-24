"""Antinuke (audit-log driven, threshold based) and Antiraid (join filters)."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import timedelta

import discord
from discord.ext import commands

from core.config import settings
from core.utils import Colors, Context, UserError, antinuke_admin, has_perms, is_dangerous

A = discord.AuditLogAction
MODULES = {
    "ban": "Mass banning members", "kick": "Mass kicking members",
    "channeldelete": "Deleting channels", "channelcreate": "Creating channels",
    "roledelete": "Deleting roles", "rolecreate": "Creating roles",
    "webhook": "Creating webhooks", "emoji": "Deleting emojis/stickers",
    "botadd": "Adding bots", "vanity": "Changing the vanity URL",
    "permissions": "Granting dangerous permissions to roles/members",
    "prune": "Pruning members",
}
ACTION_MAP = {
    A.ban: "ban", A.kick: "kick", A.member_prune: "prune",
    A.channel_delete: "channeldelete", A.channel_create: "channelcreate",
    A.role_delete: "roledelete", A.role_create: "rolecreate",
    A.webhook_create: "webhook", A.emoji_delete: "emoji", A.sticker_delete: "emoji",
    A.bot_add: "botadd", A.guild_update: "vanity",
    A.role_update: "permissions", A.member_role_update: "permissions",
}
INSTANT = {"botadd", "vanity", "permissions", "prune"}  # threshold of 1
LABELS = {
    "ban": "Mass Ban", "kick": "Mass Kick", "prune": "Member Prune",
    "channeldelete": "Channel Delete", "channelcreate": "Channel Create",
    "roledelete": "Role Delete", "rolecreate": "Role Create", "webhook": "Webhook Create",
    "emoji": "Emoji Delete", "botadd": "Bot Add", "vanity": "Vanity Change", "permissions": "Permission Grant",
}


def alert_embed(*, guild, user, actor_id, module, threshold, window, punishment, highest, roles,
                reverted, failed, footer) -> discord.Embed:
    """The alert card sent to the log channel, server owner and developers."""
    e = discord.Embed(title="Antinuke Triggered", color=Colors.error, timestamp=discord.utils.utcnow())
    if user:
        e.set_author(name=user.name, icon_url=user.display_avatar.url)
    e.add_field(name="Guild", value=f"**{guild.name}**\n`{guild.id}`")
    e.add_field(name="User", value=f"{user.name if user else 'Unknown'}\n(`{actor_id}`)")
    e.add_field(name="Bot", value="Yes" if user and user.bot else "No")
    e.add_field(name="Triggered Action", value=LABELS.get(module, module))
    e.add_field(name="Threshold", value=f"{threshold} in {window}s")
    e.add_field(name="Punishment", value=punishment + (" (failed)" if failed else ""))
    e.add_field(name="Highest Role", value=highest or "None", inline=False)
    held = ", ".join(roles)
    e.add_field(name="Roles Held", value=(held[:1020] + "…") if len(held) > 1024 else (held or "None"), inline=False)
    if reverted:
        e.add_field(name="Reverted", value="\n".join(reverted)[:1024], inline=False)
    if failed:
        e.add_field(name="Problems", value="\n".join(failed)[:1024], inline=False)
    e.set_footer(text=footer)
    return e
DEFAULT = {"enabled": False, "punishment": "ban", "window": 60, "whitelist": [], "admins": [],
           "modules": {}, "log": None}


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
        if actor_id in (None, self.bot.user.id, guild.owner_id) or actor_id in cfg["whitelist"] \
                or actor_id in cfg["admins"]:
            return

        # filter the noisy events down to the dangerous ones
        if module == "vanity":
            if getattr(entry.before, "vanity_url_code", None) == getattr(entry.after, "vanity_url_code", None):
                return
        if module == "permissions":
            if entry.action == A.role_update:
                before = getattr(entry.before, "permissions", None)
                after = getattr(entry.after, "permissions", None)
                if after is None or not is_dangerous(after) or (before is not None and is_dangerous(before)
                                                                 and before.value >= after.value):
                    return
            else:
                added = getattr(entry.after, "roles", []) or []
                if not any(is_dangerous(r.permissions) for r in added if isinstance(r, discord.Role)):
                    return

        threshold = 1 if module in INSTANT else max(1, int(mod.get("threshold", 3)))
        key = (guild.id, actor_id, module)
        q = self.hits[key]
        t = time.monotonic()
        q.append(t)
        while q and t - q[0] > cfg.get("window", 60):
            q.popleft()
        if len(q) < threshold:
            return
        del self.hits[key]  # don't keep an empty deque per (guild, user, module) forever
        await self.respond(guild, cfg, actor_id, module, entry, threshold)

    async def respond(self, guild: discord.Guild, cfg: dict, actor_id: int, module: str, entry, threshold: int = 1):
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
        punishment = cfg.get("punishment", "ban")
        reason = f"Antinuke: {MODULES[module]}"
        done = []
        # revert what we safely can
        try:
            if module == "botadd" and isinstance(entry.target, (discord.Member, discord.User, discord.Object)):
                await guild.ban(entry.target, reason="Antinuke: unauthorized bot")
                done.append("banned the added bot")
            elif module == "permissions" and entry.action == A.role_update and isinstance(entry.target, discord.Role):
                before = getattr(entry.before, "permissions", None)
                if before is not None:
                    await entry.target.edit(permissions=before, reason=reason)
                    done.append(f"reverted permissions on {entry.target.name}")
            elif module == "permissions" and isinstance(entry.target, discord.Member):
                added = [r for r in getattr(entry.after, "roles", []) if isinstance(r, discord.Role)
                         and is_dangerous(r.permissions)]
                if added:
                    await entry.target.remove_roles(*added, reason=reason)
                    done.append(f"removed dangerous roles from {entry.target}")
            elif module == "vanity" and getattr(entry.before, "vanity_url_code", None):
                await guild.edit(vanity_code=entry.before.vanity_url_code, reason=reason)
                done.append("restored vanity")
        except discord.HTTPException:
            pass
        # punish
        try:
            if actor is None:
                await guild.ban(discord.Object(actor_id), reason=reason)
                done.append("banned the attacker")
            elif actor.top_role >= guild.me.top_role:
                done.append("⚠️ could not punish — attacker is above my role")
            elif punishment == "kick":
                await actor.kick(reason=reason); done.append("kicked the attacker")
            elif punishment == "strip":
                roles = [r for r in actor.roles if is_dangerous(r.permissions) and r < guild.me.top_role and not r.managed]
                await actor.remove_roles(*roles, reason=reason); done.append("stripped the attacker's staff roles")
            else:
                await actor.ban(reason=reason); done.append("banned the attacker")
        except discord.HTTPException as e:
            done.append(f"⚠️ punishment failed ({e.status})")

        outcome = {"ban": "Ban", "kick": "Kick", "strip": "Strip"}.get(punishment, punishment.title())
        if actor is None:
            outcome = "Ban (not in server)"
        reverted = [d for d in done if not d.endswith("attacker") and not d.startswith("⚠️")
                    and "staff roles" not in d]
        info = dict(guild=guild, user=user, actor_id=actor_id, module=module, threshold=threshold,
                    window=cfg.get("window", 60), punishment=outcome, highest=highest, roles=roles_held,
                    reverted=reverted, failed=[d for d in done if d.startswith("⚠️")])

        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Jump to Server", emoji="↗️",
                                        url=f"https://discord.com/channels/{guild.id}"))
        # server copies: log channel + server owner
        for d in (guild.get_channel(cfg.get("log") or 0), guild.owner):
            if d:
                try:
                    await d.send(embed=alert_embed(**info, footer="Antinuke alert"), view=view)
                except discord.HTTPException:
                    pass
        # global copy: every bot developer
        if settings.antinuke_dev_alerts:
            from cogs.authorization import developers
            for uid in await developers(self.bot):
                if guild.owner_id == uid:
                    continue  # already got the owner copy
                try:
                    dev = self.bot.get_user(uid) or await self.bot.fetch_user(uid)
                    await dev.send(embed=alert_embed(**info, footer="Global antinuke alert"), view=view)
                except discord.HTTPException:
                    pass

    # ================================================================ commands
    @commands.group(aliases=["an"], invoke_without_command=True)
    @commands.guild_only()
    async def antinuke(self, ctx: Context):
        """Configure antinuke protection"""
        await ctx.send_help(ctx.command)

    @antinuke.command(name="enable", aliases=["on"])
    async def an_enable(self, ctx: Context):
        """Turn antinuke on with recommended settings"""
        await antinuke_admin(ctx)
        cfg = await self.cfg(ctx.guild.id)
        cfg["enabled"] = True
        for m in MODULES:
            cfg["modules"].setdefault(m, {"on": True, "threshold": 3})
        await self.save(ctx.guild.id, cfg)
        await ctx.approve("Antinuke **enabled** with every module on (threshold 3 / 60s). "
                          "Make sure my role is at the **top** of the role list.")

    @antinuke.command(name="disable", aliases=["off"])
    async def an_disable(self, ctx: Context):
        """Turn antinuke off"""
        await antinuke_admin(ctx)
        cfg = await self.cfg(ctx.guild.id)
        cfg["enabled"] = False
        await self.save(ctx.guild.id, cfg)
        await ctx.approve("Antinuke **disabled**")

    @antinuke.command(name="module", aliases=["set"])
    async def an_module(self, ctx: Context, module: str, state: str, threshold: int = 3):
        """Toggle a module: `antinuke module ban on 3`. Modules: ban, kick, channeldelete, channelcreate, roledelete, rolecreate, webhook, emoji, botadd, vanity, permissions, prune"""
        await antinuke_admin(ctx)
        module = module.lower()
        if module not in MODULES:
            raise UserError(f"Unknown module. Options: {', '.join(f'`{m}`' for m in MODULES)}")
        cfg = await self.cfg(ctx.guild.id)
        cfg["modules"][module] = {"on": state.lower() in ("on", "enable", "true", "yes"), "threshold": max(1, threshold)}
        await self.save(ctx.guild.id, cfg)
        await ctx.approve(f"`{module}` is now **{'on' if cfg['modules'][module]['on'] else 'off'}**"
                          + ("" if module in INSTANT else f" (threshold **{threshold}**)"))

    @antinuke.command(name="punishment")
    async def an_punishment(self, ctx: Context, punishment: str):
        """Set the punishment: ban, kick or strip"""
        await antinuke_admin(ctx)
        if punishment.lower() not in ("ban", "kick", "strip"):
            raise UserError("Punishment must be `ban`, `kick` or `strip`")
        cfg = await self.cfg(ctx.guild.id)
        cfg["punishment"] = punishment.lower()
        await self.save(ctx.guild.id, cfg)
        await ctx.approve(f"Antinuke will **{punishment.lower()}** offenders")

    @antinuke.command(name="window")
    async def an_window(self, ctx: Context, seconds: int):
        """Set the time window thresholds are counted over"""
        await antinuke_admin(ctx)
        cfg = await self.cfg(ctx.guild.id)
        cfg["window"] = max(5, min(seconds, 3600))
        await self.save(ctx.guild.id, cfg)
        await ctx.approve(f"Threshold window set to **{cfg['window']}s**")

    @antinuke.command(name="whitelist", aliases=["wl", "trust"])
    async def an_whitelist(self, ctx: Context, user: discord.Member | discord.User):
        """Toggle a user/bot being ignored by antinuke"""
        await antinuke_admin(ctx)
        cfg = await self.cfg(ctx.guild.id)
        if user.id in cfg["whitelist"]:
            cfg["whitelist"].remove(user.id); msg = f"Removed **{user}** from the whitelist"
        else:
            cfg["whitelist"].append(user.id); msg = f"Whitelisted **{user}**"
        await self.save(ctx.guild.id, cfg)
        await ctx.approve(msg)

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

    @antinuke.command(name="config", aliases=["settings", "status"])
    async def an_config(self, ctx: Context):
        """View antinuke settings"""
        await antinuke_admin(ctx)  # the whitelist/admin list tells an attacker exactly whose account to target
        cfg = await self.cfg(ctx.guild.id)
        embed = discord.Embed(title="Antinuke", color=Colors.main,
                              description=f"Status: **{'enabled' if cfg['enabled'] else 'disabled'}** • "
                                          f"Punishment: **{cfg['punishment']}** • Window: **{cfg['window']}s**")
        lines = []
        for m, desc in MODULES.items():
            st = cfg["modules"].get(m, {})
            mark = "🟢" if st.get("on") else "🔴"
            thr = "instant" if m in INSTANT else f"{st.get('threshold', 3)}x"
            lines.append(f"{mark} `{m}` — {desc} ({thr})")
        embed.add_field(name="Modules", value="\n".join(lines), inline=False)
        embed.add_field(name="Whitelisted", value=", ".join(f"<@{u}>" for u in cfg["whitelist"]) or "none")
        embed.add_field(name="Admins", value=", ".join(f"<@{u}>" for u in cfg["admins"]) or "none")
        await ctx.send(embed=embed)

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

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
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
        if mj and not reason:
            q = self.joins[member.guild.id]
            t = time.monotonic()
            q.append(t)
            while q and t - q[0] > mj["seconds"]:
                q.popleft()
            if len(q) >= mj["joins"]:
                self.raid_until[member.guild.id] = t + 300  # raid mode for 5 minutes
            if self.raid_until.get(member.guild.id, 0) > t:
                reason = "mass join (raid mode)"
        if not reason:
            return
        p = cfg.get("punishment", "kick")
        try:
            if p == "ban":
                await member.ban(reason=f"Antiraid: {reason}")
            elif p == "timeout":
                await member.timeout(timedelta(days=1), reason=f"Antiraid: {reason}")
            else:
                await member.kick(reason=f"Antiraid: {reason}")
        except discord.HTTPException:
            pass


async def setup(bot):
    await bot.add_cog(Antinuke(bot))
