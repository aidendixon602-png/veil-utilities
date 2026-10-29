"""Antinuke alert cards: one card per incident, an honest Result field, and notice DMs only to the right people."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from cogs.antinuke import Antinuke

BOT_ID, OWNER, ATTACKER, VICTIM = 4, 1, 5, 6


def make_role(rid, name, pos, **perms):
    return SimpleNamespace(id=rid, name=name, position=pos, managed=False, permissions=discord.Permissions(**perms))


def make_member(uid, name, roles, top_pos):
    return SimpleNamespace(id=uid, name=name, bot=False, roles=roles, top_role=SimpleNamespace(position=top_pos),
                           send=AsyncMock(), remove_roles=AsyncMock(), ban=AsyncMock(), kick=AsyncMock(),
                           display_avatar=SimpleNamespace(url="https://cdn.example/a.png"))


def setup(attacker_top=10):
    everyone = make_role(0, "@everyone", 0)
    admin = make_role(77, "Admin", 9, administrator=True)
    attacker = make_member(ATTACKER, "rogue", [everyone, admin], attacker_top)
    victim = make_member(VICTIM, "victim", [everyone], 1)
    sent = []

    async def owner_send(**kw):
        msg = MagicMock()
        msg.edit = AsyncMock()
        sent.append((kw, msg))
        return msg

    guild = SimpleNamespace(
        id=10, name="Test", owner_id=OWNER, owner=SimpleNamespace(id=OWNER, send=AsyncMock(side_effect=owner_send)),
        me=SimpleNamespace(top_role=SimpleNamespace(position=50)), text_channels=[], ban=AsyncMock(), unban=AsyncMock(),
        get_member=lambda uid: {ATTACKER: attacker, VICTIM: victim}.get(uid),
        get_channel=lambda _id: None, get_role=lambda _id: None,
    )
    bot = SimpleNamespace(user=SimpleNamespace(id=BOT_ID), get_user=lambda _id: None, fetch_user=AsyncMock(),
                          db=SimpleNamespace(get_config=AsyncMock(return_value={}), set_config=AsyncMock()))
    cog = Antinuke.__new__(Antinuke)
    Antinuke.__init__(cog, bot)
    return cog, guild, attacker, victim, sent


def respond(cog, guild, module, entry):
    with patch("cogs.authorization.developers", AsyncMock(return_value=set())):
        asyncio.run(cog.respond(guild, {"enabled": True, "punishment": "ban"}, ATTACKER, module, entry))


def field(embed, name):
    return next((f.value for f in embed.fields if f.name == name), None)


def punishment(embed):
    """What the card says happened to the attacker."""
    line = next(line for line in embed.description.splitlines() if line.startswith("**Punishment:**"))
    return line.removeprefix("**Punishment:** ")


def test_repeats_update_one_card_instead_of_sending_new_ones():
    cog, guild, attacker, victim, sent = setup()
    entry = SimpleNamespace(target=victim, action=discord.AuditLogAction.ban)
    respond(cog, guild, "ban", entry)
    respond(cog, guild, "ban", entry)
    respond(cog, guild, "ban", entry)
    assert guild.owner.send.await_count == 1
    _, msg = sent[0]
    assert msg.edit.await_count == 2
    last = msg.edit.await_args.kwargs["embed"]
    assert field(last, "Repeated").startswith("3 times")


def test_ban_victim_does_not_get_the_roles_removed_notice():
    cog, guild, attacker, victim, sent = setup()
    respond(cog, guild, "ban", SimpleNamespace(target=victim, action=discord.AuditLogAction.ban))
    victim.send.assert_not_awaited()
    attacker.send.assert_not_awaited()
    attacker.ban.assert_awaited_once()
    card = sent[0][0]["embed"]
    assert card.title == "🛡️ Antinuke: Mass Ban"
    assert punishment(card) == "Banned"
    assert field(card, "Their roles") == "Admin"
    assert "went over the **Mass Ban** limit" in card.description
    assert field(card, "Attacker").endswith("-# rogue · 5")


def test_result_is_honest_when_the_attacker_is_above_the_bot():
    cog, guild, attacker, victim, sent = setup(attacker_top=99)
    respond(cog, guild, "ban", SimpleNamespace(target=victim, action=discord.AuditLogAction.ban))
    attacker.ban.assert_not_awaited()
    assert punishment(sent[0][0]["embed"]).startswith("⚠️ Not punished: their top role is above mine")


def test_permission_grant_strips_and_tells_the_attacker_why():
    cog, guild, attacker, victim, sent = setup()
    respond(cog, guild, "permissions", None)
    attacker.remove_roles.assert_awaited_once()
    attacker.ban.assert_not_awaited()
    assert punishment(sent[0][0]["embed"]) == "Stripped 1 staff role(s)"
    notice = attacker.send.await_args.kwargs["embed"].description
    assert "removed your staff roles (**Admin**)" in notice


def test_failed_punishment_is_reported_as_a_failure():
    cog, guild, attacker, victim, sent = setup()
    response = MagicMock(status=403, reason="Forbidden")
    attacker.ban.side_effect = discord.Forbidden(response, "Missing Permissions")
    respond(cog, guild, "ban", SimpleNamespace(target=victim, action=discord.AuditLogAction.ban))
    assert punishment(sent[0][0]["embed"]) == "⚠️ Ban failed (Discord said 403)"


def test_the_log_channel_copy_has_no_button_but_dms_do():
    cog, guild, attacker, victim, sent = setup()
    log = MagicMock()
    log.send = AsyncMock(return_value=MagicMock())
    cog._log_channel = AsyncMock(return_value=log)
    respond(cog, guild, "ban", SimpleNamespace(target=victim, action=discord.AuditLogAction.ban))
    assert "view" not in log.send.await_args.kwargs
    owner_kwargs = sent[0][0]
    assert owner_kwargs["view"].children[0].label == "Open server"


def test_no_roles_means_no_roles_field():
    cog, guild, attacker, victim, sent = setup()
    attacker.roles = [attacker.roles[0]]  # only @everyone
    respond(cog, guild, "ban", SimpleNamespace(target=victim, action=discord.AuditLogAction.ban))
    assert field(sent[0][0]["embed"], "Their roles") is None
