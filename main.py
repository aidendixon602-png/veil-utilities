"""veil utilities — entry point."""
import asyncio
import importlib.util
import logging
import os
import sys
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


load_env_file(os.path.join(ROOT, ".env"))

from core.config import ConfigError  # noqa: E402  (must load after .env)

try:
    from core.config import settings  # noqa: E402
    settings.require_runtime()
except ConfigError as error:
    sys.exit(f"{error}\n\nFix these in your .env file (see .env.example) and start again.")

from core.bot import Veil  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(settings.log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8"),
    ],
)
log = logging.getLogger("veil")


async def main() -> None:
    log.info("Starting with %s", settings.summary())
    async with Veil() as bot:
        await bot.start(settings.discord_token)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
