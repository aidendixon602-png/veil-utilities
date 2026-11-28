<div align="center">

![Veil Utilities banner](assets/banner.svg)

# Veil Utilities

**The all-in-one Discord bot** — antinuke · moderation · VoiceMaster · feeds · AI · music

Self-hosted Discord bot for server security, moderation, logging and community management.
Written in Python with `discord.py`, using **prefix commands** (default prefix `,`, as in `,help`).

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776ab)](https://www.python.org/)
[![discord.py](https://img.shields.io/badge/discord.py-2.4%2B-5865F2)](https://github.com/Rapptz/discord.py)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![Self-hosted](https://img.shields.io/badge/self--hosted-b10f2e)](docs/HOSTING.md)
[![Prefix](https://img.shields.io/badge/prefix-%2C-ff4d6d)](#commands)

[Website](website/index.html) · [Getting started](#getting-started) · [Command reference](docs/COMMANDS.md) · [Developer reference](docs/DEVELOPER_COMMANDS.md) · [Configuration](docs/CONFIGURATION.md) · [Permissions](docs/PERMISSIONS.md) · [Operations](docs/OPERATIONS.md) · [Developer guide](docs/DEVELOPERS.md) · [Hosting](docs/HOSTING.md) · [Contributing](.github/CONTRIBUTING.md)

</div>

## Contents

- [What it does](#what-it-does)
- [Getting started](#getting-started)
- [Commands](#commands)
- [Developer help](#developer-help)
- [Secret help](#secret-help)
- [Security status](#security-status)
- [Requirements](#requirements)
- [Configuration and storage](#configuration-and-storage)
- [Hosting and troubleshooting](#hosting-and-troubleshooting)
- [Project layout](#project-layout)
- [Development](#development)
- [Releases and license](#releases-and-license)

## What it does

| Area | Capabilities |
| --- | --- |
| 🛡️ Security | Configurable antinuke and anti-raid rules, audit-log monitoring, trusted users and roles, bot quarantine, and best-effort response and restoration. |
| 🔨 Moderation | Warnings, cases, bans, kicks, timeouts, filters, role tools and moderation logs. |
| ⚙️ Server management | Configurable log channels, welcome messages, autoroles, reaction and button roles, tickets, giveaways and VoiceMaster. |
| ✨ Community and integrations | Levels, reminders, feeds, music through Lavalink, AI, image tools and external-service lookups. Some integrations need credentials or external services. |
| ⚡ Administration | Server authorization, permission levels, grouped **Developer** help (bot ops, authorized servers, genreban, blacklists), **Secret** help for sensitive tools, and an optional web dashboard. |

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
,help developer               Developer help (developers only)
,help secret                  Secret help — sensitive tools (developers only)
,an status                    View antinuke settings
,an health                    Check antinuke readiness
,an ban on --threshold 3 --do ban --window 60s --command on
,logs                         Open the logging panel
,logs set messages #logs      Route message logs to a channel
,perm roles                   List role permission assignments
,reloadall                    Reload every loaded extension
,authedservers                List authorized servers
```

Server authorization, private mode, Veil grants, Discord permissions and role hierarchy still apply. The public reference omits hidden developer commands, which are listed separately in the [developer reference](docs/DEVELOPER_COMMANDS.md); its permission column is not a complete authorization policy. See the [permissions guide](docs/PERMISSIONS.md), [operations](docs/OPERATIONS.md), and the [developer guide](docs/DEVELOPERS.md).

## Developer help

Run `,help developer` (aliases `,dev`, `,devhelp`) in Discord. Developer copies the Midtown Utilities Developer help, mapped to the Veil commands that actually do the job. Prefix: `,` · dev only.

**Developer**

```text
,reload <cog>          - hot-reload one extension
,reloadall             - reload every loaded extension
,restart               - restart the bot, then reply with how long it took
,role delete <role>    - permanently delete a role
,version               - Show which update the bot is running
,system                - Show the bot's health
,cogs                  - List the loaded modules and their commands
,setpresence <status> [text] - Change the bot's online status and status text
,setstatus <arg>       - Change the bot's status or status text
,guilds                - list servers the bot is in
,lookupserver <guild_id> - Everything about a server the bot is in
,leaveguild <guild_id> - leave one or more servers
,invite                - Get a private link to install Veil in a server
,developer             - Show the developer commands
```

**Authorized servers (official Midtown only)**

```text
,authserver [id]       - authorize this server (or by ID)
,deauth [id]           - remove a server from the auth list
,authedservers         - list authorized servers
```

**Channels**

```text
,logs                  - log successful command usage
,logs set <category> <#channel> - Send a log category to a channel
,joinleavelogs [channel] - Log joins, leaves and kicks in their own channel
,modlog [channel]      - Set (or clear) the channel where moderation cases are posted
```

**Genreban**

```text
,genreban <user>       - ban across all authorized servers
,ungenreban <user>     - remove a genreban
,genrebanlist          - list all genrebanned users
```

**Hard blacklist (roles only devs can assign)**

```text
,hardbl add <role>     - hard-blacklist a role
,hardbl remove <role>  - remove from hard blacklist
,hardbl list           - view hard-blacklisted roles
```

**Role permissions**

```text
,removepermall <permission> - strip a permission from a role
,addpermall <permission | all> [role] - add permission(s) to a role
```

**Autorole (owner / dev)**

```text
,autorole add <role>   - join role automation
,autorole humans <role> - Toggle a role for humans only
,autorole bots <role>  - Toggle a role for bots only
,autorole delay <seconds> - Wait before giving autoroles
```

**User blacklist**

```text
,userrolebl <user> [role] - Blacklist a user from getting roles in this server
,userrolebl remove <user> [role] - Remove a user or role from the user role blacklist
,userrolebl list       - List users blacklisted from receiving roles in this server
,stripstaff <member>   - strip staff/perms; only owners can role them
```

**Role blacklist bypass**

```text
,bypass <user>         - allow user to assign soft blacklisted roles
,bypass remove <user>  - remove bypass
,bypass list           - List separate soft role-blacklist bypasses
```

**Ignore**

```text
,ignore <user>         - bot ignores all their commands
,ignore list           - List ignored channels and users
```

**DM**

```text
,dm <user> <message>   - DM a user
```

> Where Veil has no equivalent for a Midtown command (`fixnick`, `nocolor`, `cmdlogs`, `secret <#channel>`, `blacklistrole`, `setdm`), the closest real command is listed instead — no fake commands are shown. `,reloadall` and `,authedservers` are real aliases added so the copied names work.

## Secret help

Run `,help secret` (aliases `,secrets`, `,tokens`) in Discord. Secret holds the sensitive stuff that used to sit in Developer: code execution, host/data access, the global blacklist, and the dangerous DevTools role tools. Any future hidden command lands here by default, never in Developer.

**Code execution**

```text
,eval <code>           - Execute arbitrary Python code live inside the bot
,shell <command>       - Execute a shell command in the container environment
,sql <query>           - Execute a raw SQL query against the database
,sudo <target> <command> - Execute a command as another member (bypassing permissions)
```

**Host & data**

```text
,backup                - DM yourself a full database backup
,botlogs [lines]       - Show the end of the bot's log file
,errors                - Recent command errors (since the last restart)
,error [error_id]      - Full traceback and context for an error ID
,traceback             - Show the most recent exception traceback recorded by the bot
,killswitch <command>  - Turn a command off (or back on) in every server
,killswitch list       - List kill-switched commands
,maintenance [state] [duration] [reason] - Turn maintenance mode on or off
,shutdown              - Shut the bot down
,gitpull               - Pull the latest update
,broadcast <message>   - Send an announcement to every server's alert channel
```

**Global blacklist**

```text
,blacklist <target_id> [reason] - Blacklist a user or server from the bot
,unblacklist <target_id> - Lift a user's or server's blacklist
,blacklisted            - List blacklisted users and servers
```

**Dangerous role tools**

```text
,strip <member>        - Remove every role from a member and log the strip
,unstrip <member>      - Give back the roles a strip removed
,massunrole [role] [flag] - Remove a role from everyone
,revert <user> <duration> - Undo someone's recent bans and deletions
```

**Network & private**

```text
,midtown servers       - Privately scan joined server names for Midtown
,mutuals <user>        - Servers the bot shares with a user (for abuse investigations)
,portal <target>       - Create or fetch an invite to any server the bot is in
,antinukepreset save <name> - Save this server's antinuke config as a preset
,antinukepreset load <name> - Apply a saved preset to this server
,antinukepreset list   - List antinuke presets
,antinukepreset delete <name> - Delete a preset
```

## Security status

> **Self-role safety:** Veil checks self-service and selected automatic roles for Veil grants and security
> bypasses before assignment. Existing holders are not removed, and policy changes can race a grant; review
> existing roles and deployment effects in the [permissions guide](docs/PERMISSIONS.md#review-role-assignments).

Veil currently includes globally trusted or protected accounts in its source, including Ghost Vanguard and three
fully protected accounts (weba_wa2, thesimisear, protected account) that hold the developer rank for ordinary
command checks. These are separate from per-server settings; see
[built-in exemptions](docs/PERMISSIONS.md#built-in-exemptions).
Review all grants before enabling protection. Antinuke detection and restoration are **best-effort**, not a
security guarantee: Discord events, bot permissions, role hierarchy and API availability affect outcomes.
Some actions cannot be undone. Avoid overlapping protections and test in a disposable server.

The [staged audit](docs/MASTER_AUDIT.md) records unresolved security and reliability work. To report a new
vulnerability privately, follow the [security policy](.github/SECURITY.md).

## Requirements

- Python **3.10 or newer**; CI is configured for 3.10, 3.11, 3.12 and 3.13.
- A Discord application and bot token, a server where you can invite it, and network access to Discord.
- Discord's **Message Content** intent for normal prefix commands. Server Members and Presence intents are needed for some features. Enable the relevant privileged intents in the [Discord Developer Portal](https://discord.com/developers/applications).
- Writable, persistent storage for the SQLite database and logs, or an accessible PostgreSQL database.
- Optional integrations have their own service, credential, or software requirements; see [configuration](docs/CONFIGURATION.md).

## Configuration and storage

Copy [`.env.example`](.env.example) for a starting point. `DISCORD_TOKEN` is required at runtime; other settings are optional or have defaults. Important settings include `OWNER_IDS`, `PREFIX`, `AUTH_REQUIRED`, `AUTH_GRANDFATHER`, `PRIVATE_MODE`, `DB_PATH`, `DATABASE_URL` and `ANTINUKE_DEV_ALERTS`. The complete settings reference is in [configuration](docs/CONFIGURATION.md).

SQLite defaults to `veil.db` in the working directory, using WAL mode. Store the database and its sidecars on persistent storage; **back up using SQLite's backup API or the developer-only `,backup` command**, not by copying a live database file alone. Set `DATABASE_URL` for PostgreSQL; the SQLite `,backup` command does not back up PostgreSQL. Changing backends does **not** migrate data. Review [database maintenance and backups](docs/OPERATIONS.md#database-and-backups) before an upgrade or restore.

Unauthorized-server and real antinuke incidents are recorded with per-developer delivery status and retries. Delivery is at-least-once, so a crash can cause duplicate DMs; a database outage can prevent persistence. The current outbox assumes one bot process per database. Other critical event categories are not yet covered by this durable queue. See [developer notifications](docs/OPERATIONS.md#developer-notifications).

## Hosting and troubleshooting

Use a host with persistent storage and a managed process restart policy. [Hosting and updates](docs/HOSTING.md) covers Git deployments, Wispbyte-style panels and the optional dashboard. For startup failures, missing commands, permissions and notification delivery, see [troubleshooting](docs/OPERATIONS.md#troubleshooting).

## Project layout

```text
main.py              Startup and Discord connection
cogs/                Discord features and commands (owner, devtools, moderation, antinuke, ...)
core/                Shared bot, help (Developer/Secret), configuration, database and services
docs/                Operator and developer guides (COMMANDS, DEVELOPER_COMMANDS, ...)
assets/              Project and bot assets (banner, avatar, help preview)
scripts/             Documentation tooling (gen_commands.py)
tests/               Automated tests
website/             Landing page (index.html, styles.css)
.github/             CI, issue templates and project policies
.vscode/             Editor settings
```

| Path | What lives there |
| --- | --- |
| `main.py` | Startup and Discord connection |
| `cogs/` | Discord features and commands — `owner.py` (Developer/Secret tools), `devtools.py`, `authorization.py`, `moderation.py`, `antinuke.py`, and more |
| `core/` | Shared bot (`bot.py`), grouped help (`help.py` — Developer/Secret sections), configuration, database, dashboard and services |
| `docs/` | Operator and developer guides — `COMMANDS.md`, `DEVELOPER_COMMANDS.md`, `CONFIGURATION.md`, `PERMISSIONS.md`, `OPERATIONS.md`, `HOSTING.md`, `DEVELOPERS.md` |
| `assets/` | Project and bot assets — `banner.svg`, `avatar.png`, `help-preview.png` |
| `scripts/` | Documentation tooling — `gen_commands.py` regenerates both command references |
| `tests/` | Automated tests (offline, no Discord token required) |
| `website/` | Landing page — `index.html`, `styles.css` |
| `.github/` | CI workflows, issue templates and project policies |
| `.vscode/` | Editor settings |

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

## Releases and license

At this documentation update, the latest published release is [v1.1.0](https://github.com/aidendixon602-png/veil-utilities/releases/tag/v1.1.0); `main` may contain unreleased changes documented in the [changelog](CHANGELOG.md). See the [releases page](https://github.com/aidendixon602-png/veil-utilities/releases) for release history. Veil Utilities is distributed under the [MIT License](LICENSE).

For responsible disclosure, read the [security policy](.github/SECURITY.md). Contributions are welcome under the [contribution guidelines](.github/CONTRIBUTING.md) and [code of conduct](.github/CODE_OF_CONDUCT.md).
