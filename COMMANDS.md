# Command reference

475 commands. Default prefix `,`. `<required>` `[optional]`.


## 🤖 AI
_Ask AI, generate images, text-to-speech and OCR_

| Command | Description | Permission |
|---|---|---|
| `ask <prompt>` (ai, chat, gpt) | Ask the AI anything — it remembers your last few messages in this channel |  |
| `ask provider` (model, status) | Show which AI provider and model the bot is using |  |
| `ask reset` (clear, forget) | Forget your conversation in this channel |  |
| `imagegen <prompt>` (imagine, generate, img) | Generate an image from a prompt (free, no key needed) |  |
| `ocr [image]` (readimage) | Read the text in an image (attach, reply or link) |  |
| `summarize [messages=50]` (tldr) | AI summary of the recent chat |  |
| `tts <text>` (speak) | Turn text into speech. Start with a language code for other languages: `tts es hola amigos` |  |

## 🛡️ Antinuke
_Stop rogue admins and raids before they wreck your server_

| Command | Description | Permission |
|---|---|---|
| `antialt` (alt, antialts) | Configure Anti-Alt security to screen newly created burner accounts | administrator |
| `antialt action <action>` (punish, punishment) | Set action for alt accounts: `kick`, `ban` or `timeout` | administrator |
| `antialt avatar <state>` | Toggle blocking accounts without custom avatars: `antialt avatar on/off` | administrator |
| `antialt minage [days=0]` (age, days) | Set minimum account age in days before joining is allowed (0 to disable) | administrator |
| `antialt whitelist <user>` (allow, trust) | Whitelist an alt account so it can join without being blocked | administrator |
| `antinuke` (an) | View antinuke rules and status overview |  |
| `antinuke admin <member>` | Toggle an antinuke admin (server owner only) — admins can configure antinuke and are trusted |  |
| `antinuke admins` (adminlist, details) | List antinuke admins and whitelisted accounts |  |
| `antinuke ban [count] [punishment] [timeframe]` | Configure the ban rule: `,an ban 3 ban 60s` |  |
| `antinuke botadd [count] [punishment] [timeframe]` (bot, bots) | Configure the bot add rule: `,an botadd 1 ban 60s` |  |
| `antinuke channelcreate [count] [punishment] [timeframe]` | Configure the channel create rule: `,an channelcreate 2 kick 120s` |  |
| `antinuke channeldelete [count] [punishment] [timeframe]` | Configure the channel delete rule: `,an channeldelete 2 ban 120s` |  |
| `antinuke disable` (off) | Turn antinuke off |  |
| `antinuke enable` (on) | Turn antinuke on |  |
| `antinuke kick [count] [punishment] [timeframe]` | Configure the kick rule: `,an kick 3 ban 120s` |  |
| `antinuke log [channel]` | Set where antinuke alerts go (owner always gets a DM) |  |
| `antinuke module <module> <state> [threshold=3]` | Toggle an individual module: `antinuke module ban on 3` |  |
| `antinuke permissions [count] [punishment] [timeframe]` (permission, perms) | Configure the permission grant rule: `,an permissions 1 strip 60s` |  |
| `antinuke preset` | Manage rule presets: `,an preset save/load/list` |  |
| `antinuke preset list` | List available antinuke presets |  |
| `antinuke preset load <name>` | Load an antinuke preset (default, strict, relaxed, or saved custom) |  |
| `antinuke preset save <name>` | Save current antinuke rules as a named preset |  |
| `antinuke punishment <punishment>` | Set the default punishment: ban, kick or strip |  |
| `antinuke reset [action]` | Reset an action rule or all rules to defaults: `,an reset ban` or `,an reset` |  |
| `antinuke rolecreate [count] [punishment] [timeframe]` | Configure the role create rule: `,an rolecreate 5 strip 120s` |  |
| `antinuke roledelete [count] [punishment] [timeframe]` | Configure the role delete rule: `,an roledelete 5 strip 120s` |  |
| `antinuke roleinvite [count] [punishment] [timeframe]` (invite) | Configure the role invite rule: `,an roleinvite 1 strip 60s` |  |
| `antinuke serverupdate [count] [punishment] [timeframe]` (server) | Configure the server update rule: `,an serverupdate 1 strip 60s` |  |
| `antinuke set <action> <arg2> [arg3] [arg4]` (rule, configure) | Configure an antinuke rule: `,an set ban 3 ban 60s` or `,an set ban on 3` |  |
| `antinuke status` (config, settings) | View antinuke rules and status overview |  |
| `antinuke timeout [count] [punishment] [timeframe]` (mute) | Configure the timeout rule: `,an timeout 5 strip 120s` |  |
| `antinuke toggle` (on/off) | Toggle antinuke on or off |  |
| `antinuke unwhitelist <user>` (unwl, untrust) | Remove a user or bot from the antinuke whitelist |  |
| `antinuke webhook [count] [punishment] [timeframe]` (webhooks) | Configure the webhook rule: `,an webhook 1 kick 60s` |  |
| `antinuke whitelist <user>` (wl, trust) | Add a user or bot to the antinuke whitelist |  |
| `antinuke window <seconds>` | Set the default time window thresholds are counted over |  |
| `antiraid` (raid) | Filter suspicious joins: mass joins, new accounts, no avatar | administrator |
| `antiraid age [days=0]` | Punish accounts younger than N days (0 to disable) | administrator |
| `antiraid avatar <state>` | Punish accounts with no avatar (on/off) | administrator |
| `antiraid massjoin [joins=0] [seconds=10]` | Punish everyone in a join wave: `antiraid massjoin 10 10` (0 to disable) | administrator |
| `antiraid punishment <punishment>` | kick, ban or timeout | administrator |
| `antiraid whitelist <user>` | Let a specific user bypass antiraid | administrator |

