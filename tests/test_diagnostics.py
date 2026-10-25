"""Unit tests for startup console diagnostics, brand banner, and logging formatter."""
import asyncio
import logging
import os
import sys

import discord
import pytest

from core.bot import Veil
from core.config import Settings
from core.diagnostics import (
    ConsoleFormatter,
    format_box,
    get_ready_diagnostics,
    get_system_diagnostics,
    render_startup_banner,
)


def test_startup_banner():
    banner_color = render_startup_banner(color=True)
    banner_plain = render_startup_banner(color=False)

    assert "VEIL UTILITIES" in banner_color
    assert "v1.1.0" in banner_color
    assert "\x1b[" in banner_color

    assert "VEIL UTILITIES" in banner_plain
    assert "v1.1.0" in banner_plain
    assert "\x1b[" not in banner_plain


def test_format_box():
    items = [
        ("Runtime:", "Python 3.13"),
        ("Library:", "discord.py"),
        ("Long Value:", "A" * 120),  # test truncation
    ]
    box_color = format_box("Test Diagnostics", items, width=60, color=True)
    box_plain = format_box("Test Diagnostics", items, width=60, color=False)

    assert "╭──" in box_color
    assert "╰" in box_color
    assert "Test Diagnostics" in box_color
    assert "\x1b[" in box_color

    assert "╭──" in box_plain
    assert "╰" in box_plain
    assert "Test Diagnostics" in box_plain
    assert "\x1b[" not in box_plain
    assert "…" in box_plain  # truncated long value


def test_system_diagnostics_sqlite():
    s = Settings.from_env({
        "DISCORD_TOKEN": "abc",
        "OWNER_IDS": "12345",
        "DB_PATH": "my_test.db",
        "PREFIX": "!",
    })
    diag = get_system_diagnostics(s)
    mapping = dict(diag)

    assert "Python" in mapping["Bot Runtime:"]
    assert "discord.py" in mapping["Discord API:"]
    assert "! (Default)" == mapping["Command Prefix:"]
    assert "1 authorized developer(s)" == mapping["Bot Developers:"]
    assert "SQLite (my_test.db) [WAL Mode]" == mapping["Database Engine:"]
    assert "Port 8080 (Active)" in mapping["Web Dashboard:"]


def test_system_diagnostics_postgres():
    s = Settings.from_env({
        "DISCORD_TOKEN": "abc",
        "OWNER_IDS": "12345",
        "DATABASE_URL": "postgresql://user:secret@db.example.com:5432/veil_production",
    })
    diag = get_system_diagnostics(s)
    mapping = dict(diag)

    assert "PostgreSQL (db.example.com:5432/veil_production)" == mapping["Database Engine:"]
    assert "secret" not in mapping["Database Engine:"]  # password masked


def test_ready_diagnostics():
    async def _run():
        bot = Veil()
        diag = get_ready_diagnostics(bot)
        mapping = dict(diag)

        assert "Veil Utilities" in mapping["Bot Account:"]
        assert "servers" in mapping["Guilds Served:"]
        assert "commands across" in mapping["Loaded Commands:"]
        assert "Role Blacklist & Antinuke Active" == mapping["Security Status:"]
        await bot.db.close()

    asyncio.run(_run())


def test_console_formatter():
    formatter_color = ConsoleFormatter(color=True)
    formatter_plain = ConsoleFormatter(color=False)

    record = logging.LogRecord(
        name="veil.dashboard",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="Web Dashboard listening on port 8080",
        args=(),
        exc_info=None,
    )

    out_color = formatter_color.format(record)
    out_plain = formatter_plain.format(record)

    assert "INFO" in out_color
    assert "veil.dashboard" in out_color
    assert "Web Dashboard listening on port 8080" in out_color
    assert "\x1b[" in out_color

    assert "INFO" in out_plain
    assert "veil.dashboard" in out_plain
    assert "Web Dashboard listening on port 8080" in out_plain
    assert "\x1b[" not in out_plain


def test_console_formatter_with_exception():
    formatter = ConsoleFormatter(color=False)
    try:
        raise ValueError("test exception for logger")
    except ValueError:
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="veil.core",
        level=logging.ERROR,
        pathname=__file__,
        lineno=20,
        msg="An error occurred",
        args=(),
        exc_info=exc_info,
    )

    out = formatter.format(record)
    assert "ERROR" in out
    assert "veil.core" in out
    assert "An error occurred" in out
    assert "ValueError: test exception for logger" in out
