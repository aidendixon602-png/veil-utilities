""",gitpull must restart (fresh process) when core code changes, and only hot-reload when just cogs changed."""
import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import cogs.owner as owner_mod
from core import gitinfo
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


def _run_gitpull(monkeypatch, changed, result=None, loaded=None):
    """Run ,gitpull with gitinfo.pull stubbed. `loaded` is the commit the running process started on."""
    result = result or gitinfo.PullResult(True, old="a" * 40, new="b" * 40, subject="Some change",
                                          remote="https://github.com/aidendixon602-png/veil-utilities",
                                          changed=changed)
    monkeypatch.setattr(gitinfo, "pull", lambda root, timeout=60: result)
    bot = MagicMock()
    bot.extensions = {name: None for name in LOADED}
    bot.reload_extension = AsyncMock()
    if loaded is not None:
        bot.loaded_commit = loaded
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


def test_gitpull_reports_a_failed_fetch_in_plain_words(monkeypatch):
    failed = gitinfo.PullResult(False, "Permission denied (publickey) — The host can't log in to GitHub.")
    cog, bot, msg = _run_gitpull(monkeypatch, None, result=failed)
    assert "Couldn't pull from GitHub" in msg.edit.call_args.kwargs["content"]
    assert "can't log in" in msg.edit.call_args.kwargs["content"]
    cog._restart.assert_not_awaited()


def test_gitpull_up_to_date_names_the_repo_it_pulled_from(monkeypatch):
    same = gitinfo.PullResult(True, old="c" * 40, new="c" * 40, subject="Latest",
                              remote="git@github.com:aidendixon602-png/veil-utilities.git")
    _, _, msg = _run_gitpull(monkeypatch, None, result=same, loaded="c" * 40)
    text = msg.edit.call_args.kwargs["content"]
    assert "Already up to date" in text and "aidendixon602-png/veil-utilities" in text
    assert "not `" not in text  # right repo: no warning


def test_gitpull_warns_when_the_folder_pulls_from_another_repo(monkeypatch):
    other = gitinfo.PullResult(True, old="c" * 40, new="c" * 40, subject="x",
                               remote="https://github.com/someone/else.git")
    _, _, msg = _run_gitpull(monkeypatch, None, result=other, loaded="c" * 40)
    assert "someone/else" in msg.edit.call_args.kwargs["content"]
    assert "not `aidendixon602-png/veil-utilities`" in msg.edit.call_args.kwargs["content"]


def test_gitpull_applies_files_that_are_newer_than_the_running_bot(monkeypatch):
    """The files were already pulled (disk == GitHub) but the process started earlier: still reload/restart."""
    same = gitinfo.PullResult(True, old="b" * 40, new="b" * 40, subject="x",
                              remote="https://github.com/aidendixon602-png/veil-utilities")
    monkeypatch.setattr(gitinfo, "changed_files", lambda root, old, new: ["core/help.py"])
    cog, bot, msg = _run_gitpull(monkeypatch, None, result=same, loaded="a" * 40)
    cog._restart.assert_awaited_once()
    assert "Restarting" in msg.edit.call_args.kwargs["embed"].description


def test_gitpull_remembers_what_it_loaded(monkeypatch):
    cog, bot, msg = _run_gitpull(monkeypatch, ["cogs/moderation.py"], loaded="a" * 40)
    assert bot.loaded_commit == "b" * 40
