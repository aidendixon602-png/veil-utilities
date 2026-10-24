"""AI tools: chat, image generation, text-to-speech, OCR.

Chat works with no setup (free Pollinations endpoint). Paste a Groq / Gemini / OpenRouter / OpenAI key into
AI_API_KEY for faster, better answers — the provider and model are detected automatically (core/ai_providers.py).
"""
from __future__ import annotations

import asyncio
import io
import logging
import random
import urllib.parse
from collections import defaultdict, deque

import aiohttp
import discord
from discord.ext import commands
from PIL import Image

from core import ai_providers
from core.config import settings
from core.utils import Colors, Context, UserError, shorten

log = logging.getLogger("veil.ai")

SYSTEM = ("You are veil utilities, a friendly, concise assistant inside a Discord server. "
          "Answer in Discord markdown and keep replies under 1800 characters unless asked for more. "
          "Never ping @everyone or @here.")
OCR_DEMO_KEY = "helloworld"  # OCR.space's public test key: works without signing up, but rate-limited
OCR_MAX_BYTES = 1024 * 1024  # the free OCR.space tier rejects files over 1 MB


def prepare_for_ocr(data: bytes) -> tuple[bytes, str]:
    """Convert to PNG/JPG under the OCR.space size limit (first frame of GIFs, downscaled if needed)."""
    im = Image.open(io.BytesIO(data))
    im.seek(0)
    im = im.convert("RGB")
    if max(im.size) > 2600:
        im.thumbnail((2600, 2600))
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    if buf.tell() <= OCR_MAX_BYTES:
        return buf.getvalue(), "PNG"
    for quality in (90, 80, 70, 55, 40):
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality)
        if buf.tell() <= OCR_MAX_BYTES:
            break
        im.thumbnail((max(1, int(im.width * 0.8)), max(1, int(im.height * 0.8))))
    return buf.getvalue(), "JPG"


