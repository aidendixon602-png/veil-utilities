import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlparse

import discord
import pytest
from discord.ext import commands

from cogs.owner import INVITE_BASIC_PERMISSIONS, INVITE_MANAGEMENT_PERMISSIONS, Owner
from core import perms
from core.utils import UserError

APP_ID = 123456789012345678


def make_ctx(*, guild=True):
    return SimpleNamespace(guild=SimpleNamespace(id=1) if guild else None, clean_prefix=",",
                           author=SimpleNamespace(id=123, send=AsyncMock()), send=AsyncMock(), approve=AsyncMock())


def bot(app_id=APP_ID, user_id=APP_ID):
    return SimpleNamespace(application_id=app_id, user=SimpleNamespace(id=user_id), is_owner=AsyncMock(return_value=True))


def assert_links(kwargs):
    assert kwargs["embed"].title == "🔗 Invite Veil Utilities"
    assert "Public Bot" in kwargs["embed"].description and ",authorize <server-id>" in kwargs["embed"].description
    buttons = kwargs["view"].children
    assert len(buttons) == 2
    for button, profile in zip(buttons, (INVITE_MANAGEMENT_PERMISSIONS, INVITE_BASIC_PERMISSIONS)):
        parsed = urlparse(button.url)
        query = parse_qs(parsed.query)
        assert (parsed.scheme, parsed.netloc, parsed.path) == ("https", "discord.com", "/oauth2/authorize")
        assert query["client_id"] == [str(APP_ID)]
        assert set(query["scope"][0].split()) == {"bot", "applications.commands"}
        assert int(query["permissions"][0]) == profile.value
        assert not profile.administrator
    assert INVITE_MANAGEMENT_PERMISSIONS.ban_members and INVITE_MANAGEMENT_PERMISSIONS.manage_roles
    assert not INVITE_BASIC_PERMISSIONS.ban_members and INVITE_BASIC_PERMISSIONS.send_messages


def test_server_invite_is_delivered_by_dm_only():
    ctx = make_ctx()
    asyncio.run(Owner.invite.callback(Owner(bot()), ctx))
    assert_links(ctx.author.send.await_args.kwargs)
    ctx.send.assert_not_awaited()
    ctx.approve.assert_awaited_once_with("sent the invite links to your DMs")


def test_invite_in_dm_uses_current_user_identity_if_application_id_is_missing():
    ctx = make_ctx(guild=False)
    asyncio.run(Owner.invite.callback(Owner(bot(app_id=None)), ctx))
    assert_links(ctx.send.await_args.kwargs)
    ctx.author.send.assert_not_awaited()
    ctx.approve.assert_not_awaited()


def test_failed_dm_never_posts_install_link_in_the_channel():
    ctx = make_ctx()
    ctx.author.send.side_effect = discord.Forbidden(MagicMock(status=403, reason="Forbidden"), "no DMs")
    with pytest.raises(UserError, match="couldn't DM"):
        asyncio.run(Owner.invite.callback(Owner(bot()), ctx))
    ctx.send.assert_not_awaited()
    ctx.approve.assert_not_awaited()


def test_invite_requires_a_real_application_id():
    ctx = make_ctx(guild=False)
    with pytest.raises(UserError, match="application ID"):
        asyncio.run(Owner.invite.callback(Owner(bot(app_id=None, user_id=None)), ctx))
    ctx.send.assert_not_awaited()


def test_command_is_hidden_and_owner_cog_rejects_nondevelopers(monkeypatch):
    monkeypatch.setattr(perms, "BOT_DEVELOPERS", [])
    monkeypatch.setattr(perms, "DISCOVERED_DEVELOPERS", set())
    fake = bot()
    fake.owner_ids, fake.owner_id = set(), None
    fake.is_owner = AsyncMock(return_value=False)
    ctx = make_ctx()
    ctx.author.id = 998877665544332211
    assert Owner.invite.hidden
    with pytest.raises(commands.NotOwner):
        asyncio.run(Owner(fake).cog_check(ctx))
