"""`,fakepermissions` (`,fp`), greed / bleed style: add / remove / list / reset, plain permission names, and a
picker when no permission is named."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cogs.settings import FakePermPicker, Settings, parse_perms
from core.utils import UserError


def test_permission_names_can_be_typed_naturally():
    assert parse_perms("ban members, kick_members manage roles") == (["ban_members", "kick_members", "manage_roles"], [])
    assert parse_perms("Ban Members,ban_members") == (["ban_members"], [])
    assert parse_perms("ban members fly") == (["ban_members"], ["fly"])


def setup(store=None):
    store = {"fakeperms": store if store is not None else {}}
    db = SimpleNamespace(get_config=AsyncMock(side_effect=lambda g, m: store[m]),
                         set_config=AsyncMock(side_effect=lambda g, m, d: store.__setitem__(m, d)))
    cog = Settings.__new__(Settings)
    cog.bot = SimpleNamespace(db=db)
    ctx = SimpleNamespace(guild=SimpleNamespace(id=1), author=SimpleNamespace(id=5, mention="<@5>"),
                          approve=AsyncMock(), warn=AsyncMock(), neutral=AsyncMock(), paginate=AsyncMock(),
                          clean_prefix=",")
    role = SimpleNamespace(id=9, mention="<@&9>", is_default=lambda: False)
    return cog, ctx, role, store


def test_add_remove_list_and_reset():
    cog, ctx, role, store = setup()
    asyncio.run(Settings.fp_add.callback(cog, ctx, role, permissions="ban members, kick members"))
    assert store["fakeperms"] == {"9": ["ban_members", "kick_members"]}
    assert ctx.approve.await_args.args[0] == "gave <@&9> `ban members` and `kick members` through the bot"
    asyncio.run(Settings.fp_remove.callback(cog, ctx, role, permissions="kick members"))
    assert store["fakeperms"] == {"9": ["ban_members"]}
    asyncio.run(Settings.fp_list.callback(cog, ctx))
    page = ctx.paginate.await_args.args[0][0]
    assert "<@&9>: `ban members`" in page.description
    asyncio.run(Settings.fp_reset.callback(cog, ctx))
    assert store["fakeperms"] == {}
    asyncio.run(Settings.fp_list.callback(cog, ctx))
    assert "no roles have fake permissions" in ctx.warn.await_args.args[0]


def test_removing_everything_clears_the_role():
    cog, ctx, role, store = setup({"9": ["ban_members"]})
    asyncio.run(Settings.fp_remove.callback(cog, ctx, role))
    assert store["fakeperms"] == {}


def test_unknown_names_and_everyone_are_refused():
    cog, ctx, role, _ = setup()
    with pytest.raises(UserError, match="isn't a permission"):
        asyncio.run(Settings.fp_add.callback(cog, ctx, role, permissions="fly"))
    everyone = SimpleNamespace(id=1, mention="@everyone", is_default=lambda: True)
    with pytest.raises(UserError, match="every member"):
        asyncio.run(Settings.fp_add.callback(cog, ctx, everyone, permissions="ban members"))


def test_no_permissions_named_opens_a_picker():
    cog, ctx, role, store = setup()
    asyncio.run(Settings.fp_add.callback(cog, ctx, role, permissions=None))
    view = ctx.neutral.await_args.kwargs["view"]
    assert isinstance(view, FakePermPicker)
    select = view.children[0]
    assert "ban members" in [o.label for o in select.options] and len(select.options) <= 25
    select._values = ["ban_members", "manage_roles"]
    ctx._line = lambda emoji, text: f"{emoji} <@5>: {text}"
    it = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock(), send_message=AsyncMock()))
    asyncio.run(view.picked(it))
    assert store["fakeperms"] == {"9": ["ban_members", "manage_roles"]}
    assert it.response.edit_message.await_args.kwargs["embed"].description.startswith("✅ <@5>: gave <@&9>")
