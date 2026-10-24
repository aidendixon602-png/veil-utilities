"""Offline smoke test: loads every module without a Discord token and checks the command registry.
Run with `python tests/smoke_test.py`. Used by the GitHub Actions check."""
import asyncio
import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DB_PATH", ":memory:")
logging.disable(logging.CRITICAL)

from core.bot import EXTENSIONS, Veil  # noqa: E402
from core.help import category_commands  # noqa: E402
from core.utils import build_message, parse_duration  # noqa: E402


async def main() -> int:
    bot = Veil()
    await bot.db.connect()
    problems: list[str] = []
    for ext in EXTENSIONS:
        try:
            await bot.load_extension(ext)
        except Exception as e:  # noqa: BLE001
            problems.append(f"{ext} failed to load: {e!r}")

    seen: dict[str, str] = {}
    for cmd in bot.commands:
        for name in (cmd.name, *cmd.aliases):
            if name in seen:
                problems.append(f"name clash: '{name}' used by {seen[name]} and {cmd.qualified_name}")
            seen[name] = cmd.qualified_name

    for cog in bot.cogs.values():
        if not isinstance(getattr(cog, "emoji", None), str):
            problems.append(f"{cog.qualified_name}.emoji is not a string (shadowed by a command?)")

    placed = sum(len(v) for v in category_commands(bot).values())
    visible = len([c for c in bot.commands if not c.hidden and c.cog is not None])  # help itself is excluded
    if placed != visible:
        problems.append(f"help menu shows {placed} of {visible} commands")

    msg = build_message("{embed}$v{title: hi {x}}$v{color: #ff0000}", {"x": "there"})
    if msg["embed"].title != "hi there":
        problems.append("embed scripting broken")
    if parse_duration("1h30m").total_seconds() != 5400:
        problems.append("duration parsing broken")

    total = sum(1 for _ in bot.walk_commands())
    for cog in list(bot.cogs):
        await bot.remove_cog(cog)
    await bot.db.close()

    if problems:
        print("❌ smoke test failed:")
        for p in problems:
            print("  -", p)
        return 1
    print(f"✅ {len(EXTENSIONS)} modules loaded, {total} commands, no clashes")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
