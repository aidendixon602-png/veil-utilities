"""The optional Ghost bridge must not suppress Veil's protections."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from cogs.antinuke import Antinuke
from cogs.vanguard import HANDOFF_EMOJI, Vanguard
from core import autosetup, logembeds, perms
from core.utils import UserError

GV = perms.GHOST_VANGUARD_ID


def guild(with_gv=True):
    members = {GV: SimpleNamespace(id=GV, bot=True)} if with_gv else {}
    return SimpleNamespace(id=1, owner_id=2, name="S", get_member=members.get)


def test_ghost_vanguard_is_found_by_its_id():
    assert GV == 1555002091162837022
    assert perms.ghost_guards(guild()) and not perms.ghost_guards(guild(False))
    assert not perms.ghost_guards(None)


def _gv_ctx(g, react=None):
    message = SimpleNamespace(add_reaction=react or AsyncMock(), content=",gv raid mode on")
    return SimpleNamespace(guild=g, message=message)


def test_gv_hands_the_message_over_with_the_shield():
    ctx = _gv_ctx(guild())
    asyncio.run(Vanguard.gv.callback(Vanguard(None), ctx, command="raid mode on"))
    ctx.message.add_reaction.assert_awaited_once_with(HANDOFF_EMOJI)
    assert HANDOFF_EMOJI == "🛡️"  # must match Ghost Vanguard's HANDOFF_EMOJI


def test_gv_says_so_when_ghost_vanguard_is_not_here_or_cant_be_reached():
    with pytest.raises(UserError, match="isn't in this server"):
        asyncio.run(Vanguard.gv.callback(Vanguard(None), _gv_ctx(guild(False)), command="help"))
    forbidden = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "no"))
    with pytest.raises(UserError, match="Add Reactions"):
        asyncio.run(Vanguard.gv.callback(Vanguard(None), _gv_ctx(guild(), forbidden), command="help"))


def _antinuke():
    an = Antinuke.__new__(Antinuke)
    an.bot = SimpleNamespace(user=SimpleNamespace(id=4), owner_ids=set(), owner_id=None)
    an.test_runs = {}
    an.cfg = AsyncMock(return_value={"enabled": True, "modules": {"ban": {"on": True}}})
    an.module_for = lambda entry: "ban"
    return an


def test_veil_catches_audit_events_even_when_ghost_is_present():
    from test_antinuke_undo import A, entry, feed, setup
    an, g, attacker = setup()
    original = g.get_member
    g.get_member = lambda uid: SimpleNamespace(id=GV, bot=True) if uid == GV else original(uid)
    feed(an, *(entry(g, A.ban, SimpleNamespace(id=100 + i, name="victim")) for i in range(3)))
    attacker.ban.assert_awaited_once()


def test_but_still_limits_what_is_done_through_its_own_commands():
    """Ghost Vanguard trusts Veil, so it can't see a mass ban run through Veil: Veil's own limit stays on."""
    an = _antinuke()
    an._trusted = AsyncMock(return_value=False)
    an.hits = MagicMock()
    ctx = SimpleNamespace(guild=guild(), author=SimpleNamespace(id=99))
    try:
        asyncio.run(an.guard(ctx, "ban"))
    except Exception:
        pass
    an._trusted.assert_awaited()  # the limit was checked, not skipped


def test_anti_raid_and_new_bot_checks_run_when_ghost_is_present():
    an = _antinuke()
    an.bot.db = SimpleNamespace(get_config=AsyncMock(return_value={"age": 30}))
    an._raid_punish = AsyncMock()
    member = SimpleNamespace(id=7, bot=False, guild=guild(), created_at=discord.utils.utcnow(), avatar=None)
    asyncio.run(Antinuke.on_member_join(an, member))
    an.bot.db.get_config.assert_awaited_once()
    an._raid_punish.assert_awaited_once()
    an.cfg.return_value = {"enabled": False}
    new_bot = SimpleNamespace(id=8, bot=True, guild=guild())
    asyncio.run(an.quarantine_new_bot(new_bot))
    an.cfg.assert_awaited_once()  # quarantine respects Veil's actual setting


def test_automatic_setup_enables_veil_for_fresh_servers_even_with_ghost():
    store = {}

    class DB:
        async def fetchone(self, q, gid, module):
            return None

        async def get_config(self, gid, module, default=None):
            return store.setdefault(module, dict(default or {}))

        async def set_config(self, gid, module, data):
            store[module] = data
    from test_auto_and_panels import bot_with
    bot = bot_with(DB())
    g = guild()
    g.roles, g.me = [], SimpleNamespace(guild_permissions=discord.Permissions.none())
    done = asyncio.run(autosetup.run(bot, g))
    assert store["antinuke"]["enabled"] and store["antiraid"]["massjoin"] == {"joins": 10, "seconds": 10}
    assert any("antinuke" in d for d in done) and any("raid" in d for d in done)
    assert "filters" in store


def test_command_logs_hide_gv_developer_arguments():
    def ctx(content):
        return SimpleNamespace(command=SimpleNamespace(qualified_name="gv", hidden=False, cog_name="Vanguard"),
                               prefix=",", message=SimpleNamespace(content=content))
    assert logembeds._command_text(ctx(",gv network genreban 123 spam")) == ",gv network · arguments hidden"
    assert logembeds._command_text(ctx(",gv raid mode on")) == ",gv raid mode on"


def test_ghost_presence_does_not_override_disabled_antinuke():
    an = _antinuke()
    an.cfg.return_value = {"enabled": False, "modules": {"ban": {"on": True}}}
    an._trusted = AsyncMock()
    entry = SimpleNamespace(guild=guild(), user_id=99, target=SimpleNamespace(id=5))
    asyncio.run(an.on_audit_log_entry_create(entry))
    an._trusted.assert_not_awaited()


def test_saved_security_settings_are_preserved_with_ghost_present():
    from test_auto_and_panels import FakeDB, bot_with
    existing = {"enabled": False, "modules": {"ban": {"on": False}}}
    db = FakeDB({"antinuke": existing, "antiraid": {"massjoin": None}, "filters": {}, "warnpunish": {}})
    g = guild()
    g.roles, g.me = [], SimpleNamespace(guild_permissions=discord.Permissions.none())
    asyncio.run(autosetup.run(bot_with(db), g))
    assert db.store["antinuke"] == existing and db.store["antiraid"] == {"massjoin": None}


def test_webhook_guard_runs_with_ghost_present():
    from test_antinuke_stronger import _webhook_message
    from test_antinuke_undo import setup
    an, g, _ = setup({"webhook": {"on": True, "threshold": 1, "window": 60, "punishment": "kick"}})
    original = g.get_member
    g.get_member = lambda uid: SimpleNamespace(id=GV, bot=True) if uid == GV else original(uid)
    hook = SimpleNamespace(name="leaked", delete=AsyncMock())
    an.bot.fetch_webhook = AsyncMock(return_value=hook)
    an._log_channel = AsyncMock(return_value=None)
    messages = [_webhook_message(g) for _ in range(2)]
    for msg in messages:
        asyncio.run(an.webhook_spam(msg))
    hook.delete.assert_awaited_once()
    assert all(msg.delete.await_count == 1 for msg in messages)
