"""Messages the bot itself deleted on purpose (purge results, `,say` commands...), so the message log, snipe and
ghost-ping detection don't treat them as someone deleting a message.

The bot no longer tidies channels by deleting command messages or its own replies: everything stays.
"""
from __future__ import annotations

import time
from collections import OrderedDict

_CLEANED: OrderedDict[int, float] = OrderedDict()  # message id -> when we deleted it
_REMEMBER_FOR = 300


def was_cleaned(message_id: int) -> bool:
    """True if the bot deleted this message on purpose (so it isn't a user deleting something)."""
    now = time.monotonic()
    while _CLEANED and now - next(iter(_CLEANED.values())) > _REMEMBER_FOR:
        _CLEANED.popitem(last=False)
    return message_id in _CLEANED


def remember(message_id: int) -> None:
    """Note that the bot is about to delete this message itself."""
    _mark(message_id)


def _mark(message_id: int) -> None:
    _CLEANED[message_id] = time.monotonic()
    while len(_CLEANED) > 5000:
        _CLEANED.popitem(last=False)
