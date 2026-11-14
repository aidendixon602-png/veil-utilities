# Operations and troubleshooting

[README](../README.md) · [Configuration](CONFIGURATION.md) · [Antinuke](ANTINUKE.md) · [Hosting](HOSTING.md) · [Command reference](COMMANDS.md)

## Command navigation

Use `,help` and the category buttons to browse commands, or `,help <command>` for a direct guide. If the
category catalog outgrows Discord's button layout, the home screen uses a category selector instead. Paginated lists use the arrow buttons;
the middle indicator shows the current page and the close button dismisses the menu. These controls are limited
to the person who opened the menu (bot developers can also navigate standard list menus). Permission assignment
lists show **direct saved grants**, not a complete view of automatic or fake permissions. Presentation and help
visibility do not replace the checks that run when a command is used.

## Permissions and server authorization

The actual Discord server owner or a recognized bot developer is required for `,security`, `,logs` and its
subcommands, `,setuplogs`, `,cleanuplogs`, `,masskick`, `,massban`, `,nuke`, `,stripstaff`, and role-blacklist
commands including their standalone aliases and action buttons. A Veil level named Owner, Discord Administrator,
fake permissions or a command restriction cannot substitute for the actual server owner. The hidden `,strip`
command remains **developer-only**. Existing Discord bot permissions, hierarchy, and other checks still apply.
Owners should review application team access and `OWNER_IDS` before upgrading. Existing assignments are unchanged.

