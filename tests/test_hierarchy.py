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
        """Restart must trigger in-process reboot without killing the container (prevents crash backoff)."""
        import asyncio

        from cogs.owner import Owner

        bot = SimpleNamespace(_restart_requested=False, close=AsyncMock())
        ctx = SimpleNamespace(approve=AsyncMock())
        cog = SimpleNamespace(bot=bot)
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
        cog = SimpleNamespace(bot=bot)
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
