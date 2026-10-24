<div align="center">

<img src="assets/banner.svg" alt="veil utilities — the all-in-one discord bot" width="100%"/>

<br/>

[![check](https://github.com/aidendixon602-png/veil-utilities/actions/workflows/check.yml/badge.svg)](https://github.com/aidendixon602-png/veil-utilities/actions/workflows/check.yml)
![python](https://img.shields.io/badge/python-3.10%2B-ff4d6d?style=flat-square&logo=python&logoColor=white&labelColor=1a1118)
![discord.py](https://img.shields.io/badge/discord.py-2.4%2B-ff4d6d?style=flat-square&logo=discord&logoColor=white&labelColor=1a1118)
![commands](https://img.shields.io/badge/commands-371-b10f2e?style=flat-square&labelColor=1a1118)
![modules](https://img.shields.io/badge/modules-20-b10f2e?style=flat-square&labelColor=1a1118)
![storage](https://img.shields.io/badge/storage-sqlite-b10f2e?style=flat-square&logo=sqlite&logoColor=white&labelColor=1a1118)

**Protection, moderation and community tools in one bot — built to replace the five you're running now.**

[Features](#-features) · [Quick start](#-quick-start) · [Hosting](HOSTING.md) · [All commands](COMMANDS.md) · [Developer tools](#-developer-tools)

</div>

<br/>

<div align="center">

<img src="assets/banner.svg" alt="veil utilities — the all-in-one discord bot" width="100%"/>

<br/>

[![check](https://github.com/aidendixon602-png/veil-utilities/actions/workflows/check.yml/badge.svg)](https://github.com/aidendixon602-png/veil-utilities/actions/workflows/check.yml)
[![release](https://img.shields.io/github/v/release/aidendixon602-png/veil-utilities?style=flat-square&color=b10f2e&labelColor=1a1118)](https://github.com/aidendixon602-png/veil-utilities/releases/latest)
[![python](https://img.shields.io/badge/python-3.10%2B-ff4d6d?style=flat-square&logo=python&logoColor=white&labelColor=1a1118)](https://python.org)
[![discord.py](https://img.shields.io/badge/discord.py-2.4%2B-ff4d6d?style=flat-square&logo=discord&logoColor=white&labelColor=1a1118)](https://github.com/Rapptz/discord.py)
![commands](https://img.shields.io/badge/commands-386-b10f2e?style=flat-square&labelColor=1a1118)
![modules](https://img.shields.io/badge/modules-22-b10f2e?style=flat-square&labelColor=1a1118)
[![license](https://img.shields.io/badge/license-MIT-ff4d6d?style=flat-square&labelColor=1a1118)](LICENSE)
[![code style: ruff](https://img.shields.io/badge/code%20style-ruff-ff4d6d?style=flat-square&labelColor=1a1118)](https://github.com/astral-sh/ruff)
![last commit](https://img.shields.io/github/last-commit/aidendixon602-png/veil-utilities?style=flat-square&color=b10f2e&labelColor=1a1118)

**Protection, moderation and community tools in one bot — built to replace the five you're running now.**

[Features](#-features) · [Quick start](#-quick-start) · [All commands](COMMANDS.md) · [Hosting](HOSTING.md) · [Contributing](CONTRIBUTING.md) · [Changelog](CHANGELOG.md)

<details>
<summary><b>📑 Contents</b></summary>
<br/>

- [✦ Why veil](#-why-veil)
- [⚡ Features](#-features)
- [🚀 Quick start](#-quick-start)
- [🏗️ Architecture](#-architecture)
- [⚙️ Configuration](#-configuration)
- [👑 Developer tools](#-developer-tools)
- [🧪 Development](#-development)
- [🧱 Project structure](#-project-structure)
- [🗺️ Roadmap](#-roadmap)
- [🤝 Contributing](#-contributing)
- [📜 License](#-license)

</details>

</div>

<br/>

## ✦ Why veil

<table>
<tr>
<td width="33%" valign="top">

### 🛡️ Stops nukes cold
Audit-log antinuke with per-action thresholds. It **reverts the damage**, punishes the attacker, and DMs the owner a full alert card with every role they held.

</td>
<td width="33%" valign="top">

### 🔐 You decide where it runs
The bot only works in servers a developer has **authorized**. It leaves anywhere else and sends you a one-click Authorize button.

</td>
<td width="33%" valign="top">

### 🎭 Fake permissions
Give mods `ban members` **only through the bot** — per role, or per member with named **permission levels** (`uperm add @user moderator`). Nobody on staff needs dangerous real Discord permissions.

</td>
</tr>
<tr>
<td valign="top">

### ✍️ Embed scripting
`{embed}$v{title: hi {user.mention}}` with 25+ variables, for welcome, boost, autoresponders, feeds and level-ups. `embedcode` turns any embed back into a script.

</td>
<td valign="top">

### 📡 Social feeds
New **YouTube** uploads, **Twitch** go-lives, **SoundCloud**, **Reddit**, Instagram, TikTok and X posts land in your channels with role pings.

</td>
<td valign="top">

### 👑 Real developer tools
Maintenance mode, a global kill switch, error IDs with tracebacks, log tailing and one-command database backups.

</td>
</tr>
</table>

## ⚡ Features

| | Module | Highlights |
|:-:|---|---|
| 🛡️ | **Antinuke** | 14 attack types (mass ban/kick/timeout/unban, channel & role deletes, webhooks, bot adds, vanity changes, dangerous permission grants on roles, members and channel overwrites…), sliding-window thresholds, auto-revert, ban / kick / strip, global alerts to developers. Also watches actions done **through the bot's own commands**, so fake permissions can't be used to nuke |
| 🚨 | **Antiraid** | Mass-join raid mode, minimum account age, no-avatar filter, per-user bypass |
| 🔨 | **Moderation** | Numbered cases + mod-log, DM notices, warn escalation, hardban, jail with role restore, force-nickname, `massban`, 10 purge filters, lockdown, nuke, mass role tools |
| 🧹 | **Automod** | Invites, links (with allowlist), spam, caps, mentions, emojis, zalgo, words, regex → delete or timeout |
| 🎙️ | **VoiceMaster** | Join-to-create channels with a button panel: lock, hide, rename, limit, claim, transfer. Remembers each user's settings |
| 🔑 | **Permissions** | Named per-member levels (Helper → Supreme Leader, plus automatic Server Owner & Bot Dev), role blacklist, `removepermall` to strip a permission from every role after a raid |
| 🧩 | **Server** | Welcome / goodbye / boost, autoroles, reaction & button roles, autoresponders, sticky messages, starboard, counters, bump reminders, vanity rewards |
| 📈 | **Levels** | Message **and** voice XP, role rewards, XP multipliers, leaderboard |
| 🎉 | **Community** | Giveaways with requirements, tickets with transcripts |
| 🤖 | **AI** | `ask` (chat with memory), `summarize`, `imagine` (free image generation), `tts`, `ocr` |
| 🖼️ | **Images** | 21 effects (deepfry, caption, meme, blur, pixelate, warp, chaos…). GIFs stay animated |
| 🎵 | **Music** | Lavalink player, queue, loop, seek, autoplay, nightcore / bassboost / 8D presets, optional DJ role with vote-skip, auto-leaves empty channels |
| 🌐 | **Lookups** | Last.fm (np, whoknows, top charts), weather, GitHub, crypto, Roblox, Minecraft, translate, Wikipedia, Urban Dictionary |
| 🛠️ | **Utility** | Snipe / editsnipe, AFK, reminders, user & server info, emoji steal, message stats, polls, custom aliases, self prefix |
| 🎲 | **Fun** | Tic-tac-toe, ship, 8ball, 16 anime interactions, uwulock |

<details>
<summary><b>Help menu preview</b></summary>
<br/>

<div align="center">
<img src="assets/help-preview.svg" alt="The ,help menu with category buttons" width="720"/>
</div>

15 category buttons, one owner-locked menu: only the person who ran `,help` can click, the active
category lights up, and the red **Secret** button lists the developer tools — it only opens for developers.
The menu and [COMMANDS.md](COMMANDS.md) are both built from the live command list, so they can never disagree.

</details>


## 🚀 Quick start

```bash
git clone https://github.com/aidendixon602-png/veil-utilities
cd veil-utilities
pip install -r requirements.txt
cp .env.example .env          # add DISCORD_TOKEN and your OWNER_IDS
python main.py
```

1. In the [Discord developer portal](https://discord.com/developers/applications), turn on **all three Privileged Gateway Intents**.
2. Invite the bot with Administrator, then **drag its role to the top** so antinuke can act on anyone.
3. Try it out:

```
,antinuke enable            ,antiraid massjoin 10 10
,log set all #logs          ,modlog #mod-logs
,vm setup                   ,leveling on
,fp grant @Mods ban_members, kick_members, moderate_members
```

> **Hosting on Wispbyte or another Pterodactyl panel?** Follow [HOSTING.md](HOSTING.md). It covers the 512 MB free plan, GitHub auto-deploy and troubleshooting.

## 🏗️ Architecture

```mermaid
%%{init: {'theme': 'dark'}}%%
flowchart LR
    A([Discord gateway]) --> B{core/bot.py<br>prefixes · aliases<br>global checks}
    B --> C["cogs/ — 22 feature modules"]
    C --- D[("SQLite — core/db.py<br>cached per-server config")]
    C --- E["core/net.py — SSRF-safe HTTP"] --> F{{external APIs}}
    C --- G["music — wavelink → Lavalink 4.2+<br>5 free nodes, auto-failover"]
    C --- H["AI — core/ai_providers.py<br>Groq / Gemini / OpenRouter / OpenAI<br>→ free Pollinations fallback"]
```

One cog per feature, behind typed, validated config ([`core/config.py`](core/config.py)) and a single safe-HTTP
helper. Everything optional degrades instead of crashing: no Lavalink node hides music, no AI key falls back
to the free provider, and a failed background loop takes down nothing else.


## ⚙️ Configuration

Everything lives in `.env` (copy `.env.example`). Only the first two are required. All settings are typed and validated in [`core/config.py`](core/config.py) — if a value is missing or malformed, the bot refuses to start and lists every problem at once.

| Variable | Purpose |
|---|---|
| `DISCORD_TOKEN` | Your bot token. **Never commit it.** |
| `OWNER_IDS` | Developer user IDs (comma-separated). They unlock the Secret tools and server authorization |
| `AUTH_REQUIRED` | `true` = only authorized servers can use the bot |
| `LOW_MEMORY` | `true` on small hosts: smaller caches and image limits |
| `ERROR_LOG_CHANNEL` | Channel that receives a traceback for every crash |
| `ANTINUKE_DEV_ALERTS` | DM developers a "Global antinuke alert" whenever antinuke fires |

**Integrations: all optional.** Everything below works out of the box. Keys only make things faster or unlock extras.

| Variable | Without it | With it |
|---|---|---|
| `AI_API_KEY` | Free no-signup AI (slower) | Paste a Groq (`gsk_`), Gemini (`AIza`), OpenRouter (`sk-or-`) or OpenAI (`sk-`) key and the provider + model are picked automatically. Falls back to the free AI if your quota runs out. `AI_BASE_URL` / `AI_MODEL` override (e.g. local Ollama) |
| `LAVALINK_URI` / `_PASSWORD` | Free public Lavalink nodes with automatic failover | Your own Lavalink 4.2+ server(s), comma-separated. `MUSIC_ENABLED=false` turns music off |
| `OCR_SPACE_API_KEY` | Shared demo key (rate-limited) | Free personal key from ocr.space |
| `LASTFM_API_KEY` | Last.fm commands are off | Free key from last.fm/api/account/create |
| `SPOTIFY_CLIENT_ID` / `_SECRET` | `,sp` search uses Deezer data + Spotify links | Real Spotify data (Spotify requires the app owner to have Premium) |
| `TWITCH_CLIENT_ID` / `_SECRET` | Twitch feeds are off (YouTube, Reddit, SoundCloud, RSS still work) | Twitch live alerts |
| `RSSHUB_URL` | Public RSSHub for Instagram/TikTok/X feeds (often blocked) | Your own RSSHub instance |

## 👑 Developer tools

Hidden from everyone else and listed under the red **Secret** help button:

| Command | What it does |
|---|---|
| `system` | Uptime, shard latency, memory, CPU, database size, error count |
| `maintenance on [reason]` | Everyone but developers gets a maintenance notice |
| `killswitch <command>` | Disable a broken or abused command everywhere, instantly |
| `errors` · `error <id>` | Recent crashes and full tracebacks. Users see the error ID |
| `botlogs` · `backup` | Tail the log file · DM yourself the whole database |
| `strip` · `unstrip` · `massunrole` · `revert` | Dangerous role/recovery tools |
| `hardban` · `genreban` · `unhardban` · `hardbans` | Network-wide bans across every server the bot is in |
| `joinleavelogs` · `anpreset save/load/list` | Join-leave log channel · reusable antinuke presets |
| `authorize` · `unauthorize` · `authorized` | Control which servers can use the bot |
| `lookupserver` · `mutuals` · `guilds` | Investigate servers and users |
| `blacklist` · `dm` · `reload` · `restart` | Everything else you need to run it |

## 🧪 Development

```bash
pip install -r requirements-dev.txt
ruff check .          # lint
python -m pytest -q   # unit tests + offline smoke test (loads every module)
python scripts/gen_commands.py   # regenerate COMMANDS.md after adding/changing commands
```

Version lives in [`pyproject.toml`](pyproject.toml). CI — lint, the full test suite, the smoke test that loads every module, and `.env.example` validation — runs on every push, on Python 3.11 and 3.12.
A test fails if `COMMANDS.md` doesn't match the real commands, so the reference never goes stale.

## 🧱 Project structure
```
veil-utilities/
├── main.py                  entry point — validates the environment, then starts the bot
├── core/
│   ├── bot.py               prefixes, aliases, global checks, error tracking, cog loading
│   ├── config.py            typed, validated settings loaded from .env
│   ├── db.py                SQLite + cached per-server JSON config
│   ├── help.py              the button help menu
│   ├── modlog.py            numbered moderation cases
│   ├── net.py               safe HTTP for user-supplied links (blocks private/internal addresses)
│   ├── perms.py             named permission levels
│   ├── ai_providers.py      Groq / Gemini / OpenRouter / OpenAI auto-detect + free fallback
│   ├── lavalink_nodes.py    5 free Lavalink nodes with health checks and failover
│   └── utils.py             embeds, paginator, embed scripting, permission checks
├── cogs/                    one file per module (22 total)
├── assets/                  banner, help-menu preview, social preview
├── fonts/                   bundled caption font (DejaVu)
├── scripts/                 gen_commands.py (builds COMMANDS.md from the live commands)
├── tests/                   129 tests + docs check; all run on every push
├── lavalink/                optional music server config
├── COMMANDS.md              auto-generated reference of all 386 commands
├── HOSTING.md               free hosting walkthrough (Wispbyte and friends)
├── CONTRIBUTING.md · SECURITY.md · CHANGELOG.md · LICENSE
└── .github/                 CI, dependabot, issue and PR templates
```


## 🗺️ Roadmap

- [x] Full bug audit + zero-setup integrations (every optional API has a free fallback)
- [x] Auto-generated command reference that CI refuses to let go stale
- [ ] Web dashboard for per-server configuration
- [ ] PostgreSQL backend option for large deployments
- [x] Audit pass over automod · logging · permissions cogs
- [x] Developer lockdown: hidden commands are invisible to non-developers
- [ ] More image filters and AI models

Have an idea? [Open a feature request](https://github.com/aidendixon602-png/veil-utilities/issues/new?template=feature_request.md).

## 🤝 Contributing

Pull requests are welcome — [CONTRIBUTING.md](CONTRIBUTING.md) covers the setup, the coding style and
the three commands to run before pushing (`ruff`, the tests, and `gen_commands.py`).
Found a security problem? Please follow [SECURITY.md](SECURITY.md) instead of opening an issue.

## 📜 License

[MIT](LICENSE) — do whatever you want, just keep the notice.

<br/>

<div align="center">
<sub>Built with discord.py · Made by <a href="https://github.com/aidendixon602-png">@aidendixon602-png</a></sub>
</div>
