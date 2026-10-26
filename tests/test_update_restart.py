""",gitpull must restart (fresh process) when core code changes, and only hot-reload when just cogs changed."""
import asyncio
import os
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import cogs.owner as owner_mod
from cogs.owner import Owner, core_changed_on_disk, plan_update

LOADED = {"cogs.moderation", "cogs.owner", "cogs.antinuke"}


def test_only_cogs_changed_reloads_just_those():
    plan = plan_update(["cogs/moderation.py", "README.md", "tests/test_x.py"], LOADED)
    assert not plan.restart
    assert plan.reload == ["cogs.moderation"]


def test_core_or_main_changes_need_a_restart():
    for changed in (["core/utils.py"], ["cogs/moderation.py", "core/bot.py"], ["main.py"]):
        plan = plan_update(changed, LOADED)
        assert plan.restart, changed
        assert plan.reload == []


def test_new_cog_needs_a_restart_and_unknown_diff_is_treated_as_restart():
    assert plan_update(["cogs/brandnew.py"], LOADED).restart
    assert plan_update(None, LOADED).restart


def test_requirements_change_is_flagged():
    assert plan_update(["requirements.txt"], LOADED).deps_changed
    assert not plan_update(["cogs/moderation.py"], LOADED).deps_changed


def test_core_changed_on_disk_compares_against_startup_snapshot(monkeypatch):
    bot = SimpleNamespace()
    assert core_changed_on_disk(bot) is False  # no snapshot yet
    bot._code_mtimes = owner_mod._code_mtimes()
    assert core_changed_on_disk(bot) is False
    bot._code_mtimes = {**bot._code_mtimes, os.path.join("core", "utils.py"): 0.0}
    assert core_changed_on_disk(bot) is True


def _git_stub(old, new, changed):
    calls = []

    async def fake(self, *args):
        calls.append(args)
        if args[0] == "rev-parse":
            out = old if sum(1 for c in calls if c[0] == "rev-parse") == 1 else new
            return subprocess.CompletedProcess(args, 0, out + "\n", "")
        if args[0] == "diff":
            return subprocess.CompletedProcess(args, 0, "\n".join(changed) + "\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")
    return fake, calls


def _run_gitpull(monkeypatch, changed):
    fake, calls = _git_stub("a" * 40, "b" * 40, changed)
    monkeypatch.setattr(Owner, "_git", fake)
    bot = MagicMock()
    bot.extensions = {name: None for name in LOADED}
    bot.reload_extension = AsyncMock()
    cog = Owner(bot)
    cog._restart = AsyncMock()
    msg = MagicMock()
    msg.edit = AsyncMock()
    ctx = MagicMock()
    ctx.send = AsyncMock(return_value=msg)
    asyncio.run(Owner.gitpull.callback(cog, ctx))
    return cog, bot, msg


def test_gitpull_with_core_change_restarts_instead_of_reloading(monkeypatch):
    cog, bot, msg = _run_gitpull(monkeypatch, ["core/utils.py", "cogs/moderation.py"])
    cog._restart.assert_awaited_once()
    bot.reload_extension.assert_not_awaited()
    assert "Restarting" in msg.edit.call_args.kwargs["embed"].description


def test_gitpull_with_cog_change_reloads_only_that_cog(monkeypatch):
    cog, bot, msg = _run_gitpull(monkeypatch, ["cogs/moderation.py"])
    cog._restart.assert_not_awaited()
    bot.reload_extension.assert_awaited_once_with("cogs.moderation")