## 🧹 Automod
_Automatic chat filters_

| Command | Description | Permission |
|---|---|---|
| `antighostping` (ghostping, antighost) | Configure anti-ghostping detection to catch deleted mentions | manage guild |
| `antighostping channel [channel]` | Set channel where ghostping alerts are posted (omit for same channel) | manage guild |
| `antighostping off` (disable) | Disable anti-ghostping detection | manage guild |
| `antighostping on` (enable) | Enable anti-ghostping detection | manage guild |
| `filter` (automod, am) | View automod filters | manage guild |
| `filter action <action> [seconds=60]` | What happens on a violation: `delete` or `timeout <seconds>` | manage guild |
| `filter allowlink <domain>` | Toggle a domain allowed through the link filter (e.g. youtube.com) | manage guild |
| `filter caps [percent=0]` | Block messages over N% caps (0 disables) | manage guild |
| `filter emojis [limit=0]` | Block messages with N+ emojis (0 disables) | manage guild |
| `filter exempt <target>` | Toggle a role or channel bypassing filters | manage guild |
| `filter invites <state>` | Block Discord invites (on/off) | manage guild |
| `filter links <state>` | Block all links (on/off) | manage guild |
| `filter list` | List blocked words and patterns | manage guild |
| `filter massmention [limit=0]` (mentions) | Block messages with N+ mentions (0 disables) | manage guild |
| `filter newlines [limit=0]` | Block messages with N+ line breaks (0 disables) | manage guild |
| `filter regex <pattern>` | Toggle a blocked regex pattern | manage guild |
| `filter spam [messages=0] [seconds=5]` | Limit messages per N seconds: `filter spam 5 5` (0 disables) | manage guild |
| `filter word <word>` (words, add) | Toggle a blocked word (end with * to match partial words) | manage guild |
| `filter zalgo <state>` | Block zalgo/glitch text | manage guild |

## 🧩 Config
_Welcome messages, roles, responders, starboard and more_

| Command | Description | Permission |
|---|---|---|
| `autoresponder` (ar, autoresponse) | Automatic replies to trigger phrases | manage guild |
| `autoresponder add <args>` | `ar add hello, Hi {user.mention}!` — flags: --exact --reply --delete (supports embed scripts) | manage guild |
| `autoresponder list` | List autoresponders | manage guild |
| `autoresponder remove <trigger>` (delete) | Remove an autoresponder | manage guild |
| `autorole` | Roles given automatically on join | manage roles |
| `autorole add <role>` (all) | Toggle a role for everyone who joins | manage roles |
| `autorole bots <role>` | Toggle a role for bots only | manage roles |
| `autorole delay <seconds>` | Wait N seconds before giving autoroles (helps with verification gates) | manage roles |
| `autorole humans <role>` | Toggle a role for humans only | manage roles |
| `boost [channel] [message]` | Set the boost thank-you message | manage guild |
| `boost off` | Disable boost messages | manage guild |
| `boost role [role]` | Extra role given to boosters (removed when they stop) | manage roles |
| `boost test` | Preview the boost message | manage guild |
| `bump [state]` (bumpreminder) | Remind the server to /bump on Disboard every 2 hours (`bump on`/`bump off`) | manage guild |
| `bump message <message>` | Set the reminder message | manage guild |
| `bump thankyou <message>` (thanks) | Set the thank-you message | manage guild |
| `buttonrole [roles]... [title=Pick your roles]` (buttonroles, rolemenu) | Post a panel of self-role buttons: `buttonrole @Red @Blue @Green Colors` | manage roles |
| `counter` | Channels that display live server stats | manage channels |
| `counter add <kind> [template]` | `counter add members Members: {count}` — kinds: members, humans, bots, boosts, online | manage channels |
| `counter remove <channel>` | Remove a counter (and its channel) | manage channels |
| `goodbye [channel] [message]` (leave) | Set the goodbye message | manage guild |
| `goodbye off` | Disable goodbye messages | manage guild |
| `goodbye test` | Preview the goodbye message | manage guild |
| `placeholders` (variables) | List variables you can use in messages |  |
| `reactionrole` (rr) | Give roles when members react to a message | manage roles |
| `reactionrole add <message> <emoji> <role>` | `rr add <message link> 🎮 @Gamer` | manage roles |
| `reactionrole list` | List reaction roles | manage roles |
| `reactionrole remove <message> <emoji>` | Remove a reaction role | manage roles |
| `reactiontrigger` (rt) | Auto-react to messages containing a word | manage guild |
| `reactiontrigger add <emoji> <trigger>` | `rt add 💀 skull` | manage guild |
| `reactiontrigger list` | List reaction triggers | manage guild |
| `reactiontrigger remove <trigger>` | Remove all reactions for a trigger | manage guild |
| `starboard [channel] [threshold=3] [emoji=⭐]` (sb) | Set up a starboard: `starboard #stars 3 ⭐` | manage guild |
| `starboard off` | Disable the starboard | manage guild |
| `sticky [channel] [message]` | Keep a message pinned at the bottom of a channel (supports embed scripts) | manage messages |
| `sticky remove <channel>` | Remove a sticky message | manage messages |
| `vanity [role] [text]` | Reward members who put a text (e.g. your invite) in their status: `vanity @Supporter /myserver` | manage guild |
| `vanity off` | Disable vanity rewards | manage guild |
| `welcome [channel] [message]` (greet) | Set the welcome message: `welcome #ch {embed}$v{description: hi {user.mention}}` | manage guild |
| `welcome deleteafter [seconds=0]` | Auto-delete welcome messages after N seconds (0 = never) | manage guild |
| `welcome off` (disable) | Disable welcome messages | manage guild |
| `welcome preset <channel>` (card, template) | Install a sleek Midtown welcome embed preset in a channel: `welcome preset #welcome` | manage guild |
| `welcome test` | Preview the welcome message | manage guild |

