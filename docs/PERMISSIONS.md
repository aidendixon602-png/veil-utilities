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

## Real Discord role permissions

- `,rpa @Staff manage_messages, kick_members` grants real Discord permissions to one role. The caller and bot
  must hold the permissions being granted; Discord role hierarchy and Antinuke checks also apply. Mention a role
  if its name is also a permission name. Quote role names containing spaces.
- `,rpa manage_guild` previews roles that explicitly have that permission and asks for confirmation before
  removing it. `,rpa manage_guild @Role` excludes one role; a name or ID also works. Use underscores, or quote a
  permission with spaces. `,rpa all manage_guild` is not supported. `,removepermall manage_guild` uses the same
  preview and confirmation but does not offer a skip argument. `,rpa help` lists the supported permissions.
- Bulk removal requires the caller's **actual Discord Administrator** permission and the bot's Manage Roles.
  It excludes @everyone, integration-managed roles, and roles outside the caller's or bot's hierarchy. It does
  not remove permissions from members, channel overrides, fake permissions or unlisted roles. Removing one bit
  does not remove access implied by Administrator or other roles. Preview all pages before confirming.
- Discord edits are separate requests, not a transaction. Roles and authority are rechecked, but external edits
  can still race a request. A network failure can leave an edit's result unknown; check the reported roles' actual
  permissions before retrying. There is no automatic rollback.

## Permission sources

- **Server owner** is recognized from the Discord server. Bot developers are recognized from `OWNER_IDS` and
  the Discord application owner/team discovered at startup; developers have broad cross-server privileges.
- **Veil levels** are assigned to users or roles and saved per server. They supply bot permissions and a rank
  used by some checks. The default levels, in ascending order, are Helper (`helper`), Moderator (`moderator`),
  Admin (`admin`), Head Admin (`headadmin`), Manager (`manager`), Bot Manager (`bot_manager`) and Co-Owner
  (`coowner`). They do not confer developer or Antinuke access. Bot Manager and Manager currently have the same
  default standard bot permissions, but have different ranks. Server-specific overrides and fake permissions
  remain in effect; `,permlevels` shows the configured levels.
- **Fake permissions** are role-based grants used by Veil; they do not grant native Discord permissions to a
  person or role. They are not a replacement for Discord access controls. Use `,fakepermissions add @role
  ban_members`, `,fakepermissions list [@role]`, or `,fakepermissions remove @role ban_members`. Resetting all
  fake permissions requires confirmation. `,apa all @role` is an owner/developer-only, confirmed shortcut
  granting all bot-only permissions to one role (never @everyone). It does not change Discord permissions;
  owner, developer and Antinuke checks still apply. The existing `,apa <permission>` instead adds one
  non-dangerous **Discord** permission across eligible roles, after preview and confirmation. It now requires
  the caller’s actual Discord Administrator permission, and Manage Roles plus the permission being granted for
  the bot. Authority and role snapshots are rechecked; Antinuke guards apply.
- Ordinary Veil permission checks accept the member's **effective Discord permissions in the command channel**,
  Veil level grants, and fake grants (including combinations). Real or fake `moderate_members` also grants the
  bot-only warn, timeout and case actions. Administrator covers ordinary permission checks, but not independent
  owner, developer or Antinuke checks. Level-assignment changes still require their explicit Veil rank.
  Helper safety limits and target role/bot hierarchy checks still apply, as do the bot's own Discord permissions,
  disabled commands, channel restrictions, and other command-specific checks. Ticket support and dashboard
  access have separate policies. Existing roles with real Discord staff permissions may now use matching commands;
  review these permissions and channel overwrites before deployment.

Antinuke commands and panel interactions require the actual server owner or a recognized bot developer. A staff
level, fake or native Discord Administrator grant, or an Antinuke appointment does not unlock them. Appointed
admins (`,an admin @user`) are only exempt from antinuke enforcement. Existing appointments, whitelists, and
protection settings are not removed or changed.

**Existing assignments:** Co-Owner (`coowner`), Manager (`manager`), Moderator (`moderator`) and Head Admin
(`headadmin`) automatically migrate legacy saved keys (`supremeleader`, `overseer`, `mod`) while preserving
custom permission and rank overrides. Bot Manager uses the distinct key `bot_manager`: older `botmanager` keys
continue to migrate to Head Admin as before, avoiding an automatic promotion. Assignments that an earlier version
already converted to Head Admin cannot be distinguished or automatically restored. Review `,perm list` and
manually reassign them if appropriate. Earlier versions do not recognize `bot_manager`; before rollback, review
and deliberately reassign any new Bot Manager entries, after taking a database backup.

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

