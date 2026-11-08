# Permissions and trust

[README](../README.md) · [Operations](OPERATIONS.md) · [Antinuke](ANTINUKE.md) · [Security policy](../.github/SECURITY.md)

This guide describes the current implementation, not the proposed permission-system overhaul. The audit is
incomplete; see [the staged audit](MASTER_AUDIT.md) for open work. Permission settings and exceptions must be
reviewed for each deployment.

> **Known self-role risk:** Button and reaction self-roles check dangerous native Discord permissions, but do
> not check whether a role grants Veil levels or fake permissions. A member who can acquire such a role may
> gain its Veil powers. Until fixed, do not use the same roles for self-assignment and Veil privileges. Remove
> unsafe reaction mappings and delete unsafe button-panel messages; also review roles already granted to members.
> See [Review role assignments](#review-role-assignments).

## Permission sources

- **Server owner** is recognized from the Discord server. Bot developers are recognized from `OWNER_IDS` and
  the Discord application owner/team discovered at startup; developers have broad cross-server privileges.
- **Veil levels** are assigned to users or roles and saved per server. They supply bot permissions and a rank
  used by some checks. The current default levels, in ascending order, are Helper, Moderator, Admin, Server
  Manager, Co-Owner and Owner. The assignable Veil Owner level is not the same thing as Discord's server owner.
- **Fake permissions** are role-based grants used by Veil; they do not grant native Discord permissions to a
  person or role. They are not a replacement for Discord access controls. Some commands also require a Veil
  level and other checks, so a fake grant alone may not authorize them.
- Shared Veil permission checks do not fall back to a member's native Discord Administrator or moderation
  permissions. Bot-side Discord permissions and hierarchy still determine whether the bot can perform an action.
  The audit of other authorization paths is not complete.

Automatic setup no longer creates level assignments from role names. Existing saved assignments are unchanged,
including any created by an older version. Direct user and role assignments follow the current precedence rules
in [`core/perms.py`](../core/perms.py); this is not an explicit-deny system. The requested temporary grants,
per-action permission model, and Helper limited-timeout preset are not yet implemented.

## Review role assignments

In a server you control, as the server owner or another appropriately authorized operator:

1. Run `,perm roles` and `,perm list` to review saved Veil role and user levels. Run `,fakepermissions list`
   to review fake role grants. Server owners can remove incorrect levels with `,perm remove @role` or
   `,perm remove @member`, and fake permissions with `,fakepermissions remove @role all`.
2. Run `,reactionrole list` and inspect any posted `,buttonrole` panels. If a panel offers a role with a Veil
   level or fake permissions, remove its reaction mapping using `,reactionrole remove <message> <emoji>` or
   delete the unsafe button-panel message, as applicable. Check any roles assigned automatically on join or as
   rewards too; these can also confer Veil permissions if configured with the same role.
3. Review current role holders in Discord and remove any unauthorized assignments. Removing a panel or a mapping
   does **not** remove roles already given to members. Verify all remaining grants and test changes in a
   disposable server. Saved configuration, server roles and application ownership cannot be inferred from the
   repository alone.

These steps are a mitigation, not a fix to the code. Preserve legitimate staff access deliberately; take a
verified backup before changing stored configuration. Use `,help <command>` for contextual syntax and check
[the command reference](COMMANDS.md); visibility in help does not itself enforce authorization.

## Built-in exemptions

The current source includes global entries in [`core/perms.py`](../core/perms.py):

- **Ghost Vanguard**, bot ID `1555002091162837022`, is in `TRUSTED_BOTS`. Antinuke trusts it, its addition is
  exempted from the relevant bot-add response, it is not quarantined on join, and it may hold blacklisted roles.
- **thesimisear**, user ID `843909457616044092`, is in `WHITELISTED_PEOPLE`. Veil's moderation target checks and
  several antinuke, automod, antiraid, role-blacklist and network-ban paths exempt this account. This is not a
  developer permission grant.

These are code-level, cross-server exceptions, separate from per-server whitelist settings. Their implications
and removal/replacement remain under review. They must not be treated as proof that all behavior is safe or that
all exemptions are visible through a single command. The optional `,gv` bridge and overlap warnings also refer
to Ghost Vanguard. Changes to the built-in lists require code review, testing and deployment; this guide does not
change them.

Separately, previously hardcoded role-name assignments were removed from `CODE_ROLES`; the same names remain as
examples in a docstring and do not currently grant access. `BOT_DEVELOPERS`, `CODE_USERS` and `CODE_ROLES` are
empty in the current source, but the runtime environment and database can contain additional authorization.

## Security boundaries and limits

Global developer access and the built-in exemptions are powerful. Restrict `OWNER_IDS`, protect the Discord
application owner and team, and review antinuke admins and per-server trusted users and roles. A trusted account
can still be compromised. Antinuke detection and recovery depend on Discord events, permissions and hierarchy;
no bot can guarantee prevention or full restoration. Refer to the [antinuke guide](ANTINUKE.md) and
[security policy](../.github/SECURITY.md). Do not share tokens, private logs, database files or credentials.
