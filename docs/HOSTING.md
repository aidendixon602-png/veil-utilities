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

The optional dashboard is off by default. Set `DASHBOARD_ENABLED=true` to start it. Without OAuth it binds to loopback and treats local visitors as bot owners; do **not** publish this mode. To use Discord login, configure `DISCORD_CLIENT_SECRET`, `DASHBOARD_URL` and the Discord OAuth redirect `<DASHBOARD_URL>/callback`; the client ID is detected or can be set explicitly. Set a strong `DASHBOARD_SECRET` of at least 32 characters to keep sessions across restarts. Use HTTPS and a correctly configured reverse proxy, and keep the secret private. See [dashboard settings](CONFIGURATION.md#optional-dashboard) and [`core/dashboard.py`](../core/dashboard.py) for the access model.

## Rollback

Stop the bot, restore a verified database backup on a separate copy first, and deploy the intended code revision using your normal release process. Do not run a previous version against a newer schema without testing compatibility. Coordinate restoration of SQLite WAL sidecars or use the PostgreSQL provider's restore procedure. The project's startup sync must be disabled in the process environment when intentionally running an older revision.