## ⚡ DevTools
_Developer-only tools_

| Command | Description | Permission |
|---|---|---|
| `antinukepreset delete <name>` (remove) | Delete a preset |  |
| `antinukepreset list` | List antinuke presets |  |
| `antinukepreset load <name>` (apply) | Apply a saved preset to this server |  |
| `antinukepreset save <name>` | Save this server's antinuke config as a preset |  |

## 📡 Feeds
_Auto-post new content from social platforms_

| Command | Description | Permission |
|---|---|---|
| `feeds` (socials, notify, alerts) | Post new content from YouTube, Twitch, SoundCloud, Reddit, Instagram, TikTok, X or any RSS feed | manage guild |
| `feeds add <platform> <account> [channel]` | `feeds add youtube MrBeast #videos` — platforms: youtube, twitch, soundcloud, reddit, instagram, tiktok, x, rss | manage guild |
| `feeds list` | List feeds | manage guild |
| `feeds message <feed_id> [message]` | Custom post message (embed scripts + {feed.title} {feed.link} {feed.author} {feed.image} {feed.account}); `none` resets | manage guild |
| `feeds ping <feed_id> [role]` (role) | Ping a role when a feed posts (omit to stop) | manage guild |
| `feeds remove <feed_id>` (delete) | Remove a feed by its ID (see `feeds list`) | manage guild |
| `feeds test <feed_id>` | Post the latest item from a feed right now | manage guild |

## 🎲 Fun
_Games and silly commands_

| Command | Description | Permission |
|---|---|---|
| `8ball <question>` (8b) | Ask the magic 8-ball |  |
| `advice` (tip, wisdom) | Receive random daily advice and wisdom | uperm: helper |
| `bite <ctx> [member]` | Bite someone |  |
| `cat` | Random cat |  |
| `choose <options>` (pick) | Pick between options: `choose pizza, tacos, sushi` |  |
| `coinflip` (cf, flip) | Flip a coin |  |
| `coolrate [member]` (howcool, rate) | How cool is someone |  |
| `cuddle <ctx> [member]` | Cuddle someone |  |
| `dog` | Random dog |  |
| `feed <ctx> [member]` | Feed someone |  |
| `handhold <ctx> [member]` | Handhold someone |  |
| `highfive <ctx> [member]` | Highfive someone |  |
| `hug <ctx> [member]` | Hug someone |  |
| `joke` (dadjoke, pun) | Get a random dad joke or pun | uperm: helper |
| `kiss <ctx> [member]` | Kiss someone |  |
| `pat <ctx> [member]` | Pat someone |  |
| `peck <ctx> [member]` | Peck someone |  |
| `poke <ctx> [member]` | Poke someone |  |
| `punch <ctx> [member]` | Punch someone |  |
| `redditmeme` (dankmeme, rmeme) | Fetch a random trending meme from Reddit | uperm: helper |
| `reverse <text>` | Reverse text |  |
| `roll [dice=1d6]` (dice) | Roll dice: `roll 2d20`, `roll 100` |  |
| `rps <choice>` | Rock paper scissors |  |
| `ship <a> [b]` | Ship compatibility |  |
| `slap <ctx> [member]` | Slap someone |  |
| `tickle <ctx> [member]` | Tickle someone |  |
| `tictactoe <opponent>` (ttt) | Play tic-tac-toe with someone |  |
| `uwu <text>` | UwU-ify text |  |
| `uwulock <member>` | Toggle forcing someone's messages into uwu speak | manage messages, manage webhooks |
| `wave <ctx> [member]` | Wave someone |  |
| `yeet <ctx> [member]` | Yeet someone |  |

## 🎉 Giveaways
_Host giveaways_

| Command | Description | Permission |
|---|---|---|
| `giveaway` (gw, g) | Run giveaways |  |
| `giveaway cancel <message_id>` (delete) | Cancel a giveaway without picking winners | manage guild |
| `giveaway end <message_id>` | End a giveaway early | manage guild |
| `giveaway list` | Active giveaways |  |
| `giveaway reroll <message_id> [winners=1]` | Pick new winner(s) | manage guild |
| `giveaway start <duration> <winners> <prize>` (create) | `gw start 1d 2w Nitro` — add `--role @Role` for a requirement | manage guild |

## 🖼️ Imaging
_Edit images, GIFs, avatars and emojis_

