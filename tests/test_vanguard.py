"""Ghost Vanguard is the guard: `,gv` hands its commands over, and Veil's own watching steps aside where it is."""
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


def test_veil_stops_watching_the_audit_log_where_ghost_vanguard_guards():
    an = _antinuke()
    an._trusted = AsyncMock(return_value=False)
    entry = SimpleNamespace(guild=guild(), user_id=99, target=SimpleNamespace(id=5))
    asyncio.run(an.on_audit_log_entry_create(entry))
    an._trusted.assert_not_awaited()  # stopped before looking at who did it


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


def test_anti_raid_and_new_bot_holds_step_aside():
    an = _antinuke()
    an.bot.db = SimpleNamespace(get_config=AsyncMock(return_value={"age": 30}))
    member = SimpleNamespace(id=7, bot=False, guild=guild())
    asyncio.run(Antinuke.on_member_join(an, member))
    an.bot.db.get_config.assert_not_awaited()
    new_bot = SimpleNamespace(id=8, bot=True, guild=guild())
    asyncio.run(an.quarantine_new_bot(new_bot))
    an.cfg.assert_not_awaited()


def test_automatic_setup_leaves_protection_to_ghost_vanguard(monkeypatch):
    store = {}

    class DB:
        async def fetchone(self, q, gid, module):
            return None

        async def get_config(self, gid, module, default=None):
            return store.setdefault(module, dict(default or {}))

        async def set_config(self, gid, module, data):
            store[module] = data
    bot = SimpleNamespace(db=DB(), get_cog=lambda name: MagicMock())
    g = guild()
    g.roles, g.me = [], SimpleNamespace(guild_permissions=discord.Permissions.none())
    done = asyncio.run(autosetup.run(bot, g))
    assert "antiraid" not in store and not any("antinuke" in d or "raid" in d for d in done)
    assert "filters" in store  # the rest is still set up


def test_command_logs_hide_gv_developer_arguments():
    def ctx(content):
        return SimpleNamespace(command=SimpleNamespace(qualified_name="gv", hidden=False, cog_name="Vanguard"),
                               prefix=",", message=SimpleNamespace(content=content))
    assert logembeds._command_text(ctx(",gv network genreban 123 spam")) == ",gv network · arguments hidden"
    assert logembeds._command_text(ctx(",gv raid mode on")) == ",gv raid mode on"
