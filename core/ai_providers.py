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
    _candidates: list[str] = field(default_factory=list)

    @property
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.key}"} if self.key else {}

    async def candidates(self, session: aiohttp.ClientSession) -> list[str]:
        """Ranked models to try in order. A fixed AI_MODEL / keyless endpoint yields exactly one."""
        if self.model:
            return [self.model]
        if self._candidates and time.time() - self._picked_at < 6 * 3600:
            return self._candidates
        available: list[str] = []
        try:
            async with session.get(f"{self.base}/models", headers=self.headers,
                                   timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                    available = [str(m.get("id", "")).removeprefix("models/") for m in data.get("data", [])]
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            pass
        free_only = self.name == "OpenRouter"
        ranked = [m for m in self.preferred if m in available][:3]
        if not ranked:
            ranked = [choose_model(available, self.preferred, free_only=free_only)]
        self._candidates, self._picked = ranked, ranked[0]
        self._picked_at = time.time()
        log.info("AI provider %s using models %s", self.name, ranked)
        return self._candidates

    async def pick_model(self, session: aiohttp.ClientSession) -> str:
        return (await self.candidates(session))[0]


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
                                   "llama-3.3-70b-versatile", "groq/compound", "llama-3.1-8b-instant",
                                   "meta-llama/llama-4-scout-17b-16e-instruct"),
                        extra={"reasoning_effort": "low"})
    if key.startswith("AIza"):
        return Provider("Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai", key, model=model,
                        preferred=("gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite",
                                   "gemini-3.1-flash-lite", "gemini-3-flash", "gemini-2.5-flash",
                                   "gemini-2.5-flash-lite"),
                        extra={"reasoning_effort": "low"})
    if key.startswith("sk-or-"):
        return Provider("OpenRouter", "https://openrouter.ai/api/v1", key, model=model,
                        preferred=("openai/gpt-oss-120b:free", "openai/gpt-oss-20b:free",
                                   "deepseek/deepseek-chat-v3.1:free", "meta-llama/llama-3.3-70b-instruct:free",
                                   "qwen/qwen3-32b:free", "google/gemma-3-27b-it:free"))
    return Provider("OpenAI", "https://api.openai.com/v1", key, model=model,
                    preferred=("gpt-5-mini", "gpt-4.1-mini", "gpt-4o-mini"))


class AIError(Exception):
    pass


class _Retry(Exception):
    """Internal: this model failed in a way another candidate model might survive."""

    def __init__(self, msg: str, status: int = 0, why: str = ""):
        super().__init__(msg)
        self.status, self.why = status, why


RETRYABLE = {404, 408, 409, 429, 500, 502, 503, 504}  # model retired / busy / rate limited


async def _complete(session: aiohttp.ClientSession, provider: Provider, model: str,
                    messages: list[dict], max_tokens: int) -> str:
    """One attempt against one model. Raises _Retry when the NEXT model might work instead."""
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
        raise _Retry(f"model {model} took too long", why="timeout") from None
    except aiohttp.ClientError as e:
        raise AIError(f"couldn't reach {provider.name}") from e
    if status == 400 and provider.extra and isinstance(data, dict) and "reasoning" in str(data).lower():
        provider.extra = {}  # model doesn't take reasoning_effort: retry once without it
        return await _complete(session, provider, model, messages, max_tokens)
    if status in (401, 403):
        raise AIError(f"{provider.name} rejected the API key (check AI_API_KEY)")
    if status in RETRYABLE:
        raise _Retry(f"model {model} failed ({status})", status=status)
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
        raise _Retry(f"model {model} returned nothing", why="empty")
    return content


async def chat(session: aiohttp.ClientSession, provider: Provider, messages: list[dict],
               max_tokens: int = 1200) -> str:
    """One chat completion, walking the provider's ranked models. Raises AIError with a user-safe message."""
    last: _Retry | None = None
    for model in await provider.candidates(session):
        try:
            answer = await _complete(session, provider, model, messages, max_tokens)
            provider._picked = model  # remember what worked
            return answer
        except _Retry as e:
            last = e
            continue
    # everything failed: forget the ranking so the next request re-queries /models,
    # and surface the friendlier wording of the last failure
    provider._candidates = []
    if last:
        if last.why == "timeout":
            raise AIError(f"{provider.name} took too long to answer") from None
        if last.why == "empty":
            raise AIError(f"{provider.name} returned an empty answer, try rephrasing") from None
        if last.status == 429:
            raise AIError(f"{provider.name} rate limit reached, try again in a minute") from None
        if last.status:
            raise AIError(f"{provider.name} error ({last.status})") from None
    raise AIError(f"{provider.name} failed on every available model") from None
