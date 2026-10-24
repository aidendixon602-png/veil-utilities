"""Regression tests for security / reliability fixes. All offline — no Discord connection needed."""
import ast
import asyncio
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from cogs.automod import Automod, link_allowed
from cogs.utility import Utility
from core.utils import _Vars, build_message, replace_vars


# ------------------------------------------------------------------ embed scripting
def test_variables_cannot_inject_embed_blocks():
    evil = "x}$v{button: Free Nitro && https://phish.example}$v{image: https://gore.example/a.png"
    msg = build_message("{embed}$v{title: Welcome {user.name}}", {"user.name": evil})
    assert "view" not in msg                       # no smuggled link button
    assert msg["embed"].image.url is None           # no smuggled image
    assert msg["embed"].title.startswith("Welcome x}$v{button")  # the name is shown literally


def test_variables_cannot_inject_field_arguments():
    msg = build_message("{embed}$v{author: {user.name} && {user.avatar}}",
                        {"user.name": "bob && https://evil.example/icon.png", "user.avatar": "https://cdn/ok.png"})
    assert msg["embed"].author.name == "bob && https://evil.example/icon.png"
    assert msg["embed"].author.icon_url == "https://cdn/ok.png"


def test_plain_text_scripts_still_substitute():
    assert build_message("hi {user.name}", {"user.name": "ana"}) == {"content": "hi ana"}


def test_lazy_variables_only_computed_when_used():
    calls = []
    v = _Vars()
    v["a"] = "1"
    v.lazy["slow"] = lambda: calls.append(1) or "42"
    assert replace_vars("{a}", v) == "1" and not calls
    assert replace_vars("{slow} {slow}", v) == "42 42" and calls == [1]


# ------------------------------------------------------------------ calculator
def _calc(expr: str):
    cog = Utility.__new__(Utility)
    return cog._eval(ast.parse(expr.replace("^", "**"), mode="eval").body)


@pytest.mark.parametrize("expr, expected", [("2+2*3", 8), ("2^10", 1024), ("-(3**2)", -9), ("7/2", 3.5)])
def test_calculator_normal(expr, expected):
    assert _calc(expr) == expected


@pytest.mark.parametrize("expr", [
    "((((10**100)**100)**100)**100)**100",   # used to try to allocate gigabytes
    "(10**100)**100",
    "9**99**99",
    "(2**2000)*(2**2000)",
])
def test_calculator_rejects_huge_results_fast(expr):
    start = time.monotonic()
    with pytest.raises(ValueError):
        _calc(expr)
    assert time.monotonic() - start < 0.5


# ------------------------------------------------------------------ automod
@pytest.mark.parametrize("url, ok", [
    ("https://youtube.com/watch?v=1", True),
    ("https://www.youtube.com/watch?v=1", True),
    ("https://m.youtube.com/x", True),
    ("www.youtube.com/x", True),
    ("https://youtube.com.evil.ru/x", False),
    ("https://evil.ru/?ref=youtube.com", False),
    ("https://notyoutube.com/", False),
])
def test_link_allowlist_matches_hostnames(url, ok):
    assert link_allowed(url, ["youtube.com"]) is ok


def test_link_allowlist_accepts_legacy_entries():
    assert link_allowed("https://youtube.com/a", ["https://www.YouTube.com/"])


def _msg(content: str):
    return SimpleNamespace(content=content, raw_mentions=[], guild=SimpleNamespace(id=1), author=SimpleNamespace(id=2))


def test_catastrophic_regex_does_not_hang():
    cog = Automod.__new__(Automod)
    cog.spam = {}
    start = time.monotonic()
    assert cog.check({"regex": [r"(x+x+)+y"]}, _msg("x" * 5000)) is None
    assert time.monotonic() - start < 1


def test_regex_filter_still_matches():
    cog = Automod.__new__(Automod)
    cog.spam = {}
    assert cog.check({"regex": [r"free\s+nitro"]}, _msg("FREE   nitro here")) is not None


def test_edits_do_not_count_toward_spam():
    from collections import defaultdict, deque
    cog = Automod.__new__(Automod)
    cog.spam = defaultdict(deque)
    cfg = {"spam": [2, 10]}
    for _ in range(5):
        assert cog.check(cfg, _msg("hi"), count_spam=False) is None


# ------------------------------------------------------------------ blacklist
def test_blacklisted_user_cannot_run_commands_by_editing():
    from core.bot import Veil

    async def run():
        bot = Veil()
        bot.blacklist = {42}
        ran = []

        async def fake_process(message):
            ran.append(message)
        bot.process_commands = fake_process
        now = datetime.now(timezone.utc)
        author = SimpleNamespace(id=42, bot=False)
        before = SimpleNamespace(content=",ban", author=author, guild=None, created_at=now - timedelta(seconds=5))
        after = SimpleNamespace(content=",ban @x", author=author, guild=None, created_at=now - timedelta(seconds=5))
        await bot.on_message_edit(before, after)
        assert ran == []
        author.id = 7  # not blacklisted → edit re-runs the command as before
        await bot.on_message_edit(before, after)
        assert ran == [after]

    asyncio.run(run())
