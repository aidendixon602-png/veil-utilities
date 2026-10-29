# Command reference

495 commands. Default prefix `,`. `<required>` `[optional]`.


## 🤖 AI
_Ask AI, generate images, text-to-speech and OCR_

| Command | Description | Permission |
|---|---|---|
| `ask <prompt>` (ai, chat, gpt) | Ask the AI anything |  |
| `ask provider` (model, status) | Show which AI provider and model the bot is using |  |
| `ask reset` (clear, forget) | Forget your conversation in this channel |  |
| `imagegen <prompt>` (imagine, generate, img) | Generate an image from a prompt (free, no key needed) |  |
| `ocr [image]` (readimage) | Read the text in an image (attach, reply or link) |  |
| `summarize [messages=50]` (tldr) | AI summary of the recent chat |  |
| `tts <text>` (speak) | Turn text into speech |  |

## 🛡️ Antinuke
_Stop rogue admins and raids before they wreck your server_

| Command | Description | Permission |
|---|---|---|
| `antialt` (alt, antialts) | Configure Anti-Alt security to screen newly created burner accounts | administrator |
| `antialt action <action>` (punish, punishment) | Choose what happens to alt accounts | administrator |
| `antialt avatar <state>` | Block accounts without a custom avatar | administrator |
| `antialt minage [days=0]` (age, days) | Set how old an account must be to join | administrator |
| `antialt whitelist <user>` (allow, trust) | Whitelist an alt account so it can join without being blocked | administrator |
| `antinuke` (an) | Show the antinuke's status and rules |  |
| `antinuke admin <member>` | Add or remove an antinuke admin (server owner only) |  |
| `antinuke admins` (adminlist, details) | List antinuke admins and whitelisted accounts |  |
| `antinuke approve <bot>` (approvebot) | Give a quarantined new bot its permissions back (server owner only) |  |
| `antinuke ban [count] [punishment] [timeframe]` | Configure the ban rule |  |
| `antinuke botadd [count] [punishment] [timeframe]` (bot, bots) | Configure the bot add rule |  |
| `antinuke botquarantine [state]` (botq) | Hold new bots' dangerous permissions until you approve them |  |
| `antinuke channelcreate [count] [punishment] [timeframe]` | Configure the channel create rule |  |
| `antinuke channeldelete [count] [punishment] [timeframe]` | Configure the channel delete rule |  |
| `antinuke disable` (off) | Turn antinuke off (server owner only) |  |
| `antinuke enable` (on) | Turn antinuke on |  |
| `antinuke kick [count] [punishment] [timeframe]` | Configure the kick rule |  |
| `antinuke log [channel]` | Set where antinuke alerts go (owner always gets a DM) |  |
| `antinuke module <module> <state> [threshold=3]` | Turn one antinuke rule on or off |  |
| `antinuke panic [reason]` (emergency) | Lock the whole server down in an emergency |  |
| `antinuke permissions [count] [punishment] [timeframe]` (permission, perms) | Configure the permission grant rule |  |
| `antinuke preset` | Save, load or list antinuke rule presets |  |
| `antinuke preset list` | List available antinuke presets |  |
| `antinuke preset load <name>` | Load an antinuke preset (default, strict, relaxed, or saved custom) |  |
| `antinuke preset save <name>` | Save current antinuke rules as a named preset |  |
| `antinuke punishment <punishment>` | Set the default punishment: ban, kick or strip |  |
| `antinuke reset [action]` | Reset an action rule or all rules to defaults |  |
| `antinuke rolecreate [count] [punishment] [timeframe]` | Configure the role create rule |  |
| `antinuke roledelete [count] [punishment] [timeframe]` | Configure the role delete rule |  |
| `antinuke roleinvite [count] [punishment] [timeframe]` (invite) | Configure the invite spam rule |  |
| `antinuke serverupdate [count] [punishment] [timeframe]` (server) | Configure the server update rule |  |
| `antinuke set <action> <arg2> [arg3] [arg4]` (rule, configure) | Configure an antinuke rule |  |
| `antinuke status` (config, settings, panel) | Antinuke status and rules, with buttons to change them |  |
| `antinuke timeout [count] [punishment] [timeframe]` (mute) | Configure the timeout rule |  |
| `antinuke toggle` (on/off) | Toggle antinuke on or off |  |
| `antinuke unpanic` (calm, endpanic) | End panic mode and put everything back (server owner only) |  |
| `antinuke unwhitelist <target>` (unwl, untrust) | Stop trusting a user, bot or role |  |
| `antinuke webhook [count] [punishment] [timeframe]` (webhooks) | Configure the webhook rule |  |
| `antinuke whitelist [target]` (wl, trust) | Trust a user, bot or role in the antinuke |  |
| `antinuke window <seconds>` | Set the default time window thresholds are counted over |  |
| `antiraid` (raid) | Filter suspicious joins: mass joins, new accounts, no avatar | administrator |
| `antiraid age [days=0]` | Punish accounts younger than a number of days | administrator |
| `antiraid avatar <state>` | Punish accounts with no avatar (on/off) | administrator |
| `antiraid end` (stop, off, over) | End raid mode now | administrator |
| `antiraid lockdown <what> <state>` (lock) | Choose what raid mode locks down | administrator |
| `antiraid massjoin [joins=0] [seconds=10]` | Punish everyone who joins in a wave | administrator |
| `antiraid punishment <punishment>` | Choose what happens to suspicious joiners: kick, ban or timeout | administrator |
| `antiraid start` (on, now) | Turn raid mode on now | administrator |
| `antiraid whitelist <user>` | Let a specific user bypass antiraid | administrator |
| `autosetup` (autoprotect) | Switch on any protection this server hasn't set up yet |  |

