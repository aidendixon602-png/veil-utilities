import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord.ext.commands.view import StringView

from cogs.devprotection import DeveloperProtection
from cogs.logging import BanLogButton
from core import perms

DEV = 123456789012345678
ACTOR = 234567890123456789
OWNER = 345678901234567890
BOT = 456789012345678901


def setup(monkeypatch):
    monkeypatch.setattr(perms, "BOT_DEVELOPERS", [DEV])
    channel = SimpleNamespace(
        permissions_for=lambda obj: SimpleNamespace(view_channel=True, create_instant_invite=True),
        create_invite=AsyncMock(return_value=SimpleNamespace(url="https://discord.gg/oneuse")),
        send=AsyncMock(return_value=SimpleNamespace(jump_url="https://discord.com/channels/10/11/12")),
    )
    guild = SimpleNamespace(id=10, name="Test server", owner_id=OWNER, me=SimpleNamespace(id=BOT),
                            default_role=SimpleNamespace(id=10), text_channels=[channel], ban=AsyncMock(),
                            unban=AsyncMock(), get_member_named=lambda _: None)
    developer = SimpleNamespace(id=DEV, send=AsyncMock())
    antinuke = SimpleNamespace(cfg=AsyncMock(return_value={}), _log_channel=AsyncMock(return_value=channel))
    bot = SimpleNamespace(user=SimpleNamespace(id=BOT), owner_ids=set(), owner_id=None, get_user=lambda _: developer,
                          fetch_user=AsyncMock(), get_cog=lambda name: antinuke if name == "Antinuke" else None)
    return DeveloperProtection(bot), guild, developer, channel


def unknown_ban():
    return discord.NotFound(MagicMock(status=404, reason="Not Found"), {"code": 10026, "message": "Unknown Ban"})


def audit(guild, *, actor=ACTOR, target=DEV, created=None, entry_id=1):
    return SimpleNamespace(action=discord.AuditLogAction.ban, guild=guild, id=entry_id, user_id=actor,
                           target=SimpleNamespace(id=target), created_at=created or discord.utils.utcnow())


def test_audit_ban_punishes_actor_unbans_developer_and_dms_private_links(monkeypatch):
    cog, guild, dev, channel = setup(monkeypatch)
    order = []
    guild.ban.side_effect = lambda *a, **k: order.append("ban actor")
    guild.unban.side_effect = lambda *a, **k: order.append("unban developer")
    asyncio.run(cog.on_audit_log_entry_create(audit(guild)))
    assert order == ["ban actor", "unban developer"]
    assert guild.ban.await_args.args[0].id == ACTOR
    assert guild.ban.await_args.kwargs["delete_message_seconds"] == 0
    assert guild.unban.await_args.args[0].id == DEV
    channel.create_invite.assert_awaited_once_with(max_age=3600, max_uses=1, unique=True,
                                                   reason="Developer protection: one-use rejoin invite")
    dm = dev.send.await_args.kwargs
    assert {button.url for button in dm["view"].children} == {
        "https://discord.com/channels/10/11/12", "https://discord.gg/oneuse"}
    assert "discord.gg" not in str(channel.send.await_args.kwargs["embed"].to_dict())


def test_ban_failure_never_prevents_unbanning_and_reports_actual_status(monkeypatch):
    cog, guild, dev, _ = setup(monkeypatch)
    guild.ban.side_effect = discord.Forbidden(MagicMock(status=403, reason="Forbidden"), "no")
    asyncio.run(cog.on_audit_log_entry_create(audit(guild)))
    guild.unban.assert_awaited_once()
    fields = {field.name: field.value for field in dev.send.await_args.kwargs["embed"].fields}
    assert "Discord 403" in fields["Actor ban"]
    assert "Unbanned" in fields["Developer ban"]


def test_unban_failure_does_not_create_invite_and_is_reported(monkeypatch):
    cog, guild, dev, channel = setup(monkeypatch)
    guild.unban.side_effect = discord.Forbidden(MagicMock(status=403, reason="Forbidden"), "no")
    asyncio.run(cog.on_audit_log_entry_create(audit(guild)))
    channel.create_invite.assert_not_awaited()
    fields = {field.name: field.value for field in dev.send.await_args.kwargs["embed"].fields}
    assert "unban manually" in fields["Developer ban"]


