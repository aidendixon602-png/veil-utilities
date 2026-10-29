"""Nobody moderates themselves, the server owner, a bot developer or the bot, including through commands that take
a plain user ID (ban by ID, the network bans), which used to skip the member-only hierarchy check."""
import asyncio
import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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
    prompt = SimpleNamespace(edit=AsyncMock(), delete=AsyncMock())
    ctx = SimpleNamespace(author=SimpleNamespace(id=author), guild=guild, bot=bot,
                          confirm=AsyncMock(return_value=True), neutral=AsyncMock(), approve=AsyncMock(),
                          send=AsyncMock(), warn=AsyncMock(return_value=prompt), prompt=prompt)
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
    ctx.warn.assert_not_called()  # refused before any prompt


class _Answer:
    """Stands in for the red Confirm / grey Cancel prompt, already answered."""
    answer = True

    def __init__(self, *a, **k):
        self.value = None

    async def wait(self):
        self.value = _Answer.answer


def test_network_ban_skips_servers_the_target_owns():
    owned, other = server(7, OTHER), server(8, 999)
    ctx = make_ctx(guilds=[owned, other])
    tools = DevTools.__new__(DevTools)
    tools.bot = ctx.bot
    user = SimpleNamespace(id=OTHER, mention=f"<@{OTHER}>", name="raider")
    seen = []  # the prompt's text at each edit (the same embed is edited in place)
    ctx.prompt.edit = AsyncMock(side_effect=lambda **k: seen.append(k["embed"].description))
    with patch.dict(DevTools.genreban.callback.__globals__, {"DangerConfirm": _Answer, "WORKING_FOR": 0}):
        asyncio.run(DevTools.genreban.callback(tools, ctx, user))
    owned.ban.assert_not_called()
    other.ban.assert_awaited_once()
    assert other.ban.await_args.kwargs["reason"].startswith("genreban by ")  # the ban log credits who ran it
    ctx.warn.assert_awaited_once_with("Processing...")
    asked, working, done = seen
    assert working.startswith("⏳ Genrebanning @raider ( 500 ) across **1** server(s)")
    assert "@raider ( 500 )" in asked and "1** server(s)" in asked and "skipping **1** server they own" in asked
    assert done.startswith("**raider** has been genrebanned across **1** server(s).") and "skipped **1**" in done


def test_cancelling_a_network_ban_does_nothing():
    other = server(8, 999)
    ctx = make_ctx(guilds=[other])
    tools = DevTools.__new__(DevTools)
    tools.bot = ctx.bot
    _Answer.answer = False
    try:
        with patch.dict(DevTools.genreban.callback.__globals__, {"DangerConfirm": _Answer, "WORKING_FOR": 0}):
            asyncio.run(DevTools.genreban.callback(tools, ctx, SimpleNamespace(id=OTHER, mention="x", name="r")))
    finally:
        _Answer.answer = True
    other.ban.assert_not_called()
    ctx.bot.db.execute.assert_not_called()
    assert "Cancelled genreban" in ctx.prompt.edit.await_args.kwargs["embed"].description


def test_the_old_names_still_work():
    assert {"hardban", "reban", "hb", "globalban"} <= set(DevTools.genreban.aliases)
    assert {"unhardban", "unreban", "unhb"} <= set(DevTools.ungenreban.aliases)


def test_developer_tools_stay_developer_only():
    """A server owner or an Owner-level member must never reach eval, shell, gitpull, genreban..."""
    ctx = make_ctx(author=OWNER)
    tools = DevTools.__new__(DevTools)
    tools.bot = SimpleNamespace(is_owner=AsyncMock(return_value=False))
    with pytest.raises(Exception):
        asyncio.run(tools.cog_check(ctx))


def test_network_ban_of_someone_who_owns_every_server_is_refused():
    ctx = make_ctx(guilds=[server(7, OTHER)])
    tools = DevTools.__new__(DevTools)
    tools.bot = ctx.bot
    with pytest.raises(UserError, match="owns every server"):
        asyncio.run(DevTools.genreban.callback(tools, ctx, SimpleNamespace(id=OTHER, mention="x")))
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


def test_the_working_step_stays_up_a_moment():
    """Even when every server answers at once, "Genrebanning..." is shown for a moment before the result."""
    from cogs import devtools
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    async def go():
        working = devtools._Working(contextlib.nullcontext(), devtools.time.monotonic())
        async with working:
            pass
    with patch.object(devtools.asyncio, "sleep", fake_sleep):
        asyncio.run(go())
    assert slept and 0 < slept[0] <= devtools.WORKING_FOR