## 🧹 Automod
_Automatic chat filters_

| Command | Description | Permission |
|---|---|---|
| `antighostping` (ghostping, antighost) | Configure anti-ghostping detection to catch deleted mentions | manage guild |
| `antighostping channel [channel]` | Set channel where ghostping alerts are posted (omit for same channel) | manage guild |
| `antighostping off` (disable) | Disable anti-ghostping detection | manage guild |
| `antighostping on` (enable) | Enable anti-ghostping detection | manage guild |
| `filter` (automod, am) | View automod filters | manage guild |
| `filter action <action> [seconds=60]` | Choose what happens when a message breaks a filter | manage guild |
| `filter allowlink <domain>` | Allow or disallow a site through the link filter | manage guild |
| `filter caps [percent=0]` | Block messages over N% caps (0 disables) | manage guild |
| `filter emojis [limit=0]` | Block messages with N+ emojis (0 disables) | manage guild |
| `filter exempt <target>` | Toggle a role or channel bypassing filters | manage guild |
| `filter invites <state>` | Block Discord invites (on/off) | manage guild |
| `filter links <state>` | Block all links (on/off) | manage guild |
| `filter list` | List blocked words and patterns | manage guild |
| `filter massmention [limit=0]` (mentions) | Block messages with N+ mentions (0 disables) | manage guild |
| `filter newlines [limit=0]` | Block messages with N+ line breaks (0 disables) | manage guild |
| `filter regex <pattern>` | Toggle a blocked regex pattern | manage guild |
| `filter spam [messages=0] [seconds=5]` | Limit how many messages someone can send in a few seconds | manage guild |
| `filter word <word>` (words, add) | Block or unblock a word | manage guild |
| `filter zalgo <state>` | Block zalgo/glitch text | manage guild |

## 🧩 Config
_Welcome messages, roles, responders, starboard and more_

