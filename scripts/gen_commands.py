"""Regenerate docs/COMMANDS.md (and the README's command/module badges) from the bot's actual commands.

    python scripts/gen_commands.py           # rewrite docs/COMMANDS.md and the README badges
    python scripts/gen_commands.py --check   # exit 1 if either is out of date (used by the tests)
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "docs" / "COMMANDS.md"
README = ROOT / "README.md"
HIDDEN_COGS = {"Owner", "Authorization", "Jishaku"}


def signature(cmd) -> str:
    import inspect

    from discord.ext.commands import Greedy
    parts = []
    for name, param in cmd.clean_params.items():
        if param.kind is inspect.Parameter.VAR_POSITIONAL:
            parts.append(f"[{name}...]")
        elif isinstance(param.annotation, Greedy) or getattr(param.annotation, "__origin__", None) is Greedy:
            parts.append(f"[{name}]..." if param.required else f"[{name}={param.default}]...")
        elif param.required:
            parts.append(f"<{name}>")
        elif param.default is None or param.default is param.empty or param.default == ():
            parts.append(f"[{name}]")
        else:
            parts.append(f"[{name}={param.default}]")
    return " ".join([cmd.qualified_name, *parts])


def perms_of(cmd) -> str:
    perms = set(getattr(cmd.callback, "__veil_perms__", []))
    # discord.py's own has_permissions / bot_has_permissions checks keep their perms dict in a closure
    for check in cmd.checks:
        if check.__qualname__.split(".")[0] in ("has_permissions", "bot_has_permissions", "has_guild_permissions"):
            for cell in check.__closure__ or ():
                if isinstance(cell.cell_contents, dict):
                    perms.update(k for k, v in cell.cell_contents.items() if v)
    return ", ".join(sorted(p.replace("_", " ") for p in perms))


def render(bot) -> str:
    cogs = sorted((c for c in bot.cogs.values() if c.qualified_name not in HIDDEN_COGS), key=lambda c: c.qualified_name)
    total = len(list(bot.walk_commands()))
    body = []
    for cog in cogs:
        cmds = sorted((c for c in cog.walk_commands() if not c.hidden), key=lambda c: c.qualified_name)
        if not cmds:
            continue
        body.append(f"## {getattr(cog, 'emoji', '')} {cog.qualified_name}".replace("##  ", "## "))
        body.append(f"_{(cog.description or '').strip()}_\n")
        body.append("| Command | Description | Permission |")
        body.append("|---|---|---|")
        for c in cmds:
            aliases = f" ({', '.join(c.aliases)})" if c.aliases else ""
            desc = (c.help or c.short_doc or "").strip().split("\n")[0].replace("|", "\\|")
            body.append(f"| `{signature(c)}`{aliases} | {desc} | {perms_of(c)} |")
        body.append("")
    head = ["# Command reference", "", f"{total} commands. Default prefix `,`. `<required>` `[optional]`.", "", ""]
    return "\n".join(head + body).rstrip() + "\n"


def badges(readme: str, commands: int, modules: int) -> str:
    """The README with its `commands-N` / `modules-N` badges set to the real numbers."""
    readme = re.sub(r"badge/commands-\d+", f"badge/commands-{commands}", readme)
    return re.sub(r"badge/modules-\d+", f"badge/modules-{modules}", readme)


def counts(text: str) -> tuple[int, int]:
    """(commands, modules) as written in the generated reference."""
    total = int(re.search(r"^(\d+) commands\.", text, re.M).group(1))
    return total, len(re.findall(r"^## ", text, re.M))


async def build() -> str:
    tmp = tempfile.mkdtemp()
    os.environ.setdefault("DISCORD_TOKEN", "x")
    os.environ.setdefault("OWNER_IDS", "1")
    os.environ["DB_PATH"] = os.path.join(tmp, "docs.db")
    os.environ.pop("LAVALINK_URI", None)
    import logging
    logging.disable(logging.CRITICAL)
    from core.bot import Veil
    bot = Veil()
    await bot._async_setup_hook()
    await bot.setup_hook()
    try:
        return render(bot)
    finally:
        await bot.close()


def main() -> int:
    text = asyncio.run(build())
    readme = README.read_text(encoding="utf-8")
    fresh = badges(readme, *counts(text))
    if "--check" in sys.argv:
        stale = [name for name, ok in (("docs/COMMANDS.md", OUT.read_text(encoding="utf-8") == text),
                                       ("the README badges", readme == fresh)) if not ok]
        if stale:
            print(f"{' and '.join(stale)} out of date — run: python scripts/gen_commands.py")
            return 1
        print("docs/COMMANDS.md and the README badges are up to date")
        return 0
    OUT.write_text(text, encoding="utf-8")
    README.write_text(fresh, encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)} and the README badges")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
