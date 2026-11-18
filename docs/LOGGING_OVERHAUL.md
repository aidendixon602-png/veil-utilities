# Logging overhaul: review and plan

Baseline: `27c8fd6fa3bd60269fa0e703cdf8ea0c2d7613e8`. Plan recorded before implementation.

## Verified initial findings

- `cogs/logging.py` defines 11 stable config keys: joins/leaves, commands, server/invites/emojis, antinuke, messages, members, roles, channels, voice, bans/moderation and timeouts. `core/logembeds.py` constructs event cards and attempts best-effort audit attribution. Some events are buffered, and delivery depends on cache, intents, permissions, audit availability and Discord API responses.
- `logs` config stores channel IDs, `-1` for off, `None` for auto, and exclusions. Current auto routing picks channels by name; name-based fallbacks can post into unrelated channels. Existing stored IDs include both manually configured and automatically discovered channels, so provenance cannot be inferred from the ID alone.
- Current startup/join setup is enabled by default and can create channels, reuse same-named channels/categories, and move topic-marked channels or reorder an existing category. A topic or name does not prove ownership. `logs cleanup delete` currently deletes name-derived candidates despite an older audit saying cleanup only reports; this is unsafe.
- Ghost/Vanguard-specific detection and hard-coded exclusions exist in routing, panels, setup, cleanup, dashboard validation, commands, tests and generated references. They are neither a general ownership mechanism nor reliable protection for other bots' channels.
- Discord send and feed failures can be dropped without an operator-facing warning; mutable cached configuration can be modified before a write is confirmed. Some event exclusions and timeout fallback behavior are inconsistent. The developer-channel setup is a separate subsystem and requires separate consideration.

## Priorities

1. Replace channel-name inference and product-specific blocking with a neutral explicit-approval model. Preserve stored route IDs but require review before unverified legacy destinations become active; owners can explicitly select destinations. Disable automatic setup on startup/join. Never automatically reuse, move or delete existing channels based on name or topic.
2. Make setup an owner/developer-authorized preview and confirmation. Create only actual supported destinations, preserve configured IDs/off settings, report creation and persistence failures, and use recorded IDs rather than names for any managed resources. Make cleanup report-only unless ownership and intent are provable; never delete other channels.
3. Review direct and shared fallback/off semantics, selected-channel usability, exclusions, delivery failures, redaction, mention suppression, event coverage and formatting. Avoid invented attribution or broad unrelated changes.
4. Update dashboard controls/previews to the same routing and authorization model; preserve existing bot and dashboard grants. Update commands/help and migration/operational documentation. Add tests and run checks; explicitly report unverified Discord behavior and remaining concurrency/failure risks.

## Implementation status and remaining work

The first safety pass is implemented on this branch, not production-validated. Legacy routes now require review; the `approved_routes` map binds each approval to its current ID. Setup and route changes require confirmation, startup/join server-log setup and startup developer-channel provisioning are disabled, and cleanup never deletes. Created resources are recorded per destination, with failed writes reported as unconfirmed. Name-based vendor exceptions were removed from server logging; the separate external integration and antinuke protection rules remain unchanged. See [migration and operations](LOGGING.md).

The former developer-channel provisioning helper is still unsafe if invoked directly: it reuses a same-named category and moves stored channels. Startup no longer calls it. Its remediation and an operator-confirmed replacement are outstanding. Explicit antinuke alert and case modlog destinations also remain separate streams, and their configuration/exclusion behavior has not been fully redesigned.

The regression suite still includes many assertions for unsafe former behavior and must be fully migrated, with remaining genuine failures investigated individually. The implementation also needs a more complete event/exclusion and embed-length audit, stronger per-interaction confirmation UX, concurrency review across dashboard and Discord writers, and real Discord/PostgreSQL validation. Do not treat this branch as deployment-ready or assume logs are delivered until these are resolved and destinations reviewed.