| Command | Description | Permission |
|---|---|---|
| `autoresponder` (ar, autoresponse) | Automatic replies to trigger phrases | manage guild |
| `autoresponder add <args>` | Reply automatically when someone says a trigger | manage guild |
| `autoresponder list` | List autoresponders | manage guild |
| `autoresponder remove <trigger>` (delete) | Remove an autoresponder | manage guild |
| `autorole` | Roles given automatically on join | manage roles |
| `autorole add <role>` (all) | Toggle a role for everyone who joins | manage roles |
| `autorole bots <role>` | Toggle a role for bots only | manage roles |
| `autorole delay <seconds>` | Wait before giving autoroles | manage roles |
| `autorole humans <role>` | Toggle a role for humans only | manage roles |
| `boost [channel] [message]` | Set the boost thank-you message | manage guild |
| `boost off` | Disable boost messages | manage guild |
| `boost role [role]` | Extra role given to boosters (removed when they stop) | manage roles |
| `boost test` | Preview the boost message | manage guild |
| `bump [state]` (bumpreminder) | Remind the server to bump on Disboard every 2 hours | manage guild |
| `bump message <message>` | Set the reminder message | manage guild |
| `bump thankyou <message>` (thanks) | Set the thank-you message | manage guild |
| `buttonrole [roles]... [title=Pick your roles]` (buttonroles, rolemenu) | Post a panel of self-role buttons | manage roles |
| `counter` | Channels that display live server stats | manage channels |
| `counter add <kind> [template]` | Add a channel that shows a live count | manage channels |
| `counter remove <channel>` | Remove a counter (and its channel) | manage channels |
| `goodbye [channel] [message]` (leave) | Set the goodbye message | manage guild |
| `goodbye off` | Disable goodbye messages | manage guild |
| `goodbye test` | Preview the goodbye message | manage guild |
| `placeholders` (variables) | List variables you can use in messages |  |
| `reactionrole` (rr) | Give roles when members react to a message | manage roles |
| `reactionrole add <message> <emoji> <role>` | Add a reaction role to a message | manage roles |
| `reactionrole list` | List reaction roles | manage roles |
| `reactionrole remove <message> <emoji>` | Remove a reaction role | manage roles |
| `reactiontrigger` (rt) | Auto-react to messages containing a word | manage guild |
| `reactiontrigger add <emoji> <trigger>` | React automatically when a word is said | manage guild |
| `reactiontrigger list` | List reaction triggers | manage guild |
| `reactiontrigger remove <trigger>` | Remove all reactions for a trigger | manage guild |
| `starboard [channel] [threshold=3] [emoji=⭐]` (sb) | Set up a starboard | manage guild |
| `starboard off` | Disable the starboard | manage guild |
| `sticky [channel] [message]` | Keep a message at the bottom of a channel | manage messages |
| `sticky remove <channel>` | Remove a sticky message | manage messages |
| `vanity [role] [text]` | Reward members who put your invite or text in their status | manage guild |
| `vanity off` | Disable vanity rewards | manage guild |
| `welcome [channel] [message]` (greet) | Set the welcome message | manage guild |
| `welcome deleteafter [seconds=0]` | Delete welcome messages after a few seconds | manage guild |
| `welcome off` (disable) | Disable welcome messages | manage guild |
| `welcome preset <channel>` (card, template) | Set up a ready-made welcome message in a channel | manage guild |
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
| `feeds` (socials, notify, alerts) | Post new uploads from YouTube, Twitch, Reddit, X and more | manage guild |
| `feeds add <platform> <account> [channel]` | Post an account's new uploads in a channel | manage guild |
| `feeds list` | List feeds | manage guild |
| `feeds message <feed_id> [message]` | Change the message a feed posts | manage guild |
| `feeds ping <feed_id> [role]` (role) | Ping a role when a feed posts (omit to stop) | manage guild |
| `feeds remove <feed_id>` (delete) | Remove a feed | manage guild |
| `feeds test <feed_id>` | Post the latest item from a feed right now | manage guild |

## 🎲 Fun
_Games and silly commands_

| Command | Description | Permission |
|---|---|---|
| `8ball <question>` (8b) | Ask the magic 8-ball |  |
| `advice` (tip, wisdom) | Receive random daily advice and wisdom | uperm: helper |
| `bite [member]` | Bite someone |  |
| `cat` | Random cat |  |
| `choose <options>` (pick) | Pick one of several options |  |
| `coinflip` (cf, flip) | Flip a coin |  |
| `coolrate [member]` (howcool, rate) | How cool is someone |  |
| `cuddle [member]` | Cuddle someone |  |
| `dog` | Random dog |  |
| `feed [member]` | Feed someone |  |
| `handhold [member]` | Hold someone's hand |  |
| `highfive [member]` | High-five someone |  |
| `hug [member]` | Hug someone |  |
| `joke` (dadjoke, pun) | Get a random dad joke or pun | uperm: helper |
| `kiss [member]` | Kiss someone |  |
| `pat [member]` | Pat someone |  |
| `peck [member]` | Give someone a little kiss |  |
| `poke [member]` | Poke someone |  |
| `punch [member]` | Punch someone |  |
| `redditmeme` (dankmeme, rmeme) | Fetch a random trending meme from Reddit | uperm: helper |
| `reverse <text>` | Reverse text |  |
| `roll [dice=1d6]` (dice) | Roll dice |  |
| `rps <choice>` | Rock paper scissors |  |
| `ship <a> [b]` | Ship compatibility |  |
| `slap [member]` | Slap someone |  |
| `tickle [member]` | Tickle someone |  |
| `tictactoe <opponent>` (ttt) | Play tic-tac-toe with someone |  |
| `uwu <text>` | UwU-ify text |  |
| `uwulock <member>` | Toggle forcing someone's messages into uwu speak | manage messages, manage webhooks |
| `wave [member]` | Wave at someone |  |
| `yeet [member]` | Yeet someone |  |

