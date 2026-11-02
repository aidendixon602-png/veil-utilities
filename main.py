"""veil utilities — entry point."""
import asyncio
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
# Use real imports, not find_spec: a package can be installed yet unloadable on the running Python.
# On 3.13+ `import discord` dies with "No module named 'audioop'" (the stdlib module was removed;
# audioop-lts in requirements.txt brings it back). find_spec can't see that and the boot crashed with
# a raw traceback — on hosting consoles that's reported as "the bot doesn't start" with no usable error.
missing: list[str] = []
broken: list[str] = []
audioop_hint = False
for module, package in REQUIRED.items():
    try:
        __import__(module)
    except ModuleNotFoundError as error:
        if error.name == module.split(".")[0]:
            missing.append(package)  # the package itself isn't installed
        else:
            broken.append(f"{package} ({error})")  # installed, but something inside it won't load
            audioop_hint = audioop_hint or error.name == "audioop"
    except Exception as error:  # e.g. a compiled wheel built for a different Python or CPU
        broken.append(f"{package} ({type(error).__name__}: {error})")
if missing or broken:
    lines = []
    if missing:
        lines.append("Missing Python packages: " + ", ".join(missing))
    if broken:
        lines.append("Installed packages that won't load on this Python: " + ", ".join(broken))
    lines.append("Install them with:  python -m pip install -r requirements.txt")
    lines.append("(On a hosting panel, add them to the 'Additional Python Packages' field.)")
    if audioop_hint:
        lines.append("Hint: 'audioop' was removed in Python 3.13 — add audioop-lts to the packages "
                     "(already in requirements.txt) or switch the server to Python 3.11/3.12.")
    sys.exit("\n".join(lines))


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
    """Pull GitHub's main on boot and say what happened, so a host never quietly keeps running old code."""
    already_synced = os.environ.pop("VEIL_SYNCED", None) == "1"  # set by the re-exec below, once per boot
    if already_synced:
        return
    if os.environ.get("AUTO_SYNC_GIT", "1") == "0":
        print("[git] auto-sync is off (AUTO_SYNC_GIT=0): running whatever is in this folder", flush=True)
        return
    try:
        from core import gitinfo
        if not os.path.exists(os.path.join(ROOT, ".git")):
            print("[git] this folder isn't a git checkout, so nothing is pulled. Set the Git repo in the panel "
                  "or upload the files by hand.", flush=True)
            return
        before = gitinfo.snapshot(ROOT)
        if before.slug and before.slug.lower() != gitinfo.EXPECTED_REPO.lower():
            print(f"[git] WARNING: this folder pulls from {before.slug}, not {gitinfo.EXPECTED_REPO}", flush=True)
        if before.edited:
            patch = os.path.join(ROOT, time.strftime("local-edits-%Y%m%d-%H%M%S.patch"))
            try:
                diff = gitinfo._git(ROOT, "diff", "HEAD").stdout
                with open(patch, "w", encoding="utf-8") as f:
                    f.write(diff)
                print(f"[git] {before.edited} file(s) were edited by hand; saved your changes to "
                      f"{os.path.basename(patch)} and syncing anyway", flush=True)
            except Exception:
                print(f"[git] {before.edited} file(s) were edited by hand and couldn't be saved; "
                      "not syncing (set AUTO_SYNC_GIT=0 to silence this)", flush=True)
                return
        result = gitinfo.pull(ROOT)
        if not result.ok:
            print(f"[git] sync FAILED, still running {before.short}: {result.error}", flush=True)
        elif result.updated:
            print(f"[git] updated {result.old[:7]} -> {result.new[:7]}: {result.subject}", flush=True)
            if result.changed is None or "main.py" in result.changed:
                os.environ["VEIL_SYNCED"] = "1"  # main.py itself changed: start again so the new one runs
                try:
                    sys.stdout.flush()
                    os.execv(sys.executable, [sys.executable, os.path.join(ROOT, "main.py"), *sys.argv[1:]])
                except OSError:
                    os.environ.pop("VEIL_SYNCED", None)
        else:
            print(f"[git] up to date at {result.new[:7]}: {result.subject}", flush=True)
    except Exception as e:
        print(f"[git] sync error: {e}", flush=True)
    finally:
        # drop the copies loaded for the sync so the bot imports whatever is on disk now
        for name in [m for m in sys.modules if m == "core" or m.startswith("core.")]:
            del sys.modules[name]


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

from core import netcalm  # noqa: E402
netcalm.install()  # reconnect within a minute of the network coming back, one log line per failed attempt


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
