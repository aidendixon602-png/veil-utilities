""",s shows a deleted message like a card: who sent it, the text (or [no text content]), its picture,
"deleted message 1 of 5" with the time, and < Prev / Next > to look further back."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cogs.utility import SnipeView, Utility, snipe_embed
from core.utils import UserError


def author(name="_qevx_"):
    return SimpleNamespace(name=name, bot=False, display_avatar=SimpleNamespace(url="https://cdn/avatar.png"))


def attachment(name, content_type=None):
    return SimpleNamespace(filename=name, proxy_url=f"https://media/{name}", content_type=content_type)


_ids = iter(range(9_000_000, 10_000_000))  # never an id another test marked as tidied away by the bot


def deleted(content="", files=(), stickers=(), mid=None):
    return SimpleNamespace(id=mid or next(_ids), guild=SimpleNamespace(id=10), channel=SimpleNamespace(id=30), author=author(),
                           content=content, attachments=list(files), stickers=list(stickers))


def cog_with(*messages):
    cog = Utility.__new__(Utility)
    Utility.__init__(cog, SimpleNamespace())
    for m in messages:
        asyncio.run(cog.on_message_delete(m))
    return cog


def test_an_image_with_no_text_looks_like_the_reference():
    cog = cog_with(deleted(files=[attachment("photo.jpg", "image/jpeg")]))
    d = cog.deleted[30][0]
    e = snipe_embed(d, 1, 5)
    assert e.author.name == "_qevx_" and e.author.icon_url == "https://cdn/avatar.png"
    assert e.description == "*[no text content]*"
    assert e.image.url == "https://media/photo.jpg"
    assert e.footer.text == "deleted message 1 of 5"
    assert e.timestamp is not None and abs(e.timestamp.timestamp() - time.time()) < 5  # when it was deleted
    assert not e.fields and e.title is None


def test_text_is_shown_and_non_images_are_linked_not_embedded():
    cog = cog_with(deleted("look at this", files=[attachment("clip.mp4", "video/mp4"), attachment("a.png")]))
    e = snipe_embed(cog.deleted[30][0], 1, 1)
    assert e.description.startswith("look at this")
    assert e.image.url == "https://media/a.png"  # the picture goes in the card
    assert "[clip.mp4](https://media/clip.mp4)" in e.description and "a.png](" not in e.description


def test_a_sticker_is_shown_as_the_picture():
    cog = cog_with(deleted(stickers=[SimpleNamespace(url="https://media/sticker.png")]))
    assert snipe_embed(cog.deleted[30][0], 1, 1).image.url == "https://media/sticker.png"


def test_prev_and_next_page_through_and_stop_at_the_ends():
    queue = [cog_with(deleted(f"message {i}")).deleted[30][0] for i in range(1, 6)]
    view = SnipeView(7, queue)
    assert [c.label for c in view.children] == ["< Prev", "Next >"]
    assert view.prev_button.disabled and not view.next_button.disabled

    async def click(button):
        it = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()))
        await button.callback(it)
        return it.response.edit_message.await_args.kwargs["embed"]

    shown = asyncio.run(click(view.next_button))
    assert shown.footer.text == "deleted message 2 of 5" and shown.description == "message 2"
    for _ in range(5):
        asyncio.run(click(view.next_button))
    assert view.index == 5 and view.next_button.disabled and not view.prev_button.disabled
    assert asyncio.run(click(view.prev_button)).footer.text == "deleted message 4 of 5"


def test_only_the_person_who_ran_it_can_page():
    view = SnipeView(7, [{}, {}])
    it = SimpleNamespace(user=SimpleNamespace(id=8), client=SimpleNamespace(is_owner=AsyncMock(return_value=False)),
                         response=SimpleNamespace(send_message=AsyncMock()))
    assert asyncio.run(view.interaction_check(it)) is False
    assert it.response.send_message.await_args.kwargs["ephemeral"]


def snipe(cog, index=1):
    ctx = SimpleNamespace(guild=SimpleNamespace(id=10), channel=SimpleNamespace(id=30), author=SimpleNamespace(id=7),
                          send=AsyncMock())
    cog.bot = SimpleNamespace(db=SimpleNamespace(get_config=AsyncMock(return_value={})))
    asyncio.run(Utility.snipe.callback(cog, ctx, index))
    return ctx.send.await_args.kwargs


def test_one_deleted_message_needs_no_buttons_and_several_get_them():
    one = snipe(cog_with(deleted("only one")))
    assert one["embed"].footer.text == "deleted message 1 of 1" and "view" not in one
    several = snipe(cog_with(*(deleted(f"m{i}") for i in range(3))), index=2)
    assert several["embed"].footer.text == "deleted message 2 of 3" and several["embed"].description == "m1"
    assert several["view"].index == 2


def test_nothing_to_snipe_and_out_of_range_say_so():
    with pytest.raises(UserError, match="nothing to snipe"):
        snipe(cog_with())
    with pytest.raises(UserError, match="only 1 deleted message"):
        snipe(cog_with(deleted("x")), index=4)
