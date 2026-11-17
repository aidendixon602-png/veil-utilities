# Antinuke

Veil uses per-actor, per-action sliding windows to detect dangerous activity. It can punish the actor and attempt to
undo supported damage. These controls are inspired by [Bleed](https://docs.bleed.bot/security/antinuke) and
[Greed](https://docs.greed.best/security/antinuke), but they are not a drop-in copy.

## Getting started

```text
,an toggle on
,an ban on --threshold 3 --do ban --window 60s --command on
,an kick on --threshold 3 --do strip --window 2m
,an role on --threshold 3 --do ban
,an channel on --threshold 3 --do kick
,an botadd on --do ban
,an whitelist @TrustedUser
,an status
,an health
```

`,an` opens Veil's status panel. The rule selector shows six rules per page; use **Previous** and **Next**
to find a rule, then select it to edit. The rule page retains its on/off, count, time, punishment and command
detection controls, with fixed settings disabled. `,an health` checks the bot's core permissions and member intent, and warns
about overlapping protection if Ghost Vanguard is present. The check cannot guarantee that the bot can act on every
target. Existing positional commands such as `,an ban 3 ban 60s`, presets and the panel still work. Changing a rule does **not** turn on the antinuke itself; use `,an toggle on` first.
Only the server owner, a bot developer, or an explicitly appointed Antinuke admin can open or manage the
Antinuke commands and panel. Staff levels and fake or native Administrator alone do not grant access. Appointed
admins retain limited controls; only the server owner or a bot developer can turn the whole antinuke off or
appoint Antinuke admins.

- `--threshold` is the number of actions that triggers the rule, from 1 to 100.
- `--window` accepts seconds, minutes or hours, e.g. `60s`, `2m` or `1h` (clamped to 5 seconds–24 hours).
- `--do` is `ban`, `kick`, or `strip`. Permission grants always use `strip`, and bot adds, dangerous
  permission grants, prunes and server updates always trigger on the first action.
- `--command on` counts supported destructive Veil commands alongside Discord audit events and blocks the
  command that reaches the limit. It is on by default. `--command off` opts that rule out of **Veil command**
  detection, leaving its Discord audit-log detection on. Only operations wired into Veil's guard are counted;
  this is not a guarantee that every bot action is covered.
- `,an role` and `,an channel` set the create and delete rules together. Each has its **own counter**;
  a create and a delete do not combine to reach a threshold. Configure them separately with
  `,an rolecreate`, `,an roledelete`, `,an channelcreate` and `,an channeldelete`.
- Other direct rules include `ban`, `kick`, `botadd`, `permissions`, `roleupdate`, `channelupdate`,
  `memberroles`, `emoji`, `unban`, `prune`, `roleinvite`, `serverupdate`, `timeout` and `webhook`.
  Underscored aliases such as `role_update` and `bot_add` are available for several rules.
- `,an <rule> off` turns off that rule without discarding its settings. Omitted flags leave existing values alone.

## Optional server name-change monitoring

`,an namemonitor on` enables an **alert-only** monitor for server-name changes; `off` disables it
and omitting the argument shows the setting. Only the actual server owner or a bot developer may
configure it. It is off by default and does not change existing server-update rules or punish or
restore anyone. It monitors trusted actors too, without assuming a change was malicious. Veil
must have antinuke enabled, receive a usable Discord audit-log entry, and have an antinuke log
channel it can write to. Missing or delayed entries and delivery errors can prevent alerts.
A separate existing rule may still act on a simultaneous vanity change.

The Antinuke category in `,help` lists authorized Antinuke commands on one compact overview. Antiraid and
Anti-Alt appear separately under Security. Select
a command group to browse its subcommands, descriptions and aliases; long groups can have multiple
detail pages. If the command catalog outgrows Discord's message limits, the overview falls back to
multiple pages rather than omitting commands. The `,mod` and `,admin` panel commands are removed;
their individual moderation commands remain in
the unified Moderation help category with their previous permission gates.

To deploy, back up the database and test opt-in monitoring and moderation access in a disposable
server. Deploy with `AUTO_SYNC_GIT=0` in the process environment. To roll back, stop the bot and
restore the previously tested version without modifying the database; the older code ignores the
monitor setting and restores the panel commands. Review permissions before resuming. No live
Discord test or deployment is implied by local tests.

## Trust and safety

`,an whitelist @user`, `,an whitelist @bot`, or `,an whitelist @role` adds an explicit per-server exemption.
A role applies to **all current human and bot holders** and changes as their roles change; it is not a one-time
snapshot. `@everyone` is refused. Use `,an whitelist` to list entries and `,an unwhitelist @user`, `@bot`, or
`@role` to remove one. Existing whitelist entries remain in place. Do not whitelist every bot or an easily
self-assigned role: a compromised holder can evade antinuke checks. The configured antinuke administrators and
other users allowed by the existing antinuke administration check can manage this list; server owners should
review who has that access.

Explicitly whitelisted bots, including holders of a whitelisted role, are also exempt from the bot-add rule and
new-bot quarantine. Other protection systems, including hard role blacklist and developer protection, are not
whitelisted by this command. When role-based trust is needed and the member is not cached, Veil tries to fetch
that member. If it cannot verify role membership, it skips the audit-log response instead of punishing on an
unknown exemption, and blocks guarded bot commands; this can reduce protection during API or cache failures.
Role cache and Discord events can still be stale or unavailable, and configuration writes are not serialized
across all writers. Review trust entries and test changes in a disposable server. Only trust accounts you are
willing to exempt from protection.
`,an admin @user` toggles an antinuke administrator (server owner or bot developer only). **In Veil, antinuke admins are also
exempt**, and `,an trust` is a legacy alias for the whitelist, not Greed's separate admin command. Role-blacklist
authorization is a different setting, but users in its `authorized` list are also treated as trusted by the
current antinuke implementation. Review both settings before relying on enforcement.

### Scoped Administrator role grants

The actual Discord server owner or a bot developer can authorize a member or role to give eligible Administrator roles:

```text
,adminrolegrant add @member
,adminrolegrant add @role
,adminrolegrant list
,adminrolegrant remove @member
,adminrolegrant remove @role
,adminrolegrant removeid <member_id>
,adminrolegrant removeroleid <role_id>
```

Authorizing a role covers **all its current and future human holders** while they have it, not just
members present at setup. It does not grant Antinuke whitelist status. The everyone role, managed roles,
and roles explicitly whitelisted in Antinuke cannot be authorized. If an authorized role later becomes
Antinuke-whitelisted, its holders are trusted under the pre-existing Antinuke settings: review and remove
that trust separately. Do not authorize a role that members can easily give themselves. A member can also
be authorized directly; removing one authorization does not remove their other authorizations. Server owners
and developers can manage these authorizations under **Antinuke → Administrator-role granters** in the
Discord-login dashboard. The dashboard uses member or role IDs, and requires confirmation of role scope.

Once authorized, a human holder can directly give any eligible Administrator role to an individual with
`,r @recipient @AdministratorRole` (no approval for each grant). This covers eligible Administrator roles
created or changed in the future, not just roles present when they were authorized. Scoped grants are limited to one per
person per server every 60 seconds, across roles and recipients; the limit survives restarts. The slot is
recorded before the Discord request, so a failed or uncertain request can still consume it. These individual
assignments are excepted from Antinuke's Permission Grant **command guard**, including when that rule is on.
This exception means Antinuke does not block a rapid grant that falls within this limit; choose delegates
carefully. Other guarded actions and direct Discord actions are not exempt, and the authorization does not
appoint an Antinuke admin or add anyone to a whitelist. Bulk role commands cannot use this authorization.
A Discord Administrator role gives its recipient full Discord permissions; review the recipient carefully.
Antinuke remains best effort, not a guarantee against raids or nukes.

The requester still needs ordinary role-command access and must pass Discord/bot hierarchy, target, role
and dangerous-permission rank checks (including extra requirements for roles with Manage Server or Manage
Roles bits). Antinuke must be enabled for this scoped path, and both requester and recipient must be
verifiably untrusted by existing Antinuke rules. Roles whitelisted for Antinuke and hard-blacklisted roles
cannot be delegated. An ordinary blacklisted role receives a persistent exception **only for the specific
recipient and role** when assigned; revoking the grant does not remove the role or that separate exception.
Review and revoke such exceptions with the role blacklist commands if needed. Existing Antinuke admins and
legacy role-blacklist `authorized` users are already trusted; this feature does not change or remove their
exemptions. Rules that are off do not protect against those actions. The cooldown and grant locks are local
to one bot process; multiple instances sharing a database can race and must not be relied on for this limit.

Detection and rollback are best-effort, not a guarantee. Audit events must reach Veil; the bot needs the relevant
Discord permissions and a high enough role to punish or restore changes. Some events cannot be undone. Veil
intentionally ignores some routine actions by other bots. Veil works independently of Ghost Vanguard, including when
both bots are present. For Veil-first protection, disable the matching rules in Ghost Vanguard yourself to avoid duplicate punishment or rollback. Veil cannot read, change or verify Ghost Vanguard's settings.
Existing Veil settings are preserved: if antinuke is off, explicitly enable it with `,an toggle on`; check raid
settings with `,antiraid`. Veil still treats Ghost Vanguard as a built-in trusted bot; `,gv` is an optional bridge,
not a protection requirement. The server owner or a developer may disable the explicit bridge with `,gvbridge off` without affecting either bot's protection or logs; `,gvbridge on` restores it. A separate built-in global person exemption also exists. See
[built-in exemptions](PERMISSIONS.md#built-in-exemptions), `,an status` and `,an health`, and test in a disposable
server first. The current server-update rule filters for vanity changes; do not assume all server settings changes
are detected. See the [staged audit](MASTER_AUDIT.md) for outstanding work.

## Developer ban protection

Veil separately protects the bot's configured and discovered developers, even when the antinuke is disabled.
Explicit `,ban` and `,softban` commands targeting a developer are blocked before command permission checks; the
ban-log reban button is protected too. On an actual Discord ban, Veil uses the exact fresh audit entry to identify
the actor, attempts to ban that actor without deleting messages, then attempts to unban the developer even if
punishment failed. The server owner, the bot itself, other bot developers and explicitly whitelisted people remain
protected from retaliation. Discord can also refuse punishment due to missing permissions or role hierarchy.

The affected developer gets a DM describing the actual outcomes, with a link to the antinuke alert if it could be
posted. After a successful unban, Veil tries to create a **one-use, one-hour** invite in a publicly visible text
channel and sends it privately in that DM only. An unban does not automatically return someone to the server or
restore their roles. If a log, invite or DM cannot be sent, check manually. A blocked command does not mean the
developer was banned and does not produce a rejoin invite unless an existing ban was actually removed.

Discord's member-ban events and the bot's Ban Members permission are required to reverse a direct ban. Fresh
audit-log events and View Audit Log are required to identify the actor. If attribution is unavailable, Veil tries
to unban without guessing or punishing anyone. Old/replayed audit entries are ignored. Command attempts are only
detected when Veil receives and processes the message, and explicit ID/mention or a resolvable server member name
identifies the developer. Discord permissions, channel access, delivery failures, outages, and other bots can still
prevent or counteract these protections; test in a disposable server before relying on them.

## Help visibility and deployment

Ghost Vanguard and Automod are hidden from `,help`; the commands remain registered and their existing
runtime permissions still apply. Moderation and administration commands remain under Moderation. Before
deploying, back up configuration and review existing users, bots and roles on the whitelist. Test rule
pages, bot additions, removal and database failures in a disposable server. Deploy with `AUTO_SYNC_GIT=0`
in the process environment. To roll back, stop the bot and deploy the previous tested revision without
deleting saved entries; previous versions may quarantine individually whitelisted bots and handle uncached
role members differently. There has been no live Discord or production deployment verification.
