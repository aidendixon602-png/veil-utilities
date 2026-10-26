# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Status:** the bot's presence is now only `Protecting X guilds | Y users` (refreshed when it joins or leaves a
  server and every 5 minutes). The rotating lines and the `STATUS` setting are gone; a status saved by an older
  version is cleared once. Developers can still override it with `,setstatus`.
- **`#veil-errors` and `#veil-auth` live under VEIL LOGS**, the same category as the server's log channels (made if
  missing); channels created earlier at the top level are moved in on the next start.

### Security

- **Nothing private is posted where server admins can read it.** Tracebacks, error reasons, `,eval`/`,error`
  output, `,gitpull` and `,version` are scrubbed of bot tokens and every secret from `.env`, GitHub tokens,
  credentials in URLs, GitHub addresses, the repository owner/name and the bot's folder path. `,gitpull`/`,version`
  show short commit IDs only (no repository, account or commit titles). Command log cards for developer commands
  show the command name with "arguments hidden".

### Added

- **`,version` and a self-explaining update.** `,version` shows the commit the bot is running, the one on disk and
  GitHub's `main`, where the folder pulls from, and what to do about a mismatch. The console prints a `[git]` line on
  every start (updated / up to date / sync failed and why), the startup box shows the running commit, and `,gitpull`
  names the repo it pulled from and explains failures (login rejected, GitHub unreachable, wrong repo address, not a
  git checkout) instead of a bare git error.
- **Detailed log cards.** Every log is now a titled card (`Roles Added — Name`) with the person's avatar and labelled
  fields instead of a one-line note. Each one shows who it is about (mention, username and ID), who caused it from
  the audit log (or "Unknown" when Discord doesn't say), and context: account age and join date, role count, the
  full list of roles added or removed with their IDs and a ⚠️ for dangerous permissions (administrator, manage roles,
  kick/ban…), reasons, jump links, channel and role facts (colour, position, topic, slowmode), invite limits and
  usage, who moved where in voice, and the IDs in the footer. One timestamp, shown in each viewer's own time.
- **Command logs redone.** Each card shows who ran it, the full command, the channel with a jump link, the permission
  level and rank that allowed it and who granted it, whether it completed (with how long it took) or failed (with the
  reason), the module and the alias used. Failed commands are logged too. Moderation case cards use the same layout.
- **IDs are set up by the bot.** `OWNER_IDS` is optional now: developers are the owner (or all team members) of the
  bot's Discord application, found at startup, plus any IDs you still list. The dashboard login reads the Client ID
  from the bot itself, so only `DISCORD_CLIENT_SECRET` and `DASHBOARD_URL` are needed. Crash tracebacks and
  unauthorized-server alerts go to private `#veil-errors` / `#veil-auth` channels the bot creates once in a server a
  developer owns or is in (visible only to the bot and developers; made again if deleted). `OWNER_IDS`,
  `ERROR_LOG_CHANNEL`, `AUTH_LOG_CHANNEL` and `DISCORD_CLIENT_ID` still work as overrides.
- **Private mode** (`PRIVATE_MODE`, on by default). Only bot developers, the server owner and members who hold a
  permission level (directly or through a role, or a fakeperms role) can run commands. Everyone else, including
  Discord administrators without a level and anyone in DMs, is ignored without a reply. Set `PRIVATE_MODE=false`
  to open public commands to every member again.

### Removed

- `,invite` (it handed anyone an Administrator invite link).
- Server, user and channel counts from `,botinfo`, and the "guilds" line from the rotating status.

### Changed

- **One reply style for the whole bot.** `ctx.approve`, `warn`, `deny` and `neutral` now send a single coloured line
  as a reply (green worked, amber careful/empty, red failed, grey info) with no author header, footer or timestamp,
  the same look the role commands already had. Every command error is now a red `failed: reason` line;
  only the cooldown nudge stays amber. Missing Discord permissions on ordinary commands keep their rank wording.
  The reply rules are in CONTRIBUTING.md.
- **Developer, music, feed and integration replies redone** in the shared style: `blacklisted 123`, `reloaded 2
  modules`, `set the volume to 80%`, `looping the queue`, `added feed #3 · new YouTube posts from ... go to #videos`,
  `linked Last.fm ...`. Empty developer lists say so (`nothing is kill-switched`, `there are no antinuke presets`).
- The error handler's last-resort messages (not in a server, command disabled, timed out, Discord rejected the
  request, bad input) now use the same red `failed: reason` line as every other error.
