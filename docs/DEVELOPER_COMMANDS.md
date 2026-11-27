# Developer command reference

These commands require developer/owner authorization and some have additional checks. They can
affect the host, the bot or multiple servers. Restrict developer access, review commands before use,
and never run destructive tests in production. See [developer operations](DEVELOPERS.md).

The default prefix is `,`. `<required>` and `[optional]`; aliases also use the prefix.

## Authorization

| Command | Description |
|---|---|
| `,authorize [guild_id] [note]` (auth, authserver, whitelistserver) | Authorize this server or a server ID to use the bot |
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
| `,genreban <user> [reason]` (reban, hardban, globalban) | Attempt a cross-server genreban after confirmation |
| `,hardbans` (genrebans, genrebanlist, globalbans) | List network hardbans |
| `,joinleavelogs [channel]` (jllogs) | Log joins, leaves and kicks in their own channel |
| `,massunrole [role] [flag]` | Remove a role from everyone |
| `,midtown` | Search joined servers privately for Midtown |
| `,midtown servers` | Privately scan joined server names for Midtown |
| `,revert <user> <duration>` | Undo someone's recent bans and deletions |
| `,strip <member>` | Remove every role from a member and log the strip |
| `,ungenreban <user>` (unreban, unhardban, globalunban) | Lift a network genreban in every server |
| `,unstrip <member>` (restore) | Give back the roles a strip removed |
| `,userrolebl [target] [extra]` (urbl, norole, userblrole) | Blacklist a user from getting roles in this server |
| `,userrolebl add <target> [extra]` (block, set) | Blacklist a user from all roles or a specific role |
| `,userrolebl clear` (reset) | Clear all user role blacklists in this server |
| `,userrolebl keep <target> [extra]` | Block new roles for a user while keeping current roles |
| `,userrolebl list` (show, view) | List users blacklisted from receiving roles in this server |
| `,userrolebl remove <target> [role]` (rem, del, delete, unblock, unblacklist) | Remove a user or role from the user role blacklist |
| `,userrolebl strip <target>` (all) | Block all roles for a user and strip their current roles |

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
| `,invite` | Get a private link to install Veil in a server |
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
| `,traceback` (tb, lasttraceback, lastlog) | Show the most recent exception traceback recorded by the bot |
| `,unblacklist <target_id>` (unbl) | Lift a user's or server's blacklist |
| `,version` (ver, build) | Show which update the bot is running |
