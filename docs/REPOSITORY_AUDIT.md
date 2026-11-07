# Repository presentation audit

Review date: 2026-11-07. This inventory is a point-in-time review, not a security certification or evidence of a live Discord deployment. The codebase's continuing security and role-system work is tracked separately in [PRODUCTION_AUDIT.md](PRODUCTION_AUDIT.md).

## Inventory and changes

- `main.py` is the runtime entry point; `cogs/` holds Discord extensions and `core/` holds shared services. `docs/`, `tests/`, `assets/` and `.github/` already had sensible purposes, so no source files were moved or deleted for appearance.
- The project declares Python 3.10+ and version 1.1.0 in `pyproject.toml`; CI is configured for 3.10–3.13. The existing MIT license and v1.0.0/v1.1.0 GitHub releases were retained; no release or contributor history was invented. The default branch contains unreleased changes beyond v1.1.0.
- The README and operator guides were rewritten around the code's actual comma-prefix interface, configuration, authorization, integration limitations, backups and startup sync. The generated reference now counts visible documented commands, prefixes command syntax and explains that its permission column is incomplete. Hidden developer commands are included in a separately generated, clearly marked developer reference.
- Issue templates, contribution and security guidance, environment examples, `.gitignore` and CI checks were updated. Existing assets were reviewed; `assets/social-preview.png` is a branded 1280×640 image suitable for a repository social preview. Existing dependencies were retained because their imports or runtime/voice integrations have a purpose; changing lower bounds needs separate compatibility testing.
- `,botlogs` now scrubs known secrets before attaching a log tail. Redaction is best-effort and is not a reason to share logs publicly.

## Security review and limits

- The tracked worktree was scanned with `detect-secrets`; reviewed candidates were sample credentials, test fixtures, a known public example password and a public image identifier. `.env.example` secret fields are now empty. No confirmed live credential was identified in that scan.
- The fetched Git history was searched for Discord-token-like, GitHub-token, AWS access-key and private-key patterns; no matches were found. This is a limited pattern scan, **not proof** that the history or current tree contains no secrets. The existing sample Lavalink configuration uses a publicly known example password; deployers must replace it for private nodes. If a real credential is found later, rotate it even if the file is removed from the current tree.
- `pip-audit` using PyPI reported no known vulnerabilities in the resolved runtime dependencies at review time; unpinned lower bounds can resolve differently later. The existing Dependabot configuration remains. GitHub vulnerability-alert settings were inaccessible to the connected integration.
- A previously successful main-branch Actions run was identified, but downloading its logs was blocked in the execution environment; historical logs were not scanned. The updated workflow has been exercised locally by running its constituent checks, but the new workflow has not yet run on GitHub.

## GitHub settings requiring repository administration

The connected GitHub integration returned HTTP 403 when asked to edit repository settings or enable private vulnerability reporting. Therefore the live settings below were **not** changed:

1. Set the description to: `A self-hosted Discord bot for server security, moderation, logging, and community tools.` Suggested topics: `discord-bot`, `discord-py`, `python`, `moderation`, `antinuke`, `self-hosted`.
2. Enable **private vulnerability reporting** under repository security settings. Until then, the security policy instructs reporters to request a private channel without posting vulnerability details publicly. No private maintainer email was verified.
3. In repository Settings → General → Social preview, upload [`assets/social-preview.png`](../assets/social-preview.png). GitHub currently reports its generated default Open Graph image; committing the asset alone does not activate it. The public API available to this integration did not provide an image-upload operation.

## Outstanding verification

Live Discord login, actual permissions/actions, production notifications, user-hosted databases and dashboard deployment were not tested. The bot's startup sync still fetches and hard-resets to `origin/main` by default before loading `.env`; operators must set `AUTO_SYNC_GIT=0` in the process environment to disable it. Changing that behavior is a separate compatibility decision. GitHub-hosted CI for this branch, social-preview activation and repository metadata require the appropriate access or a subsequent pull request.

## Local verification

On Python 3.11 in a newly created virtual environment: dependency installation and `pip check` succeeded; syntax compilation, Ruff, both command-reference checks, offline extension/command smoke testing and the full suite succeeded (**807 tests**, including a disposable PostgreSQL server). The no-token startup path correctly rejected an empty `DISCORD_TOKEN` with startup sync disabled. The release-version check accepted the current version tag and rejected a mismatched tag. These checks do not substitute for a live Discord login, a production deployment or GitHub-hosted CI on the modified branch. The `audioop` deprecation warning remains on Python 3.11.
