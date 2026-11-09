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

`,an` opens Veil's status panel. `,an health` checks the bot's core permissions and member intent, and warns
about overlapping protection if Ghost Vanguard is present. The check cannot guarantee that the bot can act on every
target. Existing positional commands such as `,an ban 3 ban 60s`, presets and the panel still work. Changing a rule does **not** turn on the antinuke itself; use `,an toggle on` first.
Only the server owner or a bot developer can turn the whole antinuke off.

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

The Antinuke category in `,help` now lists security subcommands and aliases across pages. The
`,mod` and `,admin` panel commands are removed; their individual moderation commands remain in
the unified Moderation help category with their previous permission gates.

To deploy, back up the database and test opt-in monitoring and moderation access in a disposable
server. Deploy with `AUTO_SYNC_GIT=0` in the process environment. To roll back, stop the bot and
restore the previously tested version without modifying the database; the older code ignores the
monitor setting and restores the panel commands. Review permissions before resuming. No live
Discord test or deployment is implied by local tests.

## Trust and safety

`,an whitelist @user` or `@role` exempts that user or everyone with that role from antinuke checks. Remove with
`,an unwhitelist @user` or `@role`. Only trust accounts you are willing to exempt from protection.
`,an admin @user` toggles an antinuke administrator (server owner only). **In Veil, antinuke admins are also
exempt**, and `,an trust` is a legacy alias for the whitelist, not Greed's separate admin command. Role-blacklist
authorization is a different setting, but users in its `authorized` list are also treated as trusted by the
current antinuke implementation. Review both settings before relying on enforcement.

Detection and rollback are best-effort, not a guarantee. Audit events must reach Veil; the bot needs the relevant
Discord permissions and a high enough role to punish or restore changes. Some events cannot be undone. Veil
intentionally ignores some routine actions by other bots. Veil works independently of Ghost Vanguard, including when
both bots are present. **Disable overlapping protection in one bot** to avoid duplicate punishment or rollback.
Existing Veil settings are preserved: if antinuke is off, explicitly enable it with `,an toggle on`; check raid
settings with `,antiraid`. Veil still treats Ghost Vanguard as a built-in trusted bot; `,gv` is an optional bridge,
not a protection requirement. A separate built-in global person exemption also exists. See
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
