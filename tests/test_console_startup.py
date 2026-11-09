"""Offline checks for hosting-panel output; never log in or sync the checkout."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def _env(tmp_path):
    env = dict(os.environ)
    env.update(AUTO_SYNC_GIT="0", DISCORD_TOKEN="test-token-not-real", LOG_COLOR="0",
               LOG_FILE=str(tmp_path / "veil.log"))
    env.pop("VEIL_SYNCED", None)
    return env


def test_entry_point_prints_before_import_failure(tmp_path):
    # -S removes site-packages, so the dependency checks fail without network access.
    result = subprocess.run([sys.executable, "-S", str(ROOT / "main.py")], cwd=tmp_path,
                            env=_env(tmp_path), capture_output=True, text=True, timeout=20)
    assert result.returncode != 0
    assert result.stdout.startswith("[veil] Starting Veil Utilities; checking Python dependencies...")
    assert "Dependency check failed" in result.stdout
    assert "Missing Python packages" in result.stderr
    assert "test-token-not-real" not in result.stdout + result.stderr


def test_application_logs_go_to_stdout_and_rotating_file(tmp_path):
    # Import without running main(): no Discord session or database is opened.
    code = ("import runpy; m = runpy.run_path(" + repr(str(ROOT / "main.py")) + ", run_name='offline_test'); "
            "m['log'].info('console-check-message')")
    result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=_env(tmp_path),
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "[veil] Starting Veil Utilities" in result.stdout
    assert "[git] auto-sync is off" in result.stdout
    assert "console-check-message" in result.stdout
    assert "console-check-message" not in result.stderr
    assert "console-check-message" in (tmp_path / "veil.log").read_text()
    assert "test-token-not-real" not in result.stdout + result.stderr


def test_unwritable_log_path_is_reported_on_stdout(tmp_path):
    env = _env(tmp_path)
    env["LOG_FILE"] = str(tmp_path / "missing-directory" / "veil.log")
    code = "import runpy; runpy.run_path(" + repr(str(ROOT / "main.py")) + ", run_name='offline_test')"
    result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert "Cannot open the log file" in result.stdout
    assert "test-token-not-real" not in result.stdout + result.stderr
