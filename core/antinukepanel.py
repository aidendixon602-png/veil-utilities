"""The `,an` control panel: Veil-branded antinuke overview, grouped rules, presets, and live rule inspector.

Home: protection state, active profile, grouped core rules (or extended monitors on Advanced), inline trust/alert
summary fields, preset buttons, and a single-click rule selector.
Rule inspector: threshold, time window, punishment, Veil command guard, auto-recovery details, and instant controls.
Only the server owner and bot developers can interact; everyone else receives a private notice.
"""
from __future__ import annotations

from copy import deepcopy
import logging
from types import SimpleNamespace

import discord

from core.utils import Colors, OwnedView, UserError, antinuke_admin, respond_or_edit

log = logging.getLogger("veil.antinuke.panel")

WINDOWS = [30, 60, 120, 300, 600]  # seconds the time button cycles through
RULES_PER_PAGE = 25
PRIMARY_RULE_COUNT = 12
PUNISHMENTS = ["ban", "kick", "strip"]
PUNISHMENT_WORDS = {"ban": "Ban", "kick": "Kick", "strip": "Strip staff roles"}

CORE_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Members & Bots", ("ban", "kick", "timeout", "botadd")),
    ("Roles & Escalation", ("permissions", "rolecreate", "roledelete")),
    ("Channels & Server", ("channelcreate", "channeldelete", "serverupdate", "webhook", "roleinvite")),
)


def _an():
    import cogs.antinuke as an  # imported late: the cog imports this module
    return an


def _brand_embed(embed: discord.Embed, *, bot=None, guild=None, section: str = "Security") -> discord.Embed:
    bot_user = getattr(bot, "user", None)
    avatar = getattr(getattr(bot_user, "display_avatar", None), "url", None)
    name = getattr(bot_user, "name", None)
    author_name = f"{name} · {section}" if isinstance(name, str) and name else f"Veil Utilities · {section}"
    if isinstance(avatar, str) and avatar:
        embed.set_author(name=author_name, icon_url=avatar)
    else:
        embed.set_author(name=author_name)
    guild_icon = getattr(getattr(guild, "icon", None), "url", None)
    if isinstance(guild_icon, str) and guild_icon:
        embed.set_thumbnail(url=guild_icon)
    return embed


def _resolve_alert_channel(cfg: dict, guild):
    if guild is None:
        return None
    get_channel = getattr(guild, "get_channel", None)
    log_id = cfg.get("log")
    if callable(get_channel) and log_id:
        explicit = get_channel(log_id)
        if explicit is not None:
            return explicit
    if hasattr(guild, "text_channels"):
        try:
            from cogs.logging import _auto_channel
            return _auto_channel(guild, "antinuke", setup_only=True)
        except Exception:
            return None
    return None


def rule_state(cfg: dict, key: str) -> dict:
    """A rule's settings with the defaults filled in."""
    an = _an()
    base = an.RULE_LOOKUP[key]
    mod = (cfg.get("modules") or {}).get(key) or {}
    return {"on": bool(mod.get("on", False)),
            "threshold": 1 if key in an.INSTANT else int(mod.get("threshold", base["threshold"])),
            "window": int(mod.get("window") or cfg.get("window", base["window"])),
            "punishment": "strip" if key == "permissions" else (mod.get("punishment") or
                                                                   cfg.get("punishment", base["punishment"]))}


def active_preset(cfg: dict) -> str | None:
    """Name of the preset matching current settings ('default', 'strict', 'relaxed'), 'custom', or None when off."""
    if not cfg.get("enabled"):
        return None
    an = _an()
    for name, target in an.PRESETS.items():
        if all(
            rule_state(cfg, k)["on"] == v.get("on", True)
            and rule_state(cfg, k)["threshold"] == (1 if k in an.INSTANT else v["threshold"])
            and rule_state(cfg, k)["window"] == v["window"]
            and rule_state(cfg, k)["punishment"] == ("strip" if k == "permissions" else v["punishment"])
            for k, v in target.items()
        ):
            return name
    return "custom"


