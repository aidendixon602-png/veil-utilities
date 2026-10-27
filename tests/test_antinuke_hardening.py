"""Hardening: permission levels don't skip the antinuke, only the owner can switch protection off (and trust changes
are reported), panic mode locks the server down and puts it all back, and new bots are quarantined until approved."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from cogs.antinuke import DANGEROUS_MASK, Antinuke, ApproveBotButton
from core.utils import UserError
from test_antinuke_undo import A, entry, feed, member, setup

OWNER = 1


class Role(SimpleNamespace):
    __hash__ = object.__hash__

    def __lt__(self, other):
        return self.position < other.position

    def is_default(self):
        return getattr(self, "default", False)


def _role(rid, name, position, perms, managed=False):
    role = Role(id=rid, name=name, position=position, managed=managed, permissions=discord.Permissions(perms))

    async def edit(permissions=None, reason=None):
        role.permissions = permissions
    role.edit = AsyncMock(side_effect=edit)
    return role


def _guild(cog, guild, roles=()):
    guild.me.top_role = Role(id=999, position=50)
    guild.roles = list(roles)
    for r in roles:
        guild.roles_by_id[r.id] = r
    guild.text_channels = []
    guild.owner = SimpleNamespace(id=OWNER, send=AsyncMock())
    cog._log_channel = AsyncMock(return_value=None)
    return guild


# ------------------------------------------------------------------ 1. levels don't skip it

def test_an_admin_level_no_longer_skips_the_antinuke():
    cog, guild, attacker = setup()
    cog.bot.db.store["permlevels"] = {"users": {str(attacker.id): "supremeleader"}}
    feed(cog, *(entry(guild, A.ban, SimpleNamespace(id=100 + i, name=f"v{i}")) for i in range(3)))
    attacker.ban.assert_awaited_once()  # caught, even with the top permission level


def test_the_whitelist_and_antinuke_admins_are_still_trusted():
    for extra in ({"whitelist": [5]}, {"admins": [5]}):
        cog, guild, attacker = setup()
        cog.bot.db.store["antinuke"].update(extra)
        feed(cog, *(entry(guild, A.ban, SimpleNamespace(id=100 + i, name=f"v{i}")) for i in range(3)))
        attacker.ban.assert_not_awaited()


# ------------------------------------------------------------------ 2. guarding the off switch

def _ctx(guild, user_id):
    return SimpleNamespace(guild=guild, author=SimpleNamespace(id=user_id, mention=f"<@{user_id}>"),
                           approve=AsyncMock(), neutral=AsyncMock(), clean_prefix=",")


def _admin_ok():
    return patch("cogs.antinuke.antinuke_admin", AsyncMock(return_value=True))


def test_only_the_owner_can_turn_it_off_and_it_is_reported():
    cog, guild, _ = setup()
    _guild(cog, guild)
    with _admin_ok():
        with pytest.raises(UserError, match="Only the \\*\\*server owner\\*\\* can turn the antinuke off"):
            asyncio.run(Antinuke.an_disable.callback(cog, _ctx(guild, 77)))
        assert cog.bot.db.store["antinuke"]["enabled"] is True
        asyncio.run(Antinuke.an_disable.callback(cog, _ctx(guild, OWNER)))
    assert cog.bot.db.store["antinuke"]["enabled"] is False


def test_trust_changes_are_reported_to_the_owner():
    cog, guild, _ = setup()
    _guild(cog, guild)
    target = SimpleNamespace(id=55, mention="<@55>")
    with _admin_ok():
        asyncio.run(Antinuke.an_whitelist.callback(cog, _ctx(guild, 77), target))
    notice = guild.owner.send.await_args.kwargs["embed"]
    assert notice.title == "🔐 Antinuke settings changed"
    assert notice.description.startswith("<@77> whitelisted <@55>")


def test_the_owners_own_changes_dont_page_the_owner():
    cog, guild, _ = setup()
    _guild(cog, guild)
    with _admin_ok():
        asyncio.run(Antinuke.an_whitelist.callback(cog, _ctx(guild, OWNER), SimpleNamespace(id=56, mention="<@56>")))
    guild.owner.send.assert_not_awaited()


# ------------------------------------------------------------------ 3. panic mode

def test_panic_takes_dangerous_permissions_off_and_unpanic_puts_exactly_them_back():
    cog, guild, _ = setup()
    mod = _role(60, "Mod", 10, discord.Permissions(ban_members=True, send_messages=True).value)
    top = _role(61, "Above me", 60, discord.Permissions(administrator=True).value)
    bot_role = _role(62, "Some bot", 5, discord.Permissions(administrator=True).value, managed=True)
    plain = _role(63, "Member", 2, discord.Permissions(send_messages=True).value)
    _guild(cog, guild, [mod, top, bot_role, plain])
    guild.edit = AsyncMock()
    cog._lock_channels = AsyncMock(return_value=[7, 8])
    cog._unlock_channels = AsyncMock(return_value=2)

    done = asyncio.run(cog.start_panic(guild, SimpleNamespace(id=77, mention="<@77>"), "raid"))
    assert mod.permissions == discord.Permissions(send_messages=True)  # ban members taken off
    assert top.permissions.administrator and bot_role.permissions.administrator  # can't / mustn't touch
    plain.edit.assert_not_awaited()
    assert "took the dangerous permissions off 1 role(s)" in done and "locked 2 channel(s) for @everyone" in done
    with pytest.raises(UserError, match="already on"):
        asyncio.run(cog.start_panic(guild, None))

    mod.permissions = discord.Permissions(send_messages=True, attach_files=True)  # changed in the meantime
    done = asyncio.run(cog.end_panic(guild, SimpleNamespace(id=OWNER, mention="<@1>")))
    assert mod.permissions == discord.Permissions(send_messages=True, attach_files=True, ban_members=True)
    cog._unlock_channels.assert_awaited_once_with(guild, [7, 8])
    assert "panic" not in cog.bot.db.store["antinuke"]
    with pytest.raises(UserError, match="isn't on"):
        asyncio.run(cog.end_panic(guild, None))


def test_only_the_owner_can_lift_panic_mode():
    cog, guild, _ = setup()
    _guild(cog, guild)
    with pytest.raises(UserError, match="Only the \\*\\*server owner\\*\\* can lift panic mode"):
        asyncio.run(Antinuke.an_unpanic.callback(cog, _ctx(guild, 77)))


# ------------------------------------------------------------------ 4. new bots

def _new_bot(guild, adder_id, roles):
    bot = member(guild, 900, "SketchyBot", roles)
    bot.bot, bot.guild, bot.mention = True, guild, "<@900>"

    async def audit_logs(limit, action):
        yield SimpleNamespace(target=SimpleNamespace(id=900), user=SimpleNamespace(id=adder_id, mention=f"<@{adder_id}>"))
    guild.audit_logs = audit_logs
    return bot


def test_a_new_bot_is_quarantined_until_the_owner_approves_it(monkeypatch):
    monkeypatch.setattr("cogs.antinuke.asyncio.sleep", AsyncMock())
    cog, guild, _ = setup()
    own = _role(70, "SketchyBot", 5, discord.Permissions(administrator=True, send_messages=True).value, managed=True)
    staff = _role(71, "Staff", 8, discord.Permissions(kick_members=True).value)
    _guild(cog, guild, [own, staff])
    log = SimpleNamespace(send=AsyncMock())
    cog._log_channel = AsyncMock(return_value=log)
    bot = _new_bot(guild, 77, [own, staff])  # added by a whitelisted-but-maybe-hacked staff member
    asyncio.run(cog.quarantine_new_bot(bot))
    assert own.permissions == discord.Permissions(send_messages=True)
    bot.remove_roles.assert_awaited_once()
    alert = log.send.await_args.kwargs
    assert alert["embed"].title == "🤖 New bot quarantined"
    assert isinstance(alert["view"].children[0], ApproveBotButton)

    text = asyncio.run(cog.approve_bot(guild, 900, SimpleNamespace(id=OWNER, mention="<@1>")))
    assert text.startswith("approved <@900>")
    assert own.permissions.administrator and own.permissions.send_messages
    bot.add_roles.assert_awaited_once_with(staff, reason="Antinuke: bot approved by namespace(id=1, mention='<@1>')")
    assert asyncio.run(cog.approve_bot(guild, 900, None)).startswith("That bot isn't quarantined")


def test_bots_the_owner_adds_are_trusted(monkeypatch):
    monkeypatch.setattr("cogs.antinuke.asyncio.sleep", AsyncMock())
    cog, guild, _ = setup()
    own = _role(70, "GoodBot", 5, discord.Permissions(administrator=True).value, managed=True)
    _guild(cog, guild, [own])
    asyncio.run(cog.quarantine_new_bot(_new_bot(guild, OWNER, [own])))
    own.edit.assert_not_awaited()


def test_quarantine_can_be_switched_off_by_the_owner_only(monkeypatch):
    cog, guild, _ = setup()
    _guild(cog, guild)
    with _admin_ok():
        with pytest.raises(UserError, match="server owner"):
            asyncio.run(Antinuke.an_botquarantine.callback(cog, _ctx(guild, 77), "off"))
        asyncio.run(Antinuke.an_botquarantine.callback(cog, _ctx(guild, OWNER), "off"))
    assert cog.bot.db.store["antinuke"]["bot_quarantine"] is False


def test_the_approve_button_is_owner_only():
    guild = SimpleNamespace(id=10, owner_id=OWNER)
    client = SimpleNamespace(get_guild=lambda g: guild, get_cog=lambda n: MagicMock(), owner_ids=set(), owner_id=None)
    it = SimpleNamespace(client=client, user=SimpleNamespace(id=77),
                         response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock()))
    asyncio.run(ApproveBotButton(10, 900).callback(it))
    assert "Only the server owner" in it.response.send_message.await_args.args[0]
    assert ApproveBotButton(10, 900).item.custom_id == "anbot:10:900"
    assert DANGEROUS_MASK.administrator and DANGEROUS_MASK.ban_members and not DANGEROUS_MASK.send_messages