- **Utility, permissions, automod and AI replies redone** in the shared style: `turned snipe off for this server`,
  `you're now AFK: lunch`, `added a note for @user`, `set @user's permission level to Admin`, `turned the invites
  filter on`, `blocked spoiler`. Every toggle (link allow-list, blocked words and patterns, filter exemptions,
  uwulock) says which way it went instead of "toggled".
- `permission check` / `up check` answers with a plain grey note (`@user has the Admin permission level`) instead of an
  amber warning; `clearsnipe` replies with a line instead of only a ✅ reaction; `ping` and `uptime` lost their emoji.
- Empty lists say so (`you have no reminders`, `@user has no notes`, `no words or patterns are blocked`, `no messages
  have been counted yet`) and list titles show the total.
- **Server-setup replies redone** (settings, welcome/goodbye/boost, autoroles, starboard, counters, bump, levels,
  VoiceMaster, tickets, giveaways) in the shared style: `set the server prefix to !`, `turned leveling on`,
  `<#chat> no longer gives XP`, `added @Support to the support roles`, `locked your channel`. Toggles say which way
  they went instead of "toggled", and the wording no longer says "Midtown" in the welcome preset reply.
- Empty lists say so (`no aliases are set`, `no autoroles are set`, `nobody has any XP yet`, `there are no active
  giveaways`) and list titles show the total.
- Staff-only mode and fake-permission notes are now small subtext lines under the reply.
- **Antinuke, antiraid and anti-alt replies redone.** Short lower-case lines that say what changed:
  `turned antinuke on`, `set the Ban rule to 3 in 60s → ban`, `whitelisted @user`, `made @user an antinuke admin`,
  `mass join filter set to 10 joins in 10s`. Whitelist commands use mentions, warn (amber) when nothing changed,
  and the antiraid/anti-alt whitelist toggles now say whether they added or removed the user.
- Antinuke usage hints (`failed: usage: !an ban <count> <punishment> <timeframe>`) and the preset help follow the
  server's prefix instead of a hard-coded `,`.
- **Moderation replies redone.** Every moderation command answers in the shared style with a mention and the case
  number: `banned @user · case #12`, `timed out @user for 10 minutes · case #13`, `warned @user (warning 3) · case #14`,
  `locked #general`, `purged 12 messages`. Same for kick, softban, unban, jail, quarantine, nicknames, slowmode, the
  role blacklist and `addpermall`/`removepermall` (which no longer build their own embed with a footer).
  Failures read `failed: reason` in lower case. Mass bans and kicks say how long the window was and only mention
  failures when there are some.
- Empty lists now say so (`@user has no warnings`, `this server has no cases yet`, `nobody is jailed`,
  `no roles are blacklisted`) instead of sending nothing, and list titles show the total.
- `purge after` no longer errors when the command message was already deleted.
- **Logging rebuilt around per-category channels.** `,logs setup` creates a private **VEIL LOGS** category with
  `message-logs`, `member-logs`, `role-logs`, `channel-logs`, `voice-logs`, `mod-logs`, `server-logs`,
  `command-logs` and `antinuke-logs`; `,logs` opens a panel to point each category at a channel (or turn it off).
  Subcommands: `set <category|all> #channel`, `off`, `auto` (re-detect by channel name), `test`,
  `ignore add|remove <#channel|@user>` and `cleanup`. `,setuplogs` and `,cleanuplogs` still work.
  The group is now `logs` (alias `log`).
- Log entries (now detailed cards, see Added above): who did it (looked up in the audit log) and a footer with the user/message IDs. Edits, deletes, joins, leaves,
  role and nickname changes, channel/role/emoji/server changes, voice moves, bans/unbans, kicks, timeouts and
  command usage each get their own card. Bulk deletes, invites and emoji changes are now logged as well.
