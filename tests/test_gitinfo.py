"""Git helpers: the host must update from GitHub's main (even from a checkout cloned for another branch) and
say in plain words why when it can't."""
import os
import subprocess

import pytest

from core import gitinfo

ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
       "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, env=ENV, check=True, capture_output=True, text=True).stdout.strip()


def commit(cwd, name, text, msg):
    (cwd / name).write_text(text)
    git(cwd, "add", name)
    git(cwd, "commit", "-m", msg)
    return git(cwd, "rev-parse", "HEAD")


@pytest.fixture
def remote(tmp_path):
    """A 'GitHub' (bare repo) holding main and a stale side branch, plus the work tree used to push to it."""
    bare = tmp_path / "github.git"
    work = tmp_path / "work"
    work.mkdir()
    git(work, "init", "-b", "main")
    commit(work, "a.txt", "1", "first")
    git(work, "branch", "old-branch")
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare)], env=ENV, check=True, capture_output=True)
    git(work, "remote", "add", "origin", str(bare))
    git(work, "push", "origin", "main", "old-branch")
    return bare, work


def clone(bare, dest, *extra):
    subprocess.run(["git", "clone", *extra, str(bare), str(dest)], env=ENV, check=True, capture_output=True)
    return dest


@pytest.fixture(autouse=True)
def _isolated_git_env(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)


def test_pull_updates_to_the_new_main(remote, tmp_path):
    bare, work = remote
    host = clone(bare, tmp_path / "host")
    new = commit(work, "b.txt", "2", "second change")
    git(work, "push", "origin", "main")

    result = gitinfo.pull(str(host))

    assert result.ok and result.updated
    assert result.new == new
    assert result.subject == "second change"
    assert result.changed == ["b.txt"]
    assert gitinfo.head_sha(str(host)) == new


def test_pull_from_a_checkout_cloned_for_another_branch(remote, tmp_path):
    """A single-branch clone never refreshes origin/main, so resetting to it would leave the old code in place."""
    bare, work = remote
    host = clone(bare, tmp_path / "host", "--single-branch", "--branch", "old-branch")
    new = commit(work, "b.txt", "2", "newer main")
    git(work, "push", "origin", "main")

    result = gitinfo.pull(str(host))

    assert result.ok and result.new == new
    assert (host / "b.txt").exists()


def test_pull_reports_up_to_date(remote, tmp_path):
    bare, _ = remote
    host = clone(bare, tmp_path / "host")
    result = gitinfo.pull(str(host))
    assert result.ok and not result.updated and result.old == result.new


def test_pull_keeps_hand_edits_out_of_the_way(remote, tmp_path):
    bare, work = remote
    host = clone(bare, tmp_path / "host")
    (host / "a.txt").write_text("edited on the host")
    new = commit(work, "a.txt", "from github", "change a")
    git(work, "push", "origin", "main")
    assert gitinfo.snapshot(str(host)).edited == 1

    result = gitinfo.pull(str(host))

    assert result.ok and result.new == new
    assert (host / "a.txt").read_text() == "from github"


def test_pull_explains_a_folder_that_is_not_a_checkout(tmp_path):
    result = gitinfo.pull(str(tmp_path))
    assert not result.ok
    assert "isn't a git checkout" in result.error


def test_pull_explains_an_unreachable_remote(remote, tmp_path):
    bare, _ = remote
    host = clone(bare, tmp_path / "host")
    git(host, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    result = gitinfo.pull(str(host))
    assert not result.ok and result.error


def test_remote_head_matches_what_was_pushed(remote, tmp_path):
    bare, work = remote
    host = clone(bare, tmp_path / "host")
    sha, error = gitinfo.remote_head(str(host))
    assert sha == git(work, "rev-parse", "main") and error == ""


def test_snapshot_describes_the_checkout(remote, tmp_path):
    bare, _ = remote
    host = clone(bare, tmp_path / "host")
    snap = gitinfo.snapshot(str(host))
    assert snap.subject == "first" and snap.branch == "main" and snap.edited == 0
    assert snap.short == snap.sha[:7]


def test_snapshot_of_a_plain_folder_is_empty(tmp_path):
    snap = gitinfo.snapshot(str(tmp_path))
    assert snap.sha == "" and snap.short == "unknown" and snap.slug is None


@pytest.mark.parametrize("url,slug", [
    ("https://github.com/aidendixon602-png/veil-utilities", "aidendixon602-png/veil-utilities"),
    ("https://github.com/aidendixon602-png/veil-utilities.git", "aidendixon602-png/veil-utilities"),
    ("git@github.com:aidendixon602-png/veil-utilities.git", "aidendixon602-png/veil-utilities"),
    ("ssh://git@github.com/aidendixon602-png/veil-utilities.git", "aidendixon602-png/veil-utilities"),
    ("https://user:ghp_secret@github.com/a/b.git", "a/b"),
    ("https://gitlab.com/a/b.git", None),
])
def test_repo_slug(url, slug):
    assert gitinfo.repo_slug(url) == slug


def test_mask_url_hides_tokens():
    assert "ghp_secret" not in gitinfo.mask_url("https://user:ghp_secret@github.com/a/b.git")
    assert gitinfo.mask_url("https://x-access-token:abc@github.com/a/b") == "https://github.com/a/b"


@pytest.mark.parametrize("stderr,words", [
    ("fatal: not a git repository (or any of the parent directories): .git", "isn't a git checkout"),
    ("git@github.com: Permission denied (publickey).", "can't log in"),
    ("fatal: could not read Username for 'https://github.com': terminal prompts disabled", "can't log in"),
    ("fatal: unable to access 'https://github.com/a/b/': Could not resolve host: github.com", "couldn't reach GitHub"),
    ("fatal: couldn't find remote ref main", "no `main` branch"),
])
def test_explain_failure(stderr, words):
    assert words in gitinfo.explain_failure(stderr)


def test_verdict_prefers_the_most_useful_advice():
    repo = gitinfo.EXPECTED_REPO
    sha_a, sha_b = "a" * 40, "b" * 40
    ok, text = gitinfo.verdict(sha_a, sha_a, sha_a, "", repo)
    assert ok and "latest" in text
    ok, text = gitinfo.verdict(sha_a, sha_a, sha_b, "", repo)
    assert not ok and ",gitpull" in text
    ok, text = gitinfo.verdict(sha_a, sha_b, sha_b, "", repo)
    assert not ok and ",restart" in text
    ok, text = gitinfo.verdict(sha_a, sha_a, "", "Permission denied", repo)
    assert not ok and "Couldn't reach GitHub" in text
    ok, text = gitinfo.verdict(sha_a, sha_a, sha_a, "", "someone/else")
    assert not ok and "someone/else" in text