## 🎉 Giveaways
_Host giveaways_

| Command | Description | Permission |
|---|---|---|
| `giveaway` (gw, g) | Run giveaways |  |
| `giveaway cancel <message_id>` (delete) | Cancel a giveaway without picking winners | manage guild |
| `giveaway end <message_id>` | End a giveaway early | manage guild |
| `giveaway list` | Active giveaways |  |
| `giveaway reroll <message_id> [winners=1]` | Pick new winners for a giveaway | manage guild |
| `giveaway start <duration> <winners> <prize>` (create) | Start a giveaway | manage guild |

## 🖼️ Imaging
_Edit images, GIFs, avatars and emojis_

| Command | Description | Permission |
|---|---|---|
| `blur [image]` | Blur an image |  |
| `brighten [image]` | Brighten an image |  |
| `caption <text>` (cap) | Add a white caption bar above an image (attach or reply to one) |  |
| `censor [image]` | Mosaic-censor the middle of an image |  |
| `chaos [image]` (randomfx) | Stack 3 random effects |  |
| `circle [image]` | Crop an image into a circle |  |
| `deepfry [image]` | Deep-fry an image |  |
| `duotone [image]` | Recolor an image in a pink-on-dark duotone |  |
| `edges [image]` | Trace the edges in an image |  |
| `emboss [image]` | Emboss an image |  |
| `glitch [image]` | Add a corrupted glitch effect |  |
| `grayscale [image]` | Make an image black and white |  |
| `invert [image]` | Invert an image's colors |  |
| `jpeg [image]` | Crush an image with heavy JPEG compression |  |
| `meme <text>` | Add meme text to an image |  |
| `mirror [image]` | Mirror an image left to right |  |
| `oilify [image]` | Make an image look like an oil painting |  |
| `pixelate [image]` | Pixelate an image |  |
| `posterize [image]` | Posterize an image's colors |  |
| `rainbow [image]` | Lay a rainbow gradient over an image |  |
| `rotate [image]` | Rotate an image 90° |  |
| `saturate [image]` | Crank up an image's saturation |  |
| `sepia [image]` | Give an image an old-photo sepia tone |  |
| `sharpen [image]` | Over-sharpen an image |  |
| `sketch [image]` | Turn an image into a pencil sketch |  |
| `solarize [image]` | Solarize an image (inverted highlights) |  |
| `upsidedown [image]` | Flip an image upside down |  |
| `vignette [image]` | Add a dark vignette around an image |  |
| `warp [image]` | Warp an image with a wavy distortion |  |

## 🌐 Integrations
_Look things up across the web_

