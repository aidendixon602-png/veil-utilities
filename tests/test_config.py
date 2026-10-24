import pytest

from core.config import ConfigError, Settings


def test_defaults_with_empty_environment():
    s = Settings.from_env({})
    assert s.prefix == ","
    assert s.db_path == "veil.db"
    assert s.auth_required is True
    assert s.low_memory is False
    assert s.owner_ids == frozenset()
    assert s.auth_log_channel is None
    assert s.ai_base_url is None and s.ai_model is None  # auto-detected from the key at runtime
    assert s.music_enabled is True and s.lavalink_uri is None


@pytest.mark.parametrize("raw, expected", [
    ("true", True), ("TRUE", True), ("1", True), ("yes", True), ("on", True),
    ("false", False), ("0", False), ("no", False), ("off", False),
])
def test_boolean_values(raw, expected):
    assert Settings.from_env({"LOW_MEMORY": raw}).low_memory is expected


def test_blank_values_fall_back_to_defaults():
    s = Settings.from_env({"PREFIX": "  ", "AUTH_REQUIRED": "", "AUTH_LOG_CHANNEL": ""})
    assert s.prefix == ","
    assert s.auth_required is True
    assert s.auth_log_channel is None


def test_owner_ids_are_parsed():
    s = Settings.from_env({"OWNER_IDS": "111, 222,333 ,"})
    assert s.owner_ids == frozenset({111, 222, 333})


def test_every_problem_is_reported_at_once():
    with pytest.raises(ConfigError) as exc:
        Settings.from_env({
            "LOW_MEMORY": "maybe",
            "OWNER_IDS": "123,abc",
            "AUTH_LOG_CHANNEL": "#logs",
            "AI_BASE_URL": "api.example.com",
            "PREFIX": "x" * 11,
        })
    problems = "\n".join(exc.value.problems)
    assert len(exc.value.problems) == 5
    assert "LOW_MEMORY" in problems
    assert "abc" in problems
    assert "AUTH_LOG_CHANNEL" in problems
    assert "AI_BASE_URL" in problems
    assert "PREFIX" in problems


def test_urls_lose_trailing_slash():
    assert Settings.from_env({"AI_BASE_URL": "https://example.com/v1/"}).ai_base_url == "https://example.com/v1"


def test_runtime_requires_token_and_owner():
    with pytest.raises(ConfigError) as exc:
        Settings.from_env({}).require_runtime()
    assert len(exc.value.problems) == 2
    Settings.from_env({"DISCORD_TOKEN": "abc", "OWNER_IDS": "1"}).require_runtime()


def test_summary_never_leaks_secrets():
    s = Settings.from_env({"DISCORD_TOKEN": "super-secret-token", "AI_API_KEY": "sk-123"})
    summary = s.summary()
    assert "super-secret-token" not in summary
    assert "sk-123" not in summary
    assert "discord_token=set" in summary


def test_token_cleanup_and_music_switches():
    s = Settings.from_env({"DISCORD_TOKEN": "Bot abc.def"})
    assert s.discord_token == "abc.def"
    assert Settings.from_env({"LAVALINK_URI": "off"}).music_enabled is False
    assert Settings.from_env({"MUSIC_ENABLED": "false"}).music_enabled is False
    multi = Settings.from_env({"LAVALINK_URI": "http://a:1, https://b:2/"})
    assert multi.lavalink_uri == "http://a:1,https://b:2"
    with pytest.raises(ConfigError):
        Settings.from_env({"LAVALINK_URI": "a:1"})
