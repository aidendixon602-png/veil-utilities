"""Moderation hierarchy protections + panel-safe restart."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.utils import UserError, check_hierarchy


def _ctx(author_id=10, author_role=5, owner_id=99, devs=frozenset({7}), me_role=100):
    return SimpleNamespace(
        author=SimpleNamespace(id=author_id, top_role=author_role, mention="<@10>"),
        guild=SimpleNamespace(owner_id=owner_id, me=SimpleNamespace(id=4242, top_role=me_role)),
        bot=SimpleNamespace(owner_ids=devs),
    )


def _target(uid=20, role=1):
    return SimpleNamespace(id=uid, top_role=role, mention=f"<@{uid}>")


class TestHierarchy:
    def test_normal_target_passes(self):
        check_hierarchy(_ctx(), _target())  # no raise

    def test_cannot_moderate_self(self):
        with pytest.raises(UserError, match="yourself"):
            check_hierarchy(_ctx(), _target(uid=10))

    def test_cannot_moderate_server_owner(self):
        with pytest.raises(UserError, match="server owner"):
            check_hierarchy(_ctx(), _target(uid=99))

    def test_cannot_moderate_bot_developer(self):
        with pytest.raises(UserError, match="bot developer"):
            check_hierarchy(_ctx(), _target(uid=7))

    def test_equal_or_higher_role_blocked(self):
        with pytest.raises(UserError, match="equal or higher"):
            check_hierarchy(_ctx(author_role=5), _target(role=5))

    def test_server_owner_bypasses_role_check(self):
        check_hierarchy(_ctx(author_id=99, author_role=1), _target(role=99))  # owner can mod admins

    def test_target_above_bot_blocked(self):
        with pytest.raises(UserError, match="above mine"):
            check_hierarchy(_ctx(me_role=3), _target(role=4))


class TestRestart:
    def test_restart_triggers_clean_reboot(self):
        """Restart must close the bot and request a restart (main.py re-execs) without killing the container."""
        import asyncio

        from cogs.owner import Owner

        bot = SimpleNamespace(_restart_requested=False, close=AsyncMock())
        ctx = SimpleNamespace(approve=AsyncMock())
        cog = Owner(bot)
        asyncio.run(Owner.restart.callback(cog, ctx))
        ctx.approve.assert_awaited_once()
        assert bot._restart_requested is True
        bot.close.assert_awaited_once()

    def test_restart_saves_state_and_shows_remaining_seconds(self):
        """Restart must include the seconds countdown in message and persist .restart_state.json."""
        import asyncio
        import json
        import os

        from cogs.owner import Owner

        bot = SimpleNamespace(_restart_requested=False, close=AsyncMock(), maintenance=None)
        msg_mock = SimpleNamespace(id=777)
        ctx = SimpleNamespace(
            approve=AsyncMock(return_value=msg_mock),
            channel=SimpleNamespace(id=123),
            guild=SimpleNamespace(id=456),
            author=SimpleNamespace(id=789),
        )
        cog = Owner(bot)
        try:
            asyncio.run(Owner.restart.callback(cog, ctx, seconds=4))
            ctx.approve.assert_awaited_once()
            call_text = ctx.approve.call_args[0][0]
            assert "remaining" in call_text
            assert "back online" in call_text
            assert "~4s" in call_text

            assert os.path.exists(".restart_state.json")
            with open(".restart_state.json", "r", encoding="utf-8") as f:
                state = json.load(f)
            assert state["channel_id"] == 123
            assert state["message_id"] == 777
        finally:
            if os.path.exists(".restart_state.json"):
                os.remove(".restart_state.json")

    def test_check_restart_notification_announces_back_online(self):
        """On startup, bot reads state, edits original message, and sends back-online announcement."""
        import asyncio
        import json
        import os
        import time

        from core.bot import Veil

        state = {
            "channel_id": 555,
            "message_id": 666,
            "start_time": time.time() - 2.5,
            "target_time": time.time(),
        }
        with open(".restart_state.json", "w", encoding="utf-8") as f:
            json.dump(state, f)

        orig_msg = SimpleNamespace(id=666, embeds=[], footer=None, edit=AsyncMock())
        channel = SimpleNamespace(id=555, fetch_message=AsyncMock(return_value=orig_msg), send=AsyncMock())

        bot = Veil()
        bot.get_channel = lambda cid: channel if cid == 555 else None

        try:
            asyncio.run(bot.check_restart_notification())
            # State file should be cleaned up
            assert not os.path.exists(".restart_state.json")
            # Message edited
            orig_msg.edit.assert_awaited_once()
            # Channel notified
            channel.send.assert_awaited_once()
            send_text = channel.send.call_args[0][0]
            assert "back online" in send_text.lower()
        finally:
            if os.path.exists(".restart_state.json"):
                os.remove(".restart_state.json")

    def test_maintenance_shows_remaining_seconds_to_strangers(self):
        """Strangers running commands during maintenance receive remaining seconds and dynamic timestamp."""
        import asyncio
        import time
        from core.bot import Veil

        bot = Veil()
        bot.maintenance = "Upgrading database"
        bot.maintenance_until = time.time() + 15.0
        bot.owner_ids = frozenset([999])

        ctx = SimpleNamespace(
            guild=SimpleNamespace(id=1, owner_id=100),
            author=SimpleNamespace(id=123, roles=[], guild_permissions=SimpleNamespace(administrator=False)),
            command=SimpleNamespace(hidden=False, parents=[], qualified_name="help", root_parent=None, name="help"),
            cog=None,
        )

        with pytest.raises(UserError) as exc_info:
            asyncio.run(bot._global_check(ctx))

        err_text = str(exc_info.value)
        assert "maintenance" in err_text
        assert "remaining" in err_text
        assert "<t:" in err_text


class TestPermissionHierarchy:
    def test_hierarchy_order_and_ranks(self):
        from core import perms

        assert perms.HIERARCHY_KEYS == ["developer", "owner", "supremeleader", "overseer",
                                        "headadmin", "admin", "moderator", "helper"]
        assert perms.HIERARCHY_ORDER == ["Developer", "Server Owner", "Owner", "Co-Owner",
                                         "Server Manager", "Admin", "Moderator", "Helper"]
        levels = perms.PRESETS
        assert [levels[k][1] for k in perms.HIERARCHY_KEYS[2:]] == [7, 6, 4, 3, 2, 1]
        assert levels["supremeleader"][1] == perms.COOWNER_RANK
        assert "administrator" in levels["supremeleader"][2]
        assert set(levels["overseer"][2]) == {p for p in perms.ALL_BOT_PERMS if p != "administrator"}
        assert "botmanager" not in levels  # folded into Head Admin
        assert set(levels["headadmin"][2]) < set(levels["overseer"][2])

    def test_old_level_names_still_work_and_are_migrated(self):
        from core import perms
        cfg = {"users": {"1": "coowner", "2": "manager", "4": "mod", "5": "admin"},
               "roles": {"9": "mod"}, "overrides": {"coowner": {"name": "Co-Owner", "rank": 8},
                                                    "mod": {"perms": ["manage_messages"]}}}
        assert perms.user_key(cfg, 1) == "supremeleader" and perms.role_key(cfg, 9) == "moderator"
        assert perms.migrate(cfg) is True
        assert cfg["users"] == {"1": "supremeleader", "2": "overseer", "4": "moderator", "5": "admin"}
        assert cfg["roles"] == {"9": "moderator"}
        assert cfg["overrides"] == {"supremeleader": {"rank": 8}, "moderator": {"perms": ["manage_messages"]}}
        assert perms.all_levels(cfg)["supremeleader"]["name"] == "Owner"
        assert perms.migrate(cfg) is False
        # typed names: the new names, and every old one, mean what they say now (saved keys never changed)
        for typed, key in (("Owner", "supremeleader"), ("Co-Owner", "overseer"), ("coowner", "overseer"),
                           ("Server Manager", "headadmin"), ("Supreme Leader", "supremeleader"),
                           ("Overseer", "overseer"), ("Head Admin", "headadmin"), ("Manager", "headadmin"),
                           ("Bot Manager", "headadmin"), ("mod", "moderator"), ("Moderator", "moderator")):
            assert perms.resolve_key({}, typed) == key, typed
        for typed in ("Server Owner", "guild owner", "Bot Developer", "dev", "Developer"):
            assert perms.is_reserved(typed) in ("Server Owner", "Developer")

    def test_requires_uperm_checks(self):
        import asyncio
        from core.utils import requires_uperm, UserError
        from unittest.mock import AsyncMock

        @requires_uperm("helper")
        async def dummy_cmd(ctx):
            pass

        predicate = dummy_cmd.__commands_checks__[0]

        # 1. Dev passes
        ctx_dev = SimpleNamespace(
            guild=SimpleNamespace(id=1, owner_id=100),
            author=SimpleNamespace(id=4242, roles=[], guild_permissions=SimpleNamespace(administrator=False)),
            bot=SimpleNamespace(owner_ids=frozenset({4242}), db=SimpleNamespace(get_config=AsyncMock(return_value={})))
        )
        assert asyncio.run(predicate(ctx_dev)) is True

        # 2. Server Owner passes
        ctx_owner = SimpleNamespace(
            guild=SimpleNamespace(id=1, owner_id=100),
            author=SimpleNamespace(id=100, roles=[], guild_permissions=SimpleNamespace(administrator=False)),
            bot=SimpleNamespace(owner_ids=frozenset(), db=SimpleNamespace(get_config=AsyncMock(return_value={})))
        )
        assert asyncio.run(predicate(ctx_owner)) is True

        # 3. Member with uperm passes
        cfg_with_helper = {"users": {"200": "helper"}}
        ctx_helper = SimpleNamespace(
            guild=SimpleNamespace(id=1, owner_id=100),
            author=SimpleNamespace(id=200, roles=[], guild_permissions=SimpleNamespace(administrator=False)),
            bot=SimpleNamespace(owner_ids=frozenset(), db=SimpleNamespace(get_config=AsyncMock(return_value=cfg_with_helper)))
        )
        assert asyncio.run(predicate(ctx_helper)) is True

        # 4. Member without uperm fails
        ctx_stranger = SimpleNamespace(
            guild=SimpleNamespace(id=1, owner_id=100),
            author=SimpleNamespace(id=999, roles=[], guild_permissions=SimpleNamespace(administrator=False)),
            bot=SimpleNamespace(owner_ids=frozenset(), db=SimpleNamespace(get_config=AsyncMock(return_value={})))
        )
        with pytest.raises(UserError, match="permission level or higher"):
            asyncio.run(predicate(ctx_stranger))

        # 5. Member with helper fails command requiring mod
        @requires_uperm("mod")  # the old name still works
        async def mod_cmd(ctx):
            pass
        mod_predicate = mod_cmd.__commands_checks__[0]
        with pytest.raises(UserError, match="permission level or higher"):
            asyncio.run(mod_predicate(ctx_helper))

    def test_check_hierarchy_async_bot_rank_protection(self):
        import asyncio
        from core.utils import check_hierarchy_async, UserError
        from unittest.mock import AsyncMock

        guild = SimpleNamespace(id=1, owner_id=100, me=SimpleNamespace(id=99999, top_role=100))
        # Author has Admin uperm (rank 3), target has Head Admin uperm (rank 4)
        author = SimpleNamespace(id=201, top_role=50, roles=[], mention="<@201>")
        target = SimpleNamespace(id=202, top_role=40, roles=[], mention="<@202>")

        pl_cfg = {"users": {"201": "admin", "202": "headadmin"}}
        db_mock = SimpleNamespace(get_config=AsyncMock(return_value=pl_cfg))
        bot = SimpleNamespace(owner_ids=frozenset(), db=db_mock)
        ctx = SimpleNamespace(guild=guild, author=author, bot=bot)

        with pytest.raises(UserError, match="their bot permission rank"):
            asyncio.run(check_hierarchy_async(ctx, target, "ban"))

    def test_check_role_assignable_blocks_admin_role_for_non_supreme_leader(self):
        import asyncio
        import discord
        from core.utils import check_role_assignable, UserError
        from unittest.mock import AsyncMock, MagicMock

        guild = SimpleNamespace(id=1, owner_id=100, me=SimpleNamespace(id=99999, top_role=SimpleNamespace(position=100)))
        author = SimpleNamespace(id=201, top_role=SimpleNamespace(position=80), roles=[], mention="<@201>")

        admin_role = MagicMock(spec=discord.Role)
        admin_role.id = 777
        admin_role.position = 70
        admin_role.managed = False
        admin_role.is_default.return_value = False
        admin_role.permissions = discord.Permissions(administrator=True)

        pl_cfg = {"users": {"201": "admin"}}  # Rank 3
        db_mock = SimpleNamespace(get_config=AsyncMock(side_effect=lambda gid, table: pl_cfg if table == "permlevels" else {}))
        bot = SimpleNamespace(owner_ids=frozenset(), db=db_mock)
        ctx = SimpleNamespace(guild=guild, author=author, bot=bot)

        # Admin cannot assign an Administrator role
        with pytest.raises(UserError, match=r"\*\*Owner\*\* level can give out roles with Administrator"):
            asyncio.run(check_role_assignable(ctx, admin_role))

