"""Every message from a hardcoded person gets a 👻 reaction, and nothing else does."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

import cogs.fun as F
from cogs.fun import Fun

WEBA = 1277709272041394187
SOMEONE_ELSE = 555


def channel(cid=30):
    ch = MagicMock(spec=discord.TextChannel)
    ch.id, ch.name = cid, "general"
    ch.webhooks = AsyncMock(return_value=[])
    return ch


def message(author_id=WEBA, *, guild=True, bot=False, content="hello", channel_obj=None):
    m = MagicMock(spec=discord.Message)
    m.author = SimpleNamespace(id=author_id, bot=bot, name="weba_wa2", display_name="weba_wa2",
                               display_avatar=SimpleNamespace(url="https://example/avatar.png"))
    m.guild = SimpleNamespace(id=10) if guild else None
    m.channel = channel_obj or channel()
    m.content = content
    m.add_reaction = AsyncMock()
    m.delete = AsyncMock()
    return m


def make_cog():
    async def get_config(gid, module, default=None):
        return {"users": []}
    store = SimpleNamespace(get_config=AsyncMock(side_effect=get_config), set_config=AsyncMock())
    return Fun(SimpleNamespace(db=store, user=SimpleNamespace(id=2)))


def run(coro):
    return asyncio.run(coro)


def test_the_hardcoded_person_is_reacted_to_in_servers():
    cog, msg = make_cog(), message()
    run(cog.on_message(msg))
    msg.add_reaction.assert_awaited_once_with("👻")


def test_the_reaction_happens_even_for_messages_without_text_or_from_bots():
    """Attachments, stickers and embeds have no content; the emoji should still land."""
    cog = make_cog()
    for msg in (message(content=""), message(bot=True), message(content=None)):
        run(cog.on_message(msg))
        msg.add_reaction.assert_awaited_once_with("👻")


def test_nobody_else_gets_a_reaction():
    cog, msg = make_cog(), message(SOMEONE_ELSE)
    run(cog.on_message(msg))
    msg.add_reaction.assert_not_awaited()


def test_direct_messages_are_left_alone():
    cog, msg = make_cog(), message(guild=False)
    run(cog.on_message(msg))
    msg.add_reaction.assert_not_awaited()


def test_missing_permission_or_a_deleted_message_does_not_raise():
    cog = make_cog()
    for error in (discord.Forbidden(MagicMock(status=403), "missing access"),
                  discord.NotFound(MagicMock(status=404), "unknown message"),
                  discord.HTTPException(MagicMock(status=500), "discord broke")):
        msg = message()
        msg.add_reaction = AsyncMock(side_effect=error)
        run(cog.on_message(msg))  # must not raise


def test_a_reaction_failure_does_not_block_uwulock():
    """Both handlers share one on_message; the ghost reaction must not short-circuit the rest."""
    cog = make_cog()
    msg = message(channel_obj=channel())
    msg.add_reaction = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "no"))
    cog.bot.db.get_config = AsyncMock(return_value={"users": [WEBA]})
    msg.channel.webhooks = AsyncMock(return_value=[])
    msg.channel.create_webhook = AsyncMock(return_value=SimpleNamespace(send=AsyncMock()))
    run(cog.on_message(msg))
    msg.delete.assert_awaited()


def test_the_hardcoded_id_is_the_one_in_the_permissions_list():
    from core import perms
    assert WEBA in F.GHOSTED_USERS
    assert WEBA in perms.WHITELISTED_PEOPLE


def test_reaction_is_exactly_the_ghost_emoji():
    assert F.GHOST_REACTION == "👻"
