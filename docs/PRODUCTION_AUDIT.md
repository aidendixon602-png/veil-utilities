# Production readiness: audit and staged plan

Snapshot: 2026-11-07. This is an evolving audit, not a certification of the bot or a claim that every path has been reviewed. Keep the comma-prefix interface and existing integrations; do not enable destructive tests against real servers.

## Inventory and baseline

The application uses discord.py cogs, a central command/permission check, a SQLite or PostgreSQL adapter, an optional dashboard, and per-guild JSON configuration. Existing security includes antinuke thresholds and audit-log handling, owner/developer alerts, a server authorization list, role blacklist, developer commands, error handling and loop resilience. Many features already exist: integration and correctness matter more than replacing them. Prior to this phase, the local suite passed 772 tests, with the PostgreSQL suite skipped because its optional test server was not installed. Ghost independence changes are also present in this working tree and have not been deployed.

## Findings and priorities

| Priority | Finding | Impact / next action |
|---|---|---|
| Critical | PostgreSQL schema mapped SQLite `INTEGER` to 32-bit `INTEGER`. Discord snowflakes require 64 bits. | Fresh databases and existing schema must be upgraded with real PostgreSQL migration tests; back up before deployment. |
| Critical | Unauthorized-server rejection looks up old bot-add audit entries without checking when they occurred; failures in lookup or notification can prevent leaving, and failed leaves are logged as successes. | Strict time correlation or unknown attribution; make leave independent of delivery failures and report actual outcomes. |
| High | Authorization cache was mutated before the database write. Grandfathering was determined by an empty authorization list, so revoking all entries could re-trigger it. | Persist before changing cache; use a durable one-time initialization marker, with migration preserving existing approvals. |
| High | Antinuke and unauthorized-join developer DMs are best effort. Delivery errors are often swallowed; there is no durable incident or per-recipient retry history. | Introduce a persistent incident/outbox with per-recipient status, bounded retries and restart recovery, integrate incrementally, and preserve owner and server alerts. DMs cannot be guaranteed if Discord or the database is unavailable. |
| High | Existing globally trusted bot and person lists grant broad exemptions. Developer identities come from `OWNER_IDS` and Discord application ownership/team; `BOT_DEVELOPERS` is currently empty. | Review trust boundaries and migration with the operator before changing exemptions or existing developer access. Do not hardcode developer identities. |
| High | Antinuke merges alerts in memory; its audit attribution, concurrency, rollback coverage, command guard coverage and immunity paths need a comprehensive event-by-event review. | Test actual Discord event and duplicate scenarios; avoid claiming complete attribution or rollback. |
| High | Soft/hard role blacklist, separate soft bypass, direct role enforcement and dashboard requirements are not complete. | Design with owners/developers and preserve the existing role blacklist; explicitly test direct changes, bot hierarchy and bypass boundaries. |
| Medium | Error handling records scrubbed diagnostics but a developer-channel embed uses the raw traceback. Some log and notification failures are silent. | Redact every exported diagnostic and expose failure information without leaking secrets. |
| Medium | Developer command checks and dashboard checks exist, but each privileged action and access path has not been independently audited. | Inventory commands, authorization, configuration changes and dashboard routes; test unauthorized access and reload behavior. |
| Medium | Database connection/recovery, backups, shutdown, health checks, Discord reconnects and logging categories are not yet comprehensively verified. | Inspect and test failure modes before extending or claiming recovery. |

## Implementation order and gates

1. Fix and test the snowflake migration, unauthorized join attribution, fail-safe leaving, authorization persistence and first-run marker. Preserve existing permissions, notification destinations and approval UI.
2. Build a durable incident/outbox subsystem with unique IDs, individual delivery states, safe retries, restart recovery, truthful action outcomes, redacted diagnostics and deduplication. Integrate unauthorized joins and antinuke first, then other critical events after event-specific tests. Avoid promising exactly-once delivery: a crash after Discord accepts a DM but before recording success can cause a retry.
3. Audit and harden antinuke event attribution, thresholds, race handling, exemptions, punishment/rollback and configuration protection. Preserve existing rule settings and simulated test behavior.
4. Design and implement the requested distinct soft/hard role protections alongside legacy protections, covering commands, direct role changes and dashboard authorization.
5. Review developer commands, database recovery/backups, graceful shutdown, diagnostics and every log category. Add integrations only when their outcomes can be verified.
6. Run lint, documentation checks, the full test suite and a real PostgreSQL migration suite; review the complete diff, document limitations and explicitly identify anything not yet tested or deployed.

Each stage needs focused regression tests and a review before the next. This document records known risks, not an exhaustive security audit; unknown issues may change priorities.

## First increment: implemented locally, pending deployment

- `security_incidents` stores incident details and per-recipient delivery state in one write. Unauthorized joins
  are recorded before leave with an initially unknown outcome; finalization records success/failure. If the process
  stops mid-containment, a deferred DM eventually reports the outcome as unknown rather than claiming success.
  Real antinuke alerts are recorded at the existing alert/merge point. Test simulations retain their existing path.
- Recipients are snapshotted from `OWNER_IDS`, existing built-in entries (currently empty), and already discovered
  application owners/team members. Configure `OWNER_IDS` for all people who must receive alerts, especially if
  Discord application discovery is unavailable at startup. No-recipient incidents are recorded but are **not**
  automatically backfilled when configuration changes; a console error is logged. `ANTINUKE_DEV_ALERTS=false`
  disables antinuke developer DMs, not incident recording.
- Each recipient has independent attempts, last error type and next retry, persisted after every attempt. Retry
  intervals back off to one hour. Delivery is **at least once**: a crash after a successful Discord send but before
  the database update can duplicate a DM. The outbox assumes **one running bot process per database**; multiple
  workers/processes can duplicate sends. If the database cannot persist an incident, direct alerts are attempted;
  persistence and delivery cannot be guaranteed during a database or Discord outage. Pending deliveries survive
  restarts, but old incidents are not automatically purged. Protect and back up the database; it contains user and
  server identifiers. There is not yet a developer-facing incident history, resolution workflow, replay command,
  monitoring dashboard, or cross-restart incident deduplication.
- Antinuke repeated events still use the existing in-memory merge window; the persisted record describes the
  initial alert, not later merged updates. Protection coverage, rollback, other alert categories and direct role
  enforcement are still under review. Discord/production deployment has not been tested here.
- Existing PostgreSQL columns are widened at startup in one migration transaction, and fresh schemas use BIGINT.
  This takes locks and can take time on large databases. Back up first and test a copy; the database account needs
  ALTER TABLE privileges. PostgreSQL integration tests use a disposable local server, not the production database.
- First-run authorization enrolls a stored snapshot and resumes it if interrupted. On the **first upgrade only**,
  an old database with an empty authorization table cannot be distinguished from a new installation. With
  `AUTH_GRANDFATHER=true` it will enroll currently joined servers as before. Set it to `false` before upgrading if
  an empty list is intentional; then authorize intended servers first. Once initialized, an empty list never
  re-triggers grandfathering. Developer-owned server auto-authorization remains unchanged.
- Prior versions used an unscrubbed traceback for the developer error channel; the channel now receives the
  scrubbed record. This does not establish that every other logging path is fully redacted.
- A failed or timed-out leave is recorded and alerted, but it is not yet retried automatically within the same
  session. A restart sweep or a new join may cause another attempt; operators should investigate the
  failed outcome. Concurrent duplicate join/sweep handling is suppressed only while a matching join timestamp is
  cached, not across restarts.