def rule_line(cfg: dict, key: str) -> str:
    an = _an()
    name = an.RULE_LOOKUP[key]["name"]
    st = rule_state(cfg, key)
    if not st["on"]:
        return f"`{name}` — *off*"
    extras: list[str] = []
    if key in an.UNDOES:
        extras.append("*revert*")
    if (cfg.get("modules") or {}).get(key, {}).get("command") is False:
        extras.append("*commands off*")
    suffix = ("  ·  " + " · ".join(extras)) if extras else ""
    return f"`{name}` — **{st['threshold']}** in {st['window']}s → **{st['punishment']}**{suffix}"


def _ordered_primary_rules() -> list[dict]:
    an = _an()
    seen: set[str] = set()
    ordered: list[dict] = []
    for _, keys in CORE_SECTIONS:
        for k in keys:
            if k in an.RULE_LOOKUP and k not in seen:
                ordered.append(an.RULE_LOOKUP[k])
                seen.add(k)
    for r in an.MIDTOWN_RULES[:PRIMARY_RULE_COUNT]:
        if r["key"] not in seen:
            ordered.append(r)
            seen.add(r["key"])
    return ordered


def _summary_fields(embed: discord.Embed, cfg: dict, guild) -> None:
    users = len(cfg.get("whitelist", []))
    roles = len(cfg.get("whitelist_roles", []))
    admins = len(cfg.get("admins", []))
    parts: list[str] = []
    if users:
        parts.append(f"{users} user{'s' if users != 1 else ''}")
    if roles:
        parts.append(f"{roles} role{'s' if roles != 1 else ''}")
    if admins:
        parts.append(f"{admins} admin{'s' if admins != 1 else ''}")
    trusted_val = " + ".join(parts) if parts else "None"

    alert_ch = _resolve_alert_channel(cfg, guild)
    alert_val = getattr(alert_ch, "mention", None) or "Auto (Logs)"

    embed.add_field(name="Trusted", value=trusted_val, inline=True)
    embed.add_field(name="Can configure", value="Owner + devs", inline=True)
    embed.add_field(name="Alert Feed", value=alert_val, inline=True)


def home_embed(cfg: dict, prefix: str, guild=None, *, advanced: bool = False, bot=None) -> discord.Embed:
    an = _an()
    enabled = bool(cfg.get("enabled"))
    primary = an.MIDTOWN_RULES[:PRIMARY_RULE_COUNT]
    additional = an.MIDTOWN_RULES[PRIMARY_RULE_COUNT:]
    core_on = sum(rule_state(cfg, r["key"])["on"] for r in primary)
    adv_on = sum(rule_state(cfg, r["key"])["on"] for r in additional)
    preset = active_preset(cfg)

    if enabled:
        profile_label = preset.title() if preset else "Custom"
        status_line = (
            f"`ACTIVE`  ·  Profile: **{profile_label}**  ·  "
            f"**{core_on}/{len(primary)}** core  ·  **{adv_on}/{len(additional)}** advanced"
        )
    else:
        status_line = "`STANDBY`  ·  Protection is **off** — enable it below or pick a preset to arm rules"

    lines = [status_line]
    if advanced:
        lines += [
            "-# Monitors quieter raid, permission, and mass-edit vectors alongside core rules.",
            "",
            "**◈  Extended Monitors**",
            *(rule_line(cfg, r["key"]) for r in additional),
        ]
    else:
        for heading, keys in CORE_SECTIONS:
            section_rules = [k for k in keys if k in an.RULE_LOOKUP]
            if not section_rules:
                continue
            lines += ["", f"**◈  {heading}**", *(rule_line(cfg, k) for k in section_rules)]

    color = Colors.main if enabled else Colors.neutral
    title = "Antinuke · Advanced" if advanced else "Antinuke"
    e = discord.Embed(title=title, description="\n".join(lines), color=color)
    _brand_embed(e, bot=bot, guild=guild, section="Security")
    _summary_fields(e, cfg, guild)
    hint = "Pick a rule to tune it" if enabled else "Turn protection on, or load a preset"
    e.set_footer(text=f"Veil  •  Prefix: {prefix}  •  {hint}")
    return e


