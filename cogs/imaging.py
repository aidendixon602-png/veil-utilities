"""Image editing (Pillow) — works on attachments, replies, links, emojis or avatars. GIFs keep their animation."""
from __future__ import annotations

import asyncio
import io
import math
import os
import random
import re
from functools import partial

import discord
from discord.ext import commands
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps, ImageSequence

from core.utils import Context, UserError

MAX_BYTES = 8 * 1024 * 1024
LOW_MEMORY = os.getenv("LOW_MEMORY", "false").lower() == "true"
MAX_FRAMES = 40 if LOW_MEMORY else 80
MAX_SIDE = 768 if LOW_MEMORY else 1024
BUNDLED_FONT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts", "caption.ttf")
URL = re.compile(r"https?://\S+\.(?:png|jpe?g|gif|webp)(?:\?\S*)?", re.I)
EMOJI = re.compile(r"<(a?):\w+:(\d+)>")

FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/impact.ttf",
]


def font(size: int) -> ImageFont.FreeTypeFont:
    for p in [BUNDLED_FONT, *FONT_PATHS]:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def wrap_px(text: str, f, max_w: int, draw: ImageDraw.ImageDraw) -> list[str]:
    """Greedy word wrap by actual pixel width."""
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=f) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


# ---------------------------------------------------------------- per-frame effects (RGBA in, RGBA out)
def fx_blur(im, radius=6):
    return im.filter(ImageFilter.GaussianBlur(radius))


def fx_deepfry(im):
    rgb = im.convert("RGB")
    rgb = ImageEnhance.Color(rgb).enhance(4.5)
    rgb = ImageEnhance.Contrast(rgb).enhance(2.6)
    rgb = ImageEnhance.Sharpness(rgb).enhance(25)
    noise = Image.effect_noise(rgb.size, 60).convert("RGB")
    rgb = Image.blend(rgb, noise, 0.12)
    rgb = ImageOps.posterize(rgb, 4)
    for q in (8, 4):
        buf = io.BytesIO()
        rgb.save(buf, "JPEG", quality=q)
        rgb = Image.open(io.BytesIO(buf.getvalue())).convert("RGB")
    buf = io.BytesIO()
    rgb.save(buf, "JPEG", quality=5)
    out = Image.open(io.BytesIO(buf.getvalue())).convert("RGBA")
    r, g, b, a = out.split()
    return Image.merge("RGBA", (r.point(lambda x: min(255, x * 1.25)), g, b.point(lambda x: x * 0.7), im.getchannel("A")))


def fx_invert(im):
    r, g, b, a = im.split()
    return Image.merge("RGBA", (*ImageOps.invert(Image.merge("RGB", (r, g, b))).split(), a))


def fx_grayscale(im):
    return ImageOps.grayscale(im).convert("RGBA")


def fx_sepia(im):
    g = ImageOps.grayscale(im)
    return ImageOps.colorize(g, "#2e1a05", "#f5deb3").convert("RGBA")