def test_unknown_or_protected_actor_is_not_banned(monkeypatch):
    for actor in (None, OWNER, DEV, BOT):
        cog, guild, dev, _ = setup(monkeypatch)
        asyncio.run(cog.on_audit_log_entry_create(audit(guild, actor=actor)))
        guild.ban.assert_not_awaited()
        guild.unban.assert_awaited_once()
        dev.send.assert_awaited_once()


def test_fallback_unbans_without_guessing_a_moderator(monkeypatch):
    cog, guild, dev, _ = setup(monkeypatch)
    with patch("cogs.devprotection.AUDIT_GRACE_SECONDS", 0):
        asyncio.run(cog.on_member_ban(guild, SimpleNamespace(id=DEV)))
    guild.ban.assert_not_awaited()
    guild.unban.assert_awaited_once()
    fields = {field.name: field.value for field in dev.send.await_args.kwargs["embed"].fields}
    assert fields["Actor"] == "Unknown"


def test_fallback_does_not_duplicate_handled_audit_alert(monkeypatch):
    cog, guild, dev, _ = setup(monkeypatch)
    async def go():
        await cog.on_audit_log_entry_create(audit(guild))
        guild.unban.side_effect = unknown_ban()
        with patch("cogs.devprotection.AUDIT_GRACE_SECONDS", 0):
            await cog.on_member_ban(guild, SimpleNamespace(id=DEV))
    asyncio.run(go())
    assert dev.send.await_count == 1
    assert guild.ban.await_count == 1


def test_duplicate_or_stale_audit_entries_never_repeat_punishment(monkeypatch):
    from datetime import timedelta
    cog, guild, dev, _ = setup(monkeypatch)
    async def go():
        event = audit(guild)
        await cog.on_audit_log_entry_create(event)
        await cog.on_audit_log_entry_create(event)
        await cog.on_audit_log_entry_create(audit(guild, entry_id=2,
                                                 created=discord.utils.utcnow() - timedelta(minutes=5)))
    asyncio.run(go())
    assert guild.ban.await_count == guild.unban.await_count == dev.send.await_count == 1


def test_private_channel_is_not_used_for_rejoin_invite(monkeypatch):
    cog, guild, dev, channel = setup(monkeypatch)
    channel.permissions_for = lambda _: SimpleNamespace(view_channel=False, create_instant_invite=True)
    asyncio.run(cog.on_audit_log_entry_create(audit(guild)))
    channel.create_invite.assert_not_awaited()
    assert "No invite" in str(dev.send.await_args.kwargs["embed"].to_dict())


def test_command_attempt_identifies_explicit_target_before_permission_checks(monkeypatch):
    cog, guild, _, _ = setup(monkeypatch)
    cog.handle = AsyncMock()
    ctx = SimpleNamespace(guild=guild, command=SimpleNamespace(qualified_name="ban"),
                          view=StringView(f" <@{DEV}> reason"), author=SimpleNamespace(id=ACTOR), send=AsyncMock())
    assert asyncio.run(cog.blocked_command(ctx))
    cog.handle.assert_awaited_once_with(guild, DEV, ACTOR, source="Veil command", actual_ban=False)


def test_unrelated_command_or_user_is_not_intercepted(monkeypatch):
    cog, guild, _, _ = setup(monkeypatch)
    cog.handle = AsyncMock()
    for name, user in (("unban", DEV), ("ban", ACTOR)):
        ctx = SimpleNamespace(guild=guild, command=SimpleNamespace(qualified_name=name),
                              view=StringView(f" <@{user}>"), author=SimpleNamespace(id=ACTOR), send=AsyncMock())
        assert not asyncio.run(cog.blocked_command(ctx))
    cog.handle.assert_not_awaited()


def test_ban_log_reban_button_blocks_developer_before_authorization_check(monkeypatch):
    cog, guild, _, _ = setup(monkeypatch)
    cog.handle = AsyncMock()
    guild.get_member = lambda uid: SimpleNamespace(id=uid)
    it = SimpleNamespace(guild=guild, user=SimpleNamespace(id=ACTOR),
                         client=SimpleNamespace(get_cog=lambda name: cog, owner_ids=set(), owner_id=None),
                         response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
                         followup=SimpleNamespace(send=AsyncMock()))
    asyncio.run(BanLogButton(guild.id, DEV, False).callback(it))
    cog.handle.assert_awaited_once_with(guild, DEV, ACTOR, source="Veil ban-log button", actual_ban=False)
    guild.ban.assert_not_awaited()
    assert "blocked" in it.followup.send.await_args.args[0]


