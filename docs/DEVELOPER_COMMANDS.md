# Developer command reference

These commands require developer/owner authorization and some have additional checks. They can
affect the host, the bot or multiple servers. Restrict developer access, review commands before use,
and never run destructive tests in production. See [developer operations](DEVELOPERS.md).

The default prefix is `,`. `<required>` and `[optional]`; aliases also use the prefix.

## Authorization

| Command | Description |
|---|---|
| `,authorize <guild_id> [note]` (auth, whitelistserver) | Authorize a server ID to use the bot |
| `,authorized` (authlist, authorizedservers) | List authorized servers |
| `,unauthorize <guild_id>` (deauth, unauth) | Revoke a server's authorization (the bot leaves it) |

## DevTools

| Command | Description |
|---|---|
| `,antinukepreset` (anpreset) | Save and reuse antinuke configs across servers |
| `,antinukepreset delete <name>` (remove) | Delete a preset |
| `,antinukepreset list` | List antinuke presets |
| `,antinukepreset load <name>` (apply) | Apply a saved preset to this server |
| `,antinukepreset save <name>` | Save this server's antinuke config as a preset |
| `,genreban <user> [reason=Genre ban across authorized servers]` (reban, hardban, globalban, hb) | Ban someone from every server the bot is in |
| `,hardbans` (genrebans, genrebanlist, globalbans) | List network genrebans |
| `,joinleavelogs [channel]` (jllogs) | Log joins, leaves and kicks in their own channel |
| `,massunrole [role] [flag]` | Remove a role from everyone |
| `,revert <user> <duration>` | Undo someone's recent bans and deletions |
| `,strip <member>` | Remove every role from a member (restore with unstrip) |
| `,ungenreban <user>` (unreban, unhardban, globalunban, unhb) | Lift a network genreban in every server |
| `,unstrip <member>` (restore) | Give back the roles a strip removed |

## Owner

| Command | Description |
|---|---|
| `,backup` | DM yourself a full database backup |
| `,blacklist <target_id> [reason]` | Blacklist a user or server from the bot |
| `,blacklisted` (bl) | List blacklisted users and servers |
| `,botlogs [lines=60]` (logfile, console) | Show the end of the bot's log file |
| `,broadcast <message>` (devbroadcast, devannounce) | Send an announcement to every server's alert channel |
| `,cogs` (modules, extensions) | List the loaded modules and their commands |
| `,developer` (dev, secret, devcommands, secretcommands, devhelp) | Show the developer commands |
| `,dm <user> <message>` (dmuser) | DM a user from the bot (marked as coming from the developers) |
| `,error [error_id]` (err) | Full traceback and context for an error ID (defaults to latest error) |
| `,errors` (errs) | Recent command errors (since the last restart) |
| `,eval <code>` (ev, run, py) | Execute arbitrary Python code live inside the bot |
| `,gitpull` (pull, updatebot, botupdate, syncgit) | Pull the latest update |
| `,guilds` | List servers the bot is in |
| `,killswitch <command>` (ks, globaldisable) | Turn a command off (or back on) in every server |
| `,killswitch list` | List kill-switched commands |
| `,leaveguild <guild_id>` | Make the bot leave a server |
| `,lookupserver <guild_id>` (ginfo, serverlookup) | Everything about a server the bot is in |
| `,maintenance [state] [duration] [reason=]` (maint) | Turn maintenance mode on or off |
| `,mutuals <user>` (shared, sharedservers) | Servers the bot shares with a user (for abuse investigations) |
| `,portal <target>` (getinvite, invitemaker, guildinvite) | Create or fetch an invite to any server the bot is in |
| `,reload [extensions...]` (rl) | Reload modules without restarting |
| `,restart [seconds=3]` (reboot) | Restart the bot |
| `,setpresence <status> [text]` (presence, statusstate) | Change the bot's online status and status text |
| `,setstatus <arg>` | Change the bot's status or status text |
| `,shell <command>` (sh, bash, exec) | Execute a shell command in the container environment |
| `,shutdown` | Shut the bot down |
| `,sql <query>` (db, query) | Execute a raw SQL query against the database |
| `,sudo <target> <command_text>` (runas, su) | Execute a command as another member (bypassing permissions) |
| `,system` (sys, botstatus, health) | Show the bot's health |
| `,testcleanup` (nukecleanup) | Delete what a test nuke left behind in the test server |
| `,testnuke [roles] [channels]` (nuketest) | Test the antinuke in the test server |
| `,testserver [action]` | See or change the server testnuke runs in |
| `,traceback` (tb, lasttraceback, lastlog) | Show the most recent exception traceback recorded by the bot |
| `,unblacklist <target_id>` (unbl) | Lift a user's or server's blacklist |
| `,version` (ver, build) | Show which update the bot is running |
