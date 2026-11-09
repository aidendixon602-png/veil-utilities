# Master project audit and implementation plan

This is a **staged, incomplete audit**, not a security certification or a claim that every command, listener,
migration, dependency version or external integration has been individually reviewed. It covers the repository
at baseline commit `b6da011` plus this first hardening increment. The working branch was clean at the start. The
project has roughly a thousand Python function/method definitions and 67 test files; a complete line-by-line and
threat-model review remains open. Do not use this document as proof that production is secure.

## Architecture and feature inventory

- `main.py`: environment loading, startup, logging, reconnect/restart, and optional Git synchronization. The
  sync currently runs **before** dotenv loading and defaults to resetting the checkout to `origin/main`. Set
  `AUTO_SYNC_GIT=0` in the process environment before starting a development or manually deployed instance.
- `core/bot.py`: discord.py bot, extension loading, global checks, prefixes, errors, lifecycle and background
  work. `core/config.py` validates configuration. The project targets Python 3.10+.
- `core/db.py`: SQLite and PostgreSQL adapters, per-guild JSON configuration, cases, warns, authorization,
  other tables and incident outbox. Schema setup and PostgreSQL integer widening happen on connection; there is
  no general versioned migration framework. Snowflakes are held in signed 64-bit integer fields where used.
- `core/perms.py`, `core/utils.py`, `core/command_access.py`, `cogs/permissions.py` and `cogs/settings.py`:
  current levels, fake role permissions, checks and optional per-command role restrictions. The existing model is
  not the requested fine-grained policy engine. `core/help.py` builds an interactive command-derived help view;
  visibility is advisory, not authorization.
- `cogs/antinuke.py`, `core/nukerestore.py`, `core/antinukepanel.py`, `core/testnuke.py`: audit-entry rules,
  alerting, temporary deletion snapshots, restoration, panels and safe simulations. Antiraid lives in the
  antinuke cog. `cogs/automod.py` contains filtering and escalation. `cogs/devprotection.py` separately protects
  developers from bans. Discord permissions, audit-event availability, hierarchy and API failures limit results.
- `cogs/moderation.py`, `core/modlog.py`: moderation commands and case records; `cogs/logging.py` and
  `core/logembeds.py` route/format event logs. `core/incidents.py` persists some security incidents and a
  per-recipient developer-DM outbox, separate from ordinary Discord log delivery.
- `core/dashboard.py`: optional local-only dashboard or Discord OAuth dashboard; owner/developer access, signed
  cookies and CSRF controls exist. Its writes still require a separate full security and transaction audit.
  `core/net.py` supplies a guarded public HTTP session; verify each outbound integration uses the right client.
- Other cogs: `authorization` controls server enrollment; `config` and `settings` configure servers; `tickets`,
  `voicemaster`, `levels`, `giveaways`, `utility`, `fun` and `imaging` provide community tools; `music` and
  `spotify` provide music integrations; `feeds`, `integrations` and `ai` use external services; `owner` and
  `devtools` provide privileged operations; `vanguard` provides an optional integration. Each still needs
  individual authorization, event, failure and data-lifecycle review.
- Supporting services include `core/autosetup.py`, `autoclean.py`, `staffpanel.py`, `loops.py`, `modlog.py`,
  `redact.py`, `diagnostics.py`, `devchannels.py`, `gitinfo.py`, `lavalink_nodes.py`, `netcalm.py`, `memlimit.py`
  and `ai_providers.py`. See existing `PRODUCTION_AUDIT.md`, `REPOSITORY_AUDIT.md` and `ANTINUKE.md` for prior
  work and operational caveats; older reports may describe historical behavior.

## Prioritized findings

