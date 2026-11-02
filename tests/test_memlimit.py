"""core.memlimit: reads the container's memory cap when there is one, shrugs when there isn't.

The motivation: hosting panels (Wispbyte/Pterodactyl) OOM-kill a bot that goes over the container's
memory cap, and that kill prints no traceback — the console just shows the bot stopping. Detecting
the cap lets the bot pick its low-memory profile by itself and say so in the startup banner.
"""
import core.config
import core.memlimit
from core.memlimit import container_memory_limit_mb


def _only_file(monkeypatch, tmp_path, contents):
    file = tmp_path / "memory.max"
    if contents is not None:
        file.write_text(contents)
    monkeypatch.setattr(core.memlimit, "_CGROUP_FILES", (str(file), str(tmp_path / "missing")))
    return file


def test_no_cgroup_files_means_no_limit(tmp_path, monkeypatch):
    _only_file(monkeypatch, tmp_path, None)
    assert container_memory_limit_mb() is None


def test_cgroup_v2_limit_is_read_in_mb(tmp_path, monkeypatch):
    _only_file(monkeypatch, tmp_path, "536870912\n")  # 512 MB
    assert container_memory_limit_mb() == 512


def test_cgroup_v2_max_means_unlimited(tmp_path, monkeypatch):
    _only_file(monkeypatch, tmp_path, "max\n")
    assert container_memory_limit_mb() is None


def test_cgroup_v1_sentinel_for_unlimited_is_ignored(tmp_path, monkeypatch):
    _only_file(monkeypatch, tmp_path, str(1 << 60))
    assert container_memory_limit_mb() is None


def test_small_container_turns_low_memory_on_by_itself(monkeypatch):
    monkeypatch.delenv("LOW_MEMORY", raising=False)
    monkeypatch.setattr(core.memlimit, "container_memory_limit_mb", lambda: 512)
    assert core.config.Settings.from_env().low_memory is True


def test_big_container_stays_on_the_default_profile(monkeypatch):
    monkeypatch.delenv("LOW_MEMORY", raising=False)
    monkeypatch.setattr(core.memlimit, "container_memory_limit_mb", lambda: 4096)
    assert core.config.Settings.from_env().low_memory is False


def test_an_explicit_low_memory_always_wins(monkeypatch):
    monkeypatch.setenv("LOW_MEMORY", "false")
    monkeypatch.setattr(core.memlimit, "container_memory_limit_mb", lambda: 256)
    assert core.config.Settings.from_env().low_memory is False
    monkeypatch.setenv("LOW_MEMORY", "true")
    monkeypatch.setattr(core.memlimit, "container_memory_limit_mb", lambda: None)
    assert core.config.Settings.from_env().low_memory is True
