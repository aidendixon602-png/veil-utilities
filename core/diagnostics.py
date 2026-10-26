"""Console diagnostics, brand banner, and logging formatter for Veil Utilities."""
from __future__ import annotations

import logging
import os
import platform
import sys
import time
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from core.bot import Veil
    from core.config import Settings

VERSION = "1.1.0"


def memory_mb() -> str:
    try:
        import psutil  # optional
        return f"{psutil.Process().memory_info().rss / 1_048_576:.0f} MB"
    except ImportError:
        pass
    try:
        import resource
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return f"{rss / (1_048_576 if sys.platform == 'darwin' else 1024):.0f} MB (peak)"
    except (ImportError, AttributeError):
        return "—"


class ConsoleFormatter(logging.Formatter):
    """Clean panel-console output: `20:17:14 | INFO  | [veil.music] | message`.

    Colors use ANSI escapes (Pterodactyl-style consoles render them); disabled with LOG_COLOR=0.
    The rotating log file gets plain format instead."""

    LEVEL_COLORS = {
        "DEBUG": "\x1b[38;5;244m",
        "INFO": "\x1b[38;5;42m",
        "WARNING": "\x1b[38;5;214m",
        "ERROR": "\x1b[38;5;196m",
        "CRITICAL": "\x1b[1;38;5;196m",
    }
    RESET = "\x1b[0m"
    DIM = "\x1b[2m"

    def __init__(self, color: bool = True):
        super().__init__()
        self.color = color

    def format(self, record: logging.LogRecord) -> str:
        name = record.name.split(".")[0] if not record.name.startswith("veil") else record.name
        when = time.strftime("%H:%M:%S", time.localtime(record.created))
        level = {"WARNING": "WARN", "CRITICAL": "CRIT"}.get(record.levelname, record.levelname)
        msg = record.getMessage()
        if self.color:
            c = self.LEVEL_COLORS.get(record.levelname, "")
            badge = f"{c}{level:<5}{self.RESET}"
            mod = f"{self.DIM}[{name:<15}]{self.RESET}"
            out = f"{self.DIM}{when}{self.RESET} {badge} {mod} {msg}"
        else:
            out = f"{when} {level:<5} [{name:<15}] {msg}"
        if record.exc_info:
            if not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            if not out.endswith("\n"):
                out += "\n"
            out += record.exc_text
        if record.stack_info:
            if not out.endswith("\n"):
                out += "\n"
            out += self.formatStack(record.stack_info)
        return out


def render_startup_banner(color: bool = True) -> str:
    """Render the Veil brand ASCII banner with version and tagline."""
    crimson = "\x1b[38;5;198m" if color else ""
    pink = "\x1b[38;5;212m" if color else ""
    bold = "\x1b[1m" if color else ""
    dim = "\x1b[2m" if color else ""
    reset = "\x1b[0m" if color else ""

    lines = [
        f"{crimson}  ██▒   █▓▓█████  ██▓ ██▓{reset}",
        f"{crimson} ▓██░   █▒▓█   ▀ ▓██▒▓██▒{reset}",
        f"{pink}  ▓██  █▒░▒███   ▒██▒▒██░{reset}",
        f"{pink}   ▒██ █░░▒▓█  ▄ ░██░▒██░{reset}",
        f"{pink}    ▒▀█░  ░▒████▒░██░░██████▒{reset}   {bold}VEIL UTILITIES{reset} {dim}v{VERSION}{reset}",
        f"                               {dim}Professional Discord Administration & Security{reset}",
    ]
    return "\n".join(lines)


