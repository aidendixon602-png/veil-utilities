# Configuration reference

[README](../README.md) · [Operations](OPERATIONS.md) · [Hosting](HOSTING.md) · [Antinuke](ANTINUKE.md)

Copy [`.env.example`](../.env.example) to `.env`; the process environment takes precedence. Settings are parsed by [`core/config.py`](../core/config.py). Empty optional values generally use their defaults. Booleans accept `true`/`false`, `yes`/`no`, `on`/`off` or `1`/`0`. Keep tokens, passwords, database URLs and `.env` private.

`DISCORD_TOKEN` is required to connect. Tests and documentation generation work offline with a temporary placeholder; never use a real token in tests or issue reports.

## Core settings

| Variable | Default | Purpose |
| --- | --- | --- |
| `DISCORD_TOKEN` | unset | Discord bot token; required at runtime. |
| `OWNER_IDS` | empty | Additional developer Discord user IDs, comma-separated. Application owner/team discovery is also attempted at startup. Include everyone who must receive alerts in case discovery fails. |
| `PREFIX` | `,` | Default command prefix, up to 10 characters; server and personal prefixes can override it. |
| `BOT_STATUS` | `online` | Presence status (`online`, `dnd`, `idle`, `invisible`; `STATUS_TYPE` is a legacy fallback). |
| `DB_PATH` | `veil.db` | SQLite database path, ignored when `DATABASE_URL` is set. |
| `DATABASE_URL` | unset | PostgreSQL connection URL. Treat the complete URL as a secret. Tables are initialized at startup; existing SQLite data is not copied. |
| `LOW_MEMORY` | automatic | When unset, enables a low-memory profile on detected containers at or below 768 MB; explicit `true`/`false` overrides detection. The example explicitly sets `false`. |
| `LOG_FILE` | `veil.log` | Rotating local log path. |
| `LOG_COLOR` | `1` | Set to `0` to disable console color. |
| `DISCORD_PROXY` | unset | Proxy for Discord; falls back to `HTTPS_PROXY`, then `HTTP_PROXY`. Proxy URLs may contain credentials. |

## Authorization and developer alerts

| Variable | Default | Purpose |
| --- | --- | --- |
| `AUTH_REQUIRED` | `true` | Restrict bot use to authorized servers. Developer-owned servers are authorized automatically. |
| `AUTH_GRANDFATHER` | `true` | On an uninitialized authorization database with no existing authorizations, enroll currently joined servers once. On first upgrade, an intentionally empty old list is indistinguishable from a new installation: set this to `false` **before startup** if enrollment is not desired. |
| `PRIVATE_MODE` | `true` | Restrict command access to developers, server owners and users with assigned permission levels; set `false` for public commands. Other permission checks still apply. |
| `AUTH_LOG_CHANNEL` | unset | Optional developer authorization log channel ID. |
| `ERROR_LOG_CHANNEL` | unset | Optional developer error log channel ID. Without an override, Veil attempts to create private developer channels in a suitable server; creation is not guaranteed. |
| `ANTINUKE_DEV_ALERTS` | `true` | Queue developer DMs for real antinuke alerts. Turning this off does not disable antinuke incident recording. |
| `TEST_GUILD_ID` | unset | Optional server ID to pin developer test commands. Those tests are destructive to their test resources; use a disposable server. |
| `SUPPORT_URL` | unset | Optional HTTP(S) support link included in unauthorized-server notices. |

See [developer notifications](OPERATIONS.md#developer-notifications) for recipients, retries and limitations. Developer access includes powerful commands; protect the Discord application owner and team accounts.

## Optional integrations

| Variable | Default | Purpose |
| --- | --- | --- |
| `AI_API_KEY` | unset | Optional AI provider key. |
| `AI_BASE_URL`, `AI_MODEL` | unset | Optional HTTP(S) provider endpoint and model overrides. |
| `LASTFM_API_KEY`, `OCR_SPACE_API_KEY` | unset | Optional keys for Last.fm and OCR integrations. |
| `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET` | unset | Optional Spotify integration credentials. |
| `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET` | unset | Optional Twitch integration credentials. |
| `RSSHUB_URL` | `https://rsshub.app` | HTTP(S) RSSHub endpoint used by supported feeds. |
| `RSSHUB_ROUTE_INSTAGRAM`, `RSSHUB_ROUTE_TIKTOK`, `RSSHUB_ROUTE_X` | built-in routes | Optional route templates containing `{user}` for the respective RSSHub feed. |
| `MUSIC_ENABLED` | `true` | Enable or disable music integration. |
| `LAVALINK_URI` | unset | Comma-separated HTTP(S) Lavalink endpoints. When unset, the bot attempts configured public nodes. `off`, `none`, `false`, `disabled` or `0` disables music. |
| `LAVALINK_PASSWORD` | Lavalink's public example default | Authentication for configured nodes. Set a unique secret for a private node; public nodes and external services may be unavailable. |
| `LAVALINK_BACKUPS` | `true` | Keep public nodes available as backups when custom endpoints are configured. |

Network-dependent integrations can fail or change independently of Veil; no provider availability is guaranteed.

## Optional dashboard

The dashboard remains disabled until you configure a public HTTPS origin and Discord OAuth. It will not start an unauthenticated local listener. Discord server owners can manage only authorized servers they own; recognized bot developers can access all joined servers and manage the explicit server authorization list. Access is checked again on every request. The bot must be running, and a correctly configured HTTPS reverse proxy or hosting provider must route traffic to it.

| Variable | Default | Purpose |
| --- | --- | --- |
| `DASHBOARD_ENABLED` | `false` | Start the dashboard. |
| `DASHBOARD_HOST` | `127.0.0.1` | Backend bind address. Use a private interface for the TLS proxy; never expose the backend HTTP port publicly. |
| `DASHBOARD_PORT` | `PORT`, or `8080` | Listening port (1–65535). |
| `DASHBOARD_URL` | unset | Public HTTPS origin, with no path, query or credentials. Configure its `/callback` as a Discord OAuth redirect. |
| `DISCORD_CLIENT_ID` | detected from bot | Optional OAuth client ID override. |
| `DISCORD_CLIENT_SECRET` | unset | Discord OAuth client secret; required with `DASHBOARD_URL` for login. |
| `DASHBOARD_SECRET` | random per startup | Optional session-signing secret, at least 32 characters. Without it, sessions do not survive restart. |

See [hosting](HOSTING.md#dashboard) and the implementation in [`core/dashboard.py`](../core/dashboard.py) before exposing the dashboard.

## Startup sync

`AUTO_SYNC_GIT=0` disables the default startup fetch and **hard reset to `origin/main`**. Set this in the process environment, not only in `.env`: sync runs before `.env` is read. The default sync can overwrite tracked changes and discard local commits; it attempts to save tracked working-tree edits as a patch but that is not a complete backup. `VEIL_SYNCED` is an internal restart marker, not a user setting. See [updating safely](HOSTING.md#updating).
