"""Reading the container's memory limit, when the bot runs in one (Wispbyte/Pterodactyl, Docker).

Hosting panels keep bots in memory-capped containers and the kernel OOM-kills a process that goes over.
The kill does not come with a Python traceback — in the console the bot simply stops. Knowing the limit
lets the bot switch to its low-memory profile by itself (see Settings.from_env) and tell the owner.
"""
from __future__ import annotations

_CGROUP_FILES = (
    "/sys/fs/cgroup/memory.max",                     # cgroup v2 (current Pterodactyl/Docker hosts)
    "/sys/fs/cgroup/memory/memory.limit_in_bytes",   # cgroup v1 (older hosts)
)

# a limit is only real up to a sensible ceiling: v1 hosts write huge sentinel values for "unlimited"
_MAX_REAL_LIMIT = 1 << 50  # 1 PiB


def container_memory_limit_mb() -> int | None:
    """MB the container may use before the kernel kills the process, or None when there is no set limit
    (or the bot isn't in a container at all)."""
    for path in _CGROUP_FILES:
        try:
            with open(path, encoding="ascii") as file:
                raw = file.read().strip()
        except OSError:
            continue
        if not raw or raw == "max":  # v2's way of saying "no limit"
            return None
        try:
            limit = int(raw)
        except ValueError:
            continue
        if 0 < limit < _MAX_REAL_LIMIT:
            return limit // 1_048_576
        return None
    return None
