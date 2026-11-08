# Bleed documentation comparison

A high-level review of [notiku/bleed-docs](https://github.com/notiku/bleed-docs), compared with Veil's current
commands and implementation. It is not a claim of exact parity; the public documentation may not describe every
restriction or edge case. No Bleed code is incorporated.

## Security and moderation

| Area | Bleed documentation | Veil today / possible next step |
|---|---|---|
| [Antinuke](https://github.com/notiku/bleed-docs/blob/main/security/antinuke.mdx) | Thresholds, punishment, admin and whitelist configuration. | Veil has threshold rules, presets, exemptions, a panel, health checks and best-effort rollback. Review overlapping security bots and test failure reporting before adding more rules. |
| [Fake permissions](https://github.com/notiku/bleed-docs/blob/main/security/fake-permissions.mdx) | Grant moderation permissions through the bot instead of directly through Discord. | Veil supports role-based fake permissions in addition to its own rank checks and command restrictions. These are additive, not an exact copy of Bleed's policy. |
| [Antiraid](https://github.com/notiku/bleed-docs/blob/main/security/antiraid.mdx) | Mass joins, avatar and account-age filters, with configurable actions and exemptions. | Veil has corresponding join filters and raid mode, with different commands and options. |
| [Honeypots](https://github.com/notiku/bleed-docs/blob/main/security/honeypot.mdx) | Decoy channels that punish accounts that post there. | No `honeypot` command is documented in Veil's current command reference. Adding one would need careful owner-controlled setup, exemptions and false-positive safeguards. |
| [Moderation](https://github.com/notiku/bleed-docs/blob/main/security/moderation.mdx) | Staff roles and customizable command/DM invoke messages. | Veil has staff panels, roles, ranks and moderation commands. More configurable invoke messages could be considered separately. |

## Broader features

- [Tickets](https://github.com/notiku/bleed-docs/tree/main/configuration/tickets): both document ticket panels,
  participants and support roles. Bleed also documents reusable intake forms and role/member access rules; these
  are potential additions rather than features to assume are already identical.
- [Starboard](https://github.com/notiku/bleed-docs/blob/main/configuration/starboard.mdx): Veil has a channel,
  emoji and threshold; Bleed documents additional ignores and presentation settings.
- [Vanity roles](https://github.com/notiku/bleed-docs/blob/main/configuration/roles/vanity.mdx), reaction roles,
  leveling, giveaways, music and social notifications: Veil has related features, but options and operational
  dependencies differ (for example presence intent, Lavalink, or external feed services).

Suggested priorities: first validate security interactions and authorization in a disposable server, then choose
whether ticket forms/access controls, starboard controls or an opt-in honeypot are worth implementing. These
suggestions do not change existing settings or imply a complete comparison of every Bleed feature.
