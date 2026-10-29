"""The `,an` panel: the antinuke's status in the same plain style as `,help`, with buttons to change it.

Home: on/off, every rule on one line, and buttons to turn the antinuke on or off, load a preset, or open a rule.
A rule: its limit, punishment and what gets undone, with buttons for on/off, the count, the time window, the
punishment, and Back. Only the server owner, antinuke admins and developers can press anything; everyone else is
told so privately.
"""
from __future__ import annotations

from types import SimpleNamespace

import discord

from core.utils import OwnedView, UserError, antinuke_admin, respond_or_edit

WINDOWS = [30, 60, 120, 300, 600]  # seconds the time button cycles through
PUNISHMENTS = ["ban", "kick", "strip"]
PUNISHMENT_WORDS = {"ban": "ban", "kick": "kick", "strip": "strip their staff roles"}


def _an():
    import cogs.antinuke as an  # imported late: the cog imports this module
    return an


def rule_state(cfg: dict, key: str) -> dict:
    """A rule's settings with the defaults filled in."""
    an = _an()
    base = an.RULE_LOOKUP[key]
    mod = (cfg.get("modules") or {}).get(key) or {}
    return {"on": bool(mod.get("on", False)),
            "threshold": 1 if key in an.INSTANT else int(mod.get("threshold", base["threshold"])),
            "window": int(mod.get("window", base["window"])),
            "punishment": "strip" if key == "permissions" else mod.get("punishment", base["punishment"])}


def rule_line(cfg: dict, key: str) -> str:
    an = _an()
    name = an.RULE_LOOKUP[key]["name"]
    st = rule_state(cfg, key)
    if not st["on"]:
        return f"`{name}` - off"
    tag = " `[revert]`" if key in an.UNDOES else ""
    return f"`{name}` - {st['threshold']} in {st['window']}s → {st['punishment']}{tag}"


def home_embed(cfg: dict, prefix: str, guild=None) -> discord.Embed:
    an = _an()
    state = "on" if cfg.get("enabled") else "off"
    lines = [f"antinuke is **{state}**.", ""] + [rule_line(cfg, r["key"]) for r in an.MIDTOWN_RULES]
    roles = len(cfg.get("whitelist_roles", []))
    extra = [f"whitelisted: {len(cfg.get('whitelist', []))}" + (f" + {roles} role{'s' if roles != 1 else ''}" if roles else ""),
             f"antinuke admins: {len(cfg.get('admins', []))}"]
    log = guild.get_channel(cfg.get("log") or 0) if guild is not None else None
    if log is not None:
        extra.append(f"alerts: {log.mention}")
    lines += ["", " · ".join(extra)]
    e = discord.Embed(title="Antinuke", description="\n".join(lines))
    e.add_field(name=f"prefix: {prefix}", value="​", inline=False)
    e.set_footer(text="pick a rule below to change it" if cfg.get("enabled") else "turn it on to protect this server")
    return e


def rule_embed(cfg: dict, key: str, prefix: str) -> discord.Embed:
    an = _an()
    rule = an.RULE_LOOKUP[key]
    st = rule_state(cfg, key)
    lines = [f"watches: {an.MODULES.get(key, rule['name']).lower()}.",
             f"limit: **{st['threshold']}** in **{st['window']}s**" + (" (always 1)" if key in an.INSTANT else ""),
             f"punishment: **{PUNISHMENT_WORDS.get(st['punishment'], st['punishment'])}**",
             f"state: **{'on' if st['on'] else 'off'}**"]
    if key in an.UNDOES:
        lines.append(f"when someone is caught: {an.UNDOES[key]}.")
    if not cfg.get("enabled"):
        lines += ["", "-# the antinuke itself is off, so no rule runs until it's turned on"]
    e = discord.Embed(title=rule["name"], description="\n".join(lines))
    e.add_field(name=f"prefix: {prefix}", value="​", inline=False)
    return e


class _Button(discord.ui.Button["AntinukePanel"]):
    def __init__(self, label: str, action: str, *, style=discord.ButtonStyle.secondary, row: int = 0,
                 disabled: bool = False):
        super().__init__(label=label, style=style, row=row, disabled=disabled)
        self.action = action

    async def callback(self, it: discord.Interaction):
        await self.view.handle(it, self.action)


class RuleSelect(discord.ui.Select["AntinukePanel"]):
    def __init__(self, cfg: dict):
        an = _an()
        options = []
        for r in an.MIDTOWN_RULES:
            st = rule_state(cfg, r["key"])
            desc = f"{st['threshold']} in {st['window']}s → {st['punishment']}" if st["on"] else "off"
            options.append(discord.SelectOption(label=r["name"], value=r["key"], description=desc))
        super().__init__(placeholder="Change a rule…", options=options, row=1)

    async def callback(self, it: discord.Interaction):
        await self.view.handle(it, "open:" + self.values[0])


