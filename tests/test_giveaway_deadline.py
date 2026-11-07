import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from cogs.giveaways import EnterButton


def test_expired_giveaway_rejects_entry_even_before_checker_marks_it_ended(monkeypatch):
    monkeypatch.setattr("cogs.giveaways.now", lambda: 100)
    db = SimpleNamespace(fetchone=AsyncMock(return_value={"ended": 0, "ends": 99, "required_role": None}),
                         execute=AsyncMock())
    it = SimpleNamespace(client=SimpleNamespace(db=db), message=SimpleNamespace(id=1),
                         response=SimpleNamespace(send_message=AsyncMock()))
    asyncio.run(EnterButton(1).callback(it))
    it.response.send_message.assert_awaited_once_with("This giveaway has ended.", ephemeral=True)
    db.execute.assert_not_awaited()
