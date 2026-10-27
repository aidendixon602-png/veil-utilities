"""Tests for the bug-audit fixes and the zero-setup API integrations."""
import ast
import asyncio
import glob
import io
import re

import aiohttp
import pytest
from aiohttp import web
from PIL import Image

from cogs.ai import OCR_MAX_BYTES, prepare_for_ocr
from cogs.integrations import WEATHER_CODES, as_list, iso_time, lastfm_image
from cogs.levels import MAX_LEVEL, level_from_xp
from core import ai_providers
from core.bot import GUILD_ONLY_COGS
from core.lavalink_nodes import PUBLIC_NODES, resolve_nodes
from core.net import TooLarge, read_capped
from core.utils import UserError, parse_duration


async def serve(handler, coro):
    """Run `coro(base_url, session)` against a throwaway local aiohttp server."""
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        async with aiohttp.ClientSession() as session:
            return await coro(f"http://127.0.0.1:{port}", session)
    finally:
        await runner.cleanup()


# ------------------------------------------------------------------ AI provider detection
@pytest.mark.parametrize("key,name", [
    (None, "Pollinations (free)"), ("gsk_abc", "Groq"), ("AIzaXYZ", "Google Gemini"),
    ("sk-or-v1-abc", "OpenRouter"), ("sk-proj-abc", "OpenAI"),
])
def test_provider_detected_from_key(key, name):
    assert ai_providers.detect(key).name == name


def test_old_env_example_values_dont_break_detection():
    # the previous .env.example shipped these, so most real .env files contain them
    p = ai_providers.detect("gsk_abc", "https://api.openai.com/v1", "gpt-4o-mini")
    assert p.name == "Groq" and p.model is None
    assert ai_providers.detect(None, "https://api.openai.com/v1", "gpt-4o-mini").name == "Pollinations (free)"


def test_custom_endpoint_without_key_is_allowed():
    p = ai_providers.detect(None, "http://localhost:11434/v1/", "llama3")
    assert (p.name, p.base, p.model, p.headers) == ("Custom", "http://localhost:11434/v1", "llama3", {})


def test_choose_model_prefers_known_good_then_skips_non_chat():
    assert ai_providers.choose_model(["x", "b", "a"], ("a", "b")) == "a"
    assert ai_providers.choose_model(["whisper-large", "text-embed", "chat-1"], ("zzz",)) == "chat-1"
    assert ai_providers.choose_model(["paid", "good:free"], (), free_only=True) == "good:free"
    assert ai_providers.choose_model([], ("fallback",)) == "fallback"


def chat_against(handler, provider_key="gsk_test", extra=None):
    async def go(base, session):
        p = ai_providers.Provider("Test", base, provider_key, model="m", extra=dict(extra or {}))
        return await ai_providers.chat(session, p, [{"role": "user", "content": "hi"}])
    return asyncio.run(serve(handler, go))


def test_chat_success():
    async def ok(request):
        return web.json_response({"choices": [{"message": {"content": "  hello  "}}]})
    assert chat_against(ok) == "hello"


@pytest.mark.parametrize("status,body,expect", [
    (401, {"error": {"message": "bad"}}, "rejected the API key"),
    (400, [{"error": {"code": 400, "message": "Please pass a valid API key"}}], "rejected the API key"),
    (429, {"error": "slow down"}, "rate limit"),
    (200, {"choices": [{"message": {"content": None}}]}, "empty answer"),
    (200, {"unexpected": True}, "unexpected response"),
    (500, None, "error (500)"),
])
def test_chat_errors_are_friendly(status, body, expect):
    async def handler(request):
        if body is None:
            return web.Response(status=status, text="<html>oops</html>")
        return web.json_response(body, status=status)
    with pytest.raises(ai_providers.AIError, match=re.escape(expect)):
        chat_against(handler)


def test_chat_retries_without_reasoning_effort_when_unsupported():
    seen = []

    async def handler(request):
        body = await request.json()
        seen.append("reasoning_effort" in body)
        if "reasoning_effort" in body:
            return web.json_response({"error": {"message": "reasoning_effort is not supported"}}, status=400)
        return web.json_response({"choices": [{"message": {"content": "ok"}}]})
    assert chat_against(handler, extra={"reasoning_effort": "low"}) == "ok"
    assert seen == [True, False]