`,cleanuplogs` / `,logs cleanup` now **only reports channels with matching log names**; it does not delete or
move them. Names alone cannot establish ownership or whether logs are redundant. Inspect channels and back up
anything needed before manually changing them. Automatic log setup and name-based routing remain separate: they can create channels, select similarly named
channels outside the recognized Ghost/Midtown Vanguard categories, and move existing channels with a Veil topic
marker. A topic is not proof of ownership. Review the [logging configuration](#logging)
and other bots before using them; `,logs setup off` changes automatic setup globally and is developer-only.
To roll back, deploy the previous tested revision with `AUTO_SYNC_GIT=0`, knowing that older code restores the
broader command access and destructive name-based cleanup. Do not use production for security tests.

**Known self-role risk:** Do not put Veil level or fake-permission grants on roles members can obtain from
button or reaction panels. Review existing role holders as well as panels; removing a panel does not revoke roles.
See the [permissions guide](PERMISSIONS.md#review-role-assignments) for mitigation and current trust boundaries.

Veil recognizes the Discord application's owner/team and additional `OWNER_IDS` as bot developers. Developer commands are hidden and independently checked; they include highly privileged operations. Server owners and role/user permission levels govern access to many server commands. Actual Discord permissions and role hierarchy are also required to perform Discord actions. Use `,help <command>` and `,perm roles` to inspect available information; the generated reference cannot represent every check.

### Upgrade: explicit Veil access required

Shared Veil permission checks no longer fall back to a member's native Discord permissions when a server has no
saved levels. Automatic setup no longer grants levels based on role names, and native Administrator no longer
bypasses ignored/disabled commands, staff-only mode or the command cooldown. Ticket support now requires an
explicit support role, a Veil staff level, the server owner or a bot developer. These changes can remove access
from staff who previously relied on Discord permissions alone. Before upgrading, use the server owner account to
review `,perm roles`, `,perm list` and `,fakepermissions list`; explicitly configure appropriate access and test it
in a disposable server. **Previously saved assignments are preserved**, including any created by older automatic
setup; remove unintended grants deliberately. Native Discord permissions and hierarchy still govern what the bot
itself can do through Discord.

This is an initial hardening step, not the final authorization system: several commands additionally require a
Veil rank even when a fake permission is present. Explicit denies, temporary grants, configurable fine-grained
permissions and the requested Helper preset's limited timeout do not yet exist. Other authorization paths and
legacy trusted/exempt accounts remain under review. See [the staged audit](MASTER_AUDIT.md).

### Optional command role restrictions

The server owner or a bot developer can add an additional role requirement with `,restrict add <command>, @role`,
for example `,restrict add ban, @Staff`. Use `,restrict list` to inspect rules, `,restrict remove <command>, @role`
to remove one role, or `,restrict clear <command>` to remove a command's rule even if its role was deleted.
Removing the last role also clears that rule. Restricting a group applies to its subcommands; if both a group and a
subcommand have rules, the member must satisfy both. A missing/deleted role does not automatically unlock a command.

Restrictions are **additive**: they do not grant bot permissions or bypass ranks, Discord permissions, hierarchy,
blacklists, disabled commands, or other checks. The server owner and bot developers bypass role restrictions;
ordinary Discord administrators do not. Existing servers have no role restrictions until one is explicitly added.
Help uses a snapshot when it opens, so rerun `,help` after changing roles or rules. Commands always check current rules.
Changes are serialized within one bot process; concurrent multi-process administration is not supported.

### Inviting the bot

The hidden developer-only `,invite` command sends two Discord OAuth install links **by DM**: a basic messaging
profile and a management profile for moderation/antinuke. The latter requests powerful permissions such as Manage
Server, Manage Roles and Ban Members, but not Administrator. Review the permissions before installing and adjust
them for the features the server uses; the basic profile alone cannot run antinuke or moderation. Both require
appropriate role hierarchy and channel access. If DMs are closed, DM the bot and run the command there instead.

This does not bypass Discord's installation rules: when Public Bot is off, the Discord application owner must
use the link, and must have Manage Server in the destination. Veil's server authorization remains separate.
The command does not enable Public Bot or alter Discord's install-link setting.

With `AUTH_REQUIRED=true`, developer-owned servers are automatically authorized. An unauthorized join gets a
120-second window from the Discord join time for a developer to use `,authorize <server-id>`, the dashboard or
the alert's authorization button. A prompt is attempted in the server and developers are alerted during the window;
delivery depends on Discord and permissions. If approved in time, the bot stays. If not, it attempts to leave;
authorizing after it leaves requires a re-invite. The window is not renewed by a restart when Discord supplies the
join timestamp; if the timestamp is unavailable, a fresh window begins on detection. Existing command authorization
checks still apply during the wait. This grace period is not a sandbox for background listeners or bot permissions;
review what the bot can do in an unapproved server. `,authorized` lists approvals and explicit `,unauthorize`
revocations still trigger an immediate leave. Attribution is unknown unless a recent matching audit entry can be
correlated with the join. A failed or timed-out leave is reported, not automatically retried during that session.

`AUTH_GRANDFATHER=true` can enroll already-joined servers on first initialization. An old database with an intentionally empty list needs special attention before its first upgrade; see [configuration](CONFIGURATION.md#authorization-and-developer-alerts) and the [production audit](PRODUCTION_AUDIT.md). `PRIVATE_MODE=true` restricts who can invoke commands even in an authorized server.

## Antinuke and moderation

Protection setup is **opt-in**. When Veil joins an authorized server, it attempts to DM the server owner a preview;
no protection settings are changed by that prompt. Existing servers are not prompted or set up on startup. If the
DM cannot be delivered, the owner can still run `,autosetup` in the server to review the defaults and confirm or
cancel. Only the actual server owner can confirm; confirmation times out after two minutes. The setup skips Veil
modules with saved settings, even if disabled, and may separately create a Discord keyword-preset slur rule if none
exists and Veil has Manage Server. It enables bot quarantine, raid detection, message filters and warning actions
where no settings exist; review the preview carefully. An interrupted setup can leave partial changes. Previously
enabled protections are not disabled or rolled back by this upgrade. Automatic **logging** setup is independent.

Start with `,an status` and `,an health`, then review the [antinuke guide](ANTINUKE.md). Audit permissions, server-specific rules, exemptions, role position and punishment settings matter. Detection, punishment and restoration are best-effort. Some audit actions cannot be attributed or undone; only Veil commands wired into the guard count toward command-based thresholds. Test in a disposable server and avoid overlapping protection with another bot.

## Logging

`,logs` opens the panel; `,logs setup` attempts to create the category and channels and requires Manage Channels. `,logs set messages #logs` routes a category to an existing channel, `,logs off messages` disables it, and `,logs auto messages` restores automatic channel selection. Veil needs permission to view, send messages and embed links in a target channel. Use `,logs` to inspect categories and settings; local logs go to `LOG_FILE`. Log destinations and data may contain sensitive server information—restrict access and establish a retention policy.

Developer channels are separate from server log categories. `AUTH_LOG_CHANNEL` and `ERROR_LOG_CHANNEL` can override them; otherwise the bot attempts to create private channels in an eligible server. They may not exist if the bot lacks permission or a suitable server.

## Developer notifications

Real antinuke triggers (when `ANTINUKE_DEV_ALERTS=true`) and unauthorized server joins use the persistent incident outbox in `security_incidents`. Recipients are snapshotted from `OWNER_IDS`, configured built-in developer entries and discovered application owner/team members. Configure `OWNER_IDS` for everyone who must receive alerts; discoveries can fail. If no recipients are known, the incident is retained and an error is logged, but adding recipients later does **not** backfill it.

Each recipient has a recorded delivery state, attempts, error type and retry time; delivery failures for one recipient do not stop attempts to others. Retries back off to one hour. Delivery is **at least once**, not exactly once: a crash after Discord accepts a DM but before persistence may duplicate it. Use one bot process per database. If incident persistence fails, direct alerts are attempted but cannot be guaranteed. A leave that fails or times out is recorded as such. Other critical event classes, incident-history commands, resolution workflow and automatic retention are not yet implemented in this outbox. Protect the database and backups: they contain server and user IDs.

## Database and backups

SQLite stores data in `DB_PATH` (default `veil.db`) and uses WAL mode. Persist the working directory or use an explicit persistent path. Back up with the developer-only `,backup` command when the compressed file fits Discord's upload limit and DMs are available, or use SQLite's online backup API. For example, from a stopped or running bot host, replacing paths for your installation:

```bash
python - <<'PY'
import sqlite3
with sqlite3.connect('veil.db') as source, sqlite3.connect('veil-backup.db') as destination:
    source.backup(destination)
PY
```

Keep backups private, preferably off-host, and periodically test restoration on an isolated copy. Do not assume copying only the live `.db` file includes its WAL. When restoring, stop the bot and follow your SQLite recovery procedure, including handling existing sidecar files.

When `DATABASE_URL` points to PostgreSQL, `,backup` cannot export it. Use your PostgreSQL provider's backup mechanism or `pg_dump`, and test restoration. Do not put database credentials in shell history or issue reports. Switching database backends does not transfer data automatically. On upgrade, the PostgreSQL schema may be widened to BIGINT; back up first, allow table locks and ensure the account can alter tables. See [production audit](PRODUCTION_AUDIT.md).

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Bot exits during startup | Python version, installed requirements, token, `.env` syntax, writable database/log paths and console errors. Do not publish secrets. |
| Panel console shows zero lines | Confirm the configured process runs the expected `main.py`, the working directory is correct, and the panel captures stdout and stderr. See [blank panel console](HOSTING.md#blank-panel-console); container uptime alone does not prove the bot started. |
| `,firstmessage` cannot read a channel | Both the caller and the bot need View Channel and Read Message History in that channel. An empty channel has no first message. |
| `,genreban` shows its help card | The developer-only command requires a user mention or ID and a confirmation; it operates across servers and must not be tested on production users. |
| `,help` does not respond | Message Content intent in the Developer Portal, bot permissions, actual server/personal prefix, authorization and private mode. The bot can fall back to reduced intents when Discord rejects privileged intents. |
| Bot leaves a server | `AUTH_REQUIRED`, authorization list and developer alerts. If a leave failed, investigate the reported outcome. |
| A moderation or antinuke action fails | Audit-log and action permissions, target hierarchy, enabled rules, exemptions and `,an health`. Not all actions can be restored. |
| Developer DM missing | `OWNER_IDS`, Discord application discovery, recipient DM settings, `ANTINUKE_DEV_ALERTS`, database health and delivery retry logs. Only certain incident types use the durable outbox. |
| Dashboard cannot be reached | Confirm `DASHBOARD_ENABLED`, HTTPS URL, client secret, bind host, port, OAuth callback and reverse proxy. The backend binds to loopback by default and does not start without login. |
| Music or external lookup fails | Check the relevant integration configuration and external service availability. |
| Startup unexpectedly changes files | See the default hard-reset behavior and `AUTO_SYNC_GIT=0` in [hosting](HOSTING.md#updating). |

For bugs, use the [issue templates](../.github/ISSUE_TEMPLATE/). Never attach `.env`, tokens, unredacted database URLs or private user data. For security issues, follow the [security policy](../.github/SECURITY.md).
