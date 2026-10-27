"""The protections added after studying Wick / Bleed / Security: handing out a dangerous role is caught (it wasn't
watched before), mass role changes on members are put back, mass channel / role edits are restored, a webhook
spamming @everyone is deleted, and servers already protected get the new rules switched on."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from cogs.antinuke import NEW_RULES, RULE_LOOKUP, WEBHOOK_SPAM, Antinuke
from test_antinuke_undo import A, entry, feed, field, make_role, member, setup


class Role(SimpleNamespace):
    """Comparable and hashable like a discord.Role."""
    __hash__ = object.__hash__

    def __lt__(self, other):
        return self.position < other.position

    def is_default(self):
        return False


def _roles(guild):
    guild.me.top_role = Role(id=999, position=50)
    staff = Role(id=60, name="Staff", position=5, managed=False, permissions=discord.Permissions(manage_messages=True))
    vip = Role(id=61, name="VIP", position=4, managed=False, permissions=discord.Permissions.none())
    boss = Role(id=62, name="Boss", position=6, managed=False, permissions=discord.Permissions(administrator=True))
    for r in (staff, vip, boss):
        guild.roles_by_id[r.id] = r
    return staff, vip, boss


def test_audit_entries_go_to_the_right_rule():
    admin = SimpleNamespace(permissions=discord.Permissions(administrator=True))
    plain = SimpleNamespace(permissions=discord.Permissions.none())
    give = lambda *roles: SimpleNamespace(action=A.member_role_update, before=SimpleNamespace(roles=[]),  # noqa: E731
                                          after=SimpleNamespace(roles=list(roles)))
    assert Antinuke.module_for(give(admin)) == "permissions"  # was not watched at all before
    assert Antinuke.module_for(give(plain)) == "memberroles"
    escalate = SimpleNamespace(action=A.role_update, before=SimpleNamespace(permissions=discord.Permissions.none()),
                               after=SimpleNamespace(permissions=discord.Permissions(ban_members=True)))
    rename = SimpleNamespace(action=A.role_update, before=SimpleNamespace(permissions=None, name="a"),
                             after=SimpleNamespace(permissions=None, name="b"))
    assert Antinuke.module_for(escalate) == "permissions" and Antinuke.module_for(rename) == "roleupdate"
    assert Antinuke.module_for(SimpleNamespace(action=A.channel_update)) == "channelupdate"


def test_giving_someone_a_dangerous_role_is_taken_back():
    cog, guild, attacker = setup({"permissions": {"on": True, "threshold": 1, "window": 60, "punishment": "strip"}})
    _, _, boss = _roles(guild)
    alt = member(guild, 300, "alt")
    feed(cog, entry(guild, A.member_role_update, alt, before=SimpleNamespace(roles=[]),
                    after=SimpleNamespace(roles=[boss])))
    alt.remove_roles.assert_awaited()
    assert boss in alt.remove_roles.await_args.args


def test_mass_role_stripping_is_put_back():
    cog, guild, attacker = setup({"memberroles": {"on": True, "threshold": 3, "window": 60, "punishment": "strip"}})
    staff, vip, _ = _roles(guild)
    victims = [member(guild, 400 + i, f"mod{i}") for i in range(3)]
    feed(cog, *(entry(guild, A.member_role_update, v, before=SimpleNamespace(roles=[staff]),
                      after=SimpleNamespace(roles=[])) for v in victims))
    for v in victims:
        v.add_roles.assert_awaited_once_with(staff, reason="Antinuke: Mass adding or removing members' roles")
    card = guild.sent[0][0]["embed"]
    assert card.title == "🛡️ Antinuke: Member Roles" and "gave mod0 back Staff" in field(card, "Put back")


def test_two_role_changes_are_not_an_attack():
    cog, guild, attacker = setup({"memberroles": {"on": True, "threshold": 3, "window": 60, "punishment": "strip"}})
    staff, _, _ = _roles(guild)
    v = member(guild, 401, "x")
    feed(cog, *(entry(guild, A.member_role_update, v, after=SimpleNamespace(roles=[staff]),
                      before=SimpleNamespace(roles=[])) for _ in range(2)))
    assert guild.sent == [] and v.remove_roles.await_count == 0


def test_other_bots_doing_their_job_are_left_alone():
    cog, guild, attacker = setup({"memberroles": {"on": True, "threshold": 1, "window": 60, "punishment": "strip"},
                                  "channelupdate": {"on": True, "threshold": 1, "window": 60, "punishment": "strip"}})
    staff, _, _ = _roles(guild)
    ticket = SimpleNamespace(id=5, name="ticket-1", edit=AsyncMock())
    feed(cog, entry(guild, A.member_role_update, member(guild, 402, "y"), bot=True,
                    before=SimpleNamespace(roles=[]), after=SimpleNamespace(roles=[staff])),
         entry(guild, A.channel_update, ticket, bot=True,
               before=SimpleNamespace(name="ticket-1"), after=SimpleNamespace(name="closed-1")))
    assert guild.sent == []


def test_mass_channel_and_role_edits_are_restored():
    cog, guild, attacker = setup({"channelupdate": {"on": True, "threshold": 2, "window": 60, "punishment": "strip"},
                                  "roleupdate": {"on": True, "threshold": 2, "window": 60, "punishment": "strip"}})
    chans = [MagicMock(spec=discord.TextChannel, id=500 + i) for i in range(2)]
    for i, c in enumerate(chans):
        c.name, c.edit = f"nuked-{i}", AsyncMock()
    feed(cog, *(entry(guild, A.channel_update, c, before=SimpleNamespace(name=f"general-{i}", topic="hi"),
                      after=SimpleNamespace(name=f"nuked-{i}", topic="get nuked")) for i, c in enumerate(chans)))
    chans[0].edit.assert_awaited_once_with(reason="Antinuke: Mass editing channels", name="general-0", topic="hi")

    cog2, guild2, _ = setup({"roleupdate": {"on": True, "threshold": 2, "window": 60, "punishment": "strip"}})
    roles = [make_role(guild2, 70 + i, f"nuked{i}", 3) for i in range(2)]
    for r in roles:
        r.__class__ = type("R", (type(r),), {})
    real = [MagicMock(spec=discord.Role, id=r.id, managed=False) for r in roles]
    for i, r in enumerate(real):
        r.name, r.edit = f"nuked{i}", AsyncMock()
    feed(cog2, *(entry(guild2, A.role_update, r, before=SimpleNamespace(name=f"Mod{i}", colour=discord.Colour(1)),
                       after=SimpleNamespace(name=f"nuked{i}", colour=discord.Colour(2))) for i, r in enumerate(real)))
    real[1].edit.assert_awaited_once_with(reason="Antinuke: Mass editing roles", name="Mod1", colour=discord.Colour(1))


def test_reordering_channels_or_roles_is_not_an_edit():
    assert not Antinuke._matters("channelupdate", SimpleNamespace(before=SimpleNamespace(position=1),
                                                                   after=SimpleNamespace(position=2)))
    assert not Antinuke._matters("roleupdate", SimpleNamespace(before=SimpleNamespace(position=1),
                                                                after=SimpleNamespace(position=2)))


def _webhook_message(guild, hook_id=900, everyone=True):
    return SimpleNamespace(webhook_id=hook_id, guild=guild, mention_everyone=everyone, delete=AsyncMock(),
                           channel=SimpleNamespace(mention="<#1>"))


def test_a_webhook_spamming_everyone_is_deleted():
    cog, guild, _ = setup({"webhook": {"on": True, "threshold": 1, "window": 60, "punishment": "kick"}})
    log = SimpleNamespace(send=AsyncMock())
    cog._log_channel = AsyncMock(return_value=log)
    hook = SimpleNamespace(name="leaked", delete=AsyncMock())
    cog.bot.fetch_webhook = AsyncMock(return_value=hook)
    msgs = [_webhook_message(guild) for _ in range(WEBHOOK_SPAM[0])]
    for m in msgs:
        asyncio.run(cog.webhook_spam(m))
    hook.delete.assert_awaited_once()
    assert all(m.delete.await_count == 1 for m in msgs)
    alert = log.send.await_args.kwargs["embed"]
    assert alert.title == "🛡️ Antinuke: Webhook Spam" and "deleted the webhook leaked" in field(alert, "Put back")


def test_ordinary_webhook_messages_are_fine():
    cog, guild, _ = setup({"webhook": {"on": True, "threshold": 1, "window": 60, "punishment": "kick"}})
    cog.bot.fetch_webhook = AsyncMock()
    for _ in range(5):
        asyncio.run(cog.webhook_spam(_webhook_message(guild, everyone=False)))
    asyncio.run(cog.webhook_spam(_webhook_message(guild)))  # one announcement ping
    cog.bot.fetch_webhook.assert_not_awaited()


def test_protected_servers_get_the_new_rules_once():
    cfg = {"enabled": True, "invite_rule_v2": True, "modules": {"ban": {"on": True, "threshold": 3}}}
    assert Antinuke.migrate(cfg) is True
    for key in NEW_RULES:
        assert cfg["modules"][key]["on"] and cfg["modules"][key]["threshold"] == RULE_LOOKUP[key]["threshold"]
    cfg["modules"]["emoji"]["on"] = False  # a server's own choice afterwards sticks
    assert Antinuke.migrate(cfg) is False and cfg["modules"]["emoji"]["on"] is False
    off = {"enabled": False, "modules": {}}
    Antinuke.migrate(off)
    assert "memberroles" not in off["modules"]  # off servers get them when they turn the antinuke on
