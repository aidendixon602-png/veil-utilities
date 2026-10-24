<div align="center">

<img src="assets/banner.svg" alt="veil utilities — the all-in-one discord bot" width="100%"/>

<br/>

[![check](https://github.com/aidendixon602-png/veil-utilities/actions/workflows/check.yml/badge.svg)](https://github.com/aidendixon602-png/veil-utilities/actions/workflows/check.yml)
![python](https://img.shields.io/badge/python-3.10%2B-ff4d6d?style=flat-square&logo=python&logoColor=white&labelColor=1a1118)
![discord.py](https://img.shields.io/badge/discord.py-2.4%2B-ff4d6d?style=flat-square&logo=discord&logoColor=white&labelColor=1a1118)
![commands](https://img.shields.io/badge/commands-353-b10f2e?style=flat-square&labelColor=1a1118)
![modules](https://img.shields.io/badge/modules-20-b10f2e?style=flat-square&labelColor=1a1118)
![storage](https://img.shields.io/badge/storage-sqlite-b10f2e?style=flat-square&logo=sqlite&logoColor=white&labelColor=1a1118)

**Protection, moderation and community tools in one bot — built to replace the five you're running now.**

[Features](#-features) · [Quick start](#-quick-start) · [Hosting](HOSTING.md) · [All commands](COMMANDS.md) · [Developer tools](#-developer-tools)

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
Give mods `ban members` **only through the bot**, so nobody on staff needs dangerous real Discord permissions.

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
| 🛡️ | **Antinuke** | 12 attack types (mass ban/kick, channel & role deletes, webhooks, bot adds, vanity changes, dangerous permission grants…), sliding-window thresholds, auto-revert, ban / kick / strip, global alerts to developers |
| 🚨 | **Antiraid** | Mass-join raid mode, minimum account age, no-avatar filter, per-user bypass |
| 🔨 | **Moderation** | Numbered cases + mod-log, DM notices, warn escalation, hardban, jail with role restore, force-nickname, `massban`, 10 purge filters, lockdown, nuke, mass role tools |
| 🧹 | **Automod** | Invites, links (with allowlist), spam, caps, mentions, emojis, zalgo, words, regex → delete or timeout |
| 🎙️ | **VoiceMaster** | Join-to-create channels with a button panel: lock, hide, rename, limit, claim, transfer. Remembers each user's settings |
| 🧩 | **Server** | Welcome / goodbye / boost, autoroles, reaction & button roles, autoresponders, sticky messages, starboard, counters, bump reminders, vanity rewards |
| 📈 | **Levels** | Message **and** voice XP, role rewards, XP multipliers, leaderboard |
| 🎉 | **Community** | Giveaways with requirements, tickets with transcripts |
| 📡 | **Feeds** | YouTube · Twitch · SoundCloud · Reddit · Instagram · TikTok · X · any RSS |
| 🤖 | **AI** | `ask` (chat with memory), `summarize`, `imagine` (free image generation), `tts`, `ocr` |
| 🖼️ | **Images** | 21 effects (deepfry, caption, meme, blur, pixelate, warp, chaos…). GIFs stay animated |
| 🎵 | **Music** | Lavalink player, queue, loop, seek, autoplay, nightcore / bassboost / 8D presets |
| 🟢 | **Spotify** | See what anyone's playing, plus track, artist, album and playlist lookups |
| 🌐 | **Lookups** | Last.fm (np, whoknows, top charts), weather, GitHub, crypto, Roblox, Minecraft, translate, Wikipedia, Urban Dictionary |
| 🛠️ | **Utility** | Snipe / editsnipe, AFK, reminders, user & server info, emoji steal, message stats, polls, custom aliases, self prefix |
| 🎲 | **Fun** | Tic-tac-toe, ship, 8ball, 16 anime interactions, uwulock |

<details>
<summary><b>Help menu preview</b></summary>
<br/>

`,help` opens a card with the bot's avatar and a grid of category buttons:

```
 [ Utility ] [ Music ] [ Moderation ] [ Roles ] [ Permissions ]
 [ Antinuke ] [ Automod ] [ Server ] [ VoiceMaster ] [ Community ]
 [ Social ] [ Fun ] [ Images & AI ] [ Developer ] [ Secret ]
```

The active category lights up, and only the person who ran the command can click. **Secret** (red) lists
the developer tools and only opens for developers.

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
,feeds add youtube MrBeast #videos
,fp grant @Mods ban_members, kick_members, moderate_members
```

> **Hosting on Wispbyte or another Pterodactyl panel?** Follow [HOSTING.md](HOSTING.md). It covers the 512 MB free plan, GitHub auto-deploy and troubleshooting.

## ⚙️ Configuration

Everything lives in `.env` (copy `.env.example`). Only the first two are required.

| Variable | Purpose |
|---|---|
| `DISCORD_TOKEN` | Your bot token. **Never commit it.** |
| `OWNER_IDS` | Developer user IDs (comma-separated). They unlock the Secret tools and server authorization |
| `AUTH_REQUIRED` | `true` = only authorized servers can use the bot |
| `LOW_MEMORY` | `true` on small hosts: smaller caches and image limits |
| `ERROR_LOG_CHANNEL` | Channel that receives a traceback for every crash |
| `ANTINUKE_DEV_ALERTS` | DM developers a "Global antinuke alert" whenever antinuke fires |
| `LASTFM_API_KEY` | Last.fm commands |
| `SPOTIFY_CLIENT_ID` / `_SECRET` | Spotify search |
| `AI_API_KEY` / `AI_BASE_URL` / `AI_MODEL` | AI chat. Any OpenAI-compatible provider, including local Ollama |
| `TWITCH_CLIENT_ID` / `_SECRET` | Twitch live alerts |
| `RSSHUB_URL` | Your RSSHub instance, for Instagram, TikTok and X feeds |
| `LAVALINK_URI` / `_PASSWORD` | Music server |

## 👑 Developer tools

Hidden from everyone else and listed under the red **Secret** help button:

| | |
|---|---|
| `system` | Uptime, shard latency, memory, CPU, database size, error count |
| `maintenance on [reason]` | Everyone but developers gets a maintenance notice |
| `killswitch <command>` | Disable a broken or abused command everywhere, instantly |
| `errors` · `error <id>` | Recent crashes and full tracebacks. Users see the error ID |
| `botlogs` · `backup` | Tail the log file · DM yourself the whole database |
| `authorize` · `unauthorize` · `authorized` | Control which servers can use the bot |
| `lookupserver` · `mutuals` · `guilds` | Investigate servers and users |
| `blacklist` · `dm` · `reload` · `restart` | Everything else you need to run it |

## 🧱 Project structure

```
veil-utilities/
├── main.py              entry point
├── core/
│   ├── bot.py           prefixes, aliases, global checks, error tracking
│   ├── db.py            SQLite + cached per-server JSON config
│   ├── help.py          button help menu
│   ├── modlog.py        case system
│   └── utils.py         embeds, paginator, embed scripting, permission checks
├── cogs/                one file per module (20 total)
├── assets/              banner + social preview
├── fonts/               bundled caption font (DejaVu)
├── tests/smoke_test.py  loads every module offline; runs on every push
└── lavalink/            optional music server config
```

**Adding a feature:** drop a new file in `cogs/`, add it to `EXTENSIONS` in `core/bot.py`, and give the cog an
`emoji`. It appears in the help menu automatically. Run `python tests/smoke_test.py` before pushing.

<br/>

<div align="center">
<sub>Built with discord.py · Made by <a href="https://github.com/aidendixon602-png">@aidendixon602-png</a></sub>
</div>
