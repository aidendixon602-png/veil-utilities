# Role and Antinuke review: prioritized plan

Baseline: `18156614f7c61ca11db905c987c063f0448c88f5`. This plan was recorded before implementation. Review is scoped to role management, permissions, Antinuke and their direct integrations.

## Verified findings

1. **Privilege escalation in self-service and automatic roles:** `core/role_safety.py` checks saved Veil levels, fake permissions, role blacklists/bypasses, Antinuke trusted roles and native staff permissions, but does not check `admin_role_granter_roles` or code-defined `core.perms.CODE_ROLES`. A role that looks harmless in Discord can confer scoped Administrator-role grant authority or a built-in Veil level when self-assigned. The shared helper covers button/reaction roles, autoroles, boost/vanity rewards, level rewards and VoiceMaster, so fixing it covers those paths at configuration and grant time. Existing holders and saved configurations must not be silently changed.
2. **Bulk role safety:** `_mass_role` confirms and rechecks only shorthand additions; explicit `add` and all removals skip that path. It does not check each member's actor/bot hierarchy, and the Antinuke permission guard for dangerous grants runs only inside the shorthand confirmation branch. Thus aliases and equivalent syntax do not enforce the same safeguards. Discord operations are independent and failed requests may have uncertain outcomes.
3. **Delayed autorole state:** the join listener resolves role objects and config before sleeping and rechecks role policy against those old objects after the delay; it does not re-resolve IDs or verify the saved autorole configuration still authorizes the grant. A deleted/changed role or removed assignment can be acted on from a stale snapshot.
4. **Recovery reporting:** `core/nukerestore.py` rebuilds deleted roles with new IDs, but swallows failed role position/member/overwrite operations and can report a role as restored even when important steps were skipped. Snapshots are transient, incomplete and limited; no rollback is transactional. `core/db.py` returns mutable cached config, making copy-before-write and committed-write behavior important; not all command writers or processes share locks.
5. Existing independent boundaries matter: native Discord, Veil levels and fake permissions are distinct; Antinuke trusts owners/developers, explicit admins/whitelists, built-in trusted identities and legacy role-blacklist authorized users. Dashboard security controls are owner/developer-only; Antinuke panels check interaction access. Antinuke relies on audit events and best-effort cache and restoration, with a separate developer-protection system. Do not remove exemptions or change thresholds/settings silently.

## Implementation priority

1. Close verified self-service privilege paths, fail closed on malformed/unavailable new policy reads, and recheck delayed autorole configuration and current roles. Preserve settings and existing role holders; document which future grants are blocked.
2. Unify bulk add/remove safety across syntax and aliases: preview/confirm, recheck current authority, role state/config and exact eligible targets, apply per-target hierarchy, apply guards and report partial/uncertain outcomes without retrying. Review the destructive single-role delete path for equivalent safeguards.
3. Improve recovery reporting without unsafe automatic retry or claims that deleted messages, IDs, assignments or overwrites are guaranteed recoverable. Review Antinuke audit processing, duplicates, trust, panels and DB writes; implement only well-supported changes and document remaining races.
4. Add focused security, hierarchy, confirmation, recovery, API/DB-failure and authorization regression tests; update permissions/Antinuke/operations/help docs and generated commands. Run targeted and full tests, distinguish pre-existing logging failures, and report live Discord/PostgreSQL limitations honestly.

Additional verified finding before implementation: `Antinuke.migrate` runs on config reads and silently raises some existing invite thresholds and enables missing new rules in already-enabled servers. This conflicts with preserving operator-selected thresholds and avoiding unattended new punishments. Stop these read-time policy changes; retain saved settings verbatim and let operators explicitly choose rules. Earlier migrations already persisted cannot be reliably reversed automatically.

Additional verified risk: Antinuke's audit listener does not deduplicate audit-entry IDs before incrementing thresholds or invoking follow-up recovery, so a repeated delivery of the same entry can contribute multiple hits or repeated actions. Add bounded, process-local deduplication keyed by guild and entry ID; this is not durable across restart and must not be described as exactly-once processing. Panic/raid lockdown recovery also does not retain all original overwrite state and can lose recovery records after failures; it needs separate design and is not safely replaceable without a fuller migration.

Additional verified issue: the `addpermall`/`apa <permission>` path changes a native Discord permission across all eligible roles without preview, confirmation or post-confirmation state checks, and silently ignores failures. The actual Discord Administrator and bot permission checks used for bulk removal should also guard bulk additions; recheck snapshots and report uncertain outcomes. This changes authorization for callers who previously relied solely on Veil/fake permissions and is documented for review.

Additional verified privilege path: an ordinary role command checks dangerous native Discord bits but not whether a role confers Veil levels/fake permissions, scoped Administrator-role grant authority or trust/bypass. A lower-ranked staff member who can manage a low-position role could pass those privileges to another member. Restrict such sensitive bot/trust-bearing assignments to server owner/developers, including bulk assignment; the owner-authorized scoped path remains available for ordinary Administrator roles but does not bypass this additional sensitive-role check. Existing assignments and settings remain untouched.

## Remaining work and validation boundary

Blacklist guild-strip now reports unconfirmed member requests and hierarchy skips, and scan UI warns that
Authorize All adds broad bypasses. The scan still uses stale cached targets and roles, and the single-member
strip has limited outcome reporting; post-click and per-request authorization/state races remain. Avoid relying
on bulk scan actions until independently hardened. Panic/raid recovery needs durable state, original overwrite
and restriction snapshots, safe interrupted-operation handling, and migration/rollback design. The bounded audit
dedup cache marks before processing; failures may suppress retry until expiry. Other config writers and processes
are not serialized with Antinuke/dashboard saves. These are not solved by this partial overhaul.

Further review: quarantine/release and other recovery/restoration paths can reapply previously recorded roles
without the new direct-assignment privilege check; quarantine state is saved after the Discord edit. Do not treat
this work as covering every regrant path or as a complete authorization overhaul. Review those workflows and
existing snapshots before use, with safe recovery-state and policy design rather than silently discarding roles.

Local validation on Python 3.11: focused role, Antinuke and authorization tests: 174 passed. Full suite: 1309
passed, 46 failed, 1 skipped. Running the unmodified base revision in the same environment produced the same
46 failing test identifiers (1285 passed, 2 skipped); failures remain outstanding and should not be dismissed
as harmless merely because they predate this patch. Ruff, generated-command reference check, and `git diff
--check` pass. No live Discord test was performed; local PostgreSQL integration was skipped.