| Command | Description | Permission |
|---|---|---|
| `convert <amount> <from_curr> <to_curr>` (currency, exchange) | Convert between currencies at today's rates | uperm: helper |
| `crypto [coin=bitcoin] [currency=usd]` (price, coin) | Show a cryptocurrency's price and market data | uperm: helper |
| `define <word>` (dictionary, dict) | Look up an English word | uperm: helper |
| `github <username>` (gh) | Look up a GitHub profile | uperm: helper |
| `github repo <repo>` | Look up a GitHub repository | uperm: helper |
| `ipinfo <query>` (ip, geoip) | Look up where an IP address or domain is | uperm: helper |
| `lastfm [member]` (lf, fm) | Show what you're listening to on Last.fm | uperm: helper |
| `lastfm profile [member]` (user) | Last.fm profile |  |
| `lastfm reactions [emojis...]` | Set the reactions added to now-playing embeds (none to disable) | manage guild |
| `lastfm recent [member]` (rt) | Recent tracks |  |
| `lastfm set <username>` (login, link) | Link your Last.fm account |  |
| `lastfm topalbums [period=all] [member]` (tab) | Top albums |  |
| `lastfm topartists [period=all] [member]` (ta) | Your top artists on Last.fm |  |
| `lastfm toptracks [period=all] [member]` (tt) | Top tracks |  |
| `lastfm unlink` (logout) | Unlink Last.fm |  |
| `lastfm whoknows [artist]` (wk) | Who in this server listens to an artist the most |  |
| `minecraft <address>` (mcserver, mc_status) | Check a Minecraft server's status | uperm: helper |
| `nowplaying [member]` (np, nowplay) | Your current/last Last.fm track | uperm: helper |
| `npm <package>` (nodejs, npmpkg) | Look up a package on npm | uperm: helper |
| `pypi <package>` (pythonpkg, pip) | Look up a Python package on PyPI | uperm: helper |
| `qr <text_or_url>` (qrcode) | Make a QR code for text or a link | uperm: helper |
| `roblox <username>` (rblx) | Roblox profile lookup | uperm: helper |
| `translate <language> [text]` (tr) | Translate text, or the message you reply to | uperm: helper |
| `urban <term>` (ud) | Urban Dictionary definitions and slang | uperm: helper |
| `weather <location>` | Show the weather anywhere | uperm: helper |
| `wikipedia <query>` (wiki) | Wikipedia summary | uperm: helper |

## 📈 Levels
_Reward activity with XP and roles_

| Command | Description | Permission |
|---|---|---|
| `leaderboard` (lb, top) | Server XP leaderboard |  |
| `leveling` (levels) | Configure leveling | manage guild |
| `leveling channel [channel]` | Send level-ups to a channel (omit for same channel) | manage guild |
| `leveling ignore <channel>` | Toggle a channel not giving XP | manage guild |
| `leveling message [message]` | Set the level-up message | manage guild |
| `leveling multiplier <role> [multiplier=1.0]` | Give a role more XP per message | manage guild |
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
| `cleanuplogs` (cleanuplogchannels, purgeduplicatelogs) | Delete duplicate log channels the bot made | manage guild |
| `logs` (log) | Open the logging panel: see and change where each kind of log goes | manage guild |
| `logs auto <category>` (reset) | Go back to finding a category's channel by name | manage guild |
| `logs cleanup` (clean, purgeduplicates, cleanupchannels) | Delete duplicate log channels the bot made | manage guild |
| `logs ignore` | Skip logging messages, voice and commands from a channel or member | manage guild |
| `logs ignore add <target>` | Ignore a channel or member | manage guild |
| `logs ignore remove <target>` (del, delete) | Stop ignoring a channel or member | manage guild |
| `logs off <category>` (disable) | Stop logging a category | manage guild |
| `logs set <category> [channel]` | Send a log category to a channel | manage guild |
| `logs setup [mode]` (init, default, defaults, create) | Give every log category its own private channel | manage guild |
| `logs test` | Send a test line to every log channel | manage guild |
| `setuplogs` (setupchannels, autologs) | Make a private channel for every log category | manage guild |

## 🔨 Moderation
_Keep your server in line_