Veil now checks saved and code-defined role levels, fake permissions, scoped Administrator-role granters,
ordinary/hard blacklists, role-blacklist bypasses, antinuke role whitelists, Discord staff permissions and role hierarchy for reaction/button roles, autoroles, boost and
vanity rewards, level rewards, and VoiceMaster default roles. Checks run when configuring a new grant and
again when granting it, including after an autorole delay. Policy read failures or malformed policy prevent
new grants. Removing an existing self-role is still possible during a policy outage. These checks do not
grant a role or member any additional permissions.

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
- **Ban-log button accounts**, user IDs `843909457616044092` and `1062243262909390988`, are in `BAN_BUTTON_USERS`
  and may use the Unban and Reban buttons on moderation log cards without a per-server Admin level.

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
application owner and team, and review antinuke-trusted users and per-server trusted roles. A trusted account
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

## Permission levels on blacklisted roles

The server owner or a bot developer can use `,perm add @Role admin` for an ordinary blacklisted role after
confirming the warning, even if they hold the role themselves. **Current and future role holders can receive
that Veil level**, subject to assignment precedence, so review the holders and level before confirming. This does not grant real Discord permissions,
Antinuke trust or an exception to the role blacklist, and does not disable blacklist enforcement. The role and
saved policies are rechecked after confirmation, but concurrent changes can still race a write. Only bot developers
can assign a level to a hard-blacklisted role, also after confirmation. Others remain blocked, and nobody can
assign levels to @everyone or managed roles. Existing saved assignments are not automatically removed.

## Owner-assigned blacklisted roles

For the ordinary blacklist, the server owner or a bot developer can use `,role @member @role` to give
one blacklisted role without `,rbl auth`. Veil stores an exception for **that member and that role only**;
it does not add them to the legacy authorized list or grant Antinuke trust. Existing broad authorizations
are left unchanged. Discord's role hierarchy, bot permissions, and the hard blacklist still apply.

When the server owner grants an ordinary blacklisted role directly in Discord, Veil attempts to match a
recent, unambiguous Discord audit-log role addition to that owner and records the same scoped exception.
The bot needs **View Audit Log** as well as Manage Roles, and must receive member-update events (Server Members Intent). If the audit log is delayed, missing, inaccessible,
ambiguous, or the write fails, Veil cannot verify the owner and may remove the role; use the Veil command
instead. Audit-log correlation is best effort, not proof: rapid concurrent role changes and Discord event
or audit delays can cause incorrect attribution. Restrict Manage Roles and do not treat this as a complete
security boundary. Developer direct-Discord assignments are not automatically excepted.

Use `,rbl exceptions` to review individual exceptions and `,rbl unallow @member @role` to revoke one.
Revoking an exception does not itself remove the Discord role; a later blacklist scan/strip, role update, or
rejoin can enforce the blacklist. Veil clears the exception when it observes that role being removed, but
can miss changes while offline; review exceptions after downtime. Saved exceptions can remain when a role
is removed from and later re-added to the blacklist; review and revoke them if needed. Automatic/self-service
role grants remain blocked for blacklisted roles, and mass role assignment cannot grant them.

## Soft bypasses and hard role blacklist (initial increment)

The server owner or a bot developer may use `,bypass @member` / `,bypass @role` as shortcuts for
`,bypass add @member` / `,bypass add @role`. The shortcut **adds only**: using it again does not remove
an existing bypass. Use `,bypass remove @member` / `@role` to revoke one, or `,bypass list` to review them.
A role bypass covers its current and future holders. These are **separate** soft role-blacklist bypasses,
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

## RBL management access and delivery

Run `,rbl` in a server for a single management card covering ordinary blacklists, soft
bypasses and hard blacklists. Only the actual server owner or a recognized bot developer
can use these commands and their aliases; an assigned Owner rank, fake permission or Discord
Administrator permission is not enough. `,rbl list` **always posts in the invoking channel**, including
when other RBL replies are configured for DMs; everyone who can view the channel can see the listed roles
and IDs. Use a private staff channel if needed. List navigation is restricted to the requester and rechecks
owner/developer access. Other RBL command replies follow the `,logs` role-blacklist reply setting (channel
by default or DM when enabled); direct RBL help remains private. If a DM cannot be sent, there is no public
fallback; changes already made before a failed reply may have completed. Messages you type, old bot messages,
screenshots and information shared by recipients cannot be made private retroactively. Interactive RBL action
buttons check the current server owner/developer again when clicked.
The dashboard remains an additional settings interface with its own server-side checks and requires
Discord login; it no longer starts an unauthenticated local listener.

