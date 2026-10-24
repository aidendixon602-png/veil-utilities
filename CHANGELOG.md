# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security

- Hidden commands are now developer-only at the **global check level**: for anyone else they
  behave as if they don't exist (silent `CommandNotFound`) — guessing a dev command's name
  reveals nothing. Previously they relied on per-cog checks only.
- DevTools cog failures are silent too, consistent with the other developer cogs.

### Added

- `tests/test_lockdown.py` — 10 tests covering the hidden-command gate, help-menu lookups
  (commands and groups), proof that no permission level can unlock developer tools, static
  checks that dev cogs always require bot-owner, and that crash replies never echo exception
  messages.

### Security (follow-up)

- `,help <hidden group>` no longer shows dev group descriptions to non-developers —
  it pretends the command doesn't exist, exactly like single commands already did.
  Help-menu hidden gating now also walks parent chains.
- Verified: permission levels (helper/mod/admin/overseer…) can never unlock developer
  commands — those check bot-owner identity (`OWNER_IDS`) and nothing else; no command can
  modify `OWNER_IDS`. Startup config logging masks all secrets; crash responses show only the
  error type + ID.

### Fixed

- Automod: partial-word entries (ending in `*`) no longer also run a literal-star regex
  (`a and b or c` operator-precedence bug).
- Automod: spam tracker no longer accumulates one dict entry per (guild, user) forever.
- Automod: filter values are range-checked (caps 30-100%, mentions 3-50, timeout max 28 days…);
  capped at 100 blocked words and 20 regex patterns.

## [1.0.0] - 2026-10-24

First stable release. 386 commands across 22 modules.

### Added

- **Zero-setup AI** — `AI_API_KEY` auto-detects Groq, Gemini, OpenRouter and OpenAI keys and
  picks the provider and free model automatically. With no key at all, `ask`/`summarize` fall back
  to a free no-signup provider, and `ocr` uses the shared OCR.space demo key.
- **Zero-setup music** — five free public Lavalink 4.2+ nodes with health checks, a connection
  watchdog and automatic failover. Set `LAVALINK_URI` to bring your own, or `MUSIC_ENABLED=false`
  to turn music off.
- **Spotify fallback** — `,sp` search works with no Spotify app via Deezer data.
- Full bug audit across every module: giveaway validation, ticket dedupe, reminder caps
  (25 per user), level XP caps, VoiceMaster bitrate limits, loop isolation so one failing
  background task can't take others down.

### Fixed

- Bot-only destructive actions (timeouts, unbans) are now counted by antinuke.
- Startup no longer depends on python-dotenv and gives a clear missing-package message.
- Run from any directory — the database and log paths no longer depend on the working directory.
- Feeds survive per-post failures instead of dying on the first bad item.

### Security

- SSRF-safe HTTP helper (`core/net.py`) blocks private and link-local addresses for every
  user-supplied URL; DNS rebinding is caught via a resolver trace hook.
- Downloads are size-capped (`read_capped`) everywhere media is fetched.
- Fake permissions can no longer be granted to `@everyone`, and `uperm` ranks are enforced.

## [0.1.0] - 2026-09-01

Initial public version: antinuke, antiraid, moderation, automod, VoiceMaster, permission levels,
levels, giveaways, tickets, feeds, AI, imaging and music.

[1.0.0]: https://github.com/aidendixon602-png/veil-utilities/releases/tag/v1.0.0
