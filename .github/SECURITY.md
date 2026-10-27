# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.0.x   | :white_check_mark: |
| < 1.0   | :x:                |

## Reporting a Vulnerability

This bot runs with high Discord permissions (that's the point of antinuke), so a vulnerability
here is a vulnerability in every server that hosts it. Please report issues responsibly:

1. **Do not open a public issue** for anything exploitable (permission bypasses, antinuke gaps,
   token/secret leaks, SSRF, remote code execution paths).
2. Email the maintainer via the address on the
   [GitHub profile](https://github.com/aidendixon602-png), or open a private vulnerability report
   through GitHub's *Security → Report a vulnerability* tab.
3. Include: what you found, how to reproduce it, and the commit you're on.
   Screenshots or a short clip help a lot.

You can expect an acknowledgement within a week. If the report is accepted, a fix will ship in a
patch release and you'll be credited in the changelog (unless you'd rather stay anonymous).

## Scope notes

Things that are **by design**, not bugs:

- Bot developers can do everything, everywhere. They are the owner (or team members) of the bot's Discord application plus any `OWNER_IDS`, so guard that Discord account and team carefully.
- Servers must be authorized by a developer when `AUTH_REQUIRED=true`.
- The bot needs Administrator to do its job; it refuses moderation configuration without it.

Things that are absolutely in scope:

- Fake-permission or level-system escalation.
- Antinuke/antiraid bypasses, including through the bot's own commands.
- Secrets or tokens exposed in logs, DMs, or this repository.
- Unsafe handling of user-supplied URLs or files.
