"""Nobody moderates themselves, the server owner, a bot developer or the bot, including through commands that take
a plain user ID (ban by ID, the network bans), which used to skip the member-only hierarchy check."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from cogs.devtools import DevTools
from cogs.moderation import Moderation
from core import perms
from core.utils import UserError, check_target

AUTHOR, OWNER, BOT, DEV, OTHER = 100, 200, 300, 400, 500


def make_ctx(author=AUTHOR, owner=OWNER, guilds=()):
    db = SimpleNamespace(execute=AsyncMock(), fetchone=AsyncMock(return_value=None))
    bot = SimpleNamespace(user=SimpleNamespace(id=BOT), owner_ids=set(), owner_id=None, db=db, guilds=list(guilds))
    guild = MagicMock()
    guild.id, guild.owner_id, guild.me = 1, owner, SimpleNamespace(id=BOT)
    guild.ban = AsyncMock()
    ctx = SimpleNamespace(author=SimpleNamespace(id=author), guild=guild, bot=bot,
                          confirm=AsyncMock(return_value=True), neutral=AsyncMock(), approve=AsyncMock(),
                          send=AsyncMock())
    return ctx


@pytest.fixture(autouse=True)
def a_developer(monkeypatch):
    monkeypatch.setattr(perms, "BOT_DEVELOPERS", [DEV])


@pytest.mark.parametrize("target, words", [
    (AUTHOR, "yourself"), (OWNER, "the server owner"), (DEV, "a bot developer"), (BOT, "myself")])
def test_protected_targets_are_refused_by_id(target, words):
    with pytest.raises(UserError, match=words):
        check_target(make_ctx(), discord.Object(target), "ban")


def test_anyone_else_is_fine():
    check_target(make_ctx(), discord.Object(OTHER), "ban")


def test_the_owner_cant_moderate_themselves_either():
    with pytest.raises(UserError, match="yourself"):
        check_target(make_ctx(author=OWNER), discord.Object(OWNER), "ban")


@pytest.mark.parametrize("target", [AUTHOR, OWNER, DEV, BOT])
def test_ban_by_user_id_never_reaches_protected_people(target):
    ctx = make_ctx()
    cog = Moderation.__new__(Moderation)
    cog.bot = ctx.bot
    user = SimpleNamespace(id=target, mention=f"<@{target}>")
    with pytest.raises(UserError):
        asyncio.run(Moderation.ban.callback(cog, ctx, user))
    ctx.guild.ban.assert_not_called()


def server(gid, owner):
    return SimpleNamespace(id=gid, name=f"server {gid}", owner_id=owner, ban=AsyncMock())


@pytest.mark.parametrize("target", [AUTHOR, OWNER, DEV, BOT])
def test_network_bans_refuse_protected_people_and_record_nothing(target):
    guilds = [server(1, OWNER), server(2, OTHER)]
    ctx = make_ctx(guilds=guilds)
    tools = DevTools.__new__(DevTools)
    tools.bot = ctx.bot
    user = SimpleNamespace(id=target, mention=f"<@{target}>")
    with pytest.raises(UserError):
        asyncio.run(DevTools.genreban.callback(tools, ctx, user))
    ctx.bot.db.execute.assert_not_called()  # no hardban row, so nothing re-bans them later either
    assert not any(g.ban.called for g in guilds)
    ctx.confirm.assert_not_called()


def test_network_ban_skips_servers_the_target_owns():
    owned, other = server(7, OTHER), server(8, 999)
    ctx = make_ctx(guilds=[owned, other])
    tools = DevTools.__new__(DevTools)
    tools.bot = ctx.bot
    user = SimpleNamespace(id=OTHER, mention=f"<@{OTHER}>")
    asyncio.run(DevTools.hardban.callback(tools, ctx, user))
    owned.ban.assert_not_called()
    other.ban.assert_awaited_once()
    embed = ctx.send.await_args.kwargs["embed"]
    assert any("they own it" in f.value for f in embed.fields)


def test_network_ban_of_someone_who_owns_every_server_is_refused():
    ctx = make_ctx(guilds=[server(7, OTHER)])
    tools = DevTools.__new__(DevTools)
    tools.bot = ctx.bot
    with pytest.raises(UserError, match="owns every server"):
        asyncio.run(DevTools.hardban.callback(tools, ctx, SimpleNamespace(id=OTHER, mention="x")))
    ctx.bot.db.execute.assert_not_called()


@pytest.mark.parametrize("target", [AUTHOR, OWNER])
def test_panels_wont_open_on_yourself_or_the_owner(target):
    ctx = make_ctx()
    cog = Moderation.__new__(Moderation)
    cog.bot = ctx.bot
    member = SimpleNamespace(id=target, mention="x")
    for cmd in (Moderation.mod_panel, Moderation.admin_panel):
        with pytest.raises(UserError):
            asyncio.run(cmd.callback(cog, ctx, member))
    ctx.send.assert_not_called()
