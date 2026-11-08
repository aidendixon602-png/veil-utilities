"""Nothing posted where server administrators can read it may carry secrets, GitHub details or the bot's paths."""
from types import SimpleNamespace

from core import logembeds, redact


def test_tokens_and_secret_env_values_are_hidden(monkeypatch):
    monkeypatch.setenv("SOME_API_KEY", "supersecretvalue123")
    token = "MTIzNDU2Nzg5MDEyMzQ1Njc4.GabcDE." + "x" * 38
    text = redact.scrub(f"token={token} key=supersecretvalue123 gh=ghp_{'a' * 36}")
    assert token not in text and "supersecretvalue123" not in text and "ghp_" not in text


def test_github_addresses_and_repo_names_are_hidden(monkeypatch):
    monkeypatch.setattr(redact, "_repo_names", lambda: ("someowner", "secret-repo"))
    text = redact.scrub("fatal: unable to access 'https://u:p@github.com/someowner/secret-repo.git/' "
                        "git@github.com:someowner/secret-repo.git and SomeOwner mentioned")
    assert "someowner" not in text.lower() and "secret-repo" not in text and "u:p@" not in text


def test_the_bot_folder_path_is_shortened():
    text = redact.scrub(f'File "{redact.ROOT}/cogs/owner.py", line 1')
    assert redact.ROOT not in text and './cogs/owner.py' in text


def test_webhook_urls_are_hidden():
    assert "abc" not in redact.scrub("https://discord.com/api/webhooks/123/abc-DEF_ghi")


def _ctx(content, *, hidden=False, cog="Utility"):
    author = SimpleNamespace(id=5, name="dev", display_name="dev", mention="<@5>",
                             display_avatar=SimpleNamespace(url="https://cdn/x.png"))
    command = SimpleNamespace(name="eval", qualified_name="eval", hidden=hidden, cog_name=cog, aliases=[])
    return SimpleNamespace(message=SimpleNamespace(content=content, id=9, jump_url=None), command=command,
                           author=author, channel=SimpleNamespace(mention="#c"), prefix=",", invoked_with="eval")


def _fields(embed):
    return {f.name: f.value for f in embed.fields}


def _text(card) -> str:
    return "\n".join([card.description or ""] + [f.value for f in card.fields])


def test_developer_command_logs_hide_their_arguments():
    shown = _text(logembeds.command_used(_ctx(",eval print(bot.http.token)", hidden=True, cog="Owner"),
                                         "Bot Developer", "Built-in"))
    assert "print" not in shown and "arguments hidden" in shown and ",eval" in shown


def test_ordinary_command_logs_still_show_the_command_but_scrubbed():
    token = "MTIzNDU2Nzg5MDEyMzQ1Njc4.GabcDE." + "x" * 38
    line = _text(logembeds.command_used(_ctx(f",say {token}"), "Admin", "role"))
    assert ",say" in line and token not in line


def test_failed_command_reasons_are_scrubbed():
    line = _text(logembeds.command_used(_ctx(",x"), "Admin", "role", error=f'File "{redact.ROOT}/core/bot.py" broke'))
    assert redact.ROOT not in line and "broke" in line


def test_slotted_settings_secrets_are_scrubbed_even_when_not_in_environment(monkeypatch):
    import core.config as config

    token = "unusual-bot-token-value"
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    monkeypatch.setattr(config, "_settings", config.Settings.from_env({"DISCORD_TOKEN": token}))
    assert token not in redact.scrub("failure included " + token)


def test_developer_log_attachment_scrubs_known_secrets(tmp_path, monkeypatch):
    import asyncio
    from dataclasses import replace
    from unittest.mock import AsyncMock

    import cogs.owner as owner

    secret = "unusual-secret-value-123"
    monkeypatch.setenv("CUSTOM_API_KEY", secret)
    path = tmp_path / "veil.log"
    path.write_text(f"request failed with {secret}\n", encoding="utf-8")
    monkeypatch.setattr(owner, "settings", replace(owner.settings, log_file=str(path)))
    ctx = SimpleNamespace(send=AsyncMock())
    asyncio.run(owner.Owner.botlogs.callback(owner.Owner(None), ctx))
    attachment = ctx.send.await_args.kwargs["file"]
    attachment.fp.seek(0)
    content = attachment.fp.read().decode()
    assert secret not in content and "[hidden]" in content


def test_hidden_command_failure_keeps_arguments_private_and_reason_scrubbed(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret-value-abc123")
    ctx = _ctx(",eval private_argument", hidden=True, cog="Owner")
    card = logembeds.command_used(ctx, "Developer", "Built-in", error="Failed: secret-value-abc123")
    text = _text(card)
    assert "private_argument" not in text and "secret-value-abc123" not in text
    assert "arguments hidden" in text and "**Result:** ❌ Failed:" in text
    assert not card.fields