| Command | Description | Permission |
|---|---|---|
| `addpermall <permission>` (apa) | Add a (non-dangerous) permission to every role below me | administrator, manage roles |
| `addrole <arg1> <arg2>` (giverole) | Add a role to a member | manage roles, uperm: admin |
| `admin [member]` (adminpanel, ap) | Open the admin panel for a member or the server | uperm: admin |
| `ban <user> [reason]` | Ban a member, or anyone by user ID | ban members, uperm: admin |
| `botclear [amount=100]` (bc) | Delete bot messages and command invocations | manage messages, uperm: helper |
| `cases [user]` (history, modlogs) | View moderation history (for the server or a user) | moderate members |
| `checkbled [target]` (checkbl, scanbl, bledcheck, rblcheck, roleblcheck) | Check for members holding blacklisted roles |  |
| `clearwarns <member>` (clearwarnings) | Clear all of a member's warnings | moderate members, uperm: moderator |
| `delwarn <warn_id>` (unwarn) | Delete a single warning by ID | moderate members, uperm: moderator |
| `forcenickname <member> [nickname]` (fn, forcenick) | Lock a member's nickname (omit nickname to unlock) | manage nicknames, uperm: moderator |
| `hide [channel]` | Hide a channel from @everyone | manage channels, uperm: admin |
| `jail <member> [reason]` | Jail a member: strips their roles and confines them to #jail | manage roles, moderate members, uperm: moderator |
| `jail list` | List jailed members | moderate members, uperm: moderator |
| `jail setup` | Create the jail role and channel | administrator, uperm: admin |
| `kick <member> [reason]` | Kick a member | kick members, uperm: moderator |
| `lock [channel] [reason]` | Prevent @everyone from sending messages | manage channels, uperm: admin |
| `lockdown [reason=lockdown]` | Lock every text channel in the server | administrator, uperm: headadmin |
| `massban <minutes> [reason=raid cleanup]` (banrecent) | Ban everyone who joined in the last few minutes | administrator, ban members, uperm: admin |
| `masskick <minutes> [reason=raid cleanup]` (kickrecent) | Kick everyone who joined in the last few minutes | kick members, uperm: admin |
| `mod <member>` (modpanel, mp) | Open the moderator panel for a member | uperm: helper |
| `nickname <member> [nickname]` (nick) | Change (or reset) a member's nickname | manage nicknames, uperm: moderator |
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
| `quarantine <member> [reason=Quarantined by staff]` (isolate, freeze) | Isolate a member and take their roles | manage roles, moderate members, uperm: moderator |
| `rblauth <target>` (roleblauth, bledauth, rblbypass) | Let a member or role hold blacklisted roles | manage roles, uperm: headadmin |
| `rblunauth <target>` (roleblunauth, bledunauth) | Stop a member or role from holding blacklisted roles | manage roles, uperm: headadmin |
| `reason <case> <reason>` | Change the reason on a case | moderate members |
| `removepermall <permission>` (rpa) | Remove a permission from every role below the bot | administrator, manage roles |
| `removerole <arg1> <arg2>` (takerole) | Remove a role from a member | manage roles, uperm: admin |
| `role <arg1> [arg2]` (r, changerole, cr) | Give a member a role, or take it if they have it | manage roles, uperm: admin |
| `role add <arg1> <arg2>` | Add a role to a member | manage roles, uperm: admin |
| `role all <action> <role>` | Add or remove a role for everyone | administrator, manage roles, uperm: headadmin |
| `role bots <action> <role>` | Add/remove a role for all bots | administrator, manage roles, uperm: headadmin |
| `role color <role> <color>` (colour) | Change a role's color | manage roles, uperm: admin |
| `role create <name>` | Create a role | manage roles, uperm: admin |
| `role delete <role>` | Delete a role | manage roles, uperm: admin |
| `role hoist <role>` | Toggle showing a role separately | manage roles, uperm: admin |
| `role humans <action> <role>` | Add/remove a role for all humans | administrator, manage roles, uperm: headadmin |
| `role remove <arg1> <arg2>` (rem, del) | Remove a role from a member | manage roles, uperm: admin |
| `role rename <role> <name>` | Rename a role | manage roles, uperm: admin |
| `roleblacklist` (rbl, rolebl) | Blacklist roles so the bot refuses to assign them | manage roles, uperm: admin |
| `roleblacklist add <role>` | Blacklist a role | manage roles, uperm: admin |
| `roleblacklist authorize <target>` (auth, whitelist, allow) | Let a member or role hold blacklisted roles | manage roles, uperm: headadmin |
| `roleblacklist authorized` (allowed) | View members and roles authorized to bypass the role blacklist | manage roles |
| `roleblacklist check [target]` (scan, audit, search) | Check for members holding blacklisted roles |  |
| `roleblacklist list` | View blacklisted roles | manage roles |
| `roleblacklist remove <role>` (rem, delete) | Take a role off the role blacklist | manage roles, uperm: admin |
| `roleblacklist strip` (purge, clean) | Take blacklisted roles off everyone not allowed to have them | manage roles, uperm: headadmin |
| `roleblacklist unauthorize <target>` (unauth, unwhitelist) | Revoke a member's or role's authorization to bypass the role blacklist | manage roles, uperm: headadmin |
| `roles` (rolelist) | List every role in the server, highest first |  |
| `security` (secscan, auditsec, serveraudit) | Run a comprehensive security audit of this Discord server | manage guild |
| `slowmode [delay=0] [channel]` (sm) | Set this channel's slowmode | manage channels, uperm: admin |
| `softban <member> [reason]` | Ban and immediately unban to wipe a member's recent messages | ban members, uperm: admin |
| `stripbled` (purgebled, rblstrip, roleblstrip) | Take blacklisted roles off everyone not allowed to have them | manage roles, uperm: headadmin |
| `stripstaff <member>` | Remove every role with dangerous permissions from a member | administrator, uperm: headadmin |
| `timeout <member> [duration] [reason]` (mute, to, tmute) | Timeout a member (default 5m, max 28d) | moderate members, uperm: moderator |
| `unban <user> [reason]` | Unban a user | ban members, uperm: admin |
| `unhide [channel]` (reveal) | Reveal a hidden channel | manage channels, uperm: admin |
| `unjail <member> [reason]` | Release a member and give back their roles | moderate members, uperm: moderator |
| `unlock [channel] [reason]` | Allow @everyone to send messages again | manage channels, uperm: admin |
| `unlockdown` | Unlock every text channel | administrator, uperm: headadmin |
| `unquarantine <member> [reason=Released by staff]` (unfreeze, release) | Release a member from quarantine | manage roles, moderate members, uperm: moderator |
| `untimeout <member> [reason]` (unmute, untime, uto) | Remove a member's timeout | moderate members, uperm: moderator |
| `untimeout all [reason]` | Remove timeouts from every timed-out member | moderate members, uperm: admin |
| `warn <member> [reason=No reason provided]` | Warn a member | moderate members, uperm: moderator |
| `warnings <member>` (warns) | List a member's warnings | moderate members |
| `warnpunish <warnings> <action> [duration]` | Set what happens at a number of warnings | administrator, uperm: admin |

