# Veil Utilities

Veil Utilities is a self-hosted Discord bot for server security, moderation, logging and community management. It is written in Python with `discord.py` and uses **prefix commands**: the default prefix is a comma (`,`), as in `,help`.

[Getting started](#getting-started) · [Command reference](docs/COMMANDS.md) · [Configuration](docs/CONFIGURATION.md) · [Operations](docs/OPERATIONS.md) · [Developer guide](docs/DEVELOPERS.md) · [Hosting](docs/HOSTING.md) · [Contributing](.github/CONTRIBUTING.md)

## What it does

| Area | Capabilities |
| --- | --- |
| Security | Configurable antinuke and anti-raid rules, audit-log monitoring, trusted users and roles, bot quarantine, and best-effort response and restoration. |
| Moderation | Warnings, cases, bans, kicks, timeouts, filters, role tools and moderation logs. |
| Server management | Configurable log channels, welcome messages, autoroles, reaction and button roles, tickets, giveaways and VoiceMaster. |
| Community and integrations | Levels, reminders, feeds, music through Lavalink, AI, image tools and external-service lookups. Some integrations need credentials or external services. |
| Administration | Server authorization, permission levels, developer commands and an optional web dashboard. |

**Security is not a guarantee.** Detection and restoration depend on Discord events, bot permissions and role hierarchy; some actions cannot be undone. Avoid running overlapping protections in Veil and another bot. Read the [antinuke guide](docs/ANTINUKE.md) and [production audit](docs/PRODUCTION_AUDIT.md) before enabling destructive responses.

## Requirements

- Python **3.10 or newer**; CI is configured for 3.10, 3.11, 3.12 and 3.13.
- A Discord application and bot token, a server where you can invite it, and network access to Discord.
- Discord's **Message Content** intent for normal prefix commands. Server Members and Presence intents are needed for some features. Enable the relevant privileged intents in the [Discord Developer Portal](https://discord.com/developers/applications).
- Writable, persistent storage for the SQLite database and logs, or an accessible PostgreSQL database.
- Optional integrations have their own service, credential, or software requirements; see [configuration](docs/CONFIGURATION.md).

## Getting started

The following is a local Linux/macOS example. On Windows, activate the virtual environment with `.venv\Scripts\Activate.ps1` and use `copy .env.example .env`.

```bash
git clone https://github.com/aidendixon602-png/veil-utilities.git
cd veil-utilities
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

1. Set `DISCORD_TOKEN` in `.env`. Never commit or share this file. Set `OWNER_IDS` to the comma-separated Discord user IDs of any additional bot developers who should receive critical alerts.
2. Enable the applicable privileged gateway intents in the Discord Developer Portal and invite the bot with the permissions needed for the features you intend to use. Its role must be above roles it manages; grant only permissions appropriate to your deployment.
3. Review `AUTH_REQUIRED`, `AUTH_GRANDFATHER` and `PRIVATE_MODE` in [configuration](docs/CONFIGURATION.md). With authorization enabled, developers' own servers are automatically authorized; other servers must be approved. Initial grandfathering is a one-time enrollment of already joined servers, not a general bypass.
4. Start the bot:

   ```bash
   AUTO_SYNC_GIT=0 python main.py
   ```

   On Windows PowerShell, use `$env:AUTO_SYNC_GIT = "0"; python main.py`.
5. In an authorized server, run `,help`. Configure and test features in a disposable server before enabling them elsewhere.

> **Startup behavior:** By default, `main.py` fetches `origin/main` and performs a hard reset before reading `.env`. This can overwrite tracked local changes or move a checkout to a different commit. Set `AUTO_SYNC_GIT=0` in the **process environment before launch**, as above, to disable it. Back up data and review [updating and hosting](docs/HOSTING.md) before deploying.

## Commands

The default prefix is `,`; server and personal prefixes can be configured. The built-in help and [generated command reference](docs/COMMANDS.md) describe syntax and aliases. Examples:

```text
,help                         Show available help
,help antinuke                Explain a command
,an status                    View antinuke settings
,an health                    Check antinuke readiness
,an ban on --threshold 3 --do ban --window 60s --command on
,logs                         Open the logging panel
,logs set messages #logs      Route message logs to a channel
,perm roles                   List role permission assignments
```

Permissions, authorization, private mode, Discord permissions and role hierarchy still apply. The public reference omits hidden developer commands, which are listed separately in the [developer reference](docs/DEVELOPER_COMMANDS.md); its permission column is not a complete authorization policy. See [permissions and logging](docs/OPERATIONS.md) and the [developer guide](docs/DEVELOPERS.md).

## Configuration and storage

Copy [`.env.example`](.env.example) for a starting point. `DISCORD_TOKEN` is required at runtime; other settings are optional or have defaults. Important settings include `OWNER_IDS`, `PREFIX`, `AUTH_REQUIRED`, `AUTH_GRANDFATHER`, `PRIVATE_MODE`, `DB_PATH`, `DATABASE_URL` and `ANTINUKE_DEV_ALERTS`. The complete settings reference is in [configuration](docs/CONFIGURATION.md).

SQLite defaults to `veil.db` in the working directory, using WAL mode. Store the database and its sidecars on persistent storage; **back up using SQLite's backup API or the developer-only `,backup` command**, not by copying a live database file alone. Set `DATABASE_URL` for PostgreSQL; the SQLite `,backup` command does not back up PostgreSQL. Changing backends does **not** migrate data. Review [database maintenance and backups](docs/OPERATIONS.md#database-and-backups) before an upgrade or restore.

Unauthorized-server and real antinuke incidents are recorded with per-developer delivery status and retries. Delivery is at-least-once, so a crash can cause duplicate DMs; a database outage can prevent persistence. The current outbox assumes one bot process per database. Other critical event categories are not yet covered by this durable queue. See [developer notifications](docs/OPERATIONS.md#developer-notifications).

## Hosting and troubleshooting

Use a host with persistent storage and a managed process restart policy. [Hosting and updates](docs/HOSTING.md) covers Git deployments, Wispbyte-style panels and the optional dashboard. For startup failures, missing commands, permissions and notification delivery, see [troubleshooting](docs/OPERATIONS.md#troubleshooting).

## Development

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pip check
ruff check .
python scripts/gen_commands.py --check
python -m pytest -q
```

The offline test suite does not require a Discord token. PostgreSQL integration tests additionally require `pgserver`; see [contributing](.github/CONTRIBUTING.md). Regenerate both command references with `python scripts/gen_commands.py` after command changes. Do not run destructive security tests against production servers.

```text
main.py              Startup and Discord connection
cogs/                Discord features and commands
core/                Shared bot, configuration, database and services
docs/                Operator and developer guides
assets/              Project and bot assets
scripts/             Documentation tooling
tests/               Automated tests
.github/             CI, issue templates and project policies
```

## Releases and license

At this documentation update, the latest published release is [v1.1.0](https://github.com/aidendixon602-png/veil-utilities/releases/tag/v1.1.0); `main` may contain unreleased changes documented in the [changelog](CHANGELOG.md). See the [releases page](https://github.com/aidendixon602-png/veil-utilities/releases) for release history. Veil Utilities is distributed under the [MIT License](LICENSE).

For responsible disclosure, read the [security policy](.github/SECURITY.md). Contributions are welcome under the [contribution guidelines](.github/CONTRIBUTING.md) and [code of conduct](.github/CODE_OF_CONDUCT.md).
