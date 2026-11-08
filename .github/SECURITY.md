# Security policy

## Supported code

Security reports are most useful against the current default branch or the latest published release. This project does not currently publish a guaranteed support schedule for older releases. See the [release history](https://github.com/aidendixon602-png/veil-utilities/releases) and [changelog](../CHANGELOG.md).

## Reporting a vulnerability

Do **not** post exploit details, tokens, private logs, database contents or user information in a public issue. Do not test destructive actions against servers you do not control.

1. If the repository's **Security → Report a vulnerability** button is available, use GitHub's private vulnerability reporting to contact the maintainers.
2. If that button is unavailable, open a public issue **only to request a private reporting channel**, without technical details or secrets. Wait for a maintainer to provide a private channel before sharing the report. Repository administrators can enable private vulnerability reporting in GitHub settings.
3. Privately provide the affected commit or release, a minimal reproduction in an isolated environment, the impact and any suggested mitigation. Remove secrets and personal information from all attachments.

There is no published response-time or release-time commitment. If a credential may have been exposed, revoke or rotate it through the provider promptly; deleting it from the latest revision alone does not remove it from history or caches.

## Scope and operational precautions

Examples of issues worth reporting include permission escalation, unauthorized access to developer commands or the dashboard, antinuke bypasses, secret disclosure, unsafe URL handling and remote code execution. Developers and application owners intentionally have broad privileges; protect those accounts and restrict `OWNER_IDS`.

**Known self-role risk:** Button and reaction roles can grant a role that carries Veil levels or fake
permissions. Until fixed, remove any overlap between self-assignable roles and Veil privileges, and review members
who already hold those roles. See the [permissions guide](../docs/PERMISSIONS.md#review-role-assignments).

The source also contains [built-in global exemptions](../docs/PERMISSIONS.md#built-in-exemptions), separate from
per-server configuration. Antinuke responses and restoration are best-effort, not an assurance that damage can
always be prevented or reversed. Review the [antinuke guide](../docs/ANTINUKE.md) and
[staged audit](../docs/MASTER_AUDIT.md), use least privilege, and test only in a disposable server.
