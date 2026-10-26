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
    assert labels(panel) == ["Turn on", "Default", "Strict", "Relaxed"]
    (select,) = [i for i in panel.children if isinstance(i, RuleSelect)]
    assert len(select.options) == 12 and select.options[0].label == "Ban"


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
    assert labels(panel) == ["Turn off", "−", "count: 3", "+", "time: 60s", "punishment: ban", "← Back"]
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
