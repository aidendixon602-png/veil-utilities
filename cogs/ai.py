"""AI tools: chat, image generation, text-to-speech, OCR."""
from __future__ import annotations

import asyncio
import io
import os
import random
import urllib.parse
from collections import defaultdict, deque

import aiohttp
import discord
from discord.ext import commands

from core.utils import Colors, Context, UserError, shorten

SYSTEM = ("You are veil utilities, a friendly, concise assistant inside a Discord server. "
          "Answer in Discord markdown and keep replies under 1800 characters unless asked for more. "
          "Never ping @everyone or @here.")


class AI(commands.Cog):
    """Ask AI, generate images, text-to-speech and OCR"""
    emoji = "🤖"

    def __init__(self, bot):
        self.bot = bot
        self.key = os.getenv("AI_API_KEY")
        self.base = os.getenv("AI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        self.model = os.getenv("AI_MODEL", "gpt-4o-mini")
        self.ocr_key = os.getenv("OCR_SPACE_API_KEY")
        self.history: dict[tuple[int, int], deque] = defaultdict(lambda: deque(maxlen=12))

    # ------------------------------------------------------------ chat
    async def complete(self, messages: list[dict]) -> str:
        if not self.key:
            raise UserError("AI chat isn't configured — the bot owner needs to set `AI_API_KEY`")
        async with self.bot.session.post(
                f"{self.base}/chat/completions",
                headers={"Authorization": f"Bearer {self.key}"},
                json={"model": self.model, "messages": messages, "max_tokens": 700, "temperature": 0.7},
                timeout=aiohttp.ClientTimeout(total=60)) as r:
            data = await r.json(content_type=None)
        if r.status >= 400:
            err = data.get("error", {}) if isinstance(data, dict) else {}
            raise UserError(f"AI provider error: {err.get('message', r.status) if isinstance(err, dict) else err}")
        return data["choices"][0]["message"]["content"].strip()

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
        hist.append({"role": "assistant", "content": answer})
        chunks = [answer[i:i + 1990] for i in range(0, len(answer), 1990)] or ["…"]
        for i, chunk in enumerate(chunks[:3]):
            await (ctx.reply if i == 0 else ctx.send)(chunk, allowed_mentions=discord.AllowedMentions.none())

    @ask.command(name="reset", aliases=["clear", "forget"])
    async def ask_reset(self, ctx: Context):
        """Forget your conversation in this channel"""
        self.history.pop((ctx.channel.id, ctx.author.id), None)
        await ctx.approve("Conversation cleared")

    @commands.command(aliases=["tldr"])
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
            raise UserError("Image generation timed out, try again")
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
            raise UserError("TTS needs the `gTTS` package installed")
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
                raise UserError("Text-to-speech failed, try again")
        await ctx.send(file=discord.File(buf, "tts.mp3"))

    # ------------------------------------------------------------ OCR
    @commands.command(aliases=["readimage"])
    @commands.cooldown(1, 8, commands.BucketType.user)
    async def ocr(self, ctx: Context, *, image: str = None):
        """Read the text in an image (attach, reply or link)"""
        if not self.ocr_key:
            raise UserError("OCR isn't configured — the bot owner needs to set `OCR_SPACE_API_KEY` (free at ocr.space)")
        imaging = self.bot.get_cog("Imaging")
        data = await imaging.source(ctx, image)
        form = {"apikey": self.ocr_key, "OCREngine": "2", "scale": "true"}
        body = aiohttp.FormData(form)
        body.add_field("file", data, filename="image.png", content_type="image/png")
        async with ctx.typing():
            async with self.bot.session.post("https://api.ocr.space/parse/image", data=body, timeout=aiohttp.ClientTimeout(total=60)) as r:
                res = await r.json(content_type=None)
        parsed = (res.get("ParsedResults") or [{}])[0].get("ParsedText", "").strip() if isinstance(res, dict) else ""
        if not parsed:
            raise UserError("I couldn't find any text in that image")
        await ctx.send(embed=discord.Embed(color=Colors.main, title="Text found", description=f"```{shorten(parsed, 3900)}```"))


async def setup(bot):
    await bot.add_cog(AI(bot))