- Kicks and timeouts are logged to the **Moderation** channel alongside bans. Moderation case cards use the same style.
- The antinuke alert card honours the channel chosen under `,logs` (Antinuke) before falling back to its own `log` setting.
- The **Unban** button on ban cards is now persistent: it keeps working after a bot restart.
- The bot no longer creates log channels by itself when it joins a server; run `,logs setup` instead.
- **`,help` rebuilt.** The home card is the short "Select a category below to view its commands." prompt with one
  plain button per category you may open (Secret stays red); numbered lists, 10 per page, with ◀ ▶ and a page
  counter; 🏠 Home and 🗑 close. `,help <command>`
  now shows a card with syntax, a generated example (commands can set `extras={"example": ...}`), aliases,
  and the rank/permissions needed. A mistyped command suggests close matches you are allowed to see.
- The developer menus (Developer overview, Secret Suite) moved out of `core/help.py` into `core/devmenu.py`.
- Subcommands such as `role add` are filed under the same category as their group, so the rank needed to see
  them in help matches where they are listed.

### Security

- **Web dashboard locked down.** It previously started on every boot, listened on all interfaces and had no
  login, so anyone who found the port could list every server and add themselves as an antinuke admin.
  It is now off unless `DASHBOARD_ENABLED=true`, listens only on `127.0.0.1` unless Discord login is configured,
  signs people in with Discord and only lets server owners (and `OWNER_IDS`) edit a server. Forms carry CSRF
  tokens, cross-site POSTs are refused, and server names are HTML-escaped.

### Fixed

- **Help pages broke their own formatting.** A description cut short inside `code` (e.g. `bump`) left a backtick
  open, so the rest of the page rendered as code with raw `**` showing. Descriptions are now shortened at a word
  and never leave code, bold, spoilers or brackets open.
- **Bad member arguments said `could not convert "user" into Member or User`.** The reply and the command log
  now say what was typed: `couldn't find a member or user matching $`, without exception class names.
- **Hosts could keep running old code without saying so.** The start-up sync swallowed every failure, gave up when
  any file had been edited by hand, and reset to `origin/main`, which a checkout cloned for another branch never
  refreshes. It now resets to the commit it just fetched, saves hand edits to `local-edits-*.patch` and carries on,
  says what happened, and restarts itself once if `main.py` changed in the pull. `,gitpull` also applies files that
  are newer than the running bot even when the folder is already up to date.
- Dashboard home page crashed with a `KeyError` (unescaped CSS braces in a `.format()` template).
- Dashboard antinuke punishment was saved as `action`, which the antinuke never reads; it now sets `punishment`.
- Dashboard log channels were saved under `logging.channels`, which nothing reads; they now go to `logs`.
- Opening a server in the dashboard could cache an antinuke config without defaults, making the antinuke
  fail for that server until restart.
- The dashboard started again on every gateway reconnect; it now starts once in `setup_hook`.

### Added

- **Midtown Utilities Help Redesign** — Redesigned `,help` menu matching the elite Midtown Utilities style:
  - Blurple home overview (`0x5865F2`) with clean 2-row category grid (no secret or developer clutter).
  - Mint-accented category cards (`0x57F287`) with signatures (`,cmd [args] - desc`), aliases block, and dedicated `[Back]` navigation button.
  - Developer and Secret commands hidden from regular members.
- **Server Staff-Only Mode (`staffonly` / `botlock`)** — Global server setting allowing owners to restrict the bot so regular members cannot run commands.
- **Designated Bot Channels (`botchannels` / `botchannel`)** — Whitelist specific channels (e.g. `#bot-commands`) where non-staff are permitted to run commands.
- **Staff Notes System (`notes`)** — Dedicated staff notes for members (`,notes <member>`, `,notes add`, `,notes clear`).
- **Permission Levels Redesign** — Compact, single-card embed for `,permlevels` replacing oversized multiple fields, added `,perm` alias so `,perm add <member> <level>` works directly, and added staff protection.
- **Interactive Snipe Pagination** — Added `[◀ Prev]` and `[Next ▶]` button controls to cycle through recently deleted messages.
- **Midtown Utilities Channel Logging** — Auto-detects and formats event logging for dedicated channels:
  - `#command-logs`: Logs every executed command with user, code-block command, channel, permission level, and granted-by attribution.
  - `#join-logs`: Formats Member Joined (account age, creation date, invite used) and Member Left (roles had).
  - `#bans`: Formats bans and unbans with persistent, interactive `[Unban]` (green) and `[Reban]` (red) buttons.
  - `#veil-utilities-logging`: Auto-detected channel for general server, role, channel, and message updates.
