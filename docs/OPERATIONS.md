# Operations and troubleshooting

[README](../README.md) · [Configuration](CONFIGURATION.md) · [Antinuke](ANTINUKE.md) · [Hosting](HOSTING.md) · [Command reference](COMMANDS.md)

## Permissions and server authorization

Veil recognizes the Discord application's owner/team and additional `OWNER_IDS` as bot developers. Developer commands are hidden and independently checked; they include highly privileged operations. Server owners and role/user permission levels govern access to many server commands. Actual Discord permissions and role hierarchy are also required to perform Discord actions. Use `,help <command>` and `,perm roles` to inspect available information; the generated reference cannot represent every check.

With `AUTH_REQUIRED=true`, developer-owned servers are automatically authorized. Other servers need a developer's `,authorize <server-id>` or the authorization button in an alert, then a re-invite. `,authorized` lists approvals and `,unauthorize <server-id>` revokes one. An unauthorized join triggers a leave attempt; attribution is reported as unknown unless a recent matching audit entry can be correlated with the join. A failed or timed-out leave is reported, not automatically retried during that session.

`AUTH_GRANDFATHER=true` can enroll already-joined servers on first initialization. An old database with an intentionally empty list needs special attention before its first upgrade; see [configuration](CONFIGURATION.md#authorization-and-developer-alerts) and the [production audit](PRODUCTION_AUDIT.md). `PRIVATE_MODE=true` restricts who can invoke commands even in an authorized server.

## Antinuke and moderation

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
| `,help` does not respond | Message Content intent in the Developer Portal, bot permissions, actual server/personal prefix, authorization and private mode. The bot can fall back to reduced intents when Discord rejects privileged intents. |
| Bot leaves a server | `AUTH_REQUIRED`, authorization list and developer alerts. If a leave failed, investigate the reported outcome. |
| A moderation or antinuke action fails | Audit-log and action permissions, target hierarchy, enabled rules, exemptions and `,an health`. Not all actions can be restored. |
| Developer DM missing | `OWNER_IDS`, Discord application discovery, recipient DM settings, `ANTINUKE_DEV_ALERTS`, database health and delivery retry logs. Only certain incident types use the durable outbox. |
| Dashboard cannot be reached | Confirm `DASHBOARD_ENABLED`, bind host, port, OAuth callback and reverse proxy. Local-only mode is deliberately restricted to loopback. |
| Music or external lookup fails | Check the relevant integration configuration and external service availability. |
| Startup unexpectedly changes files | See the default hard-reset behavior and `AUTO_SYNC_GIT=0` in [hosting](HOSTING.md#updating). |

For bugs, use the [issue templates](../.github/ISSUE_TEMPLATE/). Never attach `.env`, tokens, unredacted database URLs or private user data. For security issues, follow the [security policy](../.github/SECURITY.md).
