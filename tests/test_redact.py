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


def test_developer_command_logs_hide_their_arguments():
    card = logembeds.command_used(_ctx(",eval print(bot.http.token)", hidden=True, cog="Owner"), "Bot Developer", "Built-in")
    shown = _fields(card)["Command"]
    assert "print" not in shown and "arguments hidden" in shown and ",eval" in shown


def test_ordinary_command_logs_still_show_the_command_but_scrubbed():
    token = "MTIzNDU2Nzg5MDEyMzQ1Njc4.GabcDE." + "x" * 38
    card = logembeds.command_used(_ctx(f",say {token}"), "Admin", "role")
    assert ",say" in _fields(card)["Command"] and token not in _fields(card)["Command"]


def test_failed_command_reasons_are_scrubbed():
    card = logembeds.command_used(_ctx(",x"), "Admin", "role", error=f'File "{redact.ROOT}/core/bot.py" broke')
    assert redact.ROOT not in _fields(card)["Error"]
