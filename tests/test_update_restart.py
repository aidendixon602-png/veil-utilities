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
    assert "Couldn't pull the update" in msg.edit.call_args.kwargs["content"]
    assert "can't log in" in msg.edit.call_args.kwargs["content"]
    cog._restart.assert_not_awaited()


def test_gitpull_failures_never_show_the_repository_address(monkeypatch):
    failed = gitinfo.PullResult(False, "fatal: unable to access 'https://user:ghp_" + "a" * 30 +
                                "@github.com/someone/else.git/': Could not resolve host")
    _, _, msg = _run_gitpull(monkeypatch, None, result=failed)
    text = msg.edit.call_args.kwargs["content"]
    assert "someone" not in text and "github.com/" not in text and "ghp_" not in text


def _posted(msg) -> str:
    kwargs = msg.edit.call_args.kwargs
    embed = kwargs.get("embed")
    return (kwargs.get("content") or "") + (embed.title or "" if embed else "") + (embed.description or "" if embed else "")


def test_gitpull_up_to_date_shows_no_repository_details(monkeypatch):
    same = gitinfo.PullResult(True, old="c" * 40, new="c" * 40, subject="Merge pull request #24 from someone/branch",
                              remote="git@github.com:aidendixon602-png/veil-utilities.git")
    _, _, msg = _run_gitpull(monkeypatch, None, result=same, loaded="c" * 40)
    text = _posted(msg)
    assert "Already up to date" in text and "ccccccc" in text
    for private in ("aidendixon602", "veil-utilities", "github", "someone", "Merge pull request"):
        assert private.lower() not in text.lower()


def test_gitpull_update_card_shows_no_repository_details(monkeypatch):
    result = gitinfo.PullResult(True, old="a" * 40, new="b" * 40, subject="Merge pull request #9 from someone/x",
                                remote="https://github.com/someone/else.git", changed=["cogs/moderation.py"])
    _, _, msg = _run_gitpull(monkeypatch, None, result=result, loaded="a" * 40)
    text = _posted(msg)
    assert "aaaaaaa" in text and "bbbbbbb" in text
    for private in ("someone", "else.git", "github", "Merge pull request"):
        assert private.lower() not in text.lower()


def test_gitpull_warns_without_naming_the_other_repository(monkeypatch, caplog):
    other = gitinfo.PullResult(True, old="c" * 40, new="c" * 40, subject="x",
                               remote="https://github.com/someone/else.git")
    with caplog.at_level("WARNING", logger="veil.owner"):
        _, _, msg = _run_gitpull(monkeypatch, None, result=other, loaded="c" * 40)
    assert "different repository" in msg.edit.call_args.kwargs["content"]
    assert "someone" not in msg.edit.call_args.kwargs["content"]
    assert "someone/else" in caplog.text  # the host console still says which


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
