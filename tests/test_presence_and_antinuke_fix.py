import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from cogs.antinuke import Antinuke
from cogs.logging import Logging
from core.bot import Veil


def test_presence_custom_and_dynamic():
    bot = Veil()
    bot.change_presence = AsyncMock()
    bot.default_prefix = ","
    guild_mock = SimpleNamespace(id=1, member_count=50)
    bot._guilds = {1: guild_mock}

    # 1. Dynamic status test
    asyncio.run(bot.update_dynamic_status())
    bot.change_presence.assert_called_once()
    act = bot.change_presence.call_args[1]["activity"]
    assert "🛡️ Midtown Events Department • ,help" in act.name

    # 2. Custom status override
    bot.change_presence.reset_mock()
    bot.db = SimpleNamespace(
        is_connected=True,
        get_config=AsyncMock(return_value={"custom_status_text": "🛡️ Midtown Zero-Trust Armed"}),
    )
    asyncio.run(bot.update_dynamic_status())
    bot.change_presence.assert_called_once()
    act_custom = bot.change_presence.call_args[1]["activity"]
    assert act_custom.name == "🛡️ Midtown Zero-Trust Armed"


def test_antinuke_permissions_strips_role_and_dms():
    bot = Veil()
    cog = Antinuke(bot)
    db = SimpleNamespace(
        get_config=AsyncMock(return_value={"enabled": True, "admins": [], "modules": {}}),
        set_config=AsyncMock(),
        fetchall=AsyncMock(return_value=[]),
    )
    bot.db = db

    # Setup guild and members
    bot_dev = SimpleNamespace(id=999, send=AsyncMock(), bot=False)
    bot.get_user = MagicMock(return_value=bot_dev)
    bot.fetch_user = AsyncMock(return_value=bot_dev)

    admin_role = SimpleNamespace(
        id=77,
        name="Administrator",
        permissions=discord.Permissions(administrator=True),
        managed=False,
        position=20,
    )
    target_user = SimpleNamespace(
        id=555,
        name="alt_account",
        bot=False,
        send=AsyncMock(),
        roles=[admin_role],
        top_role=admin_role,
        remove_roles=AsyncMock(),
    )

    guild = SimpleNamespace(
        id=10,
        name="Midtown Community",
        owner_id=111,
        owner=SimpleNamespace(id=111, send=AsyncMock()),
        me=SimpleNamespace(top_role=SimpleNamespace(name="Veil", position=100)),
        get_member=lambda uid: target_user if uid == 555 else None,
        get_channel=lambda cid: None,
        text_channels=[],
        ban=AsyncMock(),
    )

    # Actor gives themselves admin role
    entry = SimpleNamespace(
        target=target_user,
        after=SimpleNamespace(roles=[admin_role]),
        action=discord.AuditLogAction.member_role_update,
    )

    with patch("cogs.authorization.developers", AsyncMock(return_value={999})):
        asyncio.run(cog.respond(guild, {"enabled": True}, 555, "permissions", entry, threshold=1))

    # Should NOT ban target or actor
    guild.ban.assert_not_called()

    # Target user received alert DM
    target_user.send.assert_awaited_once()
    dm_embed = target_user.send.await_args[1]["embed"]
    assert "Veil Antinuke Security Notice" in dm_embed.title
    assert "stripped" in dm_embed.description

    # Bot developer received global alert DM
    bot_dev.send.assert_awaited_once()
    dev_embed = bot_dev.send.await_args[1]["embed"]
    assert "Antinuke" in dev_embed.title or "Veil" in dev_embed.footer.text


def test_ensure_default_channels_with_security():
    cog = Logging(Veil())
    created_channels = []

    class MockObj:
        def __init__(self, id, **kwargs):
            self.id = id
            for k, v in kwargs.items():
                setattr(self, k, v)
        def __hash__(self):
            return self.id
        def __eq__(self, other):
            return getattr(other, "id", None) == self.id

    async def fake_create_channel(name, **kwargs):
        ch = SimpleNamespace(id=len(created_channels) + 1, name=name, __class__=discord.TextChannel)
        created_channels.append(ch)
        return ch

    async def fake_create_category(name, **kwargs):
        return SimpleNamespace(id=99, name=name)

    guild = SimpleNamespace(
        id=10,
        me=MockObj(2, guild_permissions=SimpleNamespace(manage_channels=True)),
        default_role=MockObj(1),
        text_channels=[],
        categories=[],
        create_category=fake_create_category,
        create_text_channel=fake_create_channel,
        get_channel=lambda cid: None,
    )
    cog.bot.db = SimpleNamespace(
        get_config=AsyncMock(return_value={}),
        set_config=AsyncMock(),
    )

    created = asyncio.run(cog.ensure_default_channels(guild, include_security=True))
    names = [c.name for c in created]
    assert "join-logs" in names
    assert "bans" in names
    assert "command-logs" in names
    assert "veil-utilities-logging" in names
    assert "antinuke-logs" in names
    assert "message-logs" in names
    assert len(created) == 6
