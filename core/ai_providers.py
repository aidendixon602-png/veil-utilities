"""AI chat providers (all OpenAI-compatible).

Paste any of these keys into AI_API_KEY and the bot figures out the rest — no base URL or model needed:

    gsk_...      Groq        free, no card      https://console.groq.com/keys
    AIza...      Google      free tier          https://aistudio.google.com/apikey
    sk-or-...    OpenRouter  free models        https://openrouter.ai/keys
    sk-...       OpenAI      paid               https://platform.openai.com/api-keys

With no key at all, chat uses Pollinations' free anonymous endpoint (slower, rate-limited, but works out of the
box). If a keyed provider fails (quota used up, outage), the bot falls back to Pollinations for that request.

Model names change every few months, so instead of hard-coding one the bot asks the provider for its model list
once and picks the first available entry from PREFERRED (AI_MODEL overrides this).
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

import aiohttp

log = logging.getLogger("veil.ai")

FREE_BASE = "https://text.pollinations.ai/openai"
FREE_MODEL = "openai"


@dataclass
class Provider:
    name: str
    base: str
    key: str | None
    preferred: tuple[str, ...] = ()
    model: str | None = None            # fixed model (AI_MODEL or a provider without /models)
    extra: dict = field(default_factory=dict)  # provider-specific request fields
    _picked: str | None = None
    _picked_at: float = 0.0

    @property
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.key}"} if self.key else {}

    async def pick_model(self, session: aiohttp.ClientSession) -> str:
        if self.model:
            return self.model
        if self._picked and time.time() - self._picked_at < 6 * 3600:
            return self._picked
        available: list[str] = []
        try:
            async with session.get(f"{self.base}/models", headers=self.headers,
                                   timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                    available = [str(m.get("id", "")).removeprefix("models/") for m in data.get("data", [])]
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            pass
        choice = choose_model(available, self.preferred, free_only=self.name == "OpenRouter")
        self._picked, self._picked_at = choice, time.time()
        log.info("AI provider %s using model %s", self.name, choice)
        return choice


def choose_model(available: list[str], preferred: tuple[str, ...], free_only: bool = False) -> str:
    """First preferred model the provider actually offers; otherwise a sensible available one."""
    if available:
        for want in preferred:
            if want in available:
                return want
        pool = [m for m in available if not free_only or m.endswith(":free")]
        chat = [m for m in pool if not any(x in m for x in ("embed", "whisper", "tts", "guard", "image",
                                                             "audio", "vision-preview", "moderation"))]
        if chat:
            return chat[0]
    return preferred[0] if preferred else FREE_MODEL


# values the old .env.example shipped with: treat them as "not set" so key auto-detection still works
LEGACY_BASES = {"https://api.openai.com/v1"}
LEGACY_MODELS = {"gpt-4o-mini"}


def detect(key: str | None, base: str | None = None, model: str | None = None) -> Provider:
    """Build the provider from AI_API_KEY (+ optional AI_BASE_URL / AI_MODEL overrides)."""
    base = None if not base or base.rstrip("/") in LEGACY_BASES else base.rstrip("/")
    model = None if not model or model in LEGACY_MODELS else model
    if base:  # explicit custom endpoint (Ollama, LM Studio, Together, DeepSeek, ...); key optional
        return Provider("Custom", base, key, preferred=(model,) if model else (), model=model)
    if not key:
        return Provider("Pollinations (free)", FREE_BASE, None, model=model or FREE_MODEL)
    if key.startswith("gsk_"):
        return Provider("Groq", "https://api.groq.com/openai/v1", key, model=model,
                        preferred=("openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b",
                                   "llama-3.3-70b-versatile", "llama-3.1-8b-instant"),
                        extra={"reasoning_effort": "low"})
    if key.startswith("AIza"):
        return Provider("Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai", key, model=model,
                        preferred=("gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite",
                                   "gemini-3.1-flash-lite", "gemini-2.5-flash", "gemini-2.5-flash-lite"),
                        extra={"reasoning_effort": "low"})
    if key.startswith("sk-or-"):
        return Provider("OpenRouter", "https://openrouter.ai/api/v1", key, model=model,
                        preferred=("openai/gpt-oss-120b:free", "openai/gpt-oss-20b:free",
                                   "deepseek/deepseek-chat-v3.1:free", "meta-llama/llama-3.3-70b-instruct:free"))
    return Provider("OpenAI", "https://api.openai.com/v1", key, model=model,
                    preferred=("gpt-5-mini", "gpt-4.1-mini", "gpt-4o-mini"))


class AIError(Exception):
    pass


async def chat(session: aiohttp.ClientSession, provider: Provider, messages: list[dict],
               max_tokens: int = 1200) -> str:
    """One chat completion. Raises AIError with a user-safe message."""
    model = await provider.pick_model(session)
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, **provider.extra}
    try:
        async with session.post(f"{provider.base}/chat/completions", json=body, headers=provider.headers,
                                timeout=aiohttp.ClientTimeout(total=90)) as r:
            try:
                data = await r.json(content_type=None)
            except ValueError:
                data = None
            status = r.status
        if isinstance(data, list) and data and isinstance(data[0], dict):  # Gemini wraps errors in a list
            data = data[0]
    except asyncio.TimeoutError:  # (not the builtin TimeoutError on Python 3.10)
        raise AIError(f"{provider.name} took too long to answer") from None
    except aiohttp.ClientError:
        raise AIError(f"couldn't reach {provider.name}") from None
    if status == 400 and provider.extra and isinstance(data, dict) and "reasoning" in str(data).lower():
        provider.extra = {}  # model doesn't take reasoning_effort: retry once without it
        return await chat(session, provider, messages, max_tokens)
    if status in (401, 403):
        raise AIError(f"{provider.name} rejected the API key (check AI_API_KEY)")
    if status == 404 and not provider.model:
        provider._picked = None  # model was retired: pick again next time
    if status == 429:
        raise AIError(f"{provider.name} rate limit reached, try again in a minute")
    if status >= 400 or not isinstance(data, dict):
        msg = ""
        if isinstance(data, dict):
            err = data.get("error")
            msg = err.get("message", "") if isinstance(err, dict) else str(err or "")
        if "api key" in msg.lower() or "api_key" in msg.lower():
            raise AIError(f"{provider.name} rejected the API key (check AI_API_KEY)")
        raise AIError(f"{provider.name} error ({status}){': ' + msg[:200] if msg else ''}")
    try:
        content = data["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError, TypeError, AttributeError):
        raise AIError(f"{provider.name} sent an unexpected response") from None
    content = content.strip()
    if not content:
        raise AIError(f"{provider.name} returned an empty answer, try rephrasing")
    return content
