# Contributing to veil utilities

Thanks for helping out. This page covers everything you need.

## Setup

```bash
git clone https://github.com/aidendixon602-png/veil-utilities
cd veil-utilities
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
```

You don't need a Discord token to run the tests — the smoke test loads every module offline.

## Before you push

```bash
ruff check .                     # lint
python -m pytest -q              # unit tests + offline smoke test
python scripts/gen_commands.py   # regenerate docs/COMMANDS.md if you touched commands
```

All three run in CI on every push (Python 3.11 and 3.12), and a test fails if `docs/COMMANDS.md`
doesn't match the live commands, so keep it in sync.

## Adding a feature

1. Drop a new file in `cogs/` (one cog per section) and add it to `EXTENSIONS` in `core/bot.py`.
2. Give the cog an `emoji` so it appears in the help menu, and docstrings on every command —
   they are used verbatim in `,help` and `docs/COMMANDS.md`.
3. Fetch user-supplied URLs **only** through `core/net.py` (it blocks SSRF), and cap downloads
   with `read_capped`.
4. Read and write per-server settings through `core/db.py`, never raw SQL in a cog.
5. Wrap background tasks so exceptions are logged, not fatal.

## Style

- Python 3.10+, type hints, discord.py 2.x style.
- `ruff` is the source of truth for formatting/linting (see `pyproject.toml`).
- Keep freedom: every integration must degrade gracefully when its key/node is missing.

### Replies

Answer with the `ctx` helpers instead of building an embed by hand. Each sends one coloured line as a
reply to the command message, with no author header or footer:

| Helper | Colour | Use it for |
| --- | --- | --- |
| `ctx.approve(text)` (alias `done`) | green | it worked |
| `ctx.warn(text)` | amber | careful, empty results, "nothing to do", confirmations |
| `ctx.deny(text)` (alias `fail`) | red | it did not work |
| `ctx.neutral(text)` | grey | plain information |

Write successes as a short past-tense phrase in lower case (`banned @user`, `added @Role to @user`).
Raise `UserError("…")` for a problem the user caused: the bot turns it into `failed: …` in red.
Reach for a full embed (`discord.Embed`) only when the answer is a card or a list the person will read.

## Reporting bugs & requesting features

Use the [issue templates](https://github.com/aidendixon602-png/veil-utilities/issues/new/choose).
For security issues, see [SECURITY.md](SECURITY.md) instead.
