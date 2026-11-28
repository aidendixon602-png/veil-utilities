# Developer operations

[README](../README.md) · [Configuration](CONFIGURATION.md) · [Operations](OPERATIONS.md) · [Contributing](../.github/CONTRIBUTING.md)

Veil's hidden developer commands are not part of the public [command reference](COMMANDS.md). Use `,help developer` (aliases `,dev`, `,devhelp`) in Discord for the grouped Developer help in your running version, `,help secret` (aliases `,secrets`, `,tokens`) for the sensitive Secret tools, or consult the [generated developer command reference](DEVELOPER_COMMANDS.md). Developer identities include configured `OWNER_IDS` and discovered Discord application owner/team members. These identities have broad privileges across the bot; protect their accounts and never grant developer status merely to enable alerts.

| Command | Purpose and cautions |
| --- | --- |
| `,system` | Show bot health, uptime, database type, memory and maintenance status. |
| `,version` | Show loaded, on-disk and remote version information when available. |
| `,cogs` | List loaded extensions and commands. |
| `,errors`, `,error [id]`, `,traceback` | Inspect recent command errors recorded since startup. Treat diagnostics as sensitive. |
| `,botlogs [lines]` | Attach the end of the local log with known secrets scrubbed. Review the destination: redaction is not a guarantee that all private data is removed. |
| `,backup` | DM a compressed SQLite backup, subject to upload size and DM availability; it does not back up PostgreSQL. |
| `,midtown servers` | Privately scan the bot’s current joined-server cache for names containing Midtown; DM-only results with paging and refresh. Requires bot developer identity, not server ownership or administrator status. |
| `,authorize [server-id]` (also `,authserver`), `,unauthorize <server-id>`, `,authorized` | Manage and list server authorization. Omit the ID to authorize the current server; DMs require an ID. Revocation may cause the bot to leave. |
| `,maintenance on [duration] [reason]`, `,maintenance off` | Control maintenance mode. |
| `,killswitch <command>`, `,killswitch list` | Toggle a command globally or list disabled commands. |
| `,reload [extensions...]` | Reload extensions. Test in a safe environment; reload does not guarantee all state can be restored. |
| `,restart [seconds]`, `,shutdown` | Restart or stop the bot. Your hosting supervisor may affect process behavior. |
| `,gitpull` | Fetch and **hard reset** the checkout to `origin/main`. It can discard local changes and commits; see [updating](HOSTING.md#updating). |

Other developer commands can execute arbitrary code or shell commands, query databases, impersonate command invocations, change multiple servers, or alter roles and bans. Treat them as administrative access to the host and Discord servers. Do not expose output to untrusted channels or share credentials. Check `,help developer`, `,help secret` and source for current syntax and access checks rather than assuming every developer tool is safe or reversible.

Developer-channel errors and the unauthorized-server/antinuke notification outbox have different delivery and persistence behavior; see [developer notifications](OPERATIONS.md#developer-notifications). Full incident management, complete event coverage and automated recovery are not yet implemented.

### Network bans and self-strip

`genreban`, `hardban`, `reban`, `globalban`, and `hb` are aliases for the same developer-only
cross-server operation. No prior Discord ban is required, but a server context and confirmation
are required. Veil rechecks developer access, protected targets and the previewed eligible servers
after confirmation. It records the user, moderator, reason and time in `globalbans`, preserving an
existing record. Per-server ban status that cannot be verified is skipped; the cross-server loop is
best effort and may partially fail. Enforcement on later unbans/rejoins is not guaranteed.

A developer may confirm a self-strip of removable roles, except for the server owner or a globally
whitelisted person. A role snapshot is saved before Discord is contacted; Discord failures may be
partial or uncertain. `unstrip` still enforces its normal protections, hierarchy and blacklist rules,
so some roles might need manual restoration. Unstrip lists roles Discord accepted, retains skipped roles for
review, and retains the snapshot when a Discord request has an uncertain outcome. The role calls are not
atomic; check the member's roles before retrying. Concurrent changes to saved snapshots may still race, especially
across bot instances. An existing saved strip must be handled first.

### Private Midtown inventory

Use `,midtown servers` (or the configured guild, personal or mention prefix). Results are delivered
only to the requesting developer's DMs: enable DMs from the bot before use. The command never searches
servers the bot has not joined. Matching is case-insensitive; exact means the entire name equals
`Midtown`, otherwise a substring match is partial. Missing owner IDs or member counts are shown as
unavailable. Inaccessible cache entries may make totals incomplete; member counts and names are cached
and may be stale. Long names are truncated for display. Refresh takes a new cache snapshot, not a Discord-wide search or forced API refresh.

Navigation and refresh are restricted to the original requester and recheck the bot's developer
identity at each interaction. This uses the bot's existing in-process owner/developer identity model;
application/team discovery is performed at startup, so external membership revocation is not necessarily
visible until the bot refreshes that identity or restarts. Menus expire after three minutes; re-run
the command for a new scan. The command and refresh have short per-process rate limits. Server names,
IDs and counts remain visible in the recipient's DM history and should be treated as sensitive. If
DM delivery fails, the invoking channel receives only a generic failure message.
