"""Private mode: only developers, the server owner and people with a permission level can run commands."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from core.bot import Veil
from core.config import Settings
from core.utils import Ignored

OWNER, DEV, STRANGER, LEVELLED, ROLE_ID, FAKE_ROLE = 100, 1, 999, 555, 77, 88


def _bot(private=True, permlevels=None, fakeperms=None):
    bot = Veil()
    bot.owner_ids = frozenset([DEV])
    bot.auth_required = False
    bot.private_mode = private
    configs = {"permlevels": permlevels or {}, "fakeperms": fakeperms or {}, "settings": {}}
    bot.db = SimpleNamespace(get_config=AsyncMock(side_effect=lambda gid, mod, default=None: configs.get(mod, {})))
    return bot


def _ctx(author_id=STRANGER, roles=(), guild=True, admin=False):
    author = SimpleNamespace(id=author_id, roles=[SimpleNamespace(id=r, name=f"role{r}", mention=f"<@&{r}>") for r in roles],
                             guild_permissions=discord.Permissions(administrator=admin))
    cmd = SimpleNamespace(hidden=False, parents=[], qualified_name="ping", root_parent=None, name="ping")
    return SimpleNamespace(guild=SimpleNamespace(id=1, owner_id=OWNER) if guild else None,
                           channel=SimpleNamespace(id=10), author=author, command=cmd, cog=None)


LEVELS = {"users": {str(LEVELLED): "mod"}, "roles": {str(ROLE_ID): "admin"}}


def _run(bot, ctx):
    return asyncio.run(bot._global_check(ctx))


def test_it_is_on_by_default():
    assert Settings.from_env({}).private_mode is True
    assert Settings.from_env({"PRIVATE_MODE": "false"}).private_mode is False


def test_strangers_are_ignored_without_a_word():
    with pytest.raises(Ignored):
        _run(_bot(), _ctx())


def test_discord_administrators_without_a_level_are_not_trusted():
    with pytest.raises(Ignored):
        _run(_bot(), _ctx(admin=True))


def test_direct_messages_are_ignored_for_everyone_but_developers():
    with pytest.raises(Ignored):
        _run(_bot(), _ctx(guild=False))
    assert _run(_bot(), _ctx(DEV, guild=False)) is True


def test_developers_and_the_server_owner_get_through():
    assert _run(_bot(), _ctx(DEV)) is True
    assert _run(_bot(), _ctx(OWNER)) is True


def test_people_with_a_permission_level_get_through():
    bot = _bot(permlevels=LEVELS)
    assert _run(bot, _ctx(LEVELLED)) is True       # level given to the user
    assert _run(bot, _ctx(STRANGER, roles=[ROLE_ID])) is True  # level given to a role they hold


def test_fake_permission_roles_count_too():
    bot = _bot(fakeperms={str(FAKE_ROLE): ["ban_members"]})
    assert _run(bot, _ctx(STRANGER, roles=[FAKE_ROLE])) is True
    with pytest.raises(Ignored):
        _run(bot, _ctx(STRANGER, roles=[FAKE_ROLE + 1]))


def test_turning_it_off_opens_the_bot_again():
    assert _run(_bot(private=False), _ctx()) is True
