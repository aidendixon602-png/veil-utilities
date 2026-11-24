"""Safety regressions for destination review, setup and exclusions."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from cogs.logging import Logging, OFF, _channel_for
from core.dashboard_security_views import log_routes


def channel(cid, name):
    c = MagicMock(spec=discord.TextChannel)
    c.id, c.name, c.mention = cid, name, f"<#{cid}>"
    c.send, c.edit, c.delete = AsyncMock(), AsyncMock(), AsyncMock()
    c.permissions_for.return_value = SimpleNamespace(view_channel=True, send_messages=True, embed_links=True)
    return c


def guild_with(*channels):
    g = SimpleNamespace(id=1, name="test", text_channels=list(channels), categories=[],
                        me=MagicMock(guild_permissions=SimpleNamespace(manage_channels=True)),
                        default_role=MagicMock(), get_channel=lambda cid: next((c for c in channels if c.id == cid), None))
    for c in channels:
        c.guild = g
    return g


def cog_with(config):
    store = {"logs": config}
    async def get(gid, key, default=None):
        return store.setdefault(key, {})
    async def save(gid, key, data):
        store[key] = data
    db = SimpleNamespace(get_config=AsyncMock(side_effect=get), set_config=AsyncMock(side_effect=save))
    return Logging(SimpleNamespace(db=db)), store


def test_veil_channels_and_legacy_ids_route_immediately_while_vanguard_is_skipped():
    c = channel(1, "message-logs")
    vanguard = channel(2, "bans")
    g = guild_with(c, vanguard)
    assert _channel_for(g, {}, "messages") is c
    assert _channel_for(g, {"messages": 1}, "messages") is c
    assert _channel_for(g, {"bans": 2}, "bans") is None
    assert _channel_for(g, {"bans": 2, "approved_routes": {"bans": 2}}, "bans") is vanguard
    assert "unavailable" in log_routes(g, {"messages": 999}).lower()


def test_approved_routes_are_neutral_to_channel_and_category_names():
    c = channel(1, "bans")
    c.category = SimpleNamespace(name="Ghost Vanguard")
    g = guild_with(c)
    assert _channel_for(g, {"bans": 1, "approved_routes": {"bans": 1}}, "bans") is c


def test_off_and_broken_explicit_never_fall_back():
    c = channel(1, "server")
    g = guild_with(c)
    cfg = {"server": 1, "approved_routes": {"server": 1}}
    assert _channel_for(g, cfg, "messages") is c
    assert _channel_for(g, {**cfg, "messages": OFF}, "messages") is None
    assert _channel_for(g, {**cfg, "messages": 999, "approved_routes": {"server": 1, "messages": 999}}, "messages") is None
    assert _channel_for(g, {**cfg, "messages": 999}, "messages") is None


def test_setup_only_creates_unset_without_touching_existing_and_is_idempotent():
    async def run():
        old = channel(1, "join-logs")
        g = guild_with(old)
        made = []
        async def create(name, **kw):
            c = channel(10 + len(made), name)
            made.append(c)
            return c
        g.create_category = AsyncMock(return_value=SimpleNamespace(id=50))
        g.create_text_channel = AsyncMock(side_effect=create)
        cog, store = cog_with({"joinleave": 1, "commands": OFF})
        result = await cog.organize(g)
        assert len(result.created) == 6 and store["logs"]["joinleave"] == 1
        assert store["logs"]["commands"] == OFF
        assert all(store["logs"]["approved_routes"][k] == store["logs"][k] for k in store["logs"]["approved_routes"])
        old.edit.assert_not_awaited()
        old.delete.assert_not_awaited()
        assert not (await cog.organize(g)).created
        assert g.create_category.await_count == 1
    asyncio.run(run())


def test_setup_write_failure_reports_and_stops():
    async def run():
        g = guild_with()
        g.create_category = AsyncMock(return_value=SimpleNamespace(id=50))
        g.create_text_channel = AsyncMock(return_value=channel(10, "join-logs"))
        cog, _ = cog_with({})
        cog.bot.db.set_config_committed = AsyncMock(side_effect=OSError("secret"))
        report = await cog.organize(g)
        assert len(report.created) == 1 and "not confirmed" in report.failures[0]
        assert g.create_text_channel.await_count == 1
    asyncio.run(run())


def test_cleanup_delete_never_deletes():
    async def run():
        c = channel(1, "join-logs")
        g = guild_with(c)
        cog, _ = cog_with({})
        ctx = SimpleNamespace(guild=g, neutral=AsyncMock())
        await Logging.logs_cleanup.callback(cog, ctx, "delete")
        c.delete.assert_not_awaited()
        assert "Nothing was deleted" in ctx.neutral.await_args.args[0]
    asyncio.run(run())


def test_bulk_excludes_users_and_suppresses_mentions():
    async def run():
        out = channel(1, "logs")
        g = guild_with(out)
        src = SimpleNamespace(id=20, name="source", mention="<#20>", guild=g)
        now = discord.utils.utcnow()
        def msg(uid, content):
            return SimpleNamespace(id=uid, guild=g, channel=src, author=SimpleNamespace(id=uid, bot=False, mention=f"<@{uid}>"),
                                   content=content, created_at=now)
        cog, _ = cog_with({"messages": 1, "approved_routes": {"messages": 1}, "ignore": {"users": [3]}})
        await cog.on_bulk_message_delete([msg(2, "visible"), msg(3, "private")])
        args = out.send.await_args.kwargs
        assert "private" not in args["file"].fp.getvalue().decode()
        assert args["allowed_mentions"].everyone is False
    asyncio.run(run())


def test_send_failure_is_reported_without_exception_text(caplog):
    async def run():
        c = channel(1, "logs")
        c.send.side_effect = asyncio.TimeoutError("secret")
        cog, _ = cog_with({})
        assert not await cog.post(c, discord.Embed())
        assert "channel 1" in caplog.text and "secret" not in caplog.text
    asyncio.run(run())


def test_set_saves_approved_routes_and_rejects_vanguard_channels(monkeypatch):
    from core.utils import UserError
    from discord.ext import commands
    import pytest

    async def run():
        c = channel(1, "some-channel")
        vanguard = channel(2, "bans")
        g = guild_with(c, vanguard)
        cog, store = cog_with({})
        ctx = SimpleNamespace(guild=g, neutral=AsyncMock(), approve=AsyncMock())
        monkeypatch.setattr(commands.TextChannelConverter, "convert", AsyncMock(return_value=vanguard))
        with pytest.raises(UserError, match="Another bot"):
            await Logging.logs_set.callback(cog, ctx, "messages", "#bans")
        assert not store["logs"]
        monkeypatch.setattr(commands.TextChannelConverter, "convert", AsyncMock(return_value=c))
        await Logging.logs_set.callback(cog, ctx, "messages", "#some-channel")
        assert store["logs"]["approved_routes"]["messages"] == 1
        assert _channel_for(g, store["logs"], "messages") is c
    asyncio.run(run())


def test_dashboard_combined_reapproves_override_route_when_confirmed():
    from test_dashboard_security import (GUILD_ID, BOT_OWNER, combined_revision, cookie, csrf_of, login_server,
                                         make_bot, run)
    bot = make_bot()
    g = bot.guilds[0]
    c = g.get_channel(44)
    c.id, c.name, c.mention, c.guild = 44, "bans", "<#44>", g
    c.permissions_for.return_value = SimpleNamespace(view_channel=True, send_messages=True, embed_links=True)
    g.me = object()
    bot.db.store[(GUILD_ID, "logs")] = {"commands": 44}
    server = login_server(bot)
    token, headers = cookie(server, BOT_OWNER)

    async def scenario(client):
        rev = await combined_revision(client, headers)
        payload = {"csrf": csrf_of(server, token), "revision": rev, "log_commands": "44", "confirm_logs": "yes"}
        r = await client.post(f"/api/guilds/{GUILD_ID}/config", headers=headers, data=payload, allow_redirects=False)
        assert r.status == 302
        assert bot.db.store[(GUILD_ID, "logs")]["approved_routes"] == {"commands": 44}
    run(server, scenario)


def test_feature_dashboard_blocks_vanguard_unless_explicitly_confirmed():
    from core.dashboard import LOG_FIELDS
    from test_dashboard_security import (GUILD_ID, BOT_OWNER, _feature_revision, cookie, csrf_of, login_server,
                                         make_bot, run)
    bot = make_bot()
    g = bot.guilds[0]
    c = channel(88, "bans")
    c.position, c.guild, c.category = 1, g, SimpleNamespace(name="Ghost Vanguard")
    g.me, g.text_channels, g.get_channel = object(), [c], lambda cid: c if cid == 88 else None
    server = login_server(bot)
    token, headers = cookie(server, BOT_OWNER)
    path = f"/servers/{GUILD_ID}"

    async def scenario(client):
        page = await client.get(f"{path}/features?section=logging", headers=headers)
        html = await page.text()
        assert "#bans" not in html
        revision = _feature_revision(html, GUILD_ID, "logging")
        data = {field: "" for field, _ in LOG_FIELDS}
        data.update(csrf=csrf_of(server, token), revision=revision, log_messages="88", log_ignore_channels="",
                    log_ignore_users="")
        r = await client.post(f"{path}/features/logging", headers=headers, data=data)
        assert r.status == 400 and bot.db.store.get((GUILD_ID, "logs"), {}) == {}
        data["confirm_logs"] = "yes"
        r = await client.post(f"{path}/features/logging", headers=headers, data=data, allow_redirects=False)
        assert r.status == 302
        assert bot.db.store[(GUILD_ID, "logs")]["approved_routes"]["messages"] == 88
    run(server, scenario)


def test_ambiguous_audit_entries_do_not_attribute(monkeypatch):
    from core import logembeds as fmt
    async def run():
        monkeypatch.setattr(fmt, "AUDIT_SETTLE", 0)
        actor = SimpleNamespace(id=4)
        now = discord.utils.utcnow()
        entries = [SimpleNamespace(target=SimpleNamespace(id=2), created_at=now, user=actor, reason=None)
                   for _ in range(2)]
        async def audit_logs(**kwargs):
            for entry in entries:
                yield entry
        g = SimpleNamespace(me=SimpleNamespace(id=9, guild_permissions=SimpleNamespace(view_audit_log=True)),
                            audit_logs=audit_logs)
        assert await fmt.audit_actor(g, discord.AuditLogAction.kick, 2) == (None, None)
    asyncio.run(run())