## 🎵 Music
_Play music in voice channels (Spotify/YouTube/SoundCloud via Lavalink)_

| Command | Description | Permission |
|---|---|---|
| `audiofilter [preset]` (preset, fx) | Apply an audio effect to the music |  |
| `autoplay <state>` | Keep playing recommended songs when the queue ends |  |
| `clearqueue` | Clear the queue |  |
| `deafen` (deaf, undeafen) | Toggle whether the bot is deafened in the voice channel |  |
| `djrole [role]` | Set the DJ role | manage guild |
| `loop [mode=track]` | Loop mode: track, queue or off |  |
| `lyrics [query]` (ly, songlyrics) | Show a song's lyrics | uperm: helper |
| `pause` | Pause the music |  |
| `play <query>` (p) | Play a song or playlist (URL or search) |  |
| `queue` (q) | Show the queue |  |
| `remove <position>` | Remove a track from the queue |  |
| `resume` | Resume the music |  |
| `seek <position>` | Seek to a time |  |
| `shuffle` | Shuffle the queue |  |
| `skip` (next, voteskip, vs) | Skip the current song |  |
| `song` (playing, current) | What's playing |  |
| `stop` (dc, disconnect) | Stop and leave |  |
| `volume <volume>` (vol, v) | Set the volume, from 0 to 150 |  |

## 🔑 Permissions
_Assign named permission levels to members_

| Command | Description | Permission |
|---|---|---|
| `botperms` (botpermissions, botperm) | View the bot's Discord permissions in this server and channel |  |
| `permlevels` (plevels, plvl) | View the permission levels available in this server | manage messages |
| `permlevels grant <key> <permission>` | Toggle a bot-permission a level grants (server owner) |  |
| `permlevels rank <key> <rank>` | Change a level's rank (server owner) |  |
| `permlevels rename <key> <name>` | Rename a level (server owner) |  |
| `userperm [target]` (perm, perms, uperm, ups, permission, permissions) | Manage members' and roles' permission levels |  |
| `userperm add <target> <level>` (set, give) | Assign a permission level to a member or a role | administrator, uperm: headadmin |
| `userperm check [target]` (view, info) | Check a member's or role's permission level |  |
| `userperm list` (all) | List members and roles with a permission level | administrator |
| `userperm remove <target>` (rem, delete, unset, clear) | Remove a member's or role's permission level | administrator, uperm: headadmin |
| `userperm remove deleted` | Purge permission levels for members who left the server | administrator, uperm: headadmin |