- **Dynamic Presence Activity** — Custom status dynamically displaying `Guarding {users:,} users across {guilds} guilds • Watching over the community` matching Midtown's profile presence.
- **Lavalink Connection Cleanup** — Cleanly close wavelink node sessions and pool connections on shutdown/unload, fixing `Unclosed client session` and `Unclosed connector` asyncio warnings.
- `tests/test_midtown_redesign.py` — expanded to 10 unit tests (168 tests total).

### Fixed

- **Music Playback Hijacking** — Listeners can no longer stop playback, clear the queue, or change volume when no DJ role is set; controls now require staff permissions, DJ role, or being alone in VC with the bot.
- **Poll Command Unrestricted** — Added `manage_messages` permission requirement to `,poll` so normal members cannot spam polls.
- **Home button on the help menu** — one tap back to the category overview instead of
  re-clicking the active category.
- **Replies**: every bot response now threads the invoking message as a Discord reply
  (without pinging), with a plain-message fallback if the original was deleted.
- `tests/test_menu_reply.py` — Home-button and reply-threading coverage.
- `tests/test_hierarchy.py` — moderation protections (self / server owner / bot developer /
  role hierarchy) and panel-safe restart coverage.

### Fixed

- **`,restart` never came back online on container hosts** — `os.execv` replaces the process
  the panel tracks and the restart died silently. Restart now flaps the process with a
  non-zero exit code, which Pterodactyl-style panels detect as a crash and auto-restart
  within seconds.

### Security

- **Bot developers are protected from moderation** — `check_hierarchy` (ban, softban, kick,
  timeout, jail, forcenickname…) and the warn-management guard now refuse to target a bot
  developer, on top of the existing self/server-owner/role-hierarchy checks.

## [1.1.0] - 2026-10-24

### Changed

- Console logging redesigned: short timestamps, aligned colored levels, tidy logger names,
  and a versioned startup banner. discord.py/wavelink chatter is silenced — the bot's own
  logs say the same things once, properly formatted. Plain format is kept for the log file;
  `LOG_COLOR=0` disables color.

### Security

- Hidden commands are now developer-only at the **global check level**: for anyone else they
  behave as if they don't exist (silent `CommandNotFound`) — guessing a dev command's name
  reveals nothing. Previously they relied on per-cog checks only.
- DevTools cog failures are silent too, consistent with the other developer cogs.

### Added

- **8 new image filters**: `glitch`, `sketch`, `solarize`, `rainbow`, `vignette`, `censor`,
  `oilify`, plus `duotone` (veil-brand pink-on-dark). `chaos` can now stack the new ones.
  GIFs stay animated through all of them.
- **AI model failover**: every provider now prefers a ranked list of up-to-3 models (13 models
  across Groq, Gemini, OpenRouter and OpenAI) and a request silently walks to the next model
  when one is retired, rate-limited or slow. `ask provider` still shows the live pick.
- `tests/test_filters_ai.py` — render checks for the new filters (incl. animated GIFs) and
  model-rotation behavior against a fake provider.

### Fixed (continued)

- `giveaway reroll` now confirms with a link to the giveaway channel and warns when no valid
  entries remain, instead of silently doing nothing.

### Added (lockdown suite)

- `tests/test_lockdown.py` — 12 tests covering the hidden-command gate, help-menu lookups
  (commands and groups), proof that no permission level can unlock developer tools, static
  checks that dev cogs always require bot-owner, a full sweep proving all 36 hidden commands
  are refused for strangers, and that crash replies never echo exception messages.

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
