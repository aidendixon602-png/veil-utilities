import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()

from core.bot import Veil  # noqa: E402

from logging.handlers import RotatingFileHandler  # noqa: E402

LOG_FILE = os.getenv("LOG_FILE", "veil.log")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    handlers=[logging.StreamHandler(),
                              RotatingFileHandler(LOG_FILE, maxBytes=5_000_000, backupCount=3, encoding="utf-8")])


async def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        sys.exit("Set DISCORD_TOKEN in your .env file (see .env.example)")
    bot = Veil()
    async with bot:
        await bot.start(token)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
