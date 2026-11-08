"""Antinuke alert cards: one card per incident, an honest Result field, and notice DMs only to the right people."""
import asyncio
import importlib
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from cogs.antinuke import Antinuke, settings

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
    with patch.object(importlib.import_module("cogs.authorization"), "developers", AsyncMock(return_value=set())):
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
    assert card.title == "Antinuke · Mass Ban"
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


def test_real_incident_is_queued_once_with_truthful_result_and_owner_not_duplicated():
    cog, guild, attacker, victim, sent = setup(attacker_top=99)
    cog.bot.incident_manager = SimpleNamespace(record=AsyncMock(return_value="incident"))
    entry = SimpleNamespace(target=victim, action=discord.AuditLogAction.ban)
    with patch("core.incidents.recipients", return_value={OWNER}), patch(
        "cogs.antinuke.settings", replace(settings, antinuke_dev_alerts=True)
    ):
        respond(cog, guild, "ban", entry)
        respond(cog, guild, "ban", entry)
    cog.bot.incident_manager.record.assert_awaited_once()
    record = cog.bot.incident_manager.record.await_args.kwargs
    assert record["kind"] == "antinuke" and record["actor_id"] == ATTACKER
    assert "Not punished" in record["action"]
    guild.owner.send.assert_not_awaited()  # the owner is a developer and gets the queued copy


def test_failed_incident_write_keeps_existing_owner_and_developer_alerts():
    cog, guild, attacker, victim, sent = setup()
    cog.bot.incident_manager = SimpleNamespace(record=AsyncMock(side_effect=RuntimeError("DB unavailable")))
    entry = SimpleNamespace(target=victim, action=discord.AuditLogAction.ban)
    with patch("cogs.antinuke.settings", replace(settings, antinuke_dev_alerts=True)):
        respond(cog, guild, "ban", entry)
    guild.owner.send.assert_awaited_once()
    assert punishment(sent[0][0]["embed"]) == "Banned"


def test_strip_skips_everyone_even_if_it_has_dangerous_permissions():
    cog, guild, attacker, _, sent = setup()
    everyone = make_role(guild.id, "@everyone", 0, administrator=True)
    attacker.roles.insert(0, everyone)
    respond(cog, guild, "permissions", None)
    assert all(everyone not in call.args for call in attacker.remove_roles.await_args_list)
    result = punishment(sent[0][0]["embed"])
    assert "Strip incomplete" in result and "removed 1 staff role(s)" in result
    assert "@everyone (not removable by the bot; check manually)" in result


def test_strip_continues_after_unknown_role_404_and_reports_partial_result():
    cog, guild, attacker, _, sent = setup()
    first = make_role(78, "Deleted role", 3, administrator=True)
    second = make_role(79, "Staff", 4, ban_members=True)
    attacker.roles = [attacker.roles[0], first, second]
    response = MagicMock(status=404, reason="Not Found")
    attacker.remove_roles.side_effect = [discord.NotFound(response, {"code": 10011, "message": "Unknown Role"}), None]
    respond(cog, guild, "permissions", None)
    assert [call.args[0] for call in attacker.remove_roles.await_args_list] == [first, second]
    result = punishment(sent[0][0]["embed"])
    assert "Strip incomplete" in result and "removed 1 staff role(s)" in result
    assert "Deleted role (role not found; Discord 404)" in result
    assert "Staff" in attacker.send.await_args.kwargs["embed"].description
    assert "Deleted role" not in attacker.send.await_args.kwargs["embed"].description


def test_unknown_member_404_does_not_claim_success_or_try_remaining_roles():
    cog, guild, attacker, _, sent = setup()
    attacker.roles.append(make_role(79, "Other staff", 4, ban_members=True))
    response = MagicMock(status=404, reason="Not Found")
    attacker.remove_roles.side_effect = discord.NotFound(response, {"code": 10007, "message": "Unknown Member"})
    respond(cog, guild, "permissions", None)
    attacker.remove_roles.assert_awaited_once()
    result = punishment(sent[0][0]["embed"])
    assert "Strip incomplete" in result and "member not found, remaining roles not attempted" in result
    attacker.send.assert_not_awaited()


def test_uncached_attacker_is_fetched_and_not_assumed_to_have_left():
    cog, guild, attacker, _, sent = setup()
    guild.get_member = lambda uid: None
    cog.bot.get_user = lambda uid: attacker
    guild.fetch_member = AsyncMock(return_value=attacker)
    respond(cog, guild, "permissions", None)
    guild.fetch_member.assert_awaited_once_with(ATTACKER)
    attacker.remove_roles.assert_awaited_once()
    assert punishment(sent[0][0]["embed"]) == "Stripped 1 staff role(s)"


def test_uncached_attacker_reports_confirmed_unknown_member():
    cog, guild, attacker, _, sent = setup()
    guild.get_member = lambda uid: None
    cog.bot.get_user = lambda uid: attacker
    response = MagicMock(status=404, reason="Not Found")
    guild.fetch_member = AsyncMock(side_effect=discord.NotFound(response, {"code": 10007, "message": "Unknown Member"}))
    respond(cog, guild, "permissions", None)
    assert punishment(sent[0][0]["embed"]) == "Nothing to strip: Discord reports the attacker is no longer a member"