| Command | Description | Permission |
|---|---|---|
| `blur <ctx> [image]` | Blur an image — attach, reply to, or mention an image/user/emoji |  |
| `brighten <ctx> [image]` | Brighten — attach, reply to, or mention an image/user/emoji |  |
| `caption <text>` (cap) | Add a white caption bar above an image (attach or reply to one) |  |
| `censor <ctx> [image]` | Mosaic-censor the center band — attach, reply to, or mention an image/user/emoji |  |
| `chaos [image]` (randomfx) | Stack 3 random effects |  |
| `circle <ctx> [image]` | Crop into a circle — attach, reply to, or mention an image/user/emoji |  |
| `deepfry <ctx> [image]` | Deep-fry an image — attach, reply to, or mention an image/user/emoji |  |
| `duotone <ctx> [image]` | veil-brand duotone (pink on dark) — attach, reply to, or mention an image/user/emoji |  |
| `edges <ctx> [image]` | Edge detection — attach, reply to, or mention an image/user/emoji |  |
| `emboss <ctx> [image]` | Emboss effect — attach, reply to, or mention an image/user/emoji |  |
| `glitch <ctx> [image]` | Corrupted glitch effect — attach, reply to, or mention an image/user/emoji |  |
| `grayscale <ctx> [image]` | Black and white — attach, reply to, or mention an image/user/emoji |  |
| `invert <ctx> [image]` | Invert colors — attach, reply to, or mention an image/user/emoji |  |
| `jpeg <ctx> [image]` | Destroy it with JPEG compression — attach, reply to, or mention an image/user/emoji |  |
| `meme <text>` | Impact-style meme text: `meme top text \| bottom text` (attach or reply to an image) |  |
| `mirror <ctx> [image]` | Mirror horizontally — attach, reply to, or mention an image/user/emoji |  |
| `oilify <ctx> [image]` | Oil painting effect — attach, reply to, or mention an image/user/emoji |  |
| `pixelate <ctx> [image]` | Pixelate an image — attach, reply to, or mention an image/user/emoji |  |
| `posterize <ctx> [image]` | Posterize colors — attach, reply to, or mention an image/user/emoji |  |
| `rainbow <ctx> [image]` | Rainbow gradient overlay — attach, reply to, or mention an image/user/emoji |  |
| `rotate <ctx> [image]` | Rotate 90° — attach, reply to, or mention an image/user/emoji |  |
| `saturate <ctx> [image]` | Crank the saturation — attach, reply to, or mention an image/user/emoji |  |
| `sepia <ctx> [image]` | Old-photo sepia tone — attach, reply to, or mention an image/user/emoji |  |
| `sharpen <ctx> [image]` | Over-sharpen — attach, reply to, or mention an image/user/emoji |  |
| `sketch <ctx> [image]` | Pencil sketch — attach, reply to, or mention an image/user/emoji |  |
| `solarize <ctx> [image]` | Solarize (negative highlights) — attach, reply to, or mention an image/user/emoji |  |
| `upsidedown <ctx> [image]` | Flip upside down — attach, reply to, or mention an image/user/emoji |  |
| `vignette <ctx> [image]` | Dark vignette border — attach, reply to, or mention an image/user/emoji |  |
| `warp <ctx> [image]` | Wavy distortion — attach, reply to, or mention an image/user/emoji |  |

## 🌐 Integrations
_Look things up across the web_

| Command | Description | Permission |
|---|---|---|
| `convert <amount> <from_curr> <to_curr>` (currency, exchange) | Convert currencies with live European Central Bank exchange rates: `convert 100 USD EUR` | uperm: helper |
| `crypto [coin=bitcoin] [currency=usd]` (price, coin) | Crypto price, market cap, and 24h metrics: `crypto btc usd` | uperm: helper |
| `define <word>` (dictionary, dict) | English dictionary definitions, phonetics, and usage examples: `define serendipity` | uperm: helper |
| `github <username>` (gh) | GitHub profile lookup with full details: `github torvalds` | uperm: helper |
| `github repo <repo>` | Repository lookup: `gh repo owner/name` | uperm: helper |
| `ipinfo <query>` (ip, geoip) | IP address and domain geolocation intelligence: `ipinfo 1.1.1.1` | uperm: helper |
| `lastfm [member]` (lf, fm) | Show what you're listening to (same as `np`) | uperm: helper |
| `lastfm profile [member]` (user) | Last.fm profile |  |
| `lastfm reactions [emojis...]` | Set the reactions added to now-playing embeds (none to disable) | manage guild |
| `lastfm recent [member]` (rt) | Recent tracks |  |
| `lastfm set <username>` (login, link) | Link your Last.fm account |  |
| `lastfm topalbums [period=all] [member]` (tab) | Top albums |  |
| `lastfm topartists [period=all] [member]` (ta) | Top artists — period: 7d 1m 3m 6m 1y all |  |
| `lastfm toptracks [period=all] [member]` (tt) | Top tracks |  |
| `lastfm unlink` (logout) | Unlink Last.fm |  |
| `lastfm whoknows [artist]` (wk) | Who in this server listens to an artist the most |  |
| `minecraft <address>` (mcserver, mc_status) | Minecraft server status (Java & Bedrock): `minecraft hypixel.net` | uperm: helper |
| `nowplaying [member]` (np, nowplay) | Your current/last Last.fm track | uperm: helper |
| `npm <package>` (nodejs, npmpkg) | Node.js package lookup on NPM: `npm express` | uperm: helper |
| `pypi <package>` (pythonpkg, pip) | Python package lookup on PyPI: `pypi discord.py` | uperm: helper |
| `qr <text_or_url>` (qrcode) | Generate a scannable QR code for text or a URL: `qr https://discord.gg` | uperm: helper |
| `roblox <username>` (rblx) | Roblox profile lookup | uperm: helper |
| `translate <language> [text]` (tr) | Translate text (or reply to a message): `translate es hello` | uperm: helper |
| `urban <term>` (ud) | Urban Dictionary definitions and slang | uperm: helper |
| `weather <location>` | Current weather anywhere with full forecast metrics: `weather Tokyo` | uperm: helper |
| `wikipedia <query>` (wiki) | Wikipedia summary | uperm: helper |

