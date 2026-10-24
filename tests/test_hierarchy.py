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
