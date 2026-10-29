"""Keep private details out of anything the bot posts where server administrators can read it.

Tracebacks, git results and similar developer output go through `scrub` before they are sent. It removes:
bot tokens and every secret value from `.env`, GitHub tokens, credentials inside URLs, GitHub addresses and the
repository owner/name, and the bot's own folder path.
"""
from __future__ import annotations

import functools
import os
import re

from core import gitinfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SECRET_KEYS = re.compile(r"TOKEN|SECRET|PASSWORD|API_?KEY|PRIVATE|DATABASE_URL|PROXY|WEBHOOK", re.I)
_DISCORD_TOKEN = re.compile(r"\b[MNO][A-Za-z\d_-]{23,27}\.[A-Za-z\d_-]{6,7}\.[A-Za-z\d_-]{27,}\b")
_GITHUB_TOKEN = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")
_WEBHOOK = re.compile(r"https?://(?:\w+\.)?discord(?:app)?\.com/api/(?:v\d+/)?webhooks/\d+/[\w-]+", re.I)
_URL_CREDENTIALS = re.compile(r"(?<=//)[^/@\s]+@")
_GITHUB_ADDRESS = re.compile(r"(?:(?:https?|ssh|git)://|git@)?(?:[\w.-]+@)?(?:www\.)?github\.com[:/]+[^\s'\"`<>)\]]*", re.I)
HIDDEN = "[hidden]"


@functools.lru_cache(maxsize=1)
def _repo_names() -> tuple[str, ...]:
    """Owner and repository name of this checkout's remote, so they can be removed wherever they turn up."""
    try:
        slug = gitinfo.repo_slug(gitinfo.remote_url(ROOT)) or ""
    except Exception:
        slug = ""
    return tuple(part for part in slug.split("/") if len(part) >= 4)


def _secret_values() -> list[str]:
    values = {v for k, v in os.environ.items() if _SECRET_KEYS.search(k) and len(v.strip()) >= 8}
    try:
        from core.config import settings
        for name, v in vars(settings).items():
            if isinstance(v, str) and len(v) >= 8 and _SECRET_KEYS.search(name):
                values.add(v)
    except Exception:
        pass
    return sorted(values, key=len, reverse=True)


def scrub(text: object) -> str:
    """`text` with secrets, GitHub details and the bot's folder path taken out."""
    out = str(text)
    for value in _secret_values():
        out = out.replace(value, HIDDEN)
    out = _WEBHOOK.sub(HIDDEN, out)
    out = _DISCORD_TOKEN.sub(HIDDEN, out)
    out = _GITHUB_TOKEN.sub(HIDDEN, out)
    for path, shown in ((ROOT, "."), (os.path.expanduser("~"), "~")):
        if len(path) > 1:
            out = out.replace(path, shown)
    out = _GITHUB_ADDRESS.sub("[repository]", out)
    out = _URL_CREDENTIALS.sub("", out)
    for name in _repo_names():
        out = re.sub(re.escape(name), "[repository]", out, flags=re.I)
    return out
