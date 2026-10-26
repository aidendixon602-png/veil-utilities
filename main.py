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
    if os.environ.get("AUTO_SYNC_GIT", "1") == "0":
        return
    try:
        import subprocess
        git_dir = os.path.join(ROOT, ".git")
        if os.path.exists(git_dir):
            status = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, timeout=5)
            if status.returncode == 0 and status.stdout.strip():
                return
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
from core.diagnostics import ConsoleFormatter, format_box, get_system_diagnostics, render_startup_banner  # noqa: E402

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


async def run_bot(show_banner: bool = True) -> bool:
    """Run one bot session. Returns True if an in-process restart was requested, False if exiting."""
    import socket
    import aiohttp
    import discord

    if show_banner:
        use_color = os.environ.get("LOG_COLOR", "1") != "0"
        print(render_startup_banner(color=use_color), flush=True)
        print(format_box("System Startup Diagnostics", get_system_diagnostics(settings), color=use_color), flush=True)
    log.info("Connecting to Discord Gateway with auto-sharding...")

    # Dual-stack strategy: try IPv4 first (bypasses blocked datacenter IPv6 ranges);
    # fallback to IPv6 if IPv4 encounters a block, or vice-versa.
    families_to_try = [socket.AF_INET]
    if socket.has_ipv6:
        families_to_try.append(socket.AF_INET6)

    last_429 = None
    for family in families_to_try:
        family_name = "IPv4" if family == socket.AF_INET else "IPv6"
        for intents, warning in intent_plans():
            if warning:
                log.warning("Discord refused a privileged intent. %s %s", warning, INTENT_HELP)
            try:
                conn = None
                if not settings.proxy:
                    try:
                        conn = aiohttp.TCPConnector(limit=0, family=family)
                    except Exception:
                        conn = None
                bot = Veil(intents=intents, connector=conn)
                bot._restart_requested = False
                async with bot:
                    await bot.start(settings.discord_token)
                return getattr(bot, "_restart_requested", False)
            except discord.PrivilegedIntentsRequired:
                continue
            except discord.HTTPException as err:
                if getattr(err, "status", None) == 429:
                    last_429 = err
                    log.warning("%s route received Discord 429 rate limit.", family_name)
                    break
                raise
            except discord.LoginFailure:
                sys.exit("Discord rejected DISCORD_TOKEN. Open https://discord.com/developers/applications -> your bot "
                         "-> Bot -> Reset Token, paste the new token into .env (no quotes, no 'Bot ' prefix) and restart.")

    if last_429 is not None:
        raise last_429
    sys.exit("Discord refused every intent combination. " + INTENT_HELP)


def restart_process() -> None:
    """Replace this process with a fresh `python main.py`, so every module (core/ included) is loaded from disk.

    An in-process restart reuses modules that are already imported, so after an update it would mix old core
    code with new cogs. Only returns if the exec failed; the caller then falls back to an in-process restart.
    """
    for handler in logging.getLogger().handlers:
        try:
            handler.flush()
        except Exception:
            pass
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        os.execv(sys.executable, [sys.executable, os.path.join(ROOT, "main.py"), *sys.argv[1:]])
    except OSError as e:
        log.warning("Couldn't start a fresh process (%s); restarting in-process instead", e)


def main() -> None:
    rate_limit_backoff = 60
    first_boot = True
    while True:
        try:
            should_restart = asyncio.run(run_bot(show_banner=first_boot))
            if not should_restart:
                break
            log.info("──────────────────────────────────────────")
            log.info(" Restart requested — starting a fresh process…")
            log.info("──────────────────────────────────────────")
            restart_process()
            time.sleep(1)
            rate_limit_backoff = 60
            first_boot = True
        except KeyboardInterrupt:
            break
        except Exception as exc:
            import discord

            is_429 = False
            curr = exc
            while curr:
                if isinstance(curr, discord.HTTPException) and getattr(curr, "status", None) == 429:
                    is_429 = True
                    break
                curr = getattr(curr, "__cause__", None) or getattr(curr, "__context__", None)

            if not is_429 and ("429" in str(exc) and "global rate limits" in str(exc)):
                is_429 = True

            if is_429:
                first_boot = False
                log.warning("═" * 70)
                log.warning(" [RATE LIMIT] Discord Cloudflare Edge Block (HTTP 429)")
                log.warning(" Discord temporarily blocked API requests from this server's IP address")
                log.warning(" ('exceeding global rate limits'). This happens on shared hosting")
                log.warning(" nodes (Wispbyte/Pterodactyl) when other bots share the IP or after restarts.")
                log.warning(" Keeping process alive to prevent Wispbyte crash loops and allow cooldown.")
                log.warning(" Waiting %ds before reconnecting. Cooldown usually clears in 10-15m.", rate_limit_backoff)
                log.warning(" [TIP] To bypass immediately without waiting: configure DISCORD_PROXY in .env")
                log.warning("═" * 70)
                wait_left = rate_limit_backoff
                while wait_left > 0:
                    step = min(15, wait_left)
                    time.sleep(step)
                    wait_left -= step
                    if wait_left > 0:
                        log.info(" Rate limit cooldown: %ds remaining until reconnect retry...", wait_left)
                rate_limit_backoff = min(300, rate_limit_backoff + 60)
                continue

            log.exception("Unexpected error in main loop")
            break


if __name__ == "__main__":
    main()
