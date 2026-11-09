# Developer operations

[README](../README.md) · [Configuration](CONFIGURATION.md) · [Operations](OPERATIONS.md) · [Contributing](../.github/CONTRIBUTING.md)

Veil's hidden developer commands are not part of the public [command reference](COMMANDS.md). Use `,dev` in Discord for developer help in your running version, or consult the [generated developer command reference](DEVELOPER_COMMANDS.md). Developer identities include configured `OWNER_IDS` and discovered Discord application owner/team members. These identities have broad privileges across the bot; protect their accounts and never grant developer status merely to enable alerts.

| Command | Purpose and cautions |
| --- | --- |
| `,system` | Show bot health, uptime, database type, memory and maintenance status. |
| `,version` | Show loaded, on-disk and remote version information when available. |
| `,cogs` | List loaded extensions and commands. |
| `,errors`, `,error [id]`, `,traceback` | Inspect recent command errors recorded since startup. Treat diagnostics as sensitive. |
| `,botlogs [lines]` | Attach the end of the local log with known secrets scrubbed. Review the destination: redaction is not a guarantee that all private data is removed. |
| `,backup` | DM a compressed SQLite backup, subject to upload size and DM availability; it does not back up PostgreSQL. |
| `,authorize <server-id>`, `,unauthorize <server-id>`, `,authorized` | Manage and list server authorization. Revocation may cause the bot to leave. |
| `,maintenance on [duration] [reason]`, `,maintenance off` | Control maintenance mode. |
| `,killswitch <command>`, `,killswitch list` | Toggle a command globally or list disabled commands. |
| `,reload [extensions...]` | Reload extensions. Test in a safe environment; reload does not guarantee all state can be restored. |
| `,restart [seconds]`, `,shutdown` | Restart or stop the bot. Your hosting supervisor may affect process behavior. |
| `,gitpull` | Fetch and **hard reset** the checkout to `origin/main`. It can discard local changes and commits; see [updating](HOSTING.md#updating). |
| `,testserver`, `,testnuke`, `,testcleanup` | Developer test-server controls, additionally restricted to `OWNER_IDS`. Test runs can create and delete resources; use a disposable server only. |

Other developer commands can execute arbitrary code or shell commands, query databases, impersonate command invocations, change multiple servers, or alter roles and bans. Treat them as administrative access to the host and Discord servers. Do not expose output to untrusted channels or share credentials. Check `,dev` and source for current syntax and access checks rather than assuming every developer tool is safe or reversible.

Developer-channel errors and the unauthorized-server/antinuke notification outbox have different delivery and persistence behavior; see [developer notifications](OPERATIONS.md#developer-notifications). Full incident management, complete event coverage and automated recovery are not yet implemented.

### Network genreban prerequisite

`genreban`, `hardban`, `reban`, `globalban`, and `hb` are aliases for the same developer-only
cross-server operation. Before a new network ban, the target must already have a standard Discord
ban in the server where the command is invoked. Veil checks the ban via Discord before prompting
and again after confirmation; missing server context, an unbanned target, insufficient permission,
or an API failure prevents the operation. A later unban may race the final check. Veil records the
user, moderator, reason and time in `globalbans`, preserving an existing record. The cross-server
loop is best effort and may partially fail; enforcement on later unbans/rejoins is not guaranteed.
This does not change or lift existing network bans.