## 📈 Levels
_Reward activity with XP and roles_

| Command | Description | Permission |
|---|---|---|
| `leaderboard` (lb, top) | Server XP leaderboard |  |
| `leveling` (levels) | Configure leveling | manage guild |
| `leveling channel [channel]` | Send level-ups to a channel (omit for same channel) | manage guild |
| `leveling ignore <channel>` | Toggle a channel not giving XP | manage guild |
| `leveling message [message]` | Level-up message (`{level}` + all variables; `none` to silence) | manage guild |
| `leveling multiplier <role> [multiplier=1.0]` | XP multiplier for a role, e.g. boosters ×2 (1 to remove) | manage guild |
| `leveling off` (disable) | Disable leveling | manage guild |
| `leveling on` (enable) | Enable leveling | manage guild |
| `leveling reset [member]` | Reset one member's XP, or everyone's | administrator |
| `leveling reward <level> [role]` (role) | Give a role at a level (omit role to remove) | manage roles |
| `leveling setlevel <member> <level>` | Set a member's level | administrator |
| `leveling setxp <member> <xp>` | Set a member's total XP | administrator |
| `leveling stack <state>` | Keep old reward roles (true) or only the highest (false) | manage guild |
| `leveling voice <state>` | Toggle XP for time in voice | manage guild |
| `rank [member]` (level, lvl, xp) | View your level and progress |  |

## 📜 Logging
_Log server events to channels_

