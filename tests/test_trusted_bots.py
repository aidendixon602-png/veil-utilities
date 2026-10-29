"""Built-in trusted bots: whitelisted in every server without anyone running a command."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from cogs.antinuke import Antinuke
from cogs.moderation import Moderation
from core import perms

GHOST = 1555002091162837022


def test_ghost_vanguard_is_built_in():
    assert perms.TRUSTED_BOTS[GHOST] == "Ghost Vanguard"
    assert perms.is_trusted_bot(GHOST) and perms.is_trusted_bot(SimpleNamespace(id=GHOST))
    assert not perms.is_trusted_bot(123) and not perms.is_trusted_bot(None)


def _antinuke():
    bot = SimpleNamespace(user=SimpleNamespace(id=4), owner_ids=set(), owner_id=None,
                          db=SimpleNamespace(get_config=AsyncMock(return_value={})))
    an = Antinuke.__new__(Antinuke)
    an.bot = bot
    return an


def test_the_antinuke_trusts_it_in_any_server():
    an = _antinuke()
    guild = SimpleNamespace(id=1, owner_id=2, get_member=lambda uid: None)
    assert asyncio.run(an._trusted(guild, {}, GHOST))
    assert not asyncio.run(an._trusted(guild, {}, 999))


def test_adding_it_is_not_an_unauthorized_bot_add():
    an = _antinuke()
    an.module_for = lambda entry: "botadd"
    an.test_runs = {}
    an.cfg = AsyncMock(return_value={"enabled": True, "modules": {"botadd": {"on": True}}})
    an._matters = MagicMock(return_value=True)
    guild = SimpleNamespace(id=1, owner_id=2, get_member=lambda uid: None)
    entry = SimpleNamespace(guild=guild, user_id=777, target=SimpleNamespace(id=GHOST))
    asyncio.run(an.on_audit_log_entry_create(entry))
    an._matters.assert_not_called()  # stopped before anything is counted, punished or reverted


def test_it_is_not_quarantined_when_it_joins():
    an = _antinuke()
    an.cfg = AsyncMock()
    member = SimpleNamespace(id=GHOST, bot=True, guild=SimpleNamespace(id=1))
    asyncio.run(an.quarantine_new_bot(member))
    an.cfg.assert_not_awaited()


def test_it_may_hold_blacklisted_roles():
    mod = Moderation.__new__(Moderation)
    mod.bot = SimpleNamespace(owner_ids=set(), owner_id=None)
    guild = SimpleNamespace(owner_id=2)
    assert mod._is_role_authorized(guild, SimpleNamespace(id=GHOST, roles=[]), {})
    assert not mod._is_role_authorized(guild, SimpleNamespace(id=999, roles=[]), {})
