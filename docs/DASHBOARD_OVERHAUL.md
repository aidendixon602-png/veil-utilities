> Historical note: the earlier owner/developer dashboard policy described below has been superseded by the developer-only control center work documented in [DEVELOPER_CONTROL_CENTER.md](DEVELOPER_CONTROL_CENTER.md). Server owners who are not developers no longer have dashboard access.

# Dashboard overhaul: review and prioritized plan

Baseline: `04b29e54652c1795e7c148794bb666ef263827d1`. Plan recorded before implementation.

1. Remove Discord-administrator dashboard access at every entry point; preserve existing bot permissions. Recheck owner/developer and server authorization on each request, with fail-closed handling and tests for cross-server and revoked access.
2. Protect settings integrity. Review stale revision handling and combined multi-module saves; prefer small changes preserving the existing database and data. Clearly report failures and remaining concurrency limitations.
3. Improve the existing server-rendered dashboard's design system, navigation, hierarchy, explanatory and error states, accessibility, and responsive behavior while preserving supported controls. Do not invent metrics or add unimplemented controls.
4. Verify OAuth, sessions, cookies, CSRF, escaping, HTTPS/tunnel and sensitive logging paths, add focused tests, run dashboard and repository checks, and update hosting/configuration/rollback documentation. Record what was not tested.

## Initial verified findings

- `core/dashboard.py` is an aiohttp application hosted inside the bot, with server-rendered HTML/CSS, Discord OAuth `identify`, signed session/state cookies, CSRF tokens and origin checks. `core/dashboard_features.py` provides feature forms and signed revisions; `core/dashboard_security_views.py` provides read-only readiness and routing summaries. `core/dashboard_tunnel.py` requires loopback and HTTPS for ngrok.
- The current code grants Discord administrators access to several dashboard areas, despite older documentation describing owner/developer-only behavior. The explicit administrator allowance must be removed, not merely hidden in navigation.
- Feature forms validate and copy config, then persist with `set_config_committed`; their revision check is not a cross-writer atomic compare-and-swap. The legacy combined form separately writes role blacklist, logging and antinuke, without a revision; a failure can leave partial changes. The shared database cache is mutable and not safe from all concurrent writers.
- The dashboard defaults to a loopback listener, requires HTTPS/OAuth to start, and ngrok does not itself authorize users. Do not start the bot during development without `AUTO_SYNC_GIT=0` in the process environment.
- Older audit documentation is historical; some local-only and role claims conflict with present code and must not be accepted uncritically. The supplied public URL returned ngrok's browser-warning interstitial, not the authenticated dashboard. No live Discord login, browser, ngrok hosting or deployment was verified.

## Research (ideas, not copied assets or code)