| Command | Description | Permission |
|---|---|---|
| `cleanuplogs` (cleanuplogchannels, purgeduplicatelogs) | Clean up duplicate logging channels created by the bot | manage guild |
| `log` (logs) | View logging channels | manage guild |
| `log cleanup` (clean, purgeduplicates, cleanupchannels) | Clean up duplicate logging channels created by the bot | manage guild |
| `log set <kind> [channel]` | Set a log channel: `log set messages #logs` or `log set all #logs` (no channel = auto/off) | manage guild |
| `log setup [mode=default]` (init, default, defaults) | Automatically create default log channels under VEIL LOGS. Use `log setup all` to include security logs (#antinuke-logs and #message-logs). | manage guild |
| `setuplogs [mode=default]` (setupchannels, autologs) | Automatically create default log channels: #join-logs, #bans, #command-logs, #veil-utilities-logging | manage guild |

## 🔨 Moderation
_Keep your server in line_

| Command | Description | Permission |
|---|---|---|
| `addpermall <permission>` (apa) | Add a (non-dangerous) permission to every role below me | administrator, manage roles |
| `addrole <arg1> <arg2>` (giverole) | Add a role to a member: `,addrole @User @Role` | manage roles, uperm: admin |
| `ban <user> [reason]` | Ban a member (or any user by ID). Add `--purge` to delete their last 7 days of messages | ban members, uperm: admin |
| `botclear [amount=100]` (bc) | Delete bot messages and command invocations | manage messages, uperm: helper |
| `cases [user]` (history, modlogs) | View moderation history (for the server or a user) | moderate members |
| `checkbled [target]` (checkbl, scanbl, bledcheck, rblcheck, roleblcheck) | Check a user or the whole server for members with BLED (blacklisted) roles |  |
| `clearwarns <member>` (clearwarnings) | Clear all of a member's warnings | moderate members, uperm: mod |
| `delwarn <warn_id>` (unwarn) | Delete a single warning by ID | moderate members, uperm: mod |
| `forcenickname <member> [nickname]` (fn, forcenick) | Lock a member's nickname (omit nickname to unlock) | manage nicknames, uperm: mod |
| `hide [channel]` | Hide a channel from @everyone | manage channels, uperm: admin |
| `jail <member> [reason]` | Jail a member: strips their roles and confines them to #jail | manage roles, moderate members, uperm: mod |
| `jail list` | List jailed members | moderate members, uperm: mod |
| `jail setup` | Create the jail role and channel | administrator, uperm: admin |
| `kick <member> [reason]` | Kick a member | kick members, uperm: mod |
| `lock [channel] [reason]` | Prevent @everyone from sending messages | manage channels, uperm: admin |
| `lockdown [reason=lockdown]` | Lock every text channel in the server | administrator, uperm: headadmin |
| `massban <minutes> [reason=raid cleanup]` (banrecent) | Ban everyone who joined in the last N minutes (raid cleanup) | administrator, ban members, uperm: admin |
| `masskick <minutes> [reason=raid cleanup]` (kickrecent) | Kick everyone who joined in the last N minutes (raid cleanup) | kick members, uperm: admin |
| `nickname <member> [nickname]` (nick) | Change (or reset) a member's nickname | manage nicknames, uperm: mod |
| `nuke` | Clone this channel and delete the original (wipes all messages) | administrator, manage channels, uperm: headadmin |
| `purge <amount> [member]` (clear, c, prune) | Delete messages (optionally only from one member) | manage messages, uperm: helper |
| `purge after <message_id>` | Delete every message after a message ID | manage messages |
| `purge bots [amount=100]` | Delete messages from bots | manage messages |
| `purge contains <substring> [amount=100]` (has) | Delete messages containing text | manage messages |
| `purge embeds [amount=100]` | Delete messages with embeds | manage messages |
| `purge humans [amount=100]` | Delete messages from humans | manage messages |
| `purge images [amount=100]` (files, attachments) | Delete messages with attachments | manage messages |
| `purge invites [amount=100]` | Delete messages containing invites | manage messages |
| `purge links [amount=100]` | Delete messages containing links | manage messages |
| `purge mentions [amount=100]` | Delete messages that mention users | manage messages |
| `quarantine <member> [reason=Quarantined by staff]` (isolate, freeze) | Instantly isolate a suspected raider or malicious member, stripping their roles | manage roles, moderate members, uperm: mod |
| `rblauth <target>` (roleblauth, bledauth, rblbypass) | Authorize a member or role to hold blacklisted roles: `,rblauth @user` | manage roles, uperm: headadmin |
| `rblunauth <target>` (roleblunauth, bledunauth) | Revoke role blacklist authorization for a member or role: `,rblunauth @user` | manage roles, uperm: headadmin |
| `reason <case> <reason>` | Update the reason on a case (your own cases, or any case with Manage Server) | moderate members |
| `removepermall <permission>` (rpa) | Remove a permission from every role below me (e.g. `rpa administrator` after a raid) | administrator, manage roles |
| `removerole <arg1> <arg2>` (takerole) | Remove a role from a member: `,removerole @User @Role` | manage roles, uperm: admin |
| `role <arg1> [arg2]` (r, changerole, cr) | Toggle a role on a member: `,role @User @Role` or `,role @Role @User` (aliases: `,changerole`, `,cr`) | manage roles, uperm: admin |
| `role add <arg1> <arg2>` | Add a role to a member: `,role add @User @Role` | manage roles, uperm: admin |
| `role all <action> <role>` | Add/remove a role for everyone: `role all add @Role` | administrator, uperm: headadmin |
| `role bots <action> <role>` | Add/remove a role for all bots | administrator, uperm: headadmin |
| `role color <role> <color>` (colour) | Change a role's color | manage roles, uperm: admin |
| `role create <name>` | Create a role | manage roles, uperm: admin |
| `role delete <role>` | Delete a role | manage roles, uperm: admin |
| `role hoist <role>` | Toggle showing a role separately | manage roles, uperm: admin |
| `role humans <action> <role>` | Add/remove a role for all humans | administrator, uperm: headadmin |
| `role remove <arg1> <arg2>` (rem, del) | Remove a role from a member: `,role remove @User @Role` | manage roles, uperm: admin |
| `role rename <role> <name>` | Rename a role | manage roles, uperm: admin |
| `roleblacklist` (rbl, rolebl) | Blacklist roles so the bot refuses to assign them | manage roles, uperm: admin |
| `roleblacklist add <role>` | Blacklist a role | manage roles, uperm: admin |
| `roleblacklist authorize <target>` (auth, whitelist, allow) | Authorize a member or role to bypass the role blacklist: `,rbl auth @user` | manage roles, uperm: headadmin |
| `roleblacklist authorized` (allowed) | View members and roles authorized to bypass the role blacklist | manage roles |
| `roleblacklist check [target]` (scan, audit, search) | Check a user or the whole server for members with BLED (blacklisted) roles |  |
| `roleblacklist list` | View blacklisted roles | manage roles |
| `roleblacklist remove <role>` (rem, delete) | Unblacklist a role, or `remove deleted` to purge deleted roles | manage roles, uperm: admin |
| `roleblacklist strip` (purge, clean) | Strip all blacklisted roles from unauthorized members across the server | manage roles, uperm: headadmin |
| `roleblacklist unauthorize <target>` (unauth, unwhitelist) | Revoke a member's or role's authorization to bypass the role blacklist | manage roles, uperm: headadmin |
| `roles` (rolelist) | List every role in the server, highest first |  |
| `security` (secscan, auditsec, serveraudit) | Run a comprehensive security audit of this Discord server | manage guild |
| `slowmode [delay=0] [channel]` (sm) | Set slowmode (e.g. `10s`, `5m`, `0` to disable) | manage channels, uperm: admin |
| `softban <member> [reason]` | Ban and immediately unban to wipe a member's recent messages | ban members, uperm: admin |
| `stripbled` (purgebled, rblstrip, roleblstrip) | Strip all blacklisted roles from unauthorized members across the server | manage roles, uperm: headadmin |
| `stripstaff <member>` | Remove every role with dangerous permissions from a member | administrator, uperm: headadmin |
| `timeout <member> [duration] [reason]` (mute, to, tmute) | Timeout a member (default 5m, max 28d) | moderate members, uperm: mod |
| `unban <user> [reason]` | Unban a user | ban members, uperm: admin |
| `unhide [channel]` (reveal) | Reveal a hidden channel | manage channels, uperm: admin |
| `unjail <member> [reason]` | Release a member and give back their roles | moderate members, uperm: mod |
| `unlock [channel] [reason]` | Allow @everyone to send messages again | manage channels, uperm: admin |
| `unlockdown` | Unlock every text channel | administrator, uperm: headadmin |
| `unquarantine <member> [reason=Released by staff]` (unfreeze, release) | Release a member from quarantine and safely restore non-blacklisted roles | manage roles, moderate members, uperm: mod |
| `untimeout <member> [reason]` (unmute, untime, uto) | Remove a timeout (use `untimeout all` for everyone) | moderate members, uperm: mod |
| `untimeout all [reason]` | Remove timeouts from every timed-out member | moderate members, uperm: admin |
| `warn <member> [reason=No reason provided]` | Warn a member. Auto-punishments can be set with `warnpunish` | moderate members, uperm: mod |
| `warnings <member>` (warns) | List a member's warnings | moderate members |
| `warnpunish <warnings> <action> [duration]` | Auto-punish at N warnings: `warnpunish 3 timeout 1h`, `warnpunish 5 ban`, `warnpunish 3 none` | administrator, uperm: admin |

## 🎵 Music
_Play music in voice channels (Spotify/YouTube/SoundCloud via Lavalink)_

| Command | Description | Permission |
|---|---|---|
| `audiofilter [preset]` (preset, fx) | Audio presets: nightcore, vaporwave, bassboost, 8d, slowed, karaoke, clear |  |
| `autoplay <state>` | Keep playing recommended songs when the queue ends |  |
| `clearqueue` | Clear the queue |  |
| `deafen` (deaf, undeafen) | Toggle whether the bot is deafened in the voice channel |  |
| `djrole [role]` | Set the DJ role (needed to pause/stop/volume/etc.), or no role to let everyone control music | manage guild |
| `loop [mode=track]` | Loop mode: track, queue or off |  |
| `lyrics [query]` (ly, songlyrics) | Fetch song lyrics from LRCLIB: `lyrics Bohemian Rhapsody` (defaults to current voice track) | uperm: helper |
| `pause` | Pause |  |
| `play <query>` (p) | Play a song or playlist (URL or search) |  |
| `queue` (q) | Show the queue |  |
| `remove <position>` | Remove a track from the queue |  |
| `resume` | Resume |  |
| `seek <position>` | Seek to a time: `seek 1:30` |  |
| `shuffle` | Shuffle the queue |  |
| `skip` (next, voteskip, vs) | Skip the current song (DJs and the requester skip instantly, others vote) |  |
| `song` (playing, current) | What's playing |  |
| `stop` (dc, disconnect) | Stop and leave |  |
| `volume <volume>` (vol, v) | Set volume (0–150) |  |

## 🔑 Permissions
_Assign named permission levels to members_

| Command | Description | Permission |
|---|---|---|
| `botperms` (botpermissions, botperm) | View the bot's Discord permissions in this server and channel |  |
| `permlevels` (plevels, plvl) | View the permission levels available in this server | manage messages |
| `permlevels grant <key> <permission>` | Toggle a bot-permission a level grants (server owner) |  |
| `permlevels rank <key> <rank>` | Change a level's rank (server owner) |  |
| `permlevels rename <key> <name>` | Rename a level (server owner) |  |
| `userperm [target]` (perm, perms, uperm, ups) | Manage members' and roles' permission levels. Checks target if given, or shows levels. |  |
| `userperm add <target> <level>` (set, give) | Assign a permission level to a member or a role | administrator, uperm: headadmin |
| `userperm check [target]` (view, info) | Check a member's or role's permission level and what it grants |  |
| `userperm list` (all) | List members and roles with a permission level | administrator |
| `userperm remove <target>` (rem, delete, unset, clear) | Remove a member's or role's permission level | administrator, uperm: headadmin |
| `userperm remove deleted` | Purge permission levels for members who left the server | administrator, uperm: headadmin |

## ⚙️ Settings
_Configure how the bot behaves in your server_

| Command | Description | Permission |
|---|---|---|
| `alias` | Create shortcuts for commands | manage guild |
| `alias add <shortcut> <command>` | Add an alias, e.g. `alias add b ban` or `alias add hi say hello` | manage guild |
| `alias list` | List aliases | manage guild |
| `alias remove <shortcut>` (delete, del) | Remove an alias | manage guild |
| `botchannels [channel]` (cmdchannels, botchannel) | Restrict normal commands to specific channels (`botchannels <#channel>` to toggle) | manage guild |
| `disablecommand <command>` (disablecmd, dcmd) | Disable a command for non-admins (`dcmd list` to see what's disabled) | manage guild |
| `disablecommand list` | List disabled commands | manage guild |
| `enablecommand <command>` (enablecmd, ecmd) | Re-enable a disabled command | manage guild |
| `fakepermissions` (fp, fakeperms) | Give roles permissions that only work through the bot — no dangerous Discord perms needed |  |
| `fakepermissions grant <role> <permissions>` (add) | Grant fake permissions, e.g. `fp grant @Mods ban_members, kick_members` |  |
| `fakepermissions list` | List fake permissions |  |
| `fakepermissions revoke <role> [permissions=all]` (remove) | Revoke fake permissions from a role (`all` to clear) |  |
| `ignore <target>` | Toggle whether the bot ignores commands from a channel or member | manage guild |
| `ignore list` | List ignored channels and users |  |
| `modlog [channel]` | Set (or clear) the channel where moderation cases are posted | manage guild |
| `prefix` | View the server prefix |  |
| `prefix set <prefix>` | Change the server prefix | manage guild |
| `selfprefix [prefix]` | Set a personal prefix that works everywhere (`none` to remove) |  |
| `staffonly [state]` (botlock, lockbot) | Toggle staff-only mode so normal members cannot run bot commands |  |

## 🟢 Spotify
_Look up songs, artists, albums and what people are listening to on Spotify_

| Command | Description | Permission |
|---|---|---|
| `spotify [member]` (sp) | What someone is listening to on Spotify right now |  |
| `spotify album <query>` (al) | Look up an album and its tracklist |  |
| `spotify artist <query>` (a) | Look up an artist and their top tracks |  |
| `spotify cover <query>` (art) | Full-size album cover for a track or album |  |
| `spotify playlist <query>` (pl) | Look up a public playlist |  |
| `spotify track <query>` (song, t) | Look up a track |  |

## 🎫 Tickets
_Private support tickets_

| Command | Description | Permission |
|---|---|---|
| `ticket` | Ticket system |  |
| `ticket add <member>` | Add someone to this ticket |  |
| `ticket category <category>` | Category new tickets are created in | manage guild |
| `ticket close` | Close the current ticket |  |
| `ticket logs [channel]` | Where transcripts are posted | manage guild |
| `ticket message <message>` | Message shown inside new tickets | manage guild |
| `ticket panel [channel] [text]` (send, setup) | Send the ticket panel | manage guild |
| `ticket ping <state>` | Ping support roles on new tickets | manage guild |
| `ticket remove <member>` | Remove someone from this ticket |  |
| `ticket rename <name>` | Rename this ticket |  |
| `ticket support <role>` | Toggle a support role | manage guild |

## 🛠️ Utility
_Handy everyday tools_

| Command | Description | Permission |
|---|---|---|
| `afk [reason=AFK]` | Set an AFK status; people who mention you are told |  |
| `avatar [user]` (av, pfp) | Get a user's avatar |  |
| `banner [user]` | Get a user's banner |  |
| `botinfo` (bi, about, stats) | About the bot |  |
| `calculate <expression>` (calc, math) | Safely evaluate a math expression |  |
| `clearsnipe` (cs) | Clear snipe history for this channel | manage messages |
| `createembed <script>` (ce, embed) | Send a message from an embed script — see `embedhelp` | manage messages |
| `editsnipe [index=1]` (es) | Show a recently edited message |  |
| `embedcode [message_link]` (copyembed) | Turn an existing embed into a script (reply to it or give a link) | manage messages |
| `embedhelp` | How to write embed scripts |  |
| `emoji <emoji>` (e, jumbo, enlarge) | Enlarge a custom emoji |  |
| `firstmessage [channel]` (firstmsg) | Jump to the first message in a channel |  |
| `icon` (servericon) | Get the server icon |  |
| `inrole <role>` | List members with a role |  |
| `invite` (inv) | Invite the bot |  |
| `membercount` (mc) | Member count breakdown |  |
| `messageboard [days=0]` (topmsgs, topmessages) | Most active members (optionally over the last N days) |  |
| `messages [member]` (msgs) | Message stats for a member |  |
| `notes <member>` (note) | View staff notes for a member | manage messages |
| `notes add <member> <note>` | Add a staff note for a member | manage messages |
| `notes clear <member>` (remove, del) | Clear all staff notes for a member | manage messages |
| `ping` | Latency |  |
| `poll <question>` | Quick poll — `poll Pizza? \| Yes \| No \| Maybe` for options | manage messages |
| `reactionsnipe` (rs) | Show recently removed reactions |  |
| `remind <duration> [reminder=something]` (remindme, reminder) | `remind 2h30m take out the trash` |  |
| `remind delete <reminder_id>` (remove, cancel) | Cancel a reminder |  |
| `remind list` | Your reminders |  |
| `roleinfo <role>` (ri) | Info about a role |  |
| `say [channel] <message>` | Make the bot say something (embed scripts supported) | manage messages |
| `sbanner` (serverbanner) | Get the server banner |  |
| `serveravatar [member]` (sav, spfp) | Get a member's server-specific avatar |  |
| `serverinfo` (si, guildinfo) | Info about this server |  |
| `snipe [index=1]` (s) | Show a recently deleted message |  |
| `steal [emojis...]` (addemoji, stealemoji) | Add emojis from other servers (or reply to a message containing them) | manage expressions |
| `togglesnipe` | Enable/disable snipe commands server-wide | manage guild |
| `uptime` (up) | How long the bot has been online |  |
| `userinfo [user]` (ui, info, whois, user) | Info about a user |  |

## 🎙️ VoiceMaster
_Temporary personal voice channels_

| Command | Description | Permission |
|---|---|---|
| `vc` (voice) | Control your VoiceMaster channel |  |
| `vc claim` | Claim a channel whose owner left |  |
| `vc hide` (ghost) | Hide your channel |  |
| `vc limit <limit>` | Set a user limit (remembered) |  |
| `vc lock` | Lock your channel |  |
| `vc permit <target>` (allow) | Let a member/role join even when locked |  |
| `vc reject <member>` (deny, kick) | Block a member and kick them out |  |
| `vc rename <name>` (name) | Rename your channel (remembered for next time) |  |
| `vc reveal` (unhide) | Reveal your channel |  |
| `vc status [status]` | Set your channel's voice status |  |
| `vc transfer <member>` | Give ownership to someone in your channel |  |
| `vc unlock` | Unlock your channel |  |
| `voicemaster` (vm) | Set up and manage VoiceMaster |  |
| `voicemaster bitrate <kbps>` | Default bitrate for new channels | manage guild |
| `voicemaster defaultname <name>` | Default channel name (`{user}` = owner's name) | manage guild |
| `voicemaster defaultrole [role]` | Role given to members who create a channel | manage guild |
| `voicemaster interface [channel]` | Re-send the interface panel | manage guild |
| `voicemaster reset` | Delete the VoiceMaster setup | manage guild |
| `voicemaster setup` | Create the VoiceMaster category, join channel and interface | manage channels, manage guild, move members |
