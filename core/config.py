"""Typed application settings, loaded once from the environment (and `.env`).

Every setting the bot reads lives here: one place for names, defaults and validation. Invalid values
fail fast at startup with a readable error instead of crashing later inside a command.

Usage:
    from core.config import settings
    settings.prefix
"""
from __future__ import annotations

import os
from dataclasses import dataclass, fields

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class ConfigError(Exception):
    """Raised when one or more environment settings are invalid."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("Invalid configuration:\n" + "\n".join(f"  - {p}" for p in problems))


class _Reader:
    """Reads and validates environment values, collecting every problem instead of stopping at the first."""

    def __init__(self, env: dict[str, str]):
        self.env = env
        self.problems: list[str] = []

    def raw(self, name: str) -> str | None:
        value = self.env.get(name)
        if value is None:
            return None
        value = value.strip()
        return value or None

    def str(self, name: str, default: str | None = None) -> str | None:
        value = self.raw(name)
        return default if value is None else value

    def bool(self, name: str, default: bool) -> bool:
        value = self.raw(name)
        if value is None:
            return default
        if value.lower() in _TRUE:
            return True
        if value.lower() in _FALSE:
            return False
        self.problems.append(f"{name} must be true or false (got {value!r})")
        return default

    def snowflake(self, name: str) -> int | None:
        value = self.raw(name)
        if value is None:
            return None
        if not value.isdigit():
            self.problems.append(f"{name} must be a Discord ID (numbers only, got {value!r})")
            return None
        return int(value)

    def snowflakes(self, name: str) -> frozenset[int]:
        value = self.raw(name)
        if value is None:
            return frozenset()
        ids, bad = set(), []
        for part in value.split(","):
            part = part.strip()
            if not part:
                continue
            (ids.add(int(part)) if part.isdigit() else bad.append(part))
        if bad:
            self.problems.append(f"{name} must be comma-separated Discord IDs (invalid: {', '.join(bad)})")
        return frozenset(ids)

    def urls(self, name: str) -> str | None:
        """Comma-separated list of URLs (kept as a normalised comma-separated string)."""
        value = self.raw(name)
        if value is None:
            return None
        parts = [p.strip().rstrip("/") for p in value.split(",") if p.strip()]
        bad = [p for p in parts if not p.startswith(("http://", "https://"))]
        if bad:
            self.problems.append(f"{name} must be http(s) URLs separated by commas (invalid: {', '.join(bad)})")
            return None
        return ",".join(parts) or None

    def url(self, name: str, default: str | None = None) -> str | None:
        value = self.str(name, default)
        if value is not None and not value.startswith(("http://", "https://")):
            self.problems.append(f"{name} must start with http:// or https:// (got {value!r})")
            return default
        return value.rstrip("/") if value else value


def _clean_token(token: str | None) -> str | None:
    """People often paste the token as 'Bot xxx' or wrapped in quotes — accept both."""
    if not token:
        return token
    token = token.strip().strip("\"'").strip()
    if token.lower().startswith("bot "):
        token = token[4:].strip()
    return token or None


@dataclass(frozen=True, slots=True)
class Settings:
    # required
    discord_token: str | None
    owner_ids: frozenset[int]

    # general
    prefix: str
    status: str
    status_type: str
    db_path: str
    low_memory: bool
    database_url: str | None

    # server authorization
    auth_required: bool
    auth_log_channel: int | None
    auth_grandfather: bool
    support_url: str | None

    # monitoring
    error_log_channel: int | None
    log_file: str
    antinuke_dev_alerts: bool

    # integrations
    lastfm_api_key: str | None
    ai_api_key: str | None
    ai_base_url: str | None   # None = picked from the key (see core/ai_providers.py)
    ai_model: str | None      # None = best available model for the provider
    ocr_space_api_key: str | None

    # music
    music_enabled: bool
    lavalink_uri: str | None  # one or more comma-separated node URLs; empty = free public nodes
    lavalink_password: str

    # modules that are currently disabled (kept so they work if re-enabled)
    spotify_client_id: str | None
    spotify_client_secret: str | None
    twitch_client_id: str | None
    twitch_client_secret: str | None
    rsshub_url: str

    # network / proxy (bypasses datacenter or hosting IP rate limits)
    proxy: str | None = None

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        r = _Reader(dict(os.environ) if env is None else env)
        music_off = (r.raw("LAVALINK_URI") or "").lower() in {"off", "none", "false", "disabled", "0"}
        settings = cls(
            discord_token=_clean_token(r.str("DISCORD_TOKEN")),
            owner_ids=r.snowflakes("OWNER_IDS"),
            prefix=r.str("PREFIX", ","),
            status=r.str("STATUS", "veil utilities | ,help"),
            status_type=r.str("BOT_STATUS", r.str("STATUS_TYPE", "online")).lower(),
            db_path=r.str("DB_PATH", "veil.db"),
            low_memory=r.bool("LOW_MEMORY", False),
            database_url=r.str("DATABASE_URL"),
            auth_required=r.bool("AUTH_REQUIRED", True),
            auth_log_channel=r.snowflake("AUTH_LOG_CHANNEL"),
            auth_grandfather=r.bool("AUTH_GRANDFATHER", True),
            support_url=r.url("SUPPORT_URL"),
            error_log_channel=r.snowflake("ERROR_LOG_CHANNEL"),
            log_file=r.str("LOG_FILE", "veil.log"),
            antinuke_dev_alerts=r.bool("ANTINUKE_DEV_ALERTS", True),
            lastfm_api_key=r.str("LASTFM_API_KEY"),
            ai_api_key=r.str("AI_API_KEY"),
            ai_base_url=r.url("AI_BASE_URL"),
            ai_model=r.str("AI_MODEL"),
            ocr_space_api_key=r.str("OCR_SPACE_API_KEY"),
            music_enabled=r.bool("MUSIC_ENABLED", True) and not music_off,
            lavalink_uri=None if music_off else r.urls("LAVALINK_URI"),
            lavalink_password=r.str("LAVALINK_PASSWORD", "youshallnotpass"),
            spotify_client_id=r.str("SPOTIFY_CLIENT_ID"),
            spotify_client_secret=r.str("SPOTIFY_CLIENT_SECRET"),
            twitch_client_id=r.str("TWITCH_CLIENT_ID"),
            twitch_client_secret=r.str("TWITCH_CLIENT_SECRET"),
            rsshub_url=r.url("RSSHUB_URL", "https://rsshub.app"),
            proxy=r.str("DISCORD_PROXY") or r.str("HTTPS_PROXY") or r.str("HTTP_PROXY"),
        )
        if len(settings.prefix) > 10:
            r.problems.append("PREFIX can be at most 10 characters")
        if r.problems:
            raise ConfigError(r.problems)
        return settings

    def require_runtime(self) -> None:
        """Checks that only matter when actually connecting to Discord (not for tests or tooling)."""
        problems = []
        if not self.discord_token:
            problems.append("DISCORD_TOKEN is required — copy .env.example to .env and add your bot token")
        if not self.owner_ids:
            problems.append("OWNER_IDS is required — add your Discord user ID so you can manage the bot")
        if problems:
            raise ConfigError(problems)

    def summary(self) -> str:
        """Safe one-line-per-setting view for logs (secrets are masked)."""
        secret = {"discord_token", "ai_api_key", "lastfm_api_key", "ocr_space_api_key", "lavalink_password",
                  "spotify_client_secret", "twitch_client_secret", "database_url"}
        lines = []
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name in secret:
                value = "set" if value else "not set"
            elif isinstance(value, frozenset):
                value = f"{len(value)} id(s)"
            lines.append(f"{f.name}={value}")
        return ", ".join(lines)


_settings: Settings | None = None


def get_settings() -> Settings:
    """Load settings on first use and cache them."""
    global _settings
    if _settings is None:
        _settings = Settings.from_env()
    return _settings


def __getattr__(name: str):
    # `from core.config import settings` loads lazily, so main.py can catch ConfigError cleanly
    if name == "settings":
        return get_settings()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
