"""New image filters render real files, and AI chat rotates across a provider's ranked models."""
from __future__ import annotations

import asyncio
import io

import aiohttp
from aiohttp import web
from PIL import Image

from cogs.imaging import (fx_censor, fx_duotone, fx_glitch, fx_oilify, fx_rainbow, fx_sketch, fx_solarize,
                          fx_vignette, render)
from core import ai_providers

NEW_FX = [fx_solarize, fx_glitch, fx_sketch, fx_rainbow, fx_vignette, fx_duotone, fx_censor, fx_oilify]


def sample_png() -> bytes:
    im = Image.new("RGBA", (48, 48))
    im.putdata([(x * 5 % 256, y * 5 % 256, 128, 255) for y in range(48) for x in range(48)])
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_each_new_filter_renders_a_real_image():
    data = sample_png()
    for fx in NEW_FX:
        out, name = render(data, fx, fx.__name__)
        img = Image.open(out)
        img.load()
        assert img.format == "PNG" and name.endswith(".png"), f"{fx.__name__} broke"


def test_new_filters_work_on_animated_gifs():
    frames = []
    for color in ((255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255)):
        f = Image.new("RGBA", (32, 24), color)  # deliberately tiny: exercises glitch's small-image slicing
        frames.append(f)
    buf = io.BytesIO()
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=[80] * 3, loop=0)
    for fx in (fx_glitch, fx_censor, fx_duotone):
        out, name = render(buf.getvalue(), fx, "anim")
        img = Image.open(out)
        assert img.is_animated and img.n_frames == 3 and name.endswith(".gif"), f"{fx.__name__} killed the animation"


# ------------------------------------------------------------------ model rotation
async def serve(handler, coro):
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


async def rotation(scenario):
    seen: list[str] = []

    async def handler(request):
        if request.path.endswith("/models"):
            return web.json_response({"data": [{"id": m} for m in ("m1", "m2", "m3", "m4")]})
        model = (await request.json())["model"]
        seen.append(model)
        if scenario(model):
            return web.json_response({"choices": [{"message": {"content": f"hello from {model}"}}]})
        return web.json_response({"error": {"message": "gone"}}, status=404)

    async def call(base, sess):
        provider = ai_providers.Provider("Test", base, None, preferred=("m1", "m2", "m3", "m4"))
        answer = await ai_providers.chat(sess, provider, [{"role": "user", "content": "hi"}])
        return answer, seen, provider

    return await serve(handler, lambda base, sess: call(base, sess))


def test_chat_walks_to_the_next_model_when_one_dies():
    answer, seen, provider = asyncio.run(rotation(lambda m: m == "m2"))
    assert answer == "hello from m2"
    assert seen == ["m1", "m2"]
    assert provider._picked == "m2"


def test_chat_prefers_the_first_healthy_model():
    answer, seen, provider = asyncio.run(rotation(lambda m: True))
    assert answer == "hello from m1" and seen == ["m1"]


def test_candidates_are_capped_and_ranked_by_preference():
    async def go():
        async def handler(request):
            return web.json_response({"data": [{"id": m} for m in ("m4", "m2", "m3", "m1")]})
        async def call(base, sess):
            p = ai_providers.Provider("Test", base, None, preferred=("m0", "m1", "m3", "m2", "m4"))
            return p, await p.candidates(sess)
        return await serve(handler, call)
    provider, candidates = asyncio.run(go())
    assert candidates == ["m1", "m3", "m2"]  # preference order, not the provider's order; max 3
    assert provider._picked == "m1"


def test_fixed_model_never_rotates():
    async def go():
        hits = []

        async def handler(request):
            hits.append(1)
            return web.json_response({"error": {"message": "gone"}}, status=404)
        async def call(base, sess):
            p = ai_providers.Provider("Test", base, None, model="fixed-model")
            try:
                await ai_providers.chat(sess, p, [{"role": "user", "content": "hi"}])
            except ai_providers.AIError:
                pass
            return hits
        return await serve(handler, call)
    assert len(asyncio.run(go())) == 1  # no point trying "the next model" — there isn't one
