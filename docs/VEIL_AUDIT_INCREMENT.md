# Current audit increment and prioritized plan

Baseline: `062b85b997bbd541779ab9be1843bc26bbdabaee`. This is a staged review, not a complete security audit or a production-readiness certification. Historical audit documents are starting points; their statuses must be verified against current code.

## Plan, established before implementation

1. Implement private, developer-only Midtown server detection, independently checking developer identity for the command and every interaction. Add the requested Server Info attribution without changing ownership or permissions.
2. Fix verified, narrowly scoped security and reliability issues with focused regression tests. Preserve command aliases, prefixes, grants and persisted configuration; do not silently change startup synchronization behavior.
3. Improve the changed commands' formatting, help, error reporting and documentation, then regenerate references and run offline checks.
4. Record residual risks, unreviewed subsystems, test limitations, deployment and rollback considerations; defer broad or destructive changes requiring additional design or live testing.

## Initial verified observations

- The bot is a discord.py prefix-command application. `_get_prefix` supports guild, personal and mention prefixes; global checks and cog checks are separate from help visibility. `si` is an alias of `serverinfo`.
- `main.py` performs Git synchronization before reading `.env`, and `AUTO_SYNC_GIT` defaults on; this can hard-reset the checkout. Set `AUTO_SYNC_GIT=0` in the process environment before running the bot for development. This increment does not change deployment behavior.
- Developer checks are distinct from guild-owner, native permissions, fake permissions and Veil rank. Built-in developer, protected-person and trusted-bot exemptions remain broad and require separate review.
- SQLite and PostgreSQL adapters cache mutable configuration. `set_config_committed` avoids publishing before a write succeeds but is not a cross-process transaction or compare-and-swap. Many older writers still mutate cached objects.
- The dashboard has OAuth, session signing, CSRF and authorization checks, with scope distinctions between owners/developers and Discord administrators. Some multi-module writes and concurrent updates remain non-atomic.
- Unexpected command errors use a scrubbed record for Discord but the original traceback is also logged to the host console/file; known secrets can therefore be exposed there. Scrubbing is best effort and does not make logs safe to share.
- Previous PostgreSQL, incident and authorization findings have partial fixes in the current code, but their production behavior and full race/failure properties need independent verification. Runtime dependencies use lower bounds, not a reproducible lock.

## Implemented in this increment

- `midtown servers` is a hidden developer-only prefix subcommand, gated by the existing owner check and rechecked before delivery and on each DM interaction. It snapshots only joined guilds, displays exact/partial counts and identifiers in bounded, escaped pages, refreshes from the cache, and handles missing metadata and DM failures without posting results to guild channels. It does not create slash commands or grant guild owners, administrators or roles developer status. Discord application/team membership is discovered at startup, not refreshed per click.
- Server Info (`serverinfo`, `si`, `guildinfo`) includes the requested fixed Midtown attribution in full command help, separately from the actual Discord server owner. The server info embed shows the server's own description without adding the attribution. A missing member count is displayed as unavailable, and cached bot counts are labeled. `userinfo` falls back to cached user data if the remote profile fetch fails.
- Known secrets are scrubbed from the unexpected-command and error-handler tracebacks sent to the host log. Configuration summaries now mask URL-valued settings that may contain credentials. Scrubbing remains best effort, and these changes do not sanitize every logger or prove diagnostics contain no private data.
- Added focused offline tests and regenerated the developer command reference. No schema changes, data migrations, prefix changes, dependency upgrades or existing permission grants were introduced.

## Scope and outstanding work (prioritized)

1. **High: operational safety.** Automatic Git sync can fetch and hard-reset to `origin/main` before dotenv loads. This increment deliberately leaves it unchanged for compatibility. Set `AUTO_SYNC_GIT=0` in the actual process environment for a branch deployment or local work. Back up code and data before deployment, and explicitly choose the update procedure.
2. **High: authorization and destructive automation.** The core developer model, global checks, dashboard gates and selected role/target checks were inspected, but each command, permission override, event path, antinuke exemption and guild authorization race was not proven safe. Developer identities retain powerful host/code execution and cross-server actions. Discord hierarchy, audit-log attribution, events and rollbacks remain best effort; verify on disposable servers before production. No privileges were intentionally removed or widened.
3. **High: persistence/concurrency.** Both DB adapters were inspected at the config-cache boundary, not exhaustively audited. Mutable cached docs and ordinary cache-before-write remain; concurrent tasks/processes, dashboard writers, incident delivery and partial multi-module writes can lose updates or duplicate work. Solve with a designed transactional/concurrency strategy, migrations and SQLite plus real PostgreSQL tests. Do not assume `set_config_committed` is an atomic cross-writer update.
4. **Medium: privacy and external services.** Hidden command arguments are masked in guild command cards; existing diagnostics and other cogs still need a complete data-flow/logging audit. Public URL filtering exists for selected user-supplied resources; other integrations use trusted sessions and need a separate redirect/size/timeout review. For example, image generation reads a remote response body without an explicit size cap. External APIs, audio, dashboard proxying and all integrations were not exercised here.
5. **Medium: background lifecycle and UI.** Selected loop setup/unload and views were inspected, not every asynchronous task, cancellation, interaction race, embed or error case. Generic views and shared help were not rewritten. The Midtown control is transient, in-process and uses cached owner identity and guild metadata.
6. **Medium: dependency and platform assurance.** Dependencies are specified with lower bounds; no reproducible lock, dependency upgrade, security scanner or broad version-matrix execution was performed. CI contains a separate PostgreSQL job; passing local tests without a PostgreSQL service is not a PostgreSQL integration test.

The three prior audit documents were reviewed for relevant findings; their claims are not automatically validated or superseded by this increment. Full file-by-file behavioral/security coverage, live Discord testing and production certification remain out of scope for this increment.

## Validation and deployment notes

Offline Python 3.11 checks: `python -m pip check` (no broken requirements), `ruff check .`, `python scripts/gen_commands.py --check`, and `python -m pytest -q` (1,304 passed, 1 skipped; one `audioop` deprecation warning). The PostgreSQL test was skipped because optional `pgserver` is not installed; no live PostgreSQL test was run here. Mocks are not a substitute for actual Discord delivery, interaction expiry or network failure behavior.

Deploy on a test instance with the chosen update behavior and `AUTO_SYNC_GIT=0` set before process startup. Verify a developer with DMs enabled, a non-developer with server ownership/admin rights, no-match and inaccessible-DM cases, pagination and refresh, and guild command logs, using disposable guilds. Treat private DM history and host logs as sensitive. Keep a backup of the database and configuration regardless of migration-free changes; roll back by redeploying the prior commit/version and restarting with auto-sync disabled. No destructive conversion needs reversal.