## ⚙️ Settings
_Configure how the bot behaves in your server_

| Command | Description | Permission |
|---|---|---|
| `alias` | Create shortcuts for commands | manage guild |
| `alias add <shortcut> <command>` | Add a shortcut for a command | manage guild |
| `alias list` | List aliases | manage guild |
| `alias remove <shortcut>` (delete, del) | Remove an alias | manage guild |
| `botchannels [channel]` (cmdchannels, botchannel) | Limit commands to certain channels | manage guild |
| `disablecommand <command>` (disablecmd, dcmd) | Turn a command off for non-admins | manage guild |
| `disablecommand list` | List disabled commands | manage guild |
| `enablecommand <command>` (enablecmd, ecmd) | Re-enable a disabled command | manage guild |
| `fakepermissions` (fp, fakeperms, fakeperm) | Give roles permissions that only work through the bot |  |
| `fakepermissions add <role> [permissions]` (grant, give) | Give a role permissions in the bot |  |
| `fakepermissions list` (show, view) | See which roles have fake permissions |  |
| `fakepermissions remove <role> [permissions=all]` (revoke, take, rm) | Take a role's bot permissions back |  |
| `fakepermissions reset` (clear) | Remove every fake permission in the server |  |
| `ignore <target>` | Toggle whether the bot ignores commands from a channel or member | manage guild |
| `ignore list` | List ignored channels and users |  |
| `modlog [channel]` | Set (or clear) the channel where moderation cases are posted | manage guild |
| `prefix` | View the server prefix |  |
| `prefix set <prefix>` | Change the server prefix | manage guild |
| `selfprefix [prefix]` | Set your own prefix that works everywhere |  |
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
| `createembed <script>` (ce, embed) | Send a message built from an embed script | manage messages |
| `editsnipe [index=1]` (es) | Show a recently edited message |  |
| `embedcode [message_link]` (copyembed) | Turn an existing embed into a script (reply to it or give a link) | manage messages |
| `embedhelp` | How to write embed scripts |  |
| `emoji <emoji>` (e, jumbo, enlarge) | Enlarge a custom emoji |  |
| `firstmessage [channel]` (firstmsg) | Jump to the first message in a channel |  |
| `icon` (servericon) | Get the server icon |  |
| `inrole <role>` | List members with a role |  |
| `membercount` (mc) | Member count breakdown |  |
| `messageboard [days=0]` (topmsgs, topmessages) | Show the most active members |  |
| `messages [member]` (msgs) | Message stats for a member |  |
| `notes <member>` (note) | View staff notes for a member | manage messages |
| `notes add <member> <note>` | Add a staff note for a member | manage messages |
| `notes clear <member>` (remove, del) | Clear all staff notes for a member | manage messages |
| `ping` | Latency |  |
| `poll <question>` | Start a quick poll | manage messages |
| `reactionsnipe` (rs) | Show recently removed reactions |  |
| `remind <duration> [reminder=something]` (remindme, reminder) | Set a reminder |  |
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

## 👻 Vanguard
_Ghost Vanguard's commands: `,gv help` lists them_

| Command | Description | Permission |
|---|---|---|
| `gv [command]` (vanguard, ghost) | Run a Ghost Vanguard command |  |

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
| `voicemaster defaultname <name>` | Set the default name for new voice channels | manage guild |
| `voicemaster defaultrole [role]` | Role given to members who create a channel | manage guild |
| `voicemaster interface [channel]` | Re-send the interface panel | manage guild |
| `voicemaster reset` | Delete the VoiceMaster setup | manage guild |
| `voicemaster setup` | Create the VoiceMaster category, join channel and interface | manage channels, manage guild, move members |