def fx_pixelate(im, size=12):
    w, h = im.size
    small = im.resize((max(1, w // size), max(1, h // size)), Image.NEAREST)
    return small.resize((w, h), Image.NEAREST)


def fx_jpeg(im, quality=3):
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=quality)
    return Image.open(io.BytesIO(buf.getvalue())).convert("RGBA")


def fx_posterize(im):
    return ImageOps.posterize(im.convert("RGB"), 2).convert("RGBA")


def fx_edges(im):
    return im.convert("RGB").filter(ImageFilter.FIND_EDGES).convert("RGBA")


def fx_emboss(im):
    return im.convert("RGB").filter(ImageFilter.EMBOSS).convert("RGBA")


def fx_sharpen(im):
    return ImageEnhance.Sharpness(im).enhance(6)


def fx_flip(im):
    return ImageOps.flip(im)


def fx_mirror(im):
    return ImageOps.mirror(im)


def fx_rotate(im, degrees=90):
    return im.rotate(-degrees, expand=True)


def fx_circle(im):
    side = min(im.size)
    im = ImageOps.fit(im, (side, side))
    mask = Image.new("L", (side, side), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, side, side), fill=255)
    im.putalpha(mask)
    return im


def fx_bright(im, factor=1.6):
    return ImageEnhance.Brightness(im).enhance(factor)


def fx_saturate(im, factor=3.0):
    return ImageEnhance.Color(im).enhance(factor)


def fx_swirlish(im):  # cheap "wave" warp
    w, h = im.size
    out = Image.new("RGBA", im.size)
    for y in range(h):
        shift = int(10 * math.sin(y / 12))
        out.paste(im.crop((0, y, w, y + 1)), (shift, y))
    return out


def fx_caption(im, text: str):
    w, h = im.size
    size = max(18, w // 11)
    f = font(size)
    measure = ImageDraw.Draw(im)
    lines = wrap_px(text, f, int(w * 0.92), measure)[:6] or [" "]
    line_h = int(size * 1.25)
    pad = size // 2
    bar = line_h * len(lines) + pad * 2
    canvas = Image.new("RGBA", (w, h + bar), "white")
    canvas.paste(im, (0, bar), im)
    d = ImageDraw.Draw(canvas)
    for i, line in enumerate(lines):
        tw = d.textlength(line, font=f)
        d.text(((w - tw) / 2, pad + i * line_h), line, font=f, fill="black")
    return canvas


def fx_meme(im, text: str):
    top, _, bottom = text.partition("|")
    w, h = im.size
    size = max(18, w // 9)
    f = font(size)
    d = ImageDraw.Draw(im)

    def draw(block, y_from_top):
        lines = wrap_px(block.strip().upper(), f, int(w * 0.94), d)[:3]
        line_h = int(size * 1.15)
        total = line_h * len(lines)
        y = 8 if y_from_top else h - total - 12
        for line in lines:
            tw = d.textlength(line, font=f)
            d.text(((w - tw) / 2, y), line, font=f, fill="white", stroke_width=max(2, size // 12), stroke_fill="black")
            y += line_h

    if top.strip():
        draw(top, True)
    if bottom.strip():
        draw(bottom, False)
    return im


def render(data: bytes, effect, name: str) -> tuple[io.BytesIO, str]:
    src = Image.open(io.BytesIO(data))
    animated = getattr(src, "is_animated", False) and src.n_frames > 1
    frames, durations = [], []
    for i, frame in enumerate(ImageSequence.Iterator(src)):
        if i >= MAX_FRAMES:
            break
        f = frame.convert("RGBA")
        f.thumbnail((MAX_SIDE, MAX_SIDE))
        frames.append(effect(f))
        durations.append(frame.info.get("duration", 60))
        if not animated:
            break
    out = io.BytesIO()
    if animated:
        frames[0].save(out, "GIF", save_all=True, append_images=frames[1:], duration=durations, loop=0, disposal=2)
        ext = "gif"
    else:
        frames[0].save(out, "PNG", optimize=True)
        ext = "png"
    out.seek(0)
    return out, f"{name}.{ext}"


class Imaging(commands.Cog):
    """Edit images, GIFs, avatars and emojis"""
    emoji = "🖼️"

    def __init__(self, bot):
        self.bot = bot

    async def fetch(self, url: str) -> bytes:
        async with self.bot.session.get(url) as r:
            if r.status != 200:
                raise UserError("I couldn't download that image")
            if int(r.headers.get("Content-Length") or 0) > MAX_BYTES:
                raise UserError("That image is too big (8MB max)")
            data = await r.content.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise UserError("That image is too big (8MB max)")
        return data

    async def source(self, ctx: Context, arg: str | None) -> bytes:
        """Find an image: attachment > argument (user/emoji/link) > replied message > author's avatar."""
        msgs = [ctx.message]
        if ctx.message.reference and isinstance(ctx.message.reference.resolved, discord.Message):
            msgs.append(ctx.message.reference.resolved)
        for a in ctx.message.attachments:
            if (a.content_type or "").startswith("image"):
                return await self.fetch(a.url)
        if arg:
            if m := EMOJI.search(arg):
                return await self.fetch(f"https://cdn.discordapp.com/emojis/{m.group(2)}.{'gif' if m.group(1) else 'png'}")
            if m := URL.search(arg):
                return await self.fetch(m.group(0))
            try:
                user = await commands.UserConverter().convert(ctx, arg)
                return await self.fetch(user.display_avatar.with_size(512).url)
            except commands.BadArgument:
                pass
        for msg in msgs[1:]:
            for a in msg.attachments:
                if (a.content_type or "").startswith("image"):
                    return await self.fetch(a.url)
            for e in msg.embeds:
                url = (e.image and e.image.url) or (e.thumbnail and e.thumbnail.url)
                if url:
                    return await self.fetch(url)
            if m := URL.search(msg.content):
                return await self.fetch(m.group(0))
            if m := EMOJI.search(msg.content):
                return await self.fetch(f"https://cdn.discordapp.com/emojis/{m.group(2)}.{'gif' if m.group(1) else 'png'}")
        return await self.fetch(ctx.author.display_avatar.with_size(512).url)

    async def apply(self, ctx: Context, effect, name: str, arg: str | None = None):
        async with ctx.typing():
            data = await self.source(ctx, arg)
            try:
                buf, filename = await asyncio.to_thread(render, data, effect, name)
            except (OSError, ValueError) as e:
                raise UserError(f"I couldn't process that image ({e.__class__.__name__})")
        await ctx.send(file=discord.File(buf, filename))

    # ------------------------------------------------------------ simple effects
    def _simple(name, fn, doc):  # noqa: N805
        @commands.command(name=name, help=doc + " — attach, reply to, or mention an image/user/emoji")
        @commands.cooldown(1, 4, commands.BucketType.user)
        async def cmd(self, ctx: Context, *, image: str = None):
            await self.apply(ctx, fn, name, image)
        return cmd

    blur = _simple("blur", fx_blur, "Blur an image")
    deepfry = _simple("deepfry", fx_deepfry, "Deep-fry an image")
    invert = _simple("invert", fx_invert, "Invert colors")
    grayscale = _simple("grayscale", fx_grayscale, "Black and white")
    sepia = _simple("sepia", fx_sepia, "Old-photo sepia tone")
    pixelate = _simple("pixelate", fx_pixelate, "Pixelate an image")
    jpeg = _simple("jpeg", fx_jpeg, "Destroy it with JPEG compression")
    posterize = _simple("posterize", fx_posterize, "Posterize colors")
    edges = _simple("edges", fx_edges, "Edge detection")
    emboss = _simple("emboss", fx_emboss, "Emboss effect")
    sharpen = _simple("sharpen", fx_sharpen, "Over-sharpen")
    upsidedown = _simple("upsidedown", fx_flip, "Flip upside down")
    mirror = _simple("mirror", fx_mirror, "Mirror horizontally")
    rotate = _simple("rotate", fx_rotate, "Rotate 90°")
    circle = _simple("circle", fx_circle, "Crop into a circle")
    brighten = _simple("brighten", fx_bright, "Brighten")
    saturate = _simple("saturate", fx_saturate, "Crank the saturation")
    warp = _simple("warp", fx_swirlish, "Wavy distortion")
    del _simple

    # ------------------------------------------------------------ text effects
    @commands.command(aliases=["cap"])
    @commands.cooldown(1, 4, commands.BucketType.user)
    async def caption(self, ctx: Context, *, text: str):
        """Add a white caption bar above an image (attach or reply to one)"""
        await self.apply(ctx, partial(fx_caption, text=text[:300]), "caption")

    @commands.command()
    @commands.cooldown(1, 4, commands.BucketType.user)
    async def meme(self, ctx: Context, *, text: str):
        """Impact-style meme text: `meme top text | bottom text` (attach or reply to an image)"""
        await self.apply(ctx, partial(fx_meme, text=text[:200]), "meme")

    @commands.command(aliases=["randomfx"])
    @commands.cooldown(1, 4, commands.BucketType.user)
    async def chaos(self, ctx: Context, *, image: str = None):
        """Stack 3 random effects"""
        pool = [fx_deepfry, fx_invert, fx_pixelate, fx_jpeg, fx_posterize, fx_mirror, fx_saturate, fx_swirlish, fx_emboss]
        chosen = random.sample(pool, 3)

        def stack(im):
            for f in chosen:
                im = f(im)
            return im
        await self.apply(ctx, stack, "chaos", image)


async def setup(bot):
    await bot.add_cog(Imaging(bot))