# ------------------------------------------------------------------ music nodes
def test_public_nodes_used_by_default():
    nodes, public = resolve_nodes(None, None)
    assert public and nodes == list(PUBLIC_NODES) and len(nodes) >= 3
    assert all(uri.startswith(("http://", "https://")) for uri, _ in nodes)


def test_own_nodes_and_disable():
    assert resolve_nodes("http://a:1,http://b:2", "x,y") == ([("http://a:1", "x"), ("http://b:2", "y")], False)
    assert resolve_nodes("http://a:1,http://b:2", "same") == ([("http://a:1", "same"), ("http://b:2", "same")], False)
    assert resolve_nodes("http://a:1", "has,comma") == ([("http://a:1", "has,comma")], False)
    assert resolve_nodes(None, None, enabled=False) == ([], False)
    # your node first, the public ones behind it as backups (no duplicates), unless backups are off
    mine = PUBLIC_NODES[0][0]
    nodes, public = resolve_nodes(f"http://own:2333,{mine}", "pw", backups=True)
    assert not public and nodes[0] == ("http://own:2333", "pw") and nodes[1] == (mine, "pw")
    assert nodes[2:] == list(PUBLIC_NODES[1:])
    assert resolve_nodes("http://own:2333", "pw", backups=False) == ([("http://own:2333", "pw")], False)
    assert resolve_nodes(None, None, enabled=False, backups=True) == ([], False)


# ------------------------------------------------------------------ integrations helpers
def test_lastfm_single_item_is_wrapped():
    assert as_list({"name": "x"}) == [{"name": "x"}]
    assert as_list([1, 2]) == [1, 2] and as_list(None) == []


def test_lastfm_image_skips_blank_and_placeholder():
    imgs = [{"#text": "https://a/small.png"}, {"#text": ""},
            {"#text": "https://x/2a96cbd8b46e442fc41c2b86b821562f.png"}]
    assert lastfm_image(imgs) == "https://a/small.png"
    assert lastfm_image([{"#text": ""}]) is None and lastfm_image(None) is None


def test_iso_time_is_timezone_aware():
    for value in ("2011-01-25T18:44:36Z", "2019-02-13T03:50:04.4Z", "2020-01-01T00:00:00.123456789Z"):
        dt = iso_time(value)
        assert dt.tzinfo is not None and dt.utcoffset().total_seconds() == 0


def test_every_wmo_weather_code_has_a_label():
    wmo = {0, 1, 2, 3, 45, 48, 51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 71, 73, 75, 77, 80, 81, 82, 85, 86, 95, 96, 99}
    assert wmo <= set(WEATHER_CODES)


# ------------------------------------------------------------------ OCR, levels, durations
def test_ocr_image_shrunk_under_free_tier_limit():
    noisy = Image.effect_noise((3000, 3000), 100).convert("RGB")
    buf = io.BytesIO()
    noisy.save(buf, "PNG")
    assert buf.tell() > OCR_MAX_BYTES
    data, filetype = prepare_for_ocr(buf.getvalue())
    assert len(data) <= OCR_MAX_BYTES and filetype in ("PNG", "JPG")


def test_level_math_is_bounded():
    assert level_from_xp(10 ** 18)[0] == MAX_LEVEL  # used to loop ~450 million times and freeze the bot
    assert level_from_xp(-5)[0] == 0


def test_absurd_duration_rejected():
    with pytest.raises(UserError):
        parse_duration("99999999999w")
    assert parse_duration("2h30m").total_seconds() == 9000


# ------------------------------------------------------------------ networking
def test_read_capped_reads_whole_body_and_enforces_limit():
    payload = b"x" * 300_000  # several chunks: content.read(n) used to return only the first one

    async def big(request):
        return web.Response(body=payload)

    async def go(base, session):
        async with session.get(base) as r:
            assert await read_capped(r, 1_000_000) == payload
        async with session.get(base) as r:
            with pytest.raises(TooLarge):
                await read_capped(r, 100_000)
    asyncio.run(serve(big, go))


# ------------------------------------------------------------------ startup & DMs
def test_intent_fallback_order():
    from core.bot import intent_plans
    plans = intent_plans()
    assert plans[0][0].presences and plans[0][1] is None
    assert not plans[1][0].presences and plans[1][0].members
    assert not plans[-1][0].message_content


def test_guild_only_cog_names_exist():
    names = set()
    for path in glob.glob("cogs/*.py"):
        for node in ast.parse(open(path).read()).body:
            if isinstance(node, ast.ClassDef):
                names.add(node.name)
    assert GUILD_ONLY_COGS <= names
