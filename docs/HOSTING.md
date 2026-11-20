# Hosting and updating Veil Utilities

[README](../README.md) · [Configuration](CONFIGURATION.md) · [Operations](OPERATIONS.md) · [Antinuke](ANTINUKE.md)

## Deployment checklist

1. Prepare a Discord bot in the [Developer Portal](https://discord.com/developers/applications). Enable Message Content for prefix commands; enable Server Members and Presence if the features you use need them. Invite it with the permissions and role position needed for those features.
2. Choose a host with a supported Python version, internet access to Discord and any chosen integrations, and **persistent, writable storage**. CI is configured for Python 3.10–3.13; the project declares Python 3.10 or newer. Resource use depends on server size and enabled features.
3. Clone or upload this repository so `main.py`, `requirements.txt`, `core/`, `cogs/` and `assets/` are together. Create a virtual environment and install `requirements.txt`. Copy `.env.example` to `.env`, set the token and review [configuration](CONFIGURATION.md), especially `OWNER_IDS`, authorization and private mode.
4. Persist `DB_PATH`, its SQLite sidecars and backups outside ephemeral containers, or configure PostgreSQL with its own backups. Protect `.env`, database URLs, logs and backups.
5. Set the **process environment** variable `AUTO_SYNC_GIT=0` if you want a controlled deployment. Start with `python main.py` in the project directory and use a process manager or hosting panel to restart on failure.
6. Check startup logs, run `,help` in an authorized server and verify intended permissions. Test security rules and restoration in a disposable server, not production.

### Panel hosts, including Wispbyte

Panel interfaces differ. Choose a Python application, set the entry point to `main.py`, and configure the panel to install `requirements.txt` or run a startup command that does so. For a checkout already located in `/home/container`, a possible Linux command is:

```bash
cd /home/container && python -m venv .venv && .venv/bin/python -m pip install -r requirements.txt && AUTO_SYNC_GIT=0 exec .venv/bin/python main.py
```

Adjust the path and startup syntax to your host. A first installation needs package-index access. Panel storage and restart policies vary; verify that `.env`, the database and backups persist across rebuilds. For uploads, use a current repository archive rather than relying on an assumed prebuilt ZIP name. See [troubleshooting](OPERATIONS.md#troubleshooting) if imports or startup fail.

#### Blank panel console

A running container or panel uptime does not by itself show that Python or the bot is running. Check the
panel's startup configuration: it must launch the Python application and the actual `main.py` from this
repository. If the panel accepts a full command and dependencies are already installed, one option is
`AUTO_SYNC_GIT=0 python -u main.py` from the project directory; other panels require the entry point and
environment variables in separate fields. Set `AUTO_SYNC_GIT=0` in the process environment, not just
`.env`. Do not start a second instance alongside a running bot.

After a controlled restart, current Veil code reconfigures `stdout` and `stderr` with UTF-8 `line_buffering=True`
and `write_through=True`, then prints `[veil] Starting Veil Utilities` to stdout before checking dependencies.
Normal application logs (including startup diagnostics, command execution summaries without arguments, and a
5-minute status heartbeat) go to stdout as well as `LOG_FILE` (default `veil.log`); Python tracebacks and some
startup errors still go to stderr. The entry point replaces pre-existing Python root logging handlers with Veil's
flushing stdout and file handlers, so a host/library's earlier configuration cannot silently suppress Veil INFO
logs. Note that web hosting consoles such as Wispbyte only stream lines emitted while the console tab is open (or
since the most recent restart): clicking **Restart** in the panel will display the full startup sequence, and live
commands / 5-minute heartbeats keep the console populated afterwards. If `0 lines` persists immediately after
clicking **Restart**, verify the panel's **Configuration → Startup / Entry File** points to `main.py` and check
`veil.log` in the file manager. Do not delete `.env` or databases while troubleshooting.

## Updating

Back up the database and `.env` before an update and review the [changelog](../CHANGELOG.md), [production audit](PRODUCTION_AUDIT.md) and release notes. Test the update on a non-production copy where practical. A typical clean checkout of `main` can be updated manually:

```bash
git status
git pull --ff-only origin main
python -m pip install -r requirements.txt
AUTO_SYNC_GIT=0 python main.py
```

Run the last command under your process manager rather than starting a second bot instance. Do not use this procedure to overwrite local changes; inspect `git status` and resolve them first. A fast-forward pull fails rather than rewriting a divergent checkout.

**Important:** Unless `AUTO_SYNC_GIT=0` is already present in the process environment, `main.py` fetches `origin/main` and performs `git reset --hard` to that fetched commit at startup. This happens **before `.env` is loaded** and may discard local commits or tracked changes. A patch of tracked edits may be written, but it is not a complete backup. The developer `,gitpull` command also uses a hard reset. Explicit deployment and backups are safer than relying on the startup sync.

PostgreSQL upgrades may alter tables and take locks; test and back up first. Changing `DB_PATH` or `DATABASE_URL` does not migrate data. Details and restore cautions are in [database operations](OPERATIONS.md#database-and-backups).

## Dashboard

The dashboard runs in the bot process and stays **off by default**. It requires Discord OAuth and HTTPS. **Only Veil bot developers can access the dashboard**; server owners, administrators, staff and users with Veil levels have no access unless independently recognized as developers. Discord bot command permissions are unchanged. Configured `OWNER_IDS` and Veil's built-in developer IDs are authoritative until configuration changes; Discord application owners and accepted team members are checked against fresh application information on each request. An API error denies access. Developers can access joined servers and manage the authorization list. Review these access changes with operators before deploying; do not deploy without authorization.

Signed sessions last at most 12 hours and are also registered in process memory. Logout revokes the current session, and a process restart invalidates all dashboard sessions, even with the same signing secret. This is not a shared session store: use one bot/dashboard process, and do not assume logout revokes a token held by another process. Login and request limiting are process-local and use the actual TCP peer, not forwarded headers; behind one proxy anonymous visitors share a rate bucket (60/minute), while signed-in users share a per-user bucket (240/minute). Configure upstream rate limiting too. Dashboard request logs include numeric user ID, method and status but not URLs, OAuth codes, cookies, form contents or credentials. Protect and retain logs appropriately.

1. Choose a public HTTPS origin and a hosting provider or reverse proxy that can route it to the bot's backend port. The backend must bind to loopback (default `127.0.0.1:8080`) and the HTTPS reverse proxy or tunnel must run on the same host/network namespace. Non-loopback binds are refused. Do not publish the unencrypted backend directly.
2. In the Discord Developer Portal for the **same application as the bot**, add the exact redirect `<DASHBOARD_URL>/callback` to OAuth2. For example, `https://veil.example.com/callback`. The public address is an origin, without a path or query.
3. In the host's private environment, set `DISCORD_CLIENT_SECRET`, `DASHBOARD_URL`, and a random `DASHBOARD_SECRET` of at least 32 characters. The OAuth client ID is detected from the bot unless `DISCORD_CLIENT_ID` is explicitly set. Never share these secrets or put them in Git.
4. Once the proxy and OAuth redirect are ready, set `DASHBOARD_ENABLED=true` and restart the bot. Open your configured HTTPS origin and sign in with Discord. Missing login settings or a non-HTTPS URL are rejected. If the host cannot expose an HTTPS website, keep the dashboard disabled.
5. Test access using a bot developer, a server owner, an Administrator and an unrelated account; confirm the non-developer owner, administrator and unrelated account are denied on both pages and direct APIs. Test changes on a disposable server first. Verify server authorization and any requested leave independently in Discord.

### Using ngrok on the bot host

If the host cannot provide HTTPS, the optional ngrok SDK can connect a public HTTPS domain to the bot's **loopback** listener without a separate startup command. Use your account's ngrok-assigned domain as `DASHBOARD_URL` (a public HTTPS origin with no port), register its `/callback` redirect in Discord, and privately set `NGROK_AUTHTOKEN` and `DASHBOARD_NGROK_ENABLED=true`. Install the current `requirements.txt`, set `DASHBOARD_ENABLED=true` after OAuth is configured, and restart. Keep `DASHBOARD_HOST` unset or `127.0.0.1`; do not forward its port publicly. The tunnel will be stopped on shutdown. If it cannot connect, the dashboard listener is stopped and the bot continues; inspect the host's network access and ngrok account settings. The integration requires outbound access to ngrok and its SDK; compatibility with a particular hosting plan must be tested.

The ngrok HTTPS endpoint is public; the random-looking address is not access control. Veil's Discord login still enforces access. HTTPS terminates at ngrok, which can process dashboard traffic; review your ngrok traffic inspection, retention, and account access settings, and avoid exposing OAuth codes or cookies. The free plan can have quotas and interstitials. Keep your ngrok token private and do not put it in a startup command, Git or screenshots.

Bot developers can manage Administrator-role grant authorizations through the dashboard for individual
members and roles under **Antinuke → Administrator-role granters**. Enter a Discord ID and select its type;
authorizing a role covers all current and future human holders and requires acknowledging that scope. This
does not grant Antinuke whitelist status. A signed revision prevents stale dashboard edits from overwriting
newer Discord-command changes within one bot process. See [Antinuke](ANTINUKE.md#scoped-administrator-role-grants)
for the per-person cooldown, eligibility, and the narrow Permission Grant exception.

The dashboard covers server overview, the server command prefix, command-access restrictions, disabled commands, explicit server authorization for developers, logging routing and exclusions, and selected role-blacklist, automod, leveling, ticket and antinuke settings; it is not a replacement for every bot command. The developer overview links to **Server Management**, which shows searchable server cards and filters. Developers see all joined servers and an authorization shortcut. The overview has a short setup guide linking to real readiness, logging and Automod controls. Select a server to open its workspace, then use the feature cards or navigation to open individual controls. Developers can use **Server settings** through the dashboard to toggle staff-only mode, set comma-separated command-channel IDs, and disable or enable individual commands. Staff can bypass the channel and staff-only restrictions; server owners and developers bypass disabled commands. An empty channel list permits all channels, subject to other command checks. Each of these forms saves separately and rejects a stale settings snapshot. Developers can use the combined logging and security form through the dashboard; automod, leveling, tickets and antinuke rules have separate save forms. New security roles and users are verified against the server, and changes to security identities require confirmation. The Antinuke readiness panel is a snapshot, not a guarantee; permissions, hierarchy, channel access and Discord availability can change. Logging destination previews use saved configuration and currently visible channels; event-specific fallbacks, exclusions and send permissions can affect delivery. Advanced Automod list entries and exemptions save individually, and removals require confirmation. Automod can delete messages or time members out. Ticket support roles can see private tickets. Turning antinuke off requires confirmation; changing an individual rule does not turn on overall protection. Trusted users and roles are exempt from antinuke enforcement, and trusted bots are also exempt from bot-add detection and quarantine: only add identities you trust. Logging exclusions apply to events that supply a user or source channel. Feature and combined forms reject stale signed snapshots. Dashboard saves are serialized per process; the combined form writes its configuration modules in one SQL statement and only then updates the cache. They are not cross-process compare-and-swap operations: concurrent Discord-command or external writers can still race and overwrite changes. A database communication failure may leave the outcome uncertain; refresh and verify before retrying. Forms show validation errors and success messages; when JavaScript is available, changed forms also show an unsaved indicator and warn before leaving. GET `/api/guilds/{id}/config` supplies a signed `revision` required by the combined POST, in addition to the session CSRF token. The dashboard does not provide host console access. See [dashboard settings](CONFIGURATION.md#optional-dashboard) and [`core/dashboard.py`](../core/dashboard.py) before exposing it.

## Rollback

Stop the bot, restore a verified database backup on a separate copy first, and deploy the intended code revision using your normal release process. Do not run a previous version against a newer schema without testing compatibility. Coordinate restoration of SQLite WAL sidecars or use the PostgreSQL provider's restore procedure. The project's startup sync must be disabled in the process environment when intentionally running an older revision.

The Antinuke panel shows the original 12 rules first. Five additional protections remain unchanged and can be inspected or edited under **Advanced**; their enabled state is shown separately, and none enforce while Antinuke is globally off. The dashboard also places additional rule controls in its Advanced section. Existing presets and enforcement are unchanged.
