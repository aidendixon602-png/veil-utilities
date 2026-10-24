"""Developer lockdown: hidden commands must be unreachable for non-developers, with a
silent CommandNotFound so even the *existence* of a command can't be probed."""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from types import SimpleNamespace

from discord.ext import commands

os.environ.setdefault("DISCORD_TOKEN", "x")
logging.disable(logging.CRITICAL)

OWNER_ID, RANDOM_USER = 1, 99

_RESULTS: dict | None = None


def _fake_ctx(user_id: int, *, hidden: bool, parent_hidden: bool = False):
    cmd = SimpleNamespace(hidden=hidden, parents=[SimpleNamespace(hidden=parent_hidden)] if parent_hidden else [],
                          qualified_name="system", root_parent=None, name="system")
    return SimpleNamespace(guild=None, cog=None, command=cmd, author=SimpleNamespace(id=user_id))


def _results() -> dict:
    global _RESULTS
    if _RESULTS is not None:
        return _RESULTS

    async def go() -> dict:
        saved = {k: os.environ.get(k) for k in ("DB_PATH", "OWNER_IDS", "DISCORD_TOKEN")}
        os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "lockdown.db")
        os.environ["OWNER_IDS"] = str(OWNER_ID)
        os.environ.setdefault("DISCORD_TOKEN", "x")
        import core.bot
        from core.config import Settings
        saved_settings = core.bot.settings
        # core.bot binds `settings` at import time; another test may have frozen a different env
        core.bot.settings = Settings.from_env()
        try:
            from core.bot import Veil
            bot = Veil()
            await bot._async_setup_hook()
            await bot.setup_hook()
            try:
                out: dict = {"cases": {}}
                for name, ctx in {
                    "hidden_nonowner": _fake_ctx(RANDOM_USER, hidden=True),
                    "sub_of_hidden_group": _fake_ctx(RANDOM_USER, hidden=False, parent_hidden=True),
                    "hidden_owner": _fake_ctx(OWNER_ID, hidden=True),
                    "visible_nonowner": _fake_ctx(RANDOM_USER, hidden=False),
                }.items():
                    try:
                        out["cases"][name] = "passed" if await bot._global_check(ctx) else "blocked"
                    except commands.CommandNotFound:
                        out["cases"][name] = "notfound"
                    except Exception as e:  # noqa: BLE001
                        out["cases"][name] = f"raised {type(e).__name__}"
                # registry: every command in the developer cogs must be hidden
                out["unhidden_dev"] = sorted(
                    c.qualified_name
                    for cname in ("Owner", "DevTools", "Authorization")
                    for c in bot.cogs[cname].get_commands()
                    if not c.hidden
                )
                return out
            finally:
                await bot.close()
        finally:
            core.bot.settings = saved_settings
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    _RESULTS = asyncio.run(go())
    return _RESULTS


def test_hidden_command_not_found_for_strangers():
    assert _results()["cases"]["hidden_nonowner"] == "notfound"


def test_subcommand_of_hidden_group_not_found_either():
    assert _results()["cases"]["sub_of_hidden_group"] == "notfound"


def test_owner_still_reaches_hidden_commands():
    assert _results()["cases"]["hidden_owner"] == "passed"


def test_normal_commands_still_work_for_everyone():
    assert _results()["cases"]["visible_nonowner"] == "passed"


def test_every_developer_cog_command_is_hidden():
    assert _results()["unhidden_dev"] == []


# ------------------------------------------------------------------ help-menu paths
# The buttons aren't the only door: `,help <name>` text lookups must hide dev commands too.
def _help_ctx(is_dev: bool):
    from unittest.mock import AsyncMock
    return SimpleNamespace(
        guild=SimpleNamespace(),  # any truthy guild -> help replies in the channel
        channel=SimpleNamespace(send=AsyncMock()),
        author=SimpleNamespace(id=RANDOM_USER), clean_prefix=",",
        bot=SimpleNamespace(user=None, is_owner=AsyncMock(return_value=is_dev)),
    )


def _fake_group(hidden: bool):
    return SimpleNamespace(hidden=hidden, parents=[], name="killswitch", qualified_name="killswitch",
                           help="dangerous developer tools", short_doc="dangerous developer tools",
                           signature="", aliases=[], checks=[], callback=SimpleNamespace(), clean_params={},
                           walk_commands=lambda: [])


def _browse(is_dev: bool) -> dict:
    async def go() -> dict:
        from core.help import Help
        h = Help()
        h.context = _help_ctx(is_dev)
        sent: list[str] = []

        async def spy(err):
            sent.append(err)

        h.send_error_message = spy
        cmd = SimpleNamespace(hidden=True, parents=[], name="system", qualified_name="system",
                              help="shows system info", short_doc="shows system info", signature="",
                              aliases=[], checks=[], callback=SimpleNamespace(), clean_params={})
        await h.send_command_help(cmd)
        await h.send_group_help(_fake_group(hidden=True))
        return {"errors": sent, "sent": h.context.channel.send.call_count}

    return asyncio.run(go())


def test_help_command_lookup_denied_for_strangers():
    out = _browse(is_dev=False)
    assert len(out["errors"]) == 2  # both the hidden command and the hidden group
    assert all("No command" in e for e in out["errors"])
    assert out["sent"] == 0


def test_help_command_lookup_works_for_developers():
    out = _browse(is_dev=True)
    assert out["errors"] == []
    assert out["sent"] == 2
