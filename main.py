"""veil utilities — entry point."""
import asyncio
import importlib.util
import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler

# Run from the bot's own folder no matter how it was started (e.g. `python veil-utilities/main.py` on a host),
# so .env, veil.db, veil.log and the cogs are always found in the same place.
ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

# import name -> pip package name (they differ for some, e.g. `dotenv` is installed as `python-dotenv`)
REQUIRED = {"discord": "discord.py", "aiosqlite": "aiosqlite", "aiohttp": "aiohttp", "PIL": "Pillow",
            "regex": "regex"}
missing = [pkg for mod, pkg in REQUIRED.items() if importlib.util.find_spec(mod) is None]
if missing:
    sys.exit("Missing Python packages: " + ", ".join(missing) + "\n"
             "Install them with:  python -m pip install -r requirements.txt\n"
             "(On a hosting panel, add them to the 'Additional Python Packages' field.)")


def load_env_file(path: str) -> None:
    """Read KEY=VALUE lines from .env. Uses python-dotenv when installed, otherwise a small built-in parser,
    so a host that fails to install python-dotenv can still start the bot. Real environment variables win."""
    if not os.path.exists(path):
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.removeprefix("export ").partition("=")
                key, value = key.strip(), value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                os.environ.setdefault(key, value)
    else:
        load_dotenv(path)


def auto_sync_git() -> None:
    """If running in a git repo on a host like Wispbyte, auto-pull latest commits from GitHub."""
    try:
        import subprocess
        git_dir = os.path.join(ROOT, ".git")
        if os.path.exists(git_dir):
            r = subprocess.run(["git", "fetch", "origin", "main"], cwd=ROOT, capture_output=True, timeout=10)
            if r.returncode == 0:
                subprocess.run(["git", "reset", "--hard", "origin/main"], cwd=ROOT, capture_output=True, timeout=10)
    except Exception:
        pass


auto_sync_git()
load_env_file(os.path.join(ROOT, ".env"))

from core.config import ConfigError  # noqa: E402  (must load after .env)

try:
    from core.config import settings  # noqa: E402
    settings.require_runtime()
except ConfigError as error:
    sys.exit(f"{error}\n\nFix these in your .env file (see .env.example) and start again.")

from core.bot import INTENT_HELP, Veil, intent_plans  # noqa: E402

class ConsoleFormatter(logging.Formatter):
    """Clean panel-console output: `20:17:14 | INFO  | veil.music | message`.

    Colors use ANSI escapes (Pterodactyl-style consoles render them); disabled with LOG_COLOR=0.
    The rotating log file gets the plain format below instead."""

    LEVEL_COLORS = {"DEBUG": "\x1b[2;37m", "INFO": "\x1b[38;5;42m", "WARNING": "\x1b[38;5;214m",
                    "ERROR": "\x1b[31m", "CRITICAL": "\x1b[1;41m"}
    RESET = "\x1b[0m"
    DIM = "\x1b[2m"

    def __init__(self, color: bool):
        super().__init__()
        self.color = color

    def format(self, record: logging.LogRecord) -> str:
        name = record.name.split(".")[0] if not record.name.startswith("veil") else record.name
        when = time.strftime("%H:%M:%S", time.localtime(record.created))
        level = {"WARNING": "WARN", "CRITICAL": "CRIT"}.get(record.levelname, record.levelname)
        if self.color:
            c = self.LEVEL_COLORS.get(record.levelname, "")
            return f"{self.DIM}{when}{self.RESET} {c}{level:<5}{self.RESET} " \
                   f"{self.DIM}{name:<12}{self.RESET} {record.getMessage()}"
        return f"{when} {level:<5} {name:<12} {record.getMessage()}"


PLAIN_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
console = logging.StreamHandler()
console.setFormatter(ConsoleFormatter(color=os.environ.get("LOG_COLOR", "1") != "0"))
logfile = RotatingFileHandler(settings.log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
logfile.setFormatter(logging.Formatter(PLAIN_FORMAT))
logging.basicConfig(level=logging.INFO, handlers=[console, logfile])
# library noise: retries/gateway chatter add nothing the bot's own logs don't already say better
logging.getLogger("discord").setLevel(logging.WARNING)
logging.getLogger("wavelink").setLevel(logging.WARNING)
logging.getLogger("wavelink.websocket").setLevel(logging.ERROR)
log = logging.getLogger("veil")


async def run_bot() -> bool:
    """Run one bot session. Returns True if an in-process restart was requested, False if exiting."""
    import discord
    log.info("config: %s", settings.summary())
    for intents, warning in intent_plans():
        if warning:
            log.warning("Discord refused a privileged intent. %s %s", warning, INTENT_HELP)
        try:
            bot = Veil(intents=intents)
            bot._restart_requested = False
            async with bot:
                await bot.start(settings.discord_token)
            return getattr(bot, "_restart_requested", False)
        except discord.PrivilegedIntentsRequired:
            continue
        except discord.LoginFailure:
            sys.exit("Discord rejected DISCORD_TOKEN. Open https://discord.com/developers/applications -> your bot "
                     "-> Bot -> Reset Token, paste the new token into .env (no quotes, no 'Bot ' prefix) and restart.")
    sys.exit("Discord refused every intent combination. " + INTENT_HELP)


def main() -> None:
    while True:
        try:
            should_restart = asyncio.run(run_bot())
            if not should_restart:
                break
            log.info("──────────────────────────────────────────")
            log.info(" Internal restart requested — re-initializing in 1s…")
            log.info("──────────────────────────────────────────")
            time.sleep(1)
        except KeyboardInterrupt:
            break
        except Exception:
            log.exception("Unexpected error in main loop")
            break


if __name__ == "__main__":
    main()