def test_non_developer_command_keeps_parser_position(monkeypatch):
    cog, guild, _, _ = setup(monkeypatch)
    view = StringView(f" <@{ACTOR}> reason")
    ctx = SimpleNamespace(guild=guild, command=SimpleNamespace(qualified_name="ban"), view=view,
                          author=SimpleNamespace(id=ACTOR), send=AsyncMock())
    assert not asyncio.run(cog.blocked_command(ctx))
    assert view.index == 0 and view.previous == 0
    view.skip_ws()
    assert view.get_quoted_word() == f"<@{ACTOR}>"


def test_unknown_ban_without_audit_still_notifies_developer(monkeypatch):
    cog, guild, dev, _ = setup(monkeypatch)
    guild.unban.side_effect = unknown_ban()
    with patch("cogs.devprotection.AUDIT_GRACE_SECONDS", 0):
        asyncio.run(cog.on_member_ban(guild, SimpleNamespace(id=DEV)))
    guild.ban.assert_not_awaited()
    dev.send.assert_awaited_once()
    assert "already unbanned" in str(dev.send.await_args.kwargs["embed"].to_dict())


def test_unexpected_punishment_error_cannot_block_developer_unban(monkeypatch):
    cog, guild, dev, _ = setup(monkeypatch)
    cog._ban_actor = AsyncMock(side_effect=RuntimeError("unexpected"))
    asyncio.run(cog.on_audit_log_entry_create(audit(guild)))
    guild.unban.assert_awaited_once()
    assert "Failed unexpectedly" in str(dev.send.await_args.kwargs["embed"].to_dict())


def test_bot_dispatch_checks_before_invoking_and_invokes_unrelated_commands(monkeypatch):
    from core.bot import Veil
    cog, guild, _, _ = setup(monkeypatch)
    message = SimpleNamespace(author=SimpleNamespace(id=ACTOR, bot=False), guild=guild)
    context = SimpleNamespace(guild=guild, command=SimpleNamespace(qualified_name="ban"),
                              view=StringView(f" <@{DEV}>"), author=message.author, send=AsyncMock())
    cog.handle = AsyncMock()
    bot = SimpleNamespace(get_context=AsyncMock(return_value=context), get_cog=lambda name: cog,
                          invoke=AsyncMock())
    asyncio.run(Veil.process_commands(bot, message))
    bot.invoke.assert_not_awaited()
    cog.handle.assert_awaited_once()
    context.view = StringView(f" <@{ACTOR}>")
    asyncio.run(Veil.process_commands(bot, message))
    bot.invoke.assert_awaited_once_with(context)
    assert context.view.index == 0


def test_direct_command_callback_also_blocks_developer_target(monkeypatch):
    from cogs.moderation import Moderation
    from core.utils import UserError
    import pytest
    cog, guild, _, _ = setup(monkeypatch)
    cog.handle = AsyncMock()
    cog.bot.get_cog = lambda name: cog if name == "DeveloperProtection" else None
    moderation = Moderation(cog.bot)
    ctx = SimpleNamespace(guild=guild, author=SimpleNamespace(id=ACTOR))
    with pytest.raises(UserError, match="Developer ban blocked"):
        asyncio.run(moderation._protect_developer_ban(ctx, SimpleNamespace(id=DEV)))
    cog.handle.assert_awaited_once()


def test_general_antinuke_does_not_process_protected_developer_ban(monkeypatch):
    from cogs.antinuke import Antinuke
    cog, guild, _, _ = setup(monkeypatch)
    cog.bot.get_cog = lambda name: cog if name == "DeveloperProtection" else None
    antinuke = Antinuke(cog.bot)
    antinuke.cfg = AsyncMock(side_effect=AssertionError("regular antinuke must not handle this ban"))
    asyncio.run(antinuke.on_audit_log_entry_create(audit(guild)))
    antinuke.cfg.assert_not_awaited()