class AI(commands.Cog):
    """Ask AI, generate images, text-to-speech and OCR"""
    emoji = "🤖"

    def __init__(self, bot):
        self.bot = bot
        self.provider = ai_providers.detect(settings.ai_api_key, settings.ai_base_url, settings.ai_model)
        # if the owner's provider fails (quota, outage) answer with the free endpoint instead of erroring
        self.fallback = ai_providers.detect(None) if settings.ai_api_key else None
        self.ocr_key = settings.ocr_space_api_key or OCR_DEMO_KEY
        self.history: dict[tuple[int, int], deque] = defaultdict(lambda: deque(maxlen=12))

    # ------------------------------------------------------------ chat
    async def complete(self, messages: list[dict]) -> str:
        try:
            return await ai_providers.chat(self.bot.session, self.provider, messages)
        except ai_providers.AIError as first:
            if self.fallback is None:
                raise UserError(f"AI unavailable: {first}") from None
            log.warning("AI provider failed (%s), using the free fallback", first)
            try:
                return await ai_providers.chat(self.bot.session, self.fallback, messages)
            except ai_providers.AIError:
                raise UserError(f"AI unavailable: {first}") from None

    @commands.group(aliases=["ai", "chat", "gpt"], invoke_without_command=True)
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def ask(self, ctx: Context, *, prompt: str):
        """Ask the AI anything — it remembers your last few messages in this channel"""
        key = (ctx.channel.id, ctx.author.id)
        hist = self.history[key]
        messages = [{"role": "system", "content": SYSTEM}, *hist, {"role": "user", "content": prompt[:4000]}]
        async with ctx.typing():
            answer = await self.complete(messages)
        hist.append({"role": "user", "content": prompt[:4000]})
        hist.append({"role": "assistant", "content": answer[:4000]})
        chunks = [answer[i:i + 1990] for i in range(0, len(answer), 1990)] or ["…"]
        for i, chunk in enumerate(chunks[:3]):
            await (ctx.reply if i == 0 else ctx.send)(chunk, allowed_mentions=discord.AllowedMentions.none())

    @ask.command(name="reset", aliases=["clear", "forget"])
    async def ask_reset(self, ctx: Context):
        """Forget your conversation in this channel"""
        self.history.pop((ctx.channel.id, ctx.author.id), None)
        await ctx.approve("Conversation cleared")

    @ask.command(name="provider", aliases=["model", "status"])
    async def ask_provider(self, ctx: Context):
        """Show which AI provider and model the bot is using"""
        model = await self.provider.pick_model(self.bot.session)
        extra = "\nFalls back to the free endpoint if it fails." if self.fallback else \
            "\nNo `AI_API_KEY` set — using the free endpoint (slower). A free Groq or Gemini key makes it faster."
        await ctx.neutral(f"🤖 **{self.provider.name}** · model `{model}`{extra}")

    @commands.command(aliases=["tldr"])
    @commands.guild_only()
    @commands.cooldown(1, 15, commands.BucketType.channel)
    async def summarize(self, ctx: Context, messages: int = 50):
        """AI summary of the recent chat"""
        messages = max(10, min(messages, 200))
        lines = []
        async for m in ctx.channel.history(limit=messages, before=ctx.message):
            if m.content and not m.author.bot:
                lines.append(f"{m.author.display_name}: {m.content}")
        if not lines:
            raise UserError("Nothing to summarize")
        transcript = "\n".join(reversed(lines))[-12000:]
        async with ctx.typing():
            answer = await self.complete([
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"Summarize this Discord conversation in 3-6 bullet points:\n\n{transcript}"}])
        await ctx.send(embed=discord.Embed(color=Colors.main, title=f"Summary of the last {messages} messages",
                                           description=shorten(answer, 4000)))

    # ------------------------------------------------------------ image generation
    @commands.command(aliases=["imagine", "generate", "img"])
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def imagegen(self, ctx: Context, *, prompt: str):
        """Generate an image from a prompt (free, no key needed)"""
        seed = random.randint(1, 2 ** 31)
        url = (f"https://image.pollinations.ai/prompt/{urllib.parse.quote(prompt[:500])}"
               f"?width=1024&height=1024&nologo=true&safe=true&seed={seed}")
        msg = await ctx.neutral(f"🎨 Generating **{shorten(prompt, 200)}**…")
        try:
            async with self.bot.session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as r:
                if r.status != 200 or not r.headers.get("Content-Type", "").startswith("image"):
                    raise UserError("The image service is busy, try again in a moment")
                data = await r.read()
        except asyncio.TimeoutError:
            raise UserError("Image generation timed out, try again") from None
        except aiohttp.ClientError:
            raise UserError("The image service is unreachable right now, try again later") from None
        finally:
            try:
                await msg.delete()
            except discord.HTTPException:
                pass
        e = discord.Embed(color=Colors.main, description=f"**Prompt:** {shorten(prompt, 1000)}")
        e.set_image(url="attachment://image.jpg")
        e.set_footer(text=f"Requested by {ctx.author} • seed {seed}")
        await ctx.send(embed=e, file=discord.File(io.BytesIO(data), "image.jpg"))

    # ------------------------------------------------------------ text to speech
    @commands.command(aliases=["speak"])
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def tts(self, ctx: Context, *, text: str):
        """Turn text into speech. Start with a language code for other languages: `tts es hola amigos`"""
        try:
            from gtts import gTTS, lang
        except ImportError:
            raise UserError("TTS needs the `gTTS` package installed") from None
        first, _, rest = text.partition(" ")
        language = "en"
        if rest and first.lower() in lang.tts_langs():
            language, text = first.lower(), rest

        def make() -> io.BytesIO:
            buf = io.BytesIO()
            gTTS(text[:600], lang=language).write_to_fp(buf)
            buf.seek(0)
            return buf

        async with ctx.typing():
            try:
                buf = await asyncio.to_thread(make)
            except Exception:
                raise UserError("Text-to-speech failed, try again") from None
        await ctx.send(file=discord.File(buf, "tts.mp3"))

    # ------------------------------------------------------------ OCR
    @commands.command(aliases=["readimage"])
    @commands.cooldown(1, 8, commands.BucketType.user)
    async def ocr(self, ctx: Context, *, image: str = None):
        """Read the text in an image (attach, reply or link)"""
        imaging = self.bot.get_cog("Imaging")
        if imaging is None:
            raise UserError("OCR needs the image module, which isn't loaded")
        data = await imaging.source(ctx, image)
        try:
            data, filetype = await asyncio.to_thread(prepare_for_ocr, data)
        except (OSError, ValueError, Image.DecompressionBombError):
            raise UserError("That doesn't look like an image I can read") from None
        body = aiohttp.FormData({"apikey": self.ocr_key, "OCREngine": "2", "scale": "true", "filetype": filetype})
        body.add_field("file", data, filename=f"image.{filetype.lower()}",
                       content_type="image/png" if filetype == "PNG" else "image/jpeg")
        async with ctx.typing():
            try:
                async with self.bot.session.post("https://api.ocr.space/parse/image", data=body,
                                                 timeout=aiohttp.ClientTimeout(total=60)) as r:
                    res = await r.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                raise UserError("The OCR service didn't respond, try again in a moment") from None
        if not isinstance(res, dict):
            raise UserError("The OCR service is busy, try again in a minute")
        if res.get("IsErroredOnProcessing"):
            err = res.get("ErrorMessage")
            err = str(err[0] if isinstance(err, list) and err else err)
            if self.ocr_key == OCR_DEMO_KEY:
                raise UserError("The shared free OCR key is busy right now, try again in a minute "
                                f"(the bot owner can add a free personal `OCR_SPACE_API_KEY`). Details: {shorten(err, 150)}")
            raise UserError(f"OCR failed: {shorten(err, 200)}")
        parsed = "\n".join(p.get("ParsedText", "") for p in res.get("ParsedResults") or []).strip()
        if not parsed:
            raise UserError("I couldn't find any text in that image")
        parsed = parsed.replace("```", "'''")
        await ctx.send(embed=discord.Embed(color=Colors.main, title="Text found",
                                           description=f"```{shorten(parsed, 3900)}```"))


async def setup(bot):
    await bot.add_cog(AI(bot))
