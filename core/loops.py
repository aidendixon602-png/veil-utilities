"""Keeping background loops alive.

``discord.ext.tasks`` stops a loop for good when one iteration raises: the exception ends the task and
nothing restarts it (only network errors are retried). One bad row, one server with an odd config or one
failed database write would silently switch that feature off until the next restart — reminders stop being
sent, counters freeze, giveaways never end — with a single traceback in the log as the only clue.

``@resilient`` logs the failure and lets the next iteration run, so a loop only stops when it is cancelled.
Put it directly under ``@tasks.loop(...)``::

    @tasks.loop(minutes=5)
    @resilient
    async def poll(self):
        ...
"""
from __future__ import annotations

import functools
import logging

log = logging.getLogger("veil.loops")


def resilient(func):
    """Run one iteration of a background loop; a failure is logged and the loop carries on."""
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception:  # noqa: BLE001 — any failure here must not end the loop (CancelledError isn't caught)
            log.exception("Background task %s failed this round; it runs again at its next tick",
                          getattr(func, "__qualname__", func))
            return None
    return wrapper
