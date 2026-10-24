"""COMMANDS.md must match the bot's real commands (regenerate with `python scripts/gen_commands.py`)."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_commands_md_is_up_to_date():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_commands.py"), "--check"],
                       capture_output=True, text=True, cwd=ROOT, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