- [Dyno Automod](https://docs.dyno.gg/en/modules/automod) and [Action Log](https://docs.dyno.gg/en/modules/actionlog) organize settings by task/feature and make moderation and logging discoverable.
- [MEE6 audit logging guide](https://help.mee6.xyz/en/articles/615180-how-to-use-audit-logs-to-track-your-members-actions) explains channel destinations and exclusions; [its filter guide](https://help.mee6.xyz/en/articles/615177-how-to-automatically-remove-bad-words-with-mee6) explains effects and exceptions.
- [Discord server setup guide](https://support.discord.com/hc/en-us/articles/33023827550359-Discord-Server-Setup-Guide) emphasizes server permissions and setup context. These sources inspire clear grouping, effects and readiness explanations, not another product's access policy, interface, branding or implementation. No third-party code or assets are reused.

## Implementation and verification

- Server pages, JSON endpoints, feature saves and authorization actions now enforce the existing developer identity model or Discord-verified ownership of an authorized, joined server. Discord administrators have no dashboard access merely because of their roles; existing bot commands were not changed. Ownership lookup is fail-closed and uses a fresh Discord request; cache-based server discovery can temporarily delay newly transferred ownership. Veil's developer identity discovery is startup-cached, but Discord-discovered owners/team members are additionally checked against fresh application info on requests; errors deny access. Explicit configured and built-in identities remain valid until local configuration changes. Newly added team members may require a restart before the bot recognizes them.
- The shared CSS and optional script were moved into `core/dashboard_assets.py`. A loopback-only HTTP backend is enforced at startup; the HTTPS proxy/tunnel must share the host or network namespace. OAuth uses identify, signed short-lived state, signed HttpOnly/SameSite/Secure session cookies and CSRF/origin checks. The listener is never started without HTTPS and OAuth. OAuth errors and database errors do not log raw exception text; access logging remains disabled. Existing cookies persist across deployment if the signing secret is retained, but their access is rechecked. This is a targeted audit, not proof against all vulnerabilities.
- The combined security form now requires a signed user/server/snapshot revision and a security-change confirmation. New blacklist roles, trusted roles and security members are validated against the server, while pre-existing unavailable entries can be preserved. New ignored channels must be in the server; ignored user IDs may intentionally refer to users who have left. GET config exposes the matching revision for API consumers. Invalid or stale submissions save nothing.
- Combined config updates use one SQL upsert statement, publishing cached objects only after it succeeds. Same-process dashboard writes use bounded striped locks. SQLite all-or-nothing behavior was tested with a trigger-induced failure; PostgreSQL SQL construction was mock-tested but not run against a live PostgreSQL server. If the DB connection fails at commit, the outcome may be uncertain. No database schema migration is required.
- These measures are **not** a distributed compare-and-swap: other bot commands, another bot process, direct database writes or mutable cache updates can race between revision checking and commit. Multi-process stale cache and lost-update risks remain; a transaction/version design shared by all writers is future work. Server authorization requires explicit confirmation and remains a separate action; users should verify it and any attempted leave in Discord.
- Existing server-rendered feature controls, forms and confirmations remain. A refreshed shared visual system adds a task-oriented setup guide linked to actual features, responsive layout and focus/reduced-motion styles. Same-origin optional JavaScript marks unsaved forms and warns before leaving; server-side validation still works without JavaScript. Counts use available cache data rather than inventing missing member totals. Future ideas include a persistent setup checklist and true activity reporting, but neither is presented as implemented.
- Dashboard tests use fake Discord/OAuth and aiohttp test clients; live OAuth, ngrok, browser rendering/keyboard behavior, Discord permissions and production deployment were not verified. Follow hosting documentation, test with disposable servers and accounts, keep backups, and set `AUTO_SYNC_GIT=0` in the process environment before starting a development instance. Roll back by redeploying the previous version; doing so also restores the earlier administrator dashboard access and old non-atomic combined form, so evaluate that security impact before rollback.

## Check results

Offline Python 3.11 validation: dashboard tests 94 passed; `python -m pip check`, `ruff check .`, `python scripts/gen_commands.py --check`, `python -m compileall -q core tests`, and `git diff --check` passed. The full suite passed 1,320 tests with 1 skipped and one `audioop` deprecation warning. PostgreSQL integration is skipped without the optional `pgserver`; the live service was not tested.

## Roblox group and member roles

The developer-only server workspace stores a Roblox group ID and displays public metadata and up to 250 roles. A saved ID does not prove group ownership. When the host also configures a group-matched Open Cloud key, developers can preview and confirm individual member role assignments and removals. See [DEVELOPER_CONTROL_CENTER.md](DEVELOPER_CONTROL_CENTER.md#roblox-member-role-controls) for setup, authorization, limitations and rollback. API responses are mocked in tests; live Roblox functionality has not been verified. Monitoring, raid protection, backups and recovery are not implemented.

## UI verification pass

A follow-up pass audited the rendered pages rather than only the routes. It found that the shared
stylesheet had accumulated five stacked "theme layers" after the original tokens: each layer
redefined `:root` custom properties and re-declared the same components, so visible results came
from cascade order rather than intent (a teal page background with blue button borders, plum nav
highlights, pink section eyebrows and navy panels). `core/dashboard_assets.py` now holds a single
token set and one rule per component. The same pass fixed:

- dashboard reads that could return HTTP 500 for saved values with an unexpected type (a string
  where a list or mapping was expected), by funnelling config reads through one dict-guaranteeing
  helper and type-checking individual fields;
- nested feature cards that repeated their parent's `id`, producing duplicate element ids and
  dead in-page links;
- an "uptime" that printed the age of the Unix epoch when `started_at` was unset;
- a sidebar "Servers" entry that 404ed, with no other link back to the server list;
- nav highlighting on the feature pages, which never matched the section being viewed;
- a navigation link to a missing "Blacklist bypasses" section, and an unclosed form grid on the
  Role blacklist card;
- literal `None` labels in the disabled-commands and Automod exemption lists.

`tests/test_dashboard_render.py` now renders every page against deliberately malformed config
data, asserts unique ids and that no `None` is rendered, and asserts every CSS class used by a
page exists in the stylesheet.

Limitations: no browser engine was available in the review environment, so these checks are
HTTP/HTML-level (element structure, ids, labels, class coverage, route status) rather than
screenshots or computed-style checks. Live OAuth, ngrok and desktop/mobile rendering still need
manual verification. `asyncio.timeout()` in the Discord audit-attribution path also failed on
Python 3.10 and was replaced with `asyncio.wait_for`; the Python 3.10 CI job should be re-run to
confirm.
