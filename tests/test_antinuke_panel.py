"""The `,an` panel: status in the help style, and buttons that change the antinuke for antinuke admins only."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from cogs.antinuke import PRESETS, Antinuke
from core.antinukepanel import RuleSelect
from core.utils import UserError

OWNER, STRANGER = 1, 2


class FakeDB:
    def __init__(self):
        self.store = {}

    async def get_config(self, gid, module, default=None):
        # a deep copy, like core.db: the default must never be shared between servers
        return self.store.setdefault(module, {} if default is None else json.loads(json.dumps(default)))

    async def set_config(self, gid, module, data):
        self.store[module] = data

    async def set_config_committed(self, gid, module, data):
        self.store[module] = data


def setup():
    bot = SimpleNamespace(db=FakeDB(), user=SimpleNamespace(id=4), owner_ids=set(), owner_id=None,
                          is_owner=AsyncMock(return_value=False), get_user=lambda _id: None)
    cog = Antinuke.__new__(Antinuke)
    Antinuke.__init__(cog, bot)
    guild = SimpleNamespace(id=10, owner_id=OWNER, get_channel=lambda _id: None)
    return cog, guild


def open_panel(cog, guild):
    sent = {}

    async def send(**kw):
        sent.update(kw)
        return MagicMock()
    ctx = SimpleNamespace(guild=guild, author=SimpleNamespace(id=OWNER), clean_prefix=",", send=send)
    asyncio.run(cog.send_panel(ctx))
    return sent["view"], sent["embed"]


def press(panel, label, user=OWNER):
    """Click a button by its label (or choose a rule by name) like Discord would, checks included."""
    it = SimpleNamespace(user=SimpleNamespace(id=user), client=SimpleNamespace(is_owner=AsyncMock(return_value=False)),
                         response=SimpleNamespace(edit_message=AsyncMock(), send_message=AsyncMock(),
                                                  is_done=lambda: False))

    async def go():
        if not await panel.interaction_check(it):
            return it
        for item in panel.children:
            if isinstance(item, RuleSelect):
                option = next((o for o in item.options if o.label == label), None)
                if option is not None:
                    item._values = [option.value]
                    await item.callback(it)
                    return it
            elif getattr(item, "label", None) == label:
                await item.callback(it)
                return it
        raise AssertionError(f"no control called {label!r}: {[getattr(i, 'label', None) for i in panel.children]}")
    return asyncio.run(go())


def labels(panel):
    return [getattr(i, "label", None) for i in panel.children if isinstance(i, discord.ui.Button)]


def saved(cog):
    return cog.bot.db.store["antinuke"]


def test_the_home_card_shows_the_state_and_offers_the_main_controls():
    cog, guild = setup()
    panel, embed = open_panel(cog, guild)
    assert embed.title == "Antinuke" and embed.description.startswith("antinuke is **off**.")
    assert labels(panel) == ["Turn on", "Default", "Strict", "Relaxed", "Previous", "Rules 1/3", "Next"]
    (select,) = [i for i in panel.children if isinstance(i, RuleSelect)]
    assert len(select.options) == 6 and select.options[0].label == "Ban"


def test_turning_it_on_fills_in_every_rule_and_flips_the_button():
    cog, guild = setup()
    panel, _ = open_panel(cog, guild)
    it = press(panel, "Turn on")
    assert saved(cog)["enabled"] is True and saved(cog)["modules"]["ban"]["threshold"] == 3
    assert labels(panel)[0] == "Turn off"
    assert it.response.edit_message.await_args.kwargs["embed"].description.startswith("antinuke is **on**.")
    press(panel, "Turn off")
    assert saved(cog)["enabled"] is False


def test_a_preset_button_loads_that_preset():
    cog, guild = setup()
    panel, _ = open_panel(cog, guild)
    press(panel, "Strict")
    assert saved(cog)["enabled"] is True
    assert saved(cog)["modules"]["kick"]["threshold"] == PRESETS["strict"]["kick"]["threshold"] == 1


def test_a_rule_page_changes_count_time_punishment_and_state():
    cog, guild = setup()
    panel, _ = open_panel(cog, guild)
    press(panel, "Turn on")
    it = press(panel, "Ban")
    assert it.response.edit_message.await_args.kwargs["embed"].title == "Ban"
    assert labels(panel) == ["Turn off", "−", "count: 3", "+", "time: 60s", "punishment: ban", "commands: on", "← Back"]
    press(panel, "+")
    press(panel, "+")
    press(panel, "−")
    assert saved(cog)["modules"]["ban"]["threshold"] == 4
    press(panel, "time: 60s")
    assert saved(cog)["modules"]["ban"]["window"] == 120
    press(panel, "punishment: ban")
    press(panel, "punishment: kick")
    assert saved(cog)["modules"]["ban"]["punishment"] == "strip"
    press(panel, "Turn off")
    assert saved(cog)["modules"]["ban"]["on"] is False
    press(panel, "← Back")
    assert panel.rule is None and "`Ban` - off" in panel.embed().description


def test_the_time_button_wraps_around():
    cog, guild = setup()
    panel, _ = open_panel(cog, guild)
    press(panel, "Turn on")
    press(panel, "Ban")
    for expected in (120, 300, 600, 30, 60):
        press(panel, next(label for label in labels(panel) if label.startswith("time:")))
        assert saved(cog)["modules"]["ban"]["window"] == expected


def test_fixed_rules_lock_the_buttons_that_dont_apply():
    cog, guild = setup()
    panel, _ = open_panel(cog, guild)
    press(panel, "Turn on")
    press(panel, "Permission Grant")
    buttons = {i.label: i for i in panel.children if isinstance(i, discord.ui.Button)}
    assert buttons["punishment: strip"].disabled  # granting permissions is always answered by taking them back
    assert buttons["−"].disabled and buttons["+"].disabled  # instant rule: always 1
    assert "(always 1)" in panel.embed().description


def test_only_antinuke_admins_can_press_anything():
    cog, guild = setup()
    panel, _ = open_panel(cog, guild)
    panel.author_id = STRANGER  # even the person who opened it needs to be an antinuke admin
    it = press(panel, "Turn on", user=STRANGER)
    it.response.send_message.assert_awaited_once()
    assert it.response.send_message.await_args.kwargs["ephemeral"] is True
    assert "antinuke" not in cog.bot.db.store or not saved(cog).get("enabled")


def test_antinuke_admins_listed_in_the_config_can_use_it():
    cog, guild = setup()
    cog.bot.db.store["antinuke"] = {"admins": [STRANGER], "modules": {}}
    panel, _ = open_panel(cog, guild)
    panel.author_id = STRANGER
    press(panel, "Turn on", user=STRANGER)
    assert saved(cog)["enabled"] is True


def test_when_it_times_out_the_status_stays_and_the_buttons_go():
    cog, guild = setup()
    panel, _ = open_panel(cog, guild)
    panel.message = SimpleNamespace(edit=AsyncMock())
    asyncio.run(panel.on_timeout())
    panel.message.edit.assert_awaited_once_with(view=None)


def test_text_commands_still_work_alongside_the_panel():
    cog, guild = setup()
    ctx = SimpleNamespace(guild=guild, author=SimpleNamespace(id=OWNER), approve=AsyncMock(), bot=cog.bot)
    asyncio.run(Antinuke.an_enable.callback(cog, ctx))
    asyncio.run(cog._update_rule(ctx, "ban", 7, "kick", "2m"))
    panel, embed = open_panel(cog, guild)
    assert "`Ban` - 7 in 120s → kick `[revert]`" in embed.description


# ------------------------------------------------------------------ whitelisting a role

def _role(rid, name="Trusted", default=False, members=()):
    return MagicMock(spec=discord.Role, id=rid, mention=f"<@&{rid}>", members=list(members),
                     is_default=lambda: default)


def _wl_ctx(cog, guild):
    return SimpleNamespace(guild=guild, author=SimpleNamespace(id=OWNER), approve=AsyncMock(), warn=AsyncMock(),
                           neutral=AsyncMock(), bot=cog.bot, clean_prefix=",")


def test_whitelisting_a_role_trusts_everyone_who_has_it():
    cog, guild = setup()
    trusted = _role(77, members=[object(), object()])
    holder = SimpleNamespace(id=50, roles=[SimpleNamespace(id=77)])
    outsider = SimpleNamespace(id=51, roles=[SimpleNamespace(id=78)])
    guild.get_member = {50: holder, 51: outsider}.get
    ctx = _wl_ctx(cog, guild)
    asyncio.run(Antinuke.an_whitelist.callback(cog, ctx, trusted))
    assert saved(cog)["whitelist_roles"] == [77] and saved(cog).get("whitelist", []) == []
    assert "everyone with it (**2** members right now)" in ctx.approve.await_args.args[0]
    assert asyncio.run(cog._trusted(guild, saved(cog), 50)) is True
    assert asyncio.run(cog._trusted(guild, saved(cog), 51)) is False


def test_everyone_cannot_be_whitelisted():
    cog, guild = setup()
    with pytest.raises(UserError, match="turn the antinuke off for everyone"):
        asyncio.run(Antinuke.an_whitelist.callback(cog, _wl_ctx(cog, guild), _role(10, default=True)))


def test_unwhitelisting_a_role_and_listing_the_whitelist():
    cog, guild = setup()
    ctx = _wl_ctx(cog, guild)
    role = _role(77)
    asyncio.run(Antinuke.an_whitelist.callback(cog, ctx, role))
    user = SimpleNamespace(id=9, mention="<@9>")
    asyncio.run(Antinuke.an_whitelist.callback(cog, ctx, user))
    asyncio.run(Antinuke.an_whitelist.callback(cog, ctx, None))
    listing = ctx.neutral.await_args.args[0]
    assert "<@9>" in listing and "<@&77>" in listing
    asyncio.run(Antinuke.an_unwhitelist.callback(cog, ctx, role))
    assert saved(cog)["whitelist_roles"] == [] and saved(cog)["whitelist"] == [9]


def test_the_panel_counts_whitelisted_roles():
    cog, guild = setup()
    cog.bot.db.store["antinuke"] = {"whitelist": [1], "whitelist_roles": [2, 3], "modules": {}}
    _, embed = open_panel(cog, guild)
    assert "whitelisted: 1 + 2 roles" in embed.description


def test_rule_panel_can_toggle_command_detection_and_notifies_on_disabling():
    cog, guild = setup()
    cog.security_notice = AsyncMock()
    panel, _ = open_panel(cog, guild)
    press(panel, "Turn on")
    press(panel, "Ban")
    press(panel, "commands: on")
    assert saved(cog)["modules"]["ban"]["command"] is False
    assert "Veil command detection: **off**" in panel.embed().description
    cog.security_notice.assert_awaited_once()
    press(panel, "commands: off")
    assert saved(cog)["modules"]["ban"]["command"] is True


def test_panel_uses_effective_global_defaults_for_legacy_rules():
    from core.antinukepanel import rule_state
    cfg = {"punishment": "kick", "window": 90, "modules": {"ban": {"on": True}}}
    state = rule_state(cfg, "ban")
    assert state["punishment"] == "kick" and state["window"] == 90


def test_overlapping_protection_warning_survives_panel_navigation():
    from core.perms import GHOST_VANGUARD_ID
    cog, guild = setup()
    guild.get_member = lambda uid: SimpleNamespace(id=uid) if uid == GHOST_VANGUARD_ID else None
    panel, embed = open_panel(cog, guild)
    assert embed.description.count("avoid duplicate actions") == 1
    press(panel, "Turn on")
    assert panel.embed().description.count("avoid duplicate actions") == 1
    press(panel, "Ban")
    assert panel.embed().description.count("avoid duplicate actions") == 1
    press(panel, "← Back")
    assert panel.embed().description.count("avoid duplicate actions") == 1


def test_rule_selector_pages_cover_every_rule_without_saving():
    from cogs.antinuke import MIDTOWN_RULES
    cog, guild = setup()
    panel, _ = open_panel(cog, guild)
    before = json.loads(json.dumps(saved(cog)))
    seen = []
    for page in range(3):
        select = next(c for c in panel.children if isinstance(c, RuleSelect))
        seen.extend(o.value for o in select.options)
        assert len(select.options) <= 6
        assert f"Rules {page + 1}/3" in labels(panel)
        if page < 2:
            press(panel, "Next")
    assert seen == [r["key"] for r in MIDTOWN_RULES]
    assert saved(cog) == before
    final_rule = next(c for c in panel.children if isinstance(c, RuleSelect)).options[-1]
    press(panel, final_rule.label)
    assert panel.rule == final_rule.value
    press(panel, "← Back")
    assert "Rules 3/3" in labels(panel)
    press(panel, "Previous")
    assert "Rules 2/3" in labels(panel)


def test_rule_panel_write_failure_does_not_mutate_cached_settings():
    cog, guild = setup()
    panel, _ = open_panel(cog, guild)
    before = json.loads(json.dumps(saved(cog)))
    cog.bot.db.set_config_committed = AsyncMock(side_effect=RuntimeError("database unavailable"))
    it = press(panel, "Turn on")
    assert saved(cog) == before
    assert it.response.send_message.await_args.kwargs["ephemeral"]
    assert "Could not confirm" in it.response.send_message.await_args.args[0]


def test_rule_panel_rejects_forged_fixed_control_and_unknown_rule():
    cog, guild = setup()
    panel, _ = open_panel(cog, guild)
    press(panel, "Turn on")
    press(panel, "Permission Grant")
    old = json.loads(json.dumps(saved(cog)))
    it = SimpleNamespace(user=SimpleNamespace(id=OWNER), response=SimpleNamespace(send_message=AsyncMock()))
    asyncio.run(panel.handle(it, "more"))
    assert "fixed" in it.response.send_message.await_args.args[0]
    asyncio.run(panel.handle(it, "punishment"))
    asyncio.run(panel.handle(it, "open:not_a_rule"))
    assert saved(cog) == old


def test_denied_and_unverifiable_permissions_cannot_change_rule(monkeypatch):
    cog, guild = setup()
    panel, _ = open_panel(cog, guild)
    ctx = SimpleNamespace(user=SimpleNamespace(id=OWNER), client=SimpleNamespace(is_owner=AsyncMock(return_value=False)),
                          response=SimpleNamespace(send_message=AsyncMock()))
    monkeypatch.setattr("core.antinukepanel.antinuke_admin", AsyncMock(return_value=False))
    assert not asyncio.run(panel.interaction_check(ctx))
    assert ctx.response.send_message.await_args.kwargs["ephemeral"]


def test_whitelist_persistence_failure_does_not_mutate_any_cached_entries():
    cog, guild = setup()
    cog.bot.db.store["antinuke"] = {"enabled": True, "whitelist": [81], "whitelist_roles": [77], "modules": {}}
    before = json.loads(json.dumps(saved(cog)))
    cog.bot.db.set_config_committed = AsyncMock(side_effect=RuntimeError("database unavailable"))
    ctx = _wl_ctx(cog, guild)
    with pytest.raises(RuntimeError):
        asyncio.run(Antinuke.an_whitelist.callback(cog, ctx, SimpleNamespace(id=9, bot=True, mention="<@9>")))
    assert saved(cog) == before
    with pytest.raises(RuntimeError):
        asyncio.run(Antinuke.an_unwhitelist.callback(cog, ctx, _role(77)))
    assert saved(cog) == before
    ctx.approve.assert_not_awaited()


def test_whitelist_one_bot_does_not_trust_others_and_removal_is_immediate():
    cog, guild = setup()
    ctx = _wl_ctx(cog, guild)
    bot = SimpleNamespace(id=501, bot=True, mention="<@501>")
    guild.get_member = lambda _: None
    asyncio.run(Antinuke.an_whitelist.callback(cog, ctx, bot))
    assert asyncio.run(cog._trusted(guild, saved(cog), 501))
    assert not asyncio.run(cog._trusted(guild, saved(cog), 502))
    asyncio.run(Antinuke.an_unwhitelist.callback(cog, ctx, bot))
    assert not asyncio.run(cog._trusted(guild, saved(cog), 501))


def test_whitelist_real_discord_converter_resolves_bot_and_role_mentions():
    from discord.ext.commands import run_converters

    async def run():
        bot_id, role_id = 123456789012345678, 223456789012345678
        bot_member = MagicMock(spec=discord.Member, id=bot_id, bot=True, mention=f"<@{bot_id}>")
        role = _role(role_id)
        guild = SimpleNamespace(get_role=lambda rid: role if rid == role_id else None,
                                get_member=lambda uid: bot_member if uid == bot_id else None, _roles={})
        ctx = SimpleNamespace(guild=guild, bot=SimpleNamespace(), message=SimpleNamespace(mentions=[]),
                              _state=SimpleNamespace(_users={}))
        param = Antinuke.an_whitelist.clean_params["target"]
        assert await run_converters(ctx, param.annotation, f"<@{bot_id}>", param) is bot_member
        assert await run_converters(ctx, param.annotation, f"<@&{role_id}>", param) is role
    asyncio.run(run())