def rule_embed(cfg: dict, key: str, prefix: str, *, guild=None, bot=None) -> discord.Embed:
    an = _an()
    rule = an.RULE_LOOKUP[key]
    st = rule_state(cfg, key)
    enabled = bool(cfg.get("enabled"))
    watches = an.MODULES.get(key, rule["name"])
    watches_sentence = watches[:1].upper() + watches[1:]

    if not st["on"]:
        badge = "`OFF`"
        color = Colors.neutral
    elif not enabled:
        badge = "`PAUSED`"
        color = Colors.warn
    else:
        badge = "`ACTIVE`"
        color = Colors.main

    desc = [f"{badge}  ·  {watches_sentence}."]
    if not enabled:
        desc.append("-# Antinuke is currently off — enable protection on the main panel for this rule to run.")

    e = discord.Embed(title=rule["name"], description="\n".join(desc), color=color)
    _brand_embed(e, bot=bot, guild=guild, section="Rule Inspector")

    limit_val = f"**{st['threshold']}** in **{st['window']}s**" + (" *(always 1)*" if key in an.INSTANT else "")
    punish_label = PUNISHMENT_WORDS.get(st["punishment"], st["punishment"].title())
    punish_val = f"**{punish_label}**" + (" *(fixed)*" if key == "permissions" else "")
    command_on = (cfg.get("modules") or {}).get(key, {}).get("command", True) is not False
    cmd_val = "**On**" if command_on else "**Off** *(ignores Veil commands)*"

    e.add_field(name="Threshold", value=limit_val, inline=True)
    e.add_field(name="Punishment", value=punish_val, inline=True)
    e.add_field(name="Command Guard", value=cmd_val, inline=True)
    if key in an.UNDOES:
        undo = an.UNDOES[key]
        e.add_field(name="Auto-Recovery", value=undo[:1].upper() + undo[1:] + ".", inline=False)

    e.set_footer(text=f"Veil  •  Prefix: {prefix}  •  Every change saves at once")
    return e


class _Button(discord.ui.Button["AntinukePanel"]):
    def __init__(self, label: str, action: str, *, style=discord.ButtonStyle.secondary, row: int = 0,
                 disabled: bool = False):
        super().__init__(label=label, style=style, row=row, disabled=disabled)
        self.action = action

    async def callback(self, it: discord.Interaction):
        await self.view.handle(it, self.action)


class RuleSelect(discord.ui.Select["AntinukePanel"]):
    def __init__(self, cfg: dict, page: int = 0, *, advanced: bool = False):
        an = _an()
        options = []
        start = page * RULES_PER_PAGE
        rules = an.MIDTOWN_RULES[PRIMARY_RULE_COUNT:] if advanced else _ordered_primary_rules()
        for r in rules[start:start + RULES_PER_PAGE]:
            key = r["key"]
            st = rule_state(cfg, key)
            if st["on"]:
                rev = " · auto-revert" if key in an.UNDOES else ""
                desc = f"{st['threshold']} in {st['window']}s → {st['punishment']}{rev}"
            else:
                desc = "Disabled"
            options.append(discord.SelectOption(label=r["name"], value=key, description=desc[:100]))
        placeholder = "Pick an advanced rule to tune…" if advanced else "Pick a rule to tune…"
        super().__init__(placeholder=placeholder, options=options, row=1)

    async def callback(self, it: discord.Interaction):
        await self.view.handle(it, "open:" + self.values[0])


