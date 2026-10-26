"""Which code is the bot running, and where does it update from?

Shared by main.py (the sync on boot), `,gitpull` and `,version`. It only needs the standard library, so
main.py can use it before anything else is imported, and every failure comes back as plain words
(auth, network, "not a git checkout") instead of disappearing.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field

EXPECTED_REPO = "aidendixon602-png/veil-utilities"
REMOTE = "origin"
BRANCH = "main"


def mask_url(url: str) -> str:
    """Hide any credentials embedded in a remote URL (https://user:token@host/...)."""
    return re.sub(r"(?<=//)[^/@\s]+@", "", url.strip())


def repo_slug(url: str) -> str | None:
    """'owner/repo' from an https, ssh or scp-style GitHub remote; None if it doesn't look like one."""
    m = re.search(r"github\.com[:/]+([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", mask_url(url))
    return f"{m.group(1)}/{m.group(2)}" if m else None


def explain_failure(stderr: str) -> str:
    """Turn git's error output into what it means and what to fix."""
    text = (stderr or "").strip() or "git gave no error output"
    low = text.lower()
    if "not a git repository" in low:
        hint = "This folder isn't a git checkout, so it can't pull. Upload the files by hand or set the Git repo in the panel."
    elif any(k in low for k in ("permission denied (publickey)", "could not read username", "authentication failed",
                                "terminal prompts disabled", "repository not found", "invalid username or password")):
        hint = "The host can't log in to GitHub. Check the Git token or deploy key in the panel's Startup/Git settings."
    elif any(k in low for k in ("could not resolve host", "timed out", "connection refused", "network is unreachable",
                                "unable to access", "connection reset")):
        hint = "The host couldn't reach GitHub. Try again in a minute; if it keeps failing, the host's network is blocking it."
    elif "couldn't find remote ref" in low:
        hint = f"The remote has no `{BRANCH}` branch."
    elif "no such remote" in low or "does not appear to be a git repository" in low:
        hint = f"This checkout has no `{REMOTE}` remote pointing at GitHub."
    else:
        hint = ""
    first = text.splitlines()[0][:300] if text else ""
    return f"{first} — {hint}" if hint else first


def _git(root: str, *args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}  # fail instead of waiting for a password nobody can type
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=timeout, env=env)


def _out(root: str, *args: str, timeout: int = 30) -> str:
    try:
        r = _git(root, *args, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def head_sha(root: str) -> str:
    return _out(root, "rev-parse", "HEAD")


def subject_of(root: str, sha: str) -> str:
    return _out(root, "log", "-1", "--format=%s", sha) if sha else ""


def changed_files(root: str, old: str, new: str) -> list[str] | None:
    """Files that differ between two commits; None if git can't say (e.g. the old commit is gone)."""
    if not old or not new:
        return None
    try:
        r = _git(root, "diff", "--name-only", old, new, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.split() if r.returncode == 0 else None


def remote_url(root: str) -> str:
    return mask_url(_out(root, "remote", "get-url", REMOTE))


@dataclass
class Snapshot:
    sha: str = ""
    subject: str = ""
    branch: str = ""
    remote: str = ""
    edited: int = 0  # tracked files changed by hand

    @property
    def short(self) -> str:
        return self.sha[:7] or "unknown"

    @property
    def slug(self) -> str | None:
        return repo_slug(self.remote)


def snapshot(root: str) -> Snapshot:
    sha = head_sha(root)
    return Snapshot(
        sha=sha,
        subject=subject_of(root, sha),
        branch=_out(root, "rev-parse", "--abbrev-ref", "HEAD"),
        remote=remote_url(root),
        edited=len(_out(root, "status", "--porcelain", "--untracked-files=no").splitlines()),
    )


def remote_head(root: str, timeout: int = 20) -> tuple[str, str]:
    """(sha of the remote's main, error). Exactly one of them is empty."""
    try:
        r = _git(root, "ls-remote", REMOTE, f"refs/heads/{BRANCH}", timeout=timeout)
    except subprocess.TimeoutExpired:
        return "", explain_failure("timed out")
    except (OSError, subprocess.SubprocessError) as e:
        return "", str(e)
    if r.returncode != 0:
        return "", explain_failure(r.stderr)
    sha = r.stdout.split()[0] if r.stdout.split() else ""
    return (sha, "") if sha else ("", explain_failure("couldn't find remote ref"))


@dataclass
class PullResult:
    ok: bool
    error: str = ""
    old: str = ""
    new: str = ""
    subject: str = ""
    remote: str = ""
    changed: list[str] | None = field(default=None)  # None = couldn't tell which files changed

    @property
    def updated(self) -> bool:
        return self.ok and self.old != self.new


def pull(root: str, timeout: int = 60) -> PullResult:
    """Fetch GitHub's main and make this checkout match it exactly.

    Resets to the commit that was just fetched (FETCH_HEAD) rather than to `origin/main`: a checkout that was
    cloned for another branch never refreshes `origin/main`, and would otherwise "update" to an old commit.
    """
    try:
        old = _git(root, "rev-parse", "HEAD", timeout=10)
        if old.returncode != 0:
            return PullResult(False, explain_failure(old.stderr))
        old_sha = old.stdout.strip()
        remote = remote_url(root)
        fetch = _git(root, "fetch", REMOTE, BRANCH, timeout=timeout)
        if fetch.returncode != 0:
            return PullResult(False, explain_failure(fetch.stderr), old=old_sha, remote=remote)
        target = _git(root, "rev-parse", "FETCH_HEAD", timeout=10)
        if target.returncode != 0:
            return PullResult(False, explain_failure(target.stderr), old=old_sha, remote=remote)
        reset = _git(root, "reset", "--hard", target.stdout.strip(), timeout=timeout)
        if reset.returncode != 0:
            return PullResult(False, explain_failure(reset.stderr), old=old_sha, remote=remote)
        new_sha = _out(root, "rev-parse", "HEAD") or target.stdout.strip()
        changed = changed_files(root, old_sha, new_sha) if old_sha != new_sha else None
        return PullResult(True, old=old_sha, new=new_sha, remote=remote, changed=changed,
                          subject=subject_of(root, new_sha))
    except subprocess.TimeoutExpired:
        return PullResult(False, explain_failure("timed out"))
    except (OSError, subprocess.SubprocessError) as e:
        return PullResult(False, f"couldn't run git: {e}")


def verdict(loaded: str, disk: str, remote: str, remote_error: str, slug: str | None) -> tuple[bool, str]:
    """(is everything current?, what to do next) from the commit the bot loaded, the one on disk and GitHub's."""
    if slug and slug.lower() != EXPECTED_REPO.lower():
        return False, f"This folder pulls from `{slug}`, not `{EXPECTED_REPO}`. Fix the Git repo address in the host panel."
    if remote_error:
        return False, f"Couldn't reach GitHub: {remote_error}"
    if remote and disk and remote != disk:
        return False, "GitHub has newer code than this folder. Run `,gitpull`."
    if loaded and disk and loaded != disk:
        return False, "The files on disk are newer than the running bot. Run `,restart`."
    return True, "Running the latest code from GitHub."