Existing legacy authorizations are retained. A role assignment made by legacy authorized staff
cannot silently authorize a new recipient; only an owner/developer can do that. Discord audit
entries no longer create new authorizations automatically: explicitly authorize the recipient
before assigning an ordinary blacklisted role directly in Discord. Existing automatic removal is
best effort and may still be affected by delayed events, permissions and role hierarchy. This does
not prevent direct Discord assignments of hard-blacklisted roles. As with other configuration
writers, RBL operations still share a mutable cache and may lose updates under concurrent writers;
serialized, transactional configuration updates remain unfinished.

Before deployment, back up the database, review existing blacklist/legacy authorization entries,
and test DM delivery and denial paths in a disposable server. Deploy the reviewed revision with
`AUTO_SYNC_GIT=0` in the process environment. Roll back by stopping the bot and deploying the
previous tested revision without altering the database; older revisions may restore the former
RBL access, audit-based automatic authorization, and public replies. Review those risks before
resuming service. This change is not a live Discord or Wispbyte deployment verification.

## Scoped Administrator-role delegation

The Discord server owner or bot developer can authorize a human to grant eligible Administrator roles with
`,adminrolegrant add @member` or authorize all current and future human holders of a role with
`,adminrolegrant add @role`. Use `,adminrolegrant list`, `,adminrolegrant remove @member` or `@role`,
`,adminrolegrant removeid <member_id>`, or `,adminrolegrant removeroleid <role_id>` to review or revoke.
Role-based authorization is separate from Antinuke whitelisting; avoid roles members can self-assign.
This covers eligible Administrator roles added in the future as well. A human with actual Discord Administrator
uses the native path instead and does not require delegation or its 60-second scoped limit.
The authorized person can use `,r` directly without further approval, limited to one individual scoped grant
per 60 seconds. Those grants are excepted from the Permission Grant command guard; other actions are not.
Command access, hierarchy, mixed dangerous-permission rank gates and blacklists still apply; no Antinuke
trust is conferred. See [Antinuke](ANTINUKE.md#scoped-administrator-role-grants) for restrictions and limits.

### Role security upgrade: operator review

Direct and bulk **assignment** of roles conferring Veil levels (including code-defined levels), fake permissions,
scoped Administrator-role granting authority, role-blacklist bypass or Antinuke role trust is limited to
the actual server owner, recognized bot developers and human holders of actual Discord Administrator. Scoped
Administrator-role delegation alone does not confer authority to grant other bot/security privileges. Ordinary staff who
previously granted such roles may lose that ability. Native Discord dangerous permissions and hierarchy are
separate checks. Policy read failures can prevent grants. Existing holders and saved settings
are not removed. Review code-defined grants, `,perm roles`, `,fakepermissions list`, scoped granter roles,
blacklist bypasses, Antinuke whitelists, existing role holders and every auto/self-service role before deployment.
Grant access intentionally; do not use a self-service role as a trust or privilege role.

Back up the database and export/review Discord role assignments and channel overwrites before deployment.
Test with a disposable server. To roll back, stop the bot, deploy the previously tested revision with
`AUTO_SYNC_GIT=0` in the process environment, and review its different access rules before resuming. Restore a database backup only after evaluating loss of later changes; database restoration cannot
restore Discord roles or their original IDs. These checks are not atomic with concurrent configuration or
Discord changes, and other bots and direct Discord actions remain outside their coverage.

### Discord Administrator role management

A human member with the **actual Discord Administrator** permission can use Veil's role commands without a
separate Veil rank or Administrator-role delegation. This includes assigning a role that itself carries Discord
Administrator or Veil/fake permissions or security trust. Role management remains a powerful authority: anyone
holding Discord Administrator can thereby delegate those powers to others. Audit Administrator holders and the
roles below them before deploying this change; removing a delegation does not remove roles already granted.
Fake Administrator and Veil levels alone do not qualify for this native-Administrator exception.

The role must be below both the member's and the bot's highest roles (the server owner retains Discord's owner
exception); managed and @everyone roles cannot be assigned. Human members with actual Discord Administrator
can individually add or remove eligible roles on themselves using `,r @yourself @role` (which toggles the role),
or the explicit add/remove forms. The actor's highest role must also be below the bot's highest role. This does
not grant access to self-role changes through bulk commands, fake Administrator, Veil ranks or scoped delegation.
The global protected-person rule and blacklist checks still apply; other self-moderation remains prohibited.
Veil also retains its stricter target-member
hierarchy, owner/developer target protections, blacklist/hard-blacklist policy, command restrictions, bot
permissions and bulk confirmations. Consequently, some Discord-permitted role changes remain blocked by Veil.
Antinuke treatment and the limitations of checking current audit state are described in the Antinuke guide.
Existing saved grants and exemptions are not altered.
