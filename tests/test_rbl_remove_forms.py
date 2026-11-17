"""The pictured RBL removal forms must remove only the intended saved IDs."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cogs.moderation import Moderation


@pytest.mark.parametrize('argument,expected', [('deleted', [10, 20]), ('$', [20, 30]), ('10', [20, 30])])
def test_rbl_remove_deleted_and_role_name_or_saved_id(argument, expected):
    async def go():
        dollar = SimpleNamespace(id=10, name='$', mention='<@&10>')
        other = SimpleNamespace(id=20, name='Other', mention='<@&20>')
        roles = {10: dollar, 20: other}
        guild = SimpleNamespace(id=1, _roles=roles, get_role=roles.get)
        store = {'roles': [10, 20, 30], 'hard_roles': [30], 'authorized': [5]}
        async def save(gid, key, data):
            store.update(deepcopy(data))
        bot = SimpleNamespace(db=SimpleNamespace(get_config=AsyncMock(return_value=store),
                                                 set_config_committed=AsyncMock(side_effect=save)))
        ctx = SimpleNamespace(guild=guild, approve=AsyncMock(), warn=AsyncMock())
        await Moderation.rbl_remove.callback(Moderation(bot), ctx, role=argument)
        assert store['roles'] == expected
        assert store['hard_roles'] == [30] and store['authorized'] == [5]
        ctx.approve.assert_awaited_once()
    asyncio.run(go())