def format_box(title: str, items: list[tuple[str, str]], width: int = 70, color: bool = True) -> str:
    """Format diagnostic items inside a sleek unicode bordered card."""
    crimson = "\x1b[38;5;198m" if color else ""
    cyan = "\x1b[38;5;39m" if color else ""
    bold = "\x1b[1m" if color else ""
    dim = "\x1b[2m" if color else ""
    reset = "\x1b[0m" if color else ""

    top_bar_len = max(2, width - len(title) - 6)
    top_border = f"{dim}╭──{reset} {bold}{crimson}{title}{reset} {dim}{'─' * top_bar_len}╮{reset}"
    bot_border = f"{dim}╰{'─' * (width - 2)}╯{reset}"

    lines = [top_border]
    for label, val in items:
        content_visual = f"  • {label:<18} {val}"
        max_content = width - 4
        if len(content_visual) > max_content:
            max_val = max(10, max_content - 22)
            val = val[:max_val] + "…"
            content_visual = f"  • {label:<18} {val}"
        pad = max(0, width - 4 - len(content_visual))
        line = f"{dim}│{reset}  • {cyan}{label:<18}{reset} {val}{' ' * pad} {dim}│{reset}"
        lines.append(line)
    lines.append(bot_border)
    return "\n".join(lines)


def get_system_diagnostics(settings: Settings) -> list[tuple[str, str]]:
    """Gather environment and configuration diagnostics for startup."""
    import discord

    db_url = getattr(settings, "database_url", None)
    if db_url:
        try:
            parsed = urlparse(db_url)
            host = parsed.hostname or "localhost"
            port = f":{parsed.port}" if parsed.port else ""
            db_engine = f"PostgreSQL ({host}{port}{parsed.path})"
        except Exception:
            db_engine = "PostgreSQL (configured)"
    else:
        db_engine = f"SQLite ({settings.db_path}) [WAL Mode]"

    dash_port = os.environ.get("DASHBOARD_PORT", os.environ.get("PORT", "8080"))
    music_status = "Lavalink (Public Nodes)" if settings.music_enabled else "Disabled"
    ai_status = f"{settings.ai_model or 'Auto'} ({'Key Set' if settings.ai_api_key else 'Public Tier'})"

    diag = [
        ("Bot Runtime:", f"Python {platform.python_version()} ({platform.system()} {platform.machine()})"),
        ("Discord API:", f"discord.py v{discord.__version__}"),
        ("Command Prefix:", f"{settings.prefix} (Default)"),
        ("Bot Developers:", f"{len(settings.owner_ids)} authorized developer(s)" if settings.owner_ids
                            else "found automatically from your Discord application"),
        ("Database Engine:", db_engine),
        ("Web Dashboard:", f"Port {dash_port} (Active)"),
        ("Music Subsystem:", music_status),
        ("Antinuke Shield:", f"Armed & Active (Dev Alerts: {'Enabled' if settings.antinuke_dev_alerts else 'Disabled'})"),
        ("AI Subsystem:", ai_status),
    ]
    if getattr(settings, "proxy", None):
        diag.append(("Network Proxy:", "Configured (Active)"))
    return diag


def get_ready_diagnostics(bot: Veil) -> list[tuple[str, str]]:
    """Gather live runtime diagnostics after gateway on_ready."""
    commands_total = sum(1 for _ in bot.walk_commands())
    total_members = sum(g.member_count or 0 for g in bot.guilds)
    latency_ms = round(bot.latency * 1000) if bot.latency is not None and bot.latency > 0 else 0
    shard_id = getattr(bot, "shard_id", 0) or 0
    shard_count = getattr(bot, "shard_count", 1) or 1
    shard_str = f"Shard #{shard_id} of {shard_count} ({latency_ms}ms)"
    bot_user = f"{bot.user} (ID: {bot.user.id})" if bot.user else "Veil Utilities"
    port = getattr(getattr(bot, "dashboard", None), "port", 8080)

    return [
        ("Bot Account:", bot_user),
        ("Gateway Latency:", shard_str),
        ("Guilds Served:", f"{len(bot.guilds)} servers"),
        ("Total Members:", f"{total_members:,} cached members"),
        ("Loaded Commands:", f"{commands_total} commands across {len(bot.cogs)} modules"),
        ("Web Dashboard:", f"http://0.0.0.0:{port}"),
        ("Default Prefix:", f"{bot.default_prefix} (Help: {bot.default_prefix}help)"),
        ("Security Status:", "Role Blacklist & Antinuke Active"),
    ]