class AntinukePanel(OwnedView):
    """`,an` with buttons. Every press saves straight away and redraws the panel in place."""

    def __init__(self, cog, guild, author_id: int, cfg: dict, prefix: str = ","):
        super().__init__(author_id, timeout=300)
        self.cog, self.guild, self.cfg, self.prefix = cog, guild, cfg, prefix
        self.rule: str | None = None
        self.message: discord.Message | None = None
        self._build()

    # -- what's on screen
    def embed(self) -> discord.Embed:
        if self.rule is None:
            return home_embed(self.cfg, self.prefix, self.guild)
        return rule_embed(self.cfg, self.rule, self.prefix)

    def _build(self):
        self.clear_items()
        an = _an()
        if self.rule is None:
            on = bool(self.cfg.get("enabled"))
            self.add_item(_Button("Turn off" if on else "Turn on", "toggle",
                                  style=discord.ButtonStyle.danger if on else discord.ButtonStyle.success))
            for name in an.PRESETS:
                self.add_item(_Button(name.title(), f"preset:{name}"))
            self.add_item(RuleSelect(self.cfg))
            return
        st = rule_state(self.cfg, self.rule)
        instant = self.rule in an.INSTANT
        self.add_item(_Button("Turn off" if st["on"] else "Turn on", "rule_toggle",
                              style=discord.ButtonStyle.danger if st["on"] else discord.ButtonStyle.success))
        self.add_item(_Button("−", "less", disabled=instant or st["threshold"] <= 1))
        self.add_item(_Button(f"count: {st['threshold']}", "noop", disabled=True))
        self.add_item(_Button("+", "more", disabled=instant or st["threshold"] >= 100))
        self.add_item(_Button(f"time: {st['window']}s", "window", row=1))
        self.add_item(_Button(f"punishment: {st['punishment']}", "punishment", row=1,
                              disabled=self.rule == "permissions"))
        self.add_item(_Button("← Back", "back", row=2))

    # -- who may press
    async def interaction_check(self, it: discord.Interaction) -> bool:
        if not await super().interaction_check(it):
            return False
        ctx = SimpleNamespace(guild=self.guild, author=it.user, bot=self.cog.bot)
        try:
            await antinuke_admin(ctx)
        except UserError:
            await it.response.send_message("🔒 Only the server owner or an antinuke admin can change the antinuke.",
                                           ephemeral=True)
            return False
        return True

    # -- what a press does
    async def handle(self, it: discord.Interaction, action: str) -> None:
        an = _an()
        cfg = await self.cog.cfg(self.guild.id)
        changed = True
        if action == "toggle":
            if cfg.get("enabled"):
                if not an.is_owner_or_dev(self.cog.bot, self.guild, it.user.id):
                    return await it.response.send_message(
                        "🔒 Only the server owner can turn the antinuke off.", ephemeral=True)
                cfg["enabled"] = False
                await self.cog.security_notice(self.guild, it.user, "turned the antinuke **off**")
            else:
                self.cog.enable_rules(cfg)
        elif action.startswith("preset:"):
            self.cog.enable_rules(cfg)
            self.cog.apply_preset(cfg, an.PRESETS[action.split(":", 1)[1]])
        elif action.startswith("open:"):
            self.rule, changed = action.split(":", 1)[1], False
        elif action == "back":
            self.rule, changed = None, False
        elif self.rule is not None:
            st = rule_state(cfg, self.rule)
            mod = cfg.setdefault("modules", {}).setdefault(self.rule, {})
            mod.update({k: st[k] for k in ("on", "threshold", "window", "punishment")})
            if action == "rule_toggle":
                mod["on"] = not st["on"]
            elif action == "less":
                mod["threshold"] = max(1, st["threshold"] - 1)
            elif action == "more":
                mod["threshold"] = min(100, st["threshold"] + 1)
            elif action == "window":
                later = [w for w in WINDOWS if w > st["window"]]
                mod["window"] = later[0] if later else WINDOWS[0]
            elif action == "punishment":
                mod["punishment"] = PUNISHMENTS[(PUNISHMENTS.index(st["punishment"]) + 1) % len(PUNISHMENTS)] \
                    if st["punishment"] in PUNISHMENTS else "ban"
            else:
                changed = False
        else:
            changed = False
        if changed:
            await self.cog.save(self.guild.id, cfg)
        self.cfg = cfg
        self._build()
        await respond_or_edit(it, self.embed(), self)

    async def on_timeout(self):
        if self.message is not None:
            try:
                await self.message.edit(view=None)  # keep the status, drop buttons that would fail now
            except discord.HTTPException:
                pass
