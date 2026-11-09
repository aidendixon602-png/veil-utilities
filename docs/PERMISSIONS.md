# Permissions and trust

[README](../README.md) · [Operations](OPERATIONS.md) · [Antinuke](ANTINUKE.md) · [Security policy](../.github/SECURITY.md)

This guide describes the current implementation, not the proposed permission-system overhaul. The audit is
incomplete; see [the staged audit](MASTER_AUDIT.md) for open work. Permission settings and exceptions must be
reviewed for each deployment.

> **Self-role safety:** Button, reaction and selected automatic role grants check for Veil levels, fake
> permissions, security bypasses and Discord staff permissions. Existing role holders are not removed, other
> assignment paths remain under review, and concurrent policy changes can race a grant. Review existing
> assignments and the deployment impact below.
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
in [`core/perms.py`](../core/perms.py); this is not an explicit-deny system. The requested temporary grants and full per-action permission model are not yet implemented.

### Helper preset update

The default Helper preset grants **warn, timeout (maximum one hour), and view cases and warnings**. It no
longer grants `manage_messages`, so Helpers with the unmodified preset lose purge and other message-management
commands. Existing Helper user/role assignments automatically receive this change when this version is deployed;
review holders first. A previously saved custom `perms` override is preserved and may still grant message
management. Fake permissions and other grants can also add access. Review them separately.

These three narrow actions are separately configurable per level by the server owner with
`,permlevels action <level> <veil_warn|veil_timeout|veil_cases>` (toggles the action). Saved action overrides
are preserved. Broad `administrator` grants bypass action checks; fake `moderate_members` grants retain their
previous moderation access. Helper's one-hour limit still applies when it has additional fake grants. Warning
automatic punishments are **skipped** for Helper-issued warnings, even when configured; the warning is recorded
and the operator notified. Review warning rules accordingly. The limit is currently fixed, not a per-server
setting. Moderator and higher levels retain their existing warning escalation behavior, which requires its own
security review. This narrow change does not complete the requested preset overhaul.

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

### Automatic and self-service role safety

Veil now checks role levels, fake permissions, ordinary/hard blacklists, role-blacklist bypasses, antinuke
role whitelists, Discord staff permissions and role hierarchy for reaction/button roles, autoroles, boost and
vanity rewards, level rewards, and VoiceMaster default roles. Checks run when configuring a new grant and
again when granting it, including after an autorole delay. Policy read failures or malformed policy prevent
new grants. Removing an existing self-role is still possible during a policy outage. These checks do not
turn native Discord permissions into Veil authorization.

**Deployment impact:** Existing panels and automated rewards that use a newly disallowed role will stop
assigning it; their saved configuration and existing role holders are not removed. Any Veil level on a role,
even a custom or unknown level, makes it ineligible for these automatic/self-service paths. Separate staff
roles from self-service and reward roles and review current holders. `,autorole` removal remains possible for
blocked roles, subject to its normal hierarchy check. Other role assignment paths, third-party bots and direct
Discord actions are not covered by this check. The underlying configuration cache and concurrent policy edits
can still race a role grant; this is not a transactional or cross-process security guarantee.

Preserve legitimate staff access deliberately; take a verified backup before changing stored configuration.
Use `,help <command>` for contextual syntax and check [the command reference](COMMANDS.md); visibility in help
does not itself enforce authorization. To roll back the code, deploy the previously tested revision with
auto-sync disabled. The previous version does not enforce these checks, so first disable unsafe self-service
and automatic roles; no data migration or automatic removal of existing roles is performed.

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

## Deploying and rolling back the Helper update

Before deploying, back up the database and review saved levels, overrides, fake permissions, role holders, and
warning-punishment rules. Test with non-production accounts in a disposable server. Deploy the reviewed revision
through your normal process, with `AUTO_SYNC_GIT=0` in the process environment, and verify the Helper commands,
timeout limit, purge denial, and higher-level access. Do not use production members to test punishments.

If a rollback is needed, stop the bot and deploy the previously tested revision with startup sync disabled. Older
code ignores the new action overrides and restores the older Helper defaults, including `manage_messages`; warn
and timeout will again require Moderator. Do not assume the old code enforces the new action overrides. Preserve
and back up the database; verify schema compatibility and access before resuming service. See
[hosting and updates](HOSTING.md#updating) for general backup and rollback guidance.

## Soft bypasses and hard role blacklist (initial increment)

The server owner or a bot developer may use `,bypass add @member` / `,bypass add @role`,
`,bypass remove ...`, and `,bypass list`. These are **separate** soft role-blacklist bypasses,
not antinuke trust, fake permissions, or general command authority. They do not waive Discord
permissions, hierarchy or other command checks. A staff member relying only on this bypass can
assign an ordinary blacklisted role through Veil only to a recipient who already has a soft bypass;
they cannot mass-assign it. Soft bypasses do not alter existing `,rbl authorize` entries, which
remain a legacy, broader trust path and still require review. Some self-role and automated role
systems always block ordinary blacklisted roles even for bypass holders.

The server owner or a bot developer can use `,hardbl add @role`, `,hardbl remove @role`, and
`,hardbl list`. Hard roles and ordinary role blacklist entries must be distinct. In the checked
Veil role commands, only a bot developer can assign a hard role; covered automated grants and
restorations skip hard roles. Adding an entry does not strip existing holders. These safeguards
are **not a complete server-wide guarantee**: direct Discord changes and other bots are not
blocked, no direct-assignment hardbl enforcement is implemented, and audit-log attribution can be
unavailable or ambiguous. Restrict Discord Manage Roles and role hierarchy independently. Discord
API failures, cache/concurrency races and unreviewed paths can also undermine enforcement. New
soft bypasses do not bypass hardbl. Hardbl is not a replacement for a complete authorization audit.

Changes use the existing per-guild role-blacklist configuration, preserving legacy entries. A
failed new-command database write is not published to the local cache. Existing writers still
mutate shared cached configuration, so concurrent edits can overwrite one another; use one bot
process and avoid simultaneous dashboard/role-blacklist changes. Back up the database before
deployment. To roll back behavior, remove new entries first or restore the pre-deployment database
backup; reverting code alone leaves the new fields in the configuration. No live Discord or
production-database testing has been performed for this increment.
