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
when Ghost Vanguard handles audit-log protection. The check cannot guarantee that the bot can act on every target. Existing positional commands such as `,an ban 3 ban 60s`, presets and the
panel still work. Changing a rule does **not** turn on the antinuke itself; use `,an toggle on` first.
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

## Trust and safety

`,an whitelist @user` or `@role` exempts that user or everyone with that role from antinuke checks. Remove with
`,an unwhitelist @user` or `@role`. Only trust accounts you are willing to exempt from protection.
`,an admin @user` toggles an antinuke administrator (server owner only). **In Veil, antinuke admins are also
exempt**, and `,an trust` is a legacy alias for the whitelist, not Greed's separate admin command. Role-blacklist
authorization is a different system and does not grant an antinuke whitelist.

Detection and rollback are best-effort, not a guarantee. Audit events must reach Veil; the bot needs the relevant
Discord permissions and a high enough role to punish or restore changes. Some events cannot be undone. Veil
intentionally ignores some routine actions by other bots, and where Ghost Vanguard is configured as the server's
protector it handles audit-log detection instead. See `,an status` and check alerts for the actual rule state.
Test changes in a disposable server before relying on them.
