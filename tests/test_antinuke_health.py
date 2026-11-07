import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from cogs.antinuke import Antinuke
from test_antinuke_panel import setup


def test_health_reports_missing_permissions_and_intents():
    cog, guild = setup()
    guild.me = SimpleNamespace(guild_permissions=SimpleNamespace(view_audit_log=False, manage_roles=True))
    cog.bot.intents = SimpleNamespace(members=False)
    ctx = SimpleNamespace(guild=guild, author=SimpleNamespace(id=guild.owner_id), bot=cog.bot,
                          clean_prefix=",", send=AsyncMock())
    asyncio.run(Antinuke.an_health.callback(cog, ctx))
    text = ctx.send.await_args.kwargs["embed"].description
    assert "Antinuke: **off**" in text
    assert "View Audit Log" in text and "Server Members intent is off" in text
    assert "can't guarantee protection" in text


def test_health_warns_about_overlapping_protection(monkeypatch):
    from core import perms
    cog, guild = setup()
    monkeypatch.setattr(perms, "ghost_guards", lambda guild: True)
    guild.me = SimpleNamespace(guild_permissions=SimpleNamespace(
        view_audit_log=True, manage_roles=True, manage_channels=True, ban_members=True,
        kick_members=True, moderate_members=True))
    cog.bot.intents = SimpleNamespace(members=True)
    ctx = SimpleNamespace(guild=guild, author=SimpleNamespace(id=guild.owner_id), bot=cog.bot,
                          clean_prefix=",", send=AsyncMock())
    asyncio.run(Antinuke.an_health.callback(cog, ctx))
    text = ctx.send.await_args.kwargs["embed"].description
    assert "Ghost Vanguard is present" in text and "avoid duplicate actions" in text
    assert "Rules won't run until" in text  # even when Ghost is present, Veil can still be off
    assert "Core audit and response permissions are present" in text
