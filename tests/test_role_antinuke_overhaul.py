"""Regression tests for role and Antinuke security behavior."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cogs.antinuke import Antinuke
from cogs.moderation import Moderation
from core import nukerestore
from core.utils import UserError
from test_antinuke_undo import A, entry, feed, setup
from test_roleblacklist_enforcement import _mock_context, _mock_guild, _mock_member, _mock_role, bot  # noqa: F401


def test_duplicate_audit_id_does_not_count_twice():
    cog, guild, attacker = setup()
    event = entry(guild, A.ban, SimpleNamespace(id=42, name="victim"))
    event.id = 12345
    feed(cog, event, event, event)
    attacker.ban.assert_not_awaited()
    feed(cog, entry(guild, A.ban, SimpleNamespace(id=43)), entry(guild, A.ban, SimpleNamespace(id=44)))
    attacker.ban.assert_awaited_once()
    feed(cog, event)
    assert attacker.ban.await_count == 1


def test_failed_antinuke_save_does_not_modify_cached_document(bot):  # noqa: F811
    async def run():
        original = {"enabled": True, "modules": {"ban": {"on": True}}}
        bot._storage[(123, "antinuke")] = deepcopy(original)
        cog = Antinuke(bot)
        cfg = await cog.cfg(123)
        cfg["modules"]["ban"]["on"] = False
        bot.db.set_config_committed.side_effect = RuntimeError("offline")
        with pytest.raises(RuntimeError):
            await cog.save(123, cfg)
        assert bot._storage[(123, "antinuke")] == original
    asyncio.run(run())


def test_role_delete_requires_confirmation_and_rechecks(bot):  # noqa: F811
    async def run():
        guild, role = _mock_guild(123), _mock_role(888, pos=10)
        ctx = _mock_context(bot, guild, _mock_member(guild.owner_id))
        ctx.confirm = AsyncMock(return_value=False)
        ctx.command.can_run = AsyncMock(return_value=True)
        guild.get_role.return_value = role
        cog = Moderation(bot)
        await Moderation.role_delete.callback(cog, ctx, role=role)
        role.delete.assert_not_awaited()
        async def modify(*args):
            role.position += 1
            return True
        ctx.confirm.side_effect = modify
        with pytest.raises(UserError, match="role changed"):
            await Moderation.role_delete.callback(cog, ctx, role=role)
        role.delete.assert_not_awaited()
    asyncio.run(run())


def test_role_recovery_reports_missing_holders_overwrites_and_uncertain_edits():
    async def run():
        restored = SimpleNamespace(id=88, edit=AsyncMock(side_effect=asyncio.TimeoutError()))
        guild = SimpleNamespace(me=SimpleNamespace(top_role=SimpleNamespace(position=10)),
            create_role=AsyncMock(return_value=restored), get_member=lambda uid: None, get_channel=lambda cid: None)
        snap = {"id": 1, "name": "Staff", "position": 2, "permissions": 0, "colour": 0,
                "hoist": False, "mentionable": False, "holders": [5], "overwrites": [(6, 0, 0)]}
        mapping, lines = await nukerestore.restore_roles(guild, [snap], "test")
        assert mapping == {1: restored}
        assert any("position" in line and "not confirmed" in line for line in lines)
        assert any("holder" in line and "not confirmed" in line for line in lines)
        assert any("overwrite" in line and "not confirmed" in line for line in lines)
    asyncio.run(run())


@pytest.mark.parametrize("module,value", [
    ("permlevels", {"roles": {"888": "supremeleader"}}),
    ("fakeperms", {"888": ["administrator"]}),
    ("admin_role_granter_roles", {"888": True}),
    ("antinuke", {"whitelist_roles": [888]}),
    ("roleblacklist", {"soft_bypass": {"roles": [888]}}),
])
def test_nonowner_cannot_assign_role_with_bot_privileges(bot, module, value):  # noqa: F811
    from core.utils import check_role_assignable
    async def run():
        guild, role = _mock_guild(123), _mock_role(888, pos=10)
        actor = _mock_member(333)
        ctx = _mock_context(bot, guild, actor)
        bot._storage[(123, module)] = value
        with pytest.raises(UserError, match="server owner or a bot developer"):
            await check_role_assignable(ctx, role)
        ctx.author = _mock_member(guild.owner_id)
        await check_role_assignable(ctx, role)
    asyncio.run(run())


def test_nonowner_role_grant_fails_closed_on_policy_outage(bot):  # noqa: F811
    from core.utils import check_role_assignable
    async def run():
        guild, role = _mock_guild(123), _mock_role(888, pos=10)
        ctx = _mock_context(bot, guild, _mock_member(333))
        async def get(gid, key, default=None):
            if key == "admin_role_granter_roles":
                raise RuntimeError("offline")
            return {}
        bot.db.get_config.side_effect = get
        with pytest.raises(UserError, match="Cannot verify"):
            await check_role_assignable(ctx, role)
    asyncio.run(run())


def test_blacklist_scan_strip_reports_unconfirmed_and_hierarchy_skips():
    from cogs.moderation import GuildBledView

    async def run():
        role = SimpleNamespace(id=10, position=1)
        high = SimpleNamespace(id=11, position=100)
        member = SimpleNamespace(id=3, remove_roles=AsyncMock(side_effect=asyncio.TimeoutError()))
        guild = SimpleNamespace(id=1, me=SimpleNamespace(top_role=SimpleNamespace(position=50)))
        db = SimpleNamespace(get_config=AsyncMock(return_value={}))
        cog = SimpleNamespace(bot=SimpleNamespace(db=db, owner_ids=set(), owner_id=None),
                              _role_exceptions=AsyncMock(return_value={}),
                              _unauthorized_bled_roles=lambda *args: [role, high])
        ctx = SimpleNamespace(guild=guild, author=SimpleNamespace(id=2))
        view = GuildBledView(cog, ctx, [(member, [role, high])])
        interaction = SimpleNamespace(message=None, user=ctx.author,
                                      response=SimpleNamespace(edit_message=AsyncMock()),
                                      edit_original_response=AsyncMock())
        await view.strip_all.callback(interaction)
        result = interaction.edit_original_response.await_args.kwargs['embed'].description
        assert '**1** role(s) skipped' in result
        assert '**1** member request(s) unconfirmed' in result
        member.remove_roles.assert_awaited_once()
    asyncio.run(run())
