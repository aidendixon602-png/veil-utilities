"""veil utilities — entry point."""
import asyncio
import logging
import sys
from logging.handlers import RotatingFileHandler

from dotenv import load_dotenv

load_dotenv()

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
