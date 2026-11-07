# Contributing to Veil Utilities

Thank you for helping improve Veil Utilities. Please follow the [code of conduct](CODE_OF_CONDUCT.md); report vulnerabilities using the [security policy](SECURITY.md), not public issues.

## Development setup

Python 3.10+ is declared by the project; CI is configured for Python 3.10, 3.11, 3.12 and 3.13. The commands below are for Linux/macOS; on Windows use `.venv\Scripts\Activate.ps1`.

```bash
git clone https://github.com/aidendixon602-png/veil-utilities.git
cd veil-utilities
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pip check
ruff check .
python scripts/gen_commands.py --check
python -m pytest -q
```

The test suite initializes the bot and extensions offline with temporary databases; it does not require a real Discord token. To run the optional real PostgreSQL tests locally, install `pgserver` into the development environment and run `python -m pytest -q tests/test_loops_and_postgres.py`. CI has a dedicated PostgreSQL job.

Do not run destructive test commands on production servers. If starting the actual bot from a Git checkout, set `AUTO_SYNC_GIT=0` in the **process environment before launch**; otherwise startup fetches `origin/main` and hard-resets the checkout. See [hosting](../docs/HOSTING.md#updating).

## Making changes

- Preserve prefix commands and existing settings. Add or modify cogs in `cogs/` and shared services in `core/`; register new extensions in `core/bot.py`.
- Give commands accurate docstrings. After command/help changes, run `python scripts/gen_commands.py` and commit both generated command references. Its permission column is not a substitute for runtime authorization checks.
- Add focused tests for permissions, errors, persistence and external-service failures. Do not require production credentials or live Discord operations in automated tests.
- Validate new environment settings in `core/config.py`, document them in `.env.example` and [configuration](../docs/CONFIGURATION.md), and do not log secrets.
- Use the existing safe HTTP helpers for user-supplied URLs; cap downloads and account for network failures. Preserve database compatibility with SQLite and PostgreSQL.
- Wrap periodic background tasks with the existing resilience helper. Keep errors observable and user-facing outcomes truthful.
- Use existing `Context` reply helpers for ordinary command results and `UserError` for user-correctable errors. Follow the configured Ruff checks rather than claiming a full formatting convention.

## Pull requests and issues

Describe the change, its risks, how it was tested and any deployment or migration requirements. Keep changes focused, update documentation where behavior changes, and use the existing issue templates. Do not include `.env` files, Discord tokens, database credentials, unredacted logs or private user data in reports or pull requests.