| Priority | Evidence and impact | Status |
|---|---|---|
| Critical | Shared `missing_perms` previously authorized from native Discord permissions if no levels were configured; auto setup granted staff levels merely from role names. This violates explicit Veil authorization and could turn a role-name change into bot power. | **Addressed in this increment** for those paths; saved grants are retained. |
| High | Native Discord Administrator/Manage Server conferred staff-mode and rate-limit exemptions, and Administrator bypassed ignored/disabled commands. Ticket support and permission inspection also used native permissions. | **Addressed in these paths**; no claim that all other paths are reviewed. |
| High | Permission changes used the shared mutable configuration cache before persistence, so failed writes could publish an uncommitted grant in memory. | `cogs/permissions.py` now works on a copy and publishes after a successful write, including migration. Other writers, read/modify/write races, process concurrency and the rest of the mutable config cache remain open. |
| High | Permission assignment did not explicitly reject direct self/owner targets, roles held by the actor, or changing a role with a higher Veil level just because its Discord position was lower. | Narrow guards added. Full authority/subset checking, inherited holder hierarchy, cached role membership and all indirect escalation paths remain open. |
| High | `core/perms.py` resolves a direct assignment before role assignments; rank is not a permission-set subset. Custom overrides and assignment paths need a single conflict model. Explicit deny, temporary grants, audit history and fine-grained action permissions are absent. | **Open.** Do not assume the new checks establish a complete hierarchy. |
| High | Selected sensitive commands and role-blacklist buttons allowed staff or native Discord permissions where the operator requested actual server-owner/developer access. Logging cleanup deleted same-named channels without proving ownership. | Owner/developer checks added to the listed command paths and action panels; cleanup now reports only, obvious Vanguard categories are excluded, and unmarked channels are no longer moved. **Open:** topics are not proof of ownership, other bots are not reliably identified, name-based routing still exists, and other privileged paths remain unaudited. |
| High | Button/reaction self-roles check native Discord permissions but not Veil levels or fake permissions. A self-assignable role can confer Veil powers. Autorole and rewards can also carry assigned Veil privileges. | **Open.** Separate self-service/reward roles from Veil permission roles and review existing holders; see [permissions](PERMISSIONS.md#review-role-assignments). |
| High | Global developer and built-in protected/trusted accounts can bypass or avoid broad protections; antinuke `_trusted` returns before detection and `BOT_JOBS` filters several bot actions. These are existing trust boundaries, not narrowly scoped, monitored exemptions. | **Open.** Review existing deployments and migration before changing trust. |
| High | Antinuke uses audit-entry events and in-memory thresholds/locks; no end-to-end review of deduplication, delayed delivery, cross-action risk or all attribution paths has been completed. `_matters` currently ignores `serverupdate` changes unless vanity changes. | **Open.** Do not assume server name/icon changes or every requested event is protected. |
| High | `core/nukerestore.py` snapshots deletions in memory for 15 minutes; restarts lose snapshots, restoration depends on Discord events, hierarchy and permissions, and messages cannot be restored. | **Open.** Durable backups, safe restore tests and outcome tracking are not complete. |
| High | The incident outbox covers selected events only, assumes one bot process per database, retries failed delivery without a permanent-failure policy, has no automatic retention and may duplicate delivery after a crash. Server log delivery is not generally durable. | **Open.** See `PRODUCTION_AUDIT.md`. |
| High | Dashboard form saves several settings independently and validates ID syntax but does not establish that every ID belongs to that guild; partial writes and authorization consistency require review. Local-only mode trusts access to the host. | **Open.** Restrict host/network access; review before wider exposure. |
| Medium | SQLite/PostgreSQL configuration is cached as mutable objects, and many unrelated paths mutate it before persistence. Permission writes are not serialized against antinuke, role-blacklist or other writers. | **Open.** Define transactional, cross-writer semantics before changing the adapter globally. |
| Medium | Runtime requirements use lower bounds rather than a reproducible lock; CI resolves dependencies at run time. GitHub Actions are referenced by tags. | **Open.** Assess upgrades, Python versions, transitive licenses and pinning without breaking deployed installations. |
| Medium | `main.py` can hard-reset tracked files to `origin/main` at startup by default; this can undo a controlled deployment or local work. | **Open.** Disable using process environment `AUTO_SYNC_GIT=0`. |

### Current preset vs requested target

Helper now has narrow actions for warnings, timeouts capped at one hour, and viewing cases/warnings. Default
Helper no longer grants message management. Saved per-level permission overrides remain in place, so a custom
Helper can retain message management. Action toggles are per level; the one-hour cap is not configurable.
Helper-issued warnings do not trigger configured auto-punishments. Existing Helper assignments automatically
receive the default changes; see [permissions](PERMISSIONS.md#helper-preset-update) for deployment implications.

This is not the complete requested permission overhaul. Moderator, Administrator, Server Manager, and Owner
presets have not been rebuilt to the requested per-action policy; old Co-Owner/Owner levels, overrides and
assignments remain. Warning escalation for higher levels, overlapping role grants, and self-role escalation
remain open. Do not assume these changes establish a complete hierarchy or eliminate indirect privileges.

## Reference-project review and reuse boundary

The following are architectural references, not guarantees that their claims are correct or their implementations
are safe. Representative README, relevant documentation or code was inspected; none was copied into Veil.
GitHub-reported SPDX values were checked during this increment; no full legal review was performed.

| Reference | Material inspected / applicable ideas | License reported by GitHub |
|---|---|---|
| [Bleed source](https://github.com/mkyxqz/bleed-src) | Moderation command organization; the sampled ban implementation relies on native permissions and is not a model for Veil's requested isolation. | No SPDX reported; reuse unclear. |
| [Bleed docs](https://github.com/notiku/bleed-docs) | Fake-permission role grants and command discoverability; its absolute antinuke claims should not be repeated. | MIT. |
| [Greed docs](https://github.com/relateful/docs-greed) | Security rule configuration and recovery coverage as a comparison checklist, not proof those guarantees are achievable. | MIT. |
| [Bleed Anti](https://github.com/NikeThe1/bleed-anti) | Audit-entry-based rule examples, including role deletion; review thresholds and error handling rather than copying it. | No SPDX reported; reuse unclear. |
| [Red DiscordBot](https://github.com/Cog-Creators/Red-DiscordBot) | Permission cog documentation describes ordered rule resolution and keeps owner-only restrictions non-overridable; modularity and extensibility are useful references. | GPL-3.0. |
| [Sentinel](https://github.com/haxurus/Sentinel) | Separation of collection from delivery; explicit trust boundaries, retention, encryption and least-privilege database access. | AGPL-3.0. |
| [Public Anti-Nuke](https://github.com/korqedev/Public-Anti-Nuke-Discord-Bot) | Quarantine, cross-action risk, structure backup and recovery concepts; README describes a single-server deployment, unlike Veil. | MIT. |
| [OpenGuard](https://github.com/openguard-bot/openguard) | Modular moderation/automod and separately organized logging configuration. | GPL-3.0. |

Further review of implementations and relevant licenses is required before reuse. Independently implement ideas;
no code from an incompatible or unclear license should be copied without proper legal review and attribution.

## Remaining phase gates

1. Finish the repository-wide command, listener, integration, dependency, migration and test inventory; record
   unreviewed paths and security decisions. Map every command and interaction to its actual runtime checks.
2. Design a centralized, fail-closed policy model and migration: explicit grants/denies, inheritance, scoped
   authority and owner/developer boundaries, per-guild isolation, temporary grants and audit history. Specify
   conflict resolution and safe behavior during database outages. Preserve settings and require operator review
   of any changed effective permissions. Do not equate a bot permission with the holder's Discord permission.
3. Implement and test the requested five presets, including a defined limited Helper timeout. Use **Helper**,
   not Trial Moderator. Integrate all privileged commands, panels, dashboard and help; establish target hierarchy.
4. Review antinuke attribution, deduplication and concurrency per event, then safely add missing detectors,
   trusted-account monitoring, recovery, backups and configurable response. Never punish on guessed attribution.
5. Cover automod, moderation, durable incidents, delivery failures and retention. Finish database migration,
   rollback, dependency, extension lifecycle, shutdown and performance reviews.
6. Update help and all operational documentation after behavior is implemented. Run targeted and full suites,
   lint, docs checks, SQLite and disposable PostgreSQL migration tests on each increment. Conduct a final diff,
   secret scan and security review. Test only in disposable Discord servers.

## Validation and deployment status

The baseline full suite passed **868 tests** on Python 3.11 with disposable PostgreSQL support installed. This
increment passed **885 tests** with one `audioop` deprecation warning. A separate PostgreSQL/loop run passed
**15 tests** with no skips. Ruff, command-reference validation, Python compilation and `git diff --check` passed.
`pip-audit` against the resolved runtime requirements using PyPI reported no known vulnerabilities at the time
of this review; this is a point-in-time advisory check, not proof of safety. `pip check` passed. No production
database, bot token or live Discord server was used. Changes are not deployed. Before upgrading, back up and
verify the database, review existing assignments and role grants, disable automatic Git sync if deploying a pinned
revision, and test on a disposable server. No schema change is introduced by this increment. Rollback requires
restoring the previous application revision and, if configuration changed after upgrading, a verified compatible
backup; the old revision restores the insecure native-permission fallback and role-name automatic grants.
