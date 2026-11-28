"""The developer `,system` card stays readable, complete and honest about what it cannot measure."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import cogs.owner as O
from cogs.owner import Owner

OWNER = SimpleNamespace(id=1, __str__=lambda self: "dev")


def field(embed, name):
    return next((item.value for item in embed.fields if item.name == name), None)


def assert_fits(embed):
    assert len(embed) <= 6000
    assert len(embed.title or "") <= 256 and len(embed.description or "") <= 4096
    assert all(len(item.name) <= 256 and 0 < len(item.value) <= 1024 for item in embed.fields)


def guild(members=10, channels=3, unavailable=False):
    return SimpleNamespace(member_count=members, channels=[object()] * channels, unavailable=unavailable)


def make_bot(**overrides):
    bot = SimpleNamespace(
        started_at=0.0, latency=0.1234, guilds=[guild(), guild(5, 2, unavailable=True)],
        authorized={1, 2}, user=SimpleNamespace(name="Veil Utilities", display_avatar=SimpleNamespace(url="https://x/a.png")),
        latencies=[(0, 0.12)], shard_count=1,
        db=SimpleNamespace(path=None, fetchone=AsyncMock(return_value=(1,))),
        _error_seq=0, recent_errors=[], maintenance=None, killswitch=set(), blacklist={1},
        extensions={"cogs.owner": object(), "cogs.utility": object()},
        walk_commands=lambda: iter(range(545)),
    )
    bot.__dict__.update(overrides)
    return bot


def run(cog, ctx):
    return asyncio.run(Owner.system.callback(cog, ctx))


def make_ctx():
    return SimpleNamespace(send=AsyncMock())


def test_healthy_card_reports_reach_health_and_suppresses_mentions(monkeypatch):
    monkeypatch.setattr(O, "memory_usage", lambda: (81.0, 81.0))
    monkeypatch.setattr(O, "core_changed_on_disk", lambda _bot: False)
    monkeypatch.setattr(O.time, "time", lambda: 26)

    ctx = make_ctx()
    run(Owner(make_bot()), ctx)

    kwargs = ctx.send.await_args.kwargs
    embed = kwargs["embed"]
    assert embed.colour.value == O.Colors.main
    assert embed.author.name == "Veil Utilities · System"
    # the one-line summary carries the three values that matter most
    assert "26 seconds" in embed.description
    assert "123 ms" in embed.description and "81 MB" in embed.description
    assert field(embed, "Servers") == "2 joined · **2** authorized"
    assert field(embed, "Members") == "15 cached"
    assert field(embed, "Channels") == "5 cached"
    assert field(embed, "Server reach") == "🟡 1 reachable · 1 unavailable"
    assert field(embed, "Errors (this run)") == "🟢 none"
    assert field(embed, "Maintenance") == "🟢 off"
    assert field(embed, "Kill-switched") == "none"
    assert field(embed, "Database") == "PostgreSQL · 🟢 " + field(embed, "Database").split("🟢 ")[1]
    assert "545 commands" in embed.footer.text and "2 modules" in embed.footer.text
    assert "1 blacklisted" in embed.footer.text
    assert kwargs["allowed_mentions"].everyone is False
    assert_fits(embed)


def test_unknown_latency_is_not_reported_as_zero(monkeypatch):
    monkeypatch.setattr(O, "memory_usage", lambda: (10.0, 10.0))
    monkeypatch.setattr(O, "core_changed_on_disk", lambda _bot: False)
    for latency in (float("nan"), None):
        ctx = make_ctx()
        run(Owner(make_bot(latency=latency)), ctx)
        assert "Unavailable" in ctx.send.await_args.kwargs["embed"].description


def test_bitbucket_latency_is_flagged_red(monkeypatch):
    monkeypatch.setattr(O, "memory_usage", lambda: (10.0, 10.0))
    monkeypatch.setattr(O, "core_changed_on_disk", lambda _bot: False)
    ctx = make_ctx()
    run(Owner(make_bot(latency=0.9)), ctx)
    assert ctx.send.await_args.kwargs["embed"].description.startswith("🔴")


def test_a_failing_database_is_reported_as_unavailable(monkeypatch):
    async def down(_query):
        raise OSError("gone")

    monkeypatch.setattr(O, "memory_usage", lambda: (10.0, 10.0))
    monkeypatch.setattr(O, "core_changed_on_disk", lambda _bot: False)
    ctx = make_ctx()
    bot = make_bot(db=SimpleNamespace(path=None, fetchone=down))
    run(Owner(bot), ctx)
    assert field(ctx.send.await_args.kwargs["embed"], "Database") == "🔴 unavailable"


def test_memory_peak_only_and_entirely_unavailable(monkeypatch):
    monkeypatch.setattr(O, "core_changed_on_disk", lambda _bot: False)
    monkeypatch.setattr(O, "memory_usage", lambda: (None, 200.0))
    ctx = make_ctx()
    run(Owner(make_bot()), ctx)
    assert "~200 MB peak" in ctx.send.await_args.kwargs["embed"].description

    monkeypatch.setattr(O, "memory_usage", lambda: (None, None))
    ctx = make_ctx()
    run(Owner(make_bot()), ctx)
    assert "**Memory** Unavailable" in ctx.send.await_args.kwargs["embed"].description


def test_errors_survive_maintenance_killswitch_and_multiple_shards(monkeypatch):
    monkeypatch.setattr(O, "memory_usage", lambda: (10.0, 10.0))
    monkeypatch.setattr(O, "core_changed_on_disk", lambda _bot: True)
    ctx = make_ctx()
    bot = make_bot(_error_seq=3, recent_errors=[{"command": "ban"}], maintenance="upgrading",
                   killswitch={"eval", "shell"}, latencies=[(0, 0.1), (1, 0.2)], shard_count=2)
    run(Owner(bot), ctx)
    embed = ctx.send.await_args.kwargs["embed"]
    assert field(embed, "Errors (this run)") == "**3** · last in `ban`"
    assert field(embed, "Maintenance") == "🛠️ on — upgrading"
    assert field(embed, "Kill-switched (2)") == "`eval`, `shell`"
    assert "Shards (2)" in [f.name for f in embed.fields]
    assert "code changed on disk" in embed.footer.text
    assert_fits(embed)


def test_a_long_killswitch_list_stays_inside_the_field_limit(monkeypatch):
    monkeypatch.setattr(O, "memory_usage", lambda: (10.0, 10.0))
    monkeypatch.setattr(O, "core_changed_on_disk", lambda _bot: False)
    ctx = make_ctx()
    run(Owner(make_bot(killswitch={f"command-number-{i:03d}" for i in range(200)})), ctx)
    embed = ctx.send.await_args.kwargs["embed"]
    assert len(field(embed, "Kill-switched (200)")) <= 1024
    assert_fits(embed)


def test_a_single_shard_does_not_get_its_own_field(monkeypatch):
    monkeypatch.setattr(O, "memory_usage", lambda: (10.0, 10.0))
    monkeypatch.setattr(O, "core_changed_on_disk", lambda _bot: False)
    ctx = make_ctx()
    run(Owner(make_bot()), ctx)
    names = [f.name for f in ctx.send.await_args.kwargs["embed"].fields]
    assert not any(name.startswith("Shards") for name in names)  # already in the summary line


def test_sqlite_size_is_shown_when_the_file_exists(tmp_path, monkeypatch):
    db_file = tmp_path / "veil.db"
    db_file.write_bytes(b"x" * 2048)
    monkeypatch.setattr(O, "memory_usage", lambda: (10.0, 10.0))
    monkeypatch.setattr(O, "core_changed_on_disk", lambda _bot: False)
    ctx = make_ctx()
    bot = make_bot(db=SimpleNamespace(path=str(db_file), fetchone=AsyncMock(return_value=(1,))))
    run(Owner(bot), ctx)
    assert field(ctx.send.await_args.kwargs["embed"], "Database").startswith("SQLite · 0.00 MB")


def test_no_servers_joined_reads_honestly(monkeypatch):
    monkeypatch.setattr(O, "memory_usage", lambda: (10.0, 10.0))
    monkeypatch.setattr(O, "core_changed_on_disk", lambda _bot: False)
    ctx = make_ctx()
    run(Owner(make_bot(guilds=[], authorized=set())), ctx)
    embed = ctx.send.await_args.kwargs["embed"]
    assert field(embed, "Server reach") is None  # nothing to warn about
    assert field(embed, "Servers") == "0 joined · **0** authorized"