class AntinukePanel(OwnedView):
    """`,an` with buttons. Every press saves straight away and redraws the panel in place."""

    def __init__(self, cog, guild, author_id: int, cfg: dict, prefix: str = ","):
        super().__init__(author_id, timeout=300)
        self.cog, self.guild, self.cfg, self.prefix = cog, guild, cfg, prefix
        self.rule: str | None = None
        self.rule_page = 0
        self.advanced = False
        self.message: discord.Message | None = None
        self._build()

    # -- what's on screen
    def embed(self) -> discord.Embed:
        bot = getattr(self.cog, "bot", None)
        if self.rule is None:
            embed = home_embed(self.cfg, self.prefix, self.guild, advanced=self.advanced, bot=bot)
        else:
            embed = rule_embed(self.cfg, self.rule, self.prefix, guild=self.guild, bot=bot)
        an = _an()
        if an._perms.ghost_guards(self.guild):
            embed.description = ((embed.description or "") + "\n\n" + an.OVERLAP_NOTE).strip()
        return embed

    def _build(self):
        self.clear_items()
        an = _an()
        if self.rule is None:
            on = bool(self.cfg.get("enabled"))
            current_preset = active_preset(self.cfg)
            self.add_item(_Button("Turn off" if on else "Turn on", "toggle",
                                  style=discord.ButtonStyle.danger if on else discord.ButtonStyle.success))
            for name in an.PRESETS:
                style = (discord.ButtonStyle.primary if current_preset == name
                         else discord.ButtonStyle.secondary)
                self.add_item(_Button(name.title(), f"preset:{name}", style=style))
            self.add_item(_Button(
                "Main rules" if self.advanced else "Advanced",
                "show_rules",
                style=discord.ButtonStyle.primary if self.advanced else discord.ButtonStyle.secondary,
            ))
            rules = an.MIDTOWN_RULES[PRIMARY_RULE_COUNT:] if self.advanced else _ordered_primary_rules()
            total = max(1, (len(rules) + RULES_PER_PAGE - 1) // RULES_PER_PAGE)
            self.rule_page = max(0, min(self.rule_page, total - 1))
            self.add_item(RuleSelect(self.cfg, self.rule_page, advanced=self.advanced))
            if total > 1:
                self.add_item(_Button("Previous", "rules_prev", row=2, disabled=self.rule_page == 0))
                self.add_item(_Button(f"Rules {self.rule_page + 1}/{total}", "noop", row=2, disabled=True))
                self.add_item(_Button("Next", "rules_next", row=2, disabled=self.rule_page >= total - 1))
            return
        st = rule_state(self.cfg, self.rule)
        instant = self.rule in an.INSTANT
        self.add_item(_Button("Turn off" if st["on"] else "Turn on", "rule_toggle",
                              style=discord.ButtonStyle.danger if st["on"] else discord.ButtonStyle.success))
        self.add_item(_Button("−", "less", disabled=instant or st["threshold"] <= 1))
        self.add_item(_Button(f"Limit: {st['threshold']}", "noop", disabled=True))
        self.add_item(_Button("+", "more", disabled=instant or st["threshold"] >= 100))
        self.add_item(_Button(f"Window: {st['window']}s", "window", row=1))
        self.add_item(_Button(f"Action: {st['punishment'].title()}", "punishment", row=1,
                              disabled=self.rule == "permissions"))
        command_on = (self.cfg.get("modules") or {}).get(self.rule, {}).get("command", True) is not False
        self.add_item(_Button(
            f"Commands: {'On' if command_on else 'Off'}",
            "command",
            row=1,
            style=discord.ButtonStyle.secondary if command_on else discord.ButtonStyle.primary,
        ))
        self.add_item(_Button("← Back", "back", row=2))

    # -- who may press
    async def interaction_check(self, it: discord.Interaction) -> bool:
        if not await super().interaction_check(it):
            return False
        ctx = SimpleNamespace(guild=self.guild, author=it.user, bot=self.cog.bot)
        try:
            if not await antinuke_admin(ctx):
                raise UserError("Unable to verify antinuke access")
        except Exception:
            await it.response.send_message("🔒 Antinuke access could not be verified for this interaction.",
                                           ephemeral=True)
            return False
        return True

    # -- what a press does
    async def handle(self, it: discord.Interaction, action: str) -> None:
        an = _an()
        cfg = deepcopy(await self.cog.bot.db.get_config(self.guild.id, "antinuke", an.DEFAULT))
        changed = True
        notice = None
        if action == "toggle":
            if cfg.get("enabled"):
                if not an.is_owner_or_dev(self.cog.bot, self.guild, it.user.id):
                    return await it.response.send_message(
                        "🔒 Only the server owner can turn the antinuke off.", ephemeral=True)
                cfg["enabled"] = False
                notice = "turned the antinuke **off**"
            else:
                self.cog.enable_rules(cfg)
        elif action.startswith("preset:"):
            preset = action.split(":", 1)[1]
            if preset not in an.PRESETS:
                return await it.response.send_message("That preset is no longer available.", ephemeral=True)
            self.cog.enable_rules(cfg)
            self.cog.apply_preset(cfg, an.PRESETS[preset])
        elif action == "show_rules":
            self.advanced = not self.advanced
            self.rule_page = 0
            changed = False
        elif action in ("rules_prev", "rules_next"):
            rules = an.MIDTOWN_RULES[PRIMARY_RULE_COUNT:] if self.advanced else _ordered_primary_rules()
            total = max(1, (len(rules) + RULES_PER_PAGE - 1) // RULES_PER_PAGE)
            self.rule_page = max(0, min(total - 1, self.rule_page + (1 if action == "rules_next" else -1)))
            changed = False
        elif action.startswith("open:"):
            key = action.split(":", 1)[1]
            if key not in an.RULE_LOOKUP:
                return await it.response.send_message("That rule is no longer available.", ephemeral=True)
            self.rule, changed = key, False
        elif action == "back":
            self.rule, changed = None, False
        elif self.rule is not None:
            if action not in {"rule_toggle", "less", "more", "window", "command", "punishment"}:
                return await it.response.send_message("That action is unavailable.", ephemeral=True)
            if (action in {"less", "more"} and self.rule in an.INSTANT) or (
                    action == "punishment" and self.rule == "permissions"):
                return await it.response.send_message("This setting is fixed for this rule.", ephemeral=True)
            st = rule_state(cfg, self.rule)
            mod = cfg.setdefault("modules", {}).setdefault(self.rule, {})
            mod.update({k: st[k] for k in ("on", "threshold", "window", "punishment")})
            if action == "rule_toggle":
                mod["on"] = not st["on"]
                if not mod["on"]:
                    notice = f"turned antinuke rule {self.rule} off"
            elif action == "less":
                mod["threshold"] = max(1, st["threshold"] - 1)
            elif action == "more":
                mod["threshold"] = min(100, st["threshold"] + 1)
            elif action == "window":
                later = [w for w in WINDOWS if w > st["window"]]
                mod["window"] = later[0] if later else WINDOWS[0]
            elif action == "command":
                mod["command"] = mod.get("command", True) is False
                if not mod["command"]:
                    notice = f"set antinuke rule {self.rule} to ignore Veil commands"
            elif action == "punishment":
                mod["punishment"] = PUNISHMENTS[(PUNISHMENTS.index(st["punishment"]) + 1) % len(PUNISHMENTS)] \
                    if st["punishment"] in PUNISHMENTS else "ban"
            else:
                changed = False
        else:
            changed = False
        if changed:
            try:
                await self.cog.bot.db.set_config_committed(self.guild.id, "antinuke", cfg)
            except Exception:
                log.exception("Failed to save antinuke settings for guild %s", self.guild.id)
                return await it.response.send_message(
                    "Could not confirm the settings were saved. Check the current settings before retrying.", ephemeral=True)
            if notice:
                await self.cog.security_notice(self.guild, it.user, notice)
        self.cfg = cfg
        self._build()
        await respond_or_edit(it, self.embed(), self)

    async def on_timeout(self):
        if self.message is not None:
            try:
                await self.message.edit(view=None)  # keep the status, drop buttons that would fail now
            except discord.HTTPException:
                pass
