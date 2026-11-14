"""Security tests for the web dashboard: login, per-server access, CSRF, escaping and bind address."""
import asyncio
import json
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from aiohttp.test_utils import TestClient, TestServer

from core.config import Settings
from core.dashboard import SESSION_COOKIE, STATE_COOKIE, DashboardServer

BOT_OWNER = 1
GUILD_OWNER = 2
STRANGER = 3
GUILD_ID = 555
OTHER_GUILD_ID = 777


class FakeDB:
    """Mimics core.db get_config: caches the first object returned, filling defaults only if nothing is stored."""

    def __init__(self):
        self.store: dict[tuple[int, str], dict] = {}
        self.writes: list[tuple[int, str]] = []

    async def get_config(self, gid, module, default=None):
        key = (gid, module)
        if key not in self.store:
            self.store[key] = json.loads(json.dumps(default)) if default is not None else {}
        return self.store[key]

    async def set_config_committed(self, gid, module, data):
        await self.set_config(gid, module, data)

    async def set_config(self, gid, module, data):
        self.store[(gid, module)] = data
        self.writes.append((gid, module))


def make_bot(guild_name="Test Server"):
    guilds = [
        SimpleNamespace(id=GUILD_ID, name=guild_name, owner_id=GUILD_OWNER, member_count=10, members=[]),
        SimpleNamespace(id=OTHER_GUILD_ID, name="Other", owner_id=999, member_count=5, members=[]),
    ]
    channels = {cid: MagicMock(spec=discord.TextChannel) for cid in (44, 66)}
    guilds[0].get_channel = lambda cid: channels.get(cid)
    guilds[0].text_channels = []
    return SimpleNamespace(
        guilds=guilds,
        get_guild=lambda gid: next((g for g in guilds if g.id == gid), None),
        is_owner=AsyncMock(side_effect=lambda uid: uid == BOT_OWNER),
        is_authorized=lambda gid: True,
        db=FakeDB(),
        latency=0.05,
        started_at=0.0,
        session=None,
    )


def login_server(bot):
    return DashboardServer(bot, port=0, public_url="https://veil.example.com",
                           client_id=123, client_secret="shh", secret="x" * 40)


def run(server, scenario):
    async def go():
        client = TestClient(TestServer(server.app))
        await client.start_server()
        try:
            await scenario(client)
        finally:
            await client.close()
    asyncio.run(go())


def cookie(server, uid, name="user"):
    token = server.make_session(uid, name)
    return token, {"Cookie": f"{SESSION_COOKIE}={token}"}


def csrf_of(server, token):
    return server._unsign(token, "session")["csrf"]


# ------------------------------------------------------------------ settings and binding

def test_dashboard_off_by_default():
    s = Settings.from_env({})
    assert s.dashboard_enabled is False
    assert s.dashboard_login_configured is False


def test_without_login_it_only_binds_localhost():
    bot = make_bot()
    assert DashboardServer(bot).host == "127.0.0.1"
    assert DashboardServer(bot, host="0.0.0.0").host == "127.0.0.1"
    assert login_server(bot).host == "127.0.0.1"
    assert DashboardServer(bot, host="0.0.0.0", public_url="https://veil.example.com",
                           client_id=1, client_secret="s").host == "0.0.0.0"


# ------------------------------------------------------------------ login required

def test_anonymous_visitor_cannot_read_or_change_anything():
    bot = make_bot()
    server = login_server(bot)

    async def scenario(client):
        r = await client.get("/")
        assert "Log in with Discord" in await r.text()
        r = await client.get("/api/guilds")
        assert r.status == 401
        r = await client.get(f"/api/guilds/{GUILD_ID}/config")
        assert r.status == 401
        r = await client.post(f"/api/guilds/{GUILD_ID}/config", data={"an_admins": "12345"})
        assert r.status == 401
        r = await client.get(f"/servers/{GUILD_ID}", allow_redirects=False)
        assert r.status == 302 and r.headers["Location"] == "/login"

    run(server, scenario)
    assert bot.db.writes == []


def test_tampered_or_forged_cookie_is_rejected():
    bot = make_bot()
    server = login_server(bot)
    token, _ = cookie(server, GUILD_OWNER)
    body, mac = token.rsplit(".", 1)
    forged = DashboardServer(bot, public_url="https://veil.example.com", client_id=1, client_secret="s",
                             secret="y" * 40).make_session(BOT_OWNER, "evil")

    async def scenario(client):
        for bad in (f"{body}.{'0' * len(mac)}", forged, "garbage"):
            r = await client.get("/api/guilds", headers={"Cookie": f"{SESSION_COOKIE}={bad}"})
            assert r.status == 401

    run(server, scenario)


# ------------------------------------------------------------------ per-server access

def test_non_owner_cannot_see_or_edit_a_server():
    bot = make_bot()
    server = login_server(bot)
    token, headers = cookie(server, STRANGER)

    async def scenario(client):
        r = await client.get("/api/guilds", headers=headers)
        assert r.status == 403
        r = await client.get(f"/servers/{GUILD_ID}", headers=headers)
        assert r.status == 403
        r = await client.get(f"/api/guilds/{GUILD_ID}/config", headers=headers)
        assert r.status == 403
        r = await client.post(f"/api/guilds/{GUILD_ID}/config", headers=headers,
                              data={"csrf": csrf_of(server, token), "an_admins": str(STRANGER)})
        assert r.status == 403

    run(server, scenario)
    assert bot.db.writes == []


def test_server_owner_sees_only_their_server_and_bot_owner_sees_all():
    bot = make_bot()
    server = login_server(bot)
    _, owner_headers = cookie(server, GUILD_OWNER)
    _, dev_headers = cookie(server, BOT_OWNER)

    async def scenario(client):
        r = await client.get("/api/guilds", headers=owner_headers)
        assert [g["id"] for g in await r.json()] == [str(GUILD_ID)]
        r = await client.get(f"/servers/{OTHER_GUILD_ID}", headers=owner_headers)
        assert r.status == 404
        r = await client.get("/api/guilds", headers=dev_headers)
        assert {g["id"] for g in await r.json()} == {str(GUILD_ID), str(OTHER_GUILD_ID)}

    run(server, scenario)


# ------------------------------------------------------------------ CSRF / cross-site

def test_post_needs_csrf_token_and_same_origin():
    bot = make_bot()
    server = login_server(bot)
    token, headers = cookie(server, GUILD_OWNER)
    url = f"/api/guilds/{GUILD_ID}/config"

    async def scenario(client):
        r = await client.post(url, headers=headers, data={"an_admins": "42"})
        assert r.status == 403
        r = await client.post(url, headers=headers, data={"csrf": "wrong", "an_admins": "42"})
        assert r.status == 403
        r = await client.post(url, headers={**headers, "Origin": "https://evil.example"},
                              data={"csrf": csrf_of(server, token), "an_admins": "42"})
        assert r.status == 403

    run(server, scenario)
    assert bot.db.writes == []


def test_owner_save_writes_the_keys_the_bot_actually_reads():
    bot = make_bot()
    server = login_server(bot)
    token, headers = cookie(server, GUILD_OWNER)

    async def scenario(client):
        r = await client.post(
            f"/api/guilds/{GUILD_ID}/config", headers=headers, allow_redirects=False,
            data={"csrf": csrf_of(server, token), "rbl_roles": "11, 22", "rbl_authorized": "33",
                  "log_commands": "44", "log_bans": "", "log_joins": "66",
                  "an_punishment": "kick", "an_admins": "77, 77, 88"},
        )
        assert r.status == 302 and r.headers["Location"] == f"/servers/{GUILD_ID}?saved=1"

    run(server, scenario)
    store = bot.db.store
    assert store[(GUILD_ID, "roleblacklist")] == {"roles": [11, 22], "authorized": [33]}
    assert store[(GUILD_ID, "logs")] == {"commands": 44, "bans": None, "joinleave": 66}
    an = store[(GUILD_ID, "antinuke")]
    assert an["punishment"] == "kick" and an["admins"] == [77, 88]
    assert "action" not in an
    assert an["whitelist"] == [] and an["modules"] == {}  # antinuke defaults kept


def test_invalid_punishment_is_ignored():
    bot = make_bot()
    server = login_server(bot)
    token, headers = cookie(server, GUILD_OWNER)

    async def scenario(client):
        await client.post(f"/api/guilds/{GUILD_ID}/config", headers=headers, allow_redirects=False,
                          data={"csrf": csrf_of(server, token), "an_punishment": "nuke"})

    run(server, scenario)
    assert bot.db.store[(GUILD_ID, "antinuke")]["punishment"] == "ban"


def test_viewing_a_server_caches_full_antinuke_defaults():
    bot = make_bot()
    server = login_server(bot)
    _, headers = cookie(server, GUILD_OWNER)

    async def scenario(client):
        r = await client.get(f"/servers/{GUILD_ID}", headers=headers)
        assert r.status == 200

    run(server, scenario)
    cached = bot.db.store[(GUILD_ID, "antinuke")]
    assert "whitelist" in cached and "modules" in cached


# ------------------------------------------------------------------ escaping and headers

def test_server_name_is_escaped_and_security_headers_are_set():
    bot = make_bot(guild_name='<script>alert(1)</script>"')
    server = login_server(bot)
    _, headers = cookie(server, GUILD_OWNER)

    async def scenario(client):
        for path in ("/", f"/servers/{GUILD_ID}"):
            r = await client.get(path, headers=headers)
            text = await r.text()
            assert "<script>alert(1)" not in text
            assert "&lt;script&gt;alert(1)&lt;/script&gt;&quot;" in text
            assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
            assert r.headers["X-Frame-Options"] == "DENY"

    run(server, scenario)


# ------------------------------------------------------------------ Discord login flow

def test_oauth_callback_checks_state_and_signs_in():
    bot = make_bot()
    server = login_server(bot)
    server._fetch_discord_user = AsyncMock(return_value={"id": str(GUILD_OWNER), "username": "owner"})

    async def scenario(client):
        r = await client.get("/login", allow_redirects=False)
        assert r.status == 302
        assert r.headers["Location"].startswith("https://discord.com/oauth2/authorize?")
        assert "scope=identify" in r.headers["Location"]
        state_cookie = r.cookies[STATE_COOKIE].value
        state = server._unsign(state_cookie, "state")["s"]

        r = await client.get(f"/callback?code=abc&state=not-{state}", allow_redirects=False,
                             headers={"Cookie": f"{STATE_COOKIE}={state_cookie}"})
        assert r.status == 400
        r = await client.get(f"/callback?code=abc&state={state}", allow_redirects=False)
        assert r.status == 400  # no state cookie

        r = await client.get(f"/callback?code=abc&state={state}", allow_redirects=False,
                             headers={"Cookie": f"{STATE_COOKIE}={state_cookie}"})
        assert r.status == 302
        session = server._unsign(r.cookies[SESSION_COOKIE].value, "session")
        assert session["uid"] == GUILD_OWNER and session["local"] is False

    run(server, scenario)
    server._fetch_discord_user.assert_awaited_once_with("abc")


def test_state_cookie_cannot_be_used_as_a_session():
    bot = make_bot()
    server = login_server(bot)
    state_token = server._sign({"t": "state", "s": "x", "uid": BOT_OWNER, "exp": 9_999_999_999})

    async def scenario(client):
        r = await client.get("/api/guilds", headers={"Cookie": f"{SESSION_COOKIE}={state_token}"})
        assert r.status == 401

    run(server, scenario)


# ------------------------------------------------------------------ localhost-only mode

def test_local_mode_rejects_other_host_headers():
    bot = make_bot()
    server = DashboardServer(bot, port=0)

    async def scenario(client):
        r = await client.get("/", headers={"Host": "evil.example"})
        assert r.status == 421
        r = await client.get("/api/guilds")
        assert r.status == 200 and len(await r.json()) == 2

    run(server, scenario)


def test_local_mode_still_needs_csrf_for_changes():
    bot = make_bot()
    server = DashboardServer(bot, port=0)

    async def scenario(client):
        r = await client.post(f"/api/guilds/{GUILD_ID}/config", data={"an_admins": "42"})
        assert r.status == 403

    run(server, scenario)
    assert bot.db.writes == []


def test_login_mode_session_is_not_valid_in_local_mode():
    bot = make_bot()
    local = DashboardServer(bot, port=0, secret="x" * 40)
    remote_token = login_server(bot).make_session(STRANGER, "x")  # same secret, other mode

    async def scenario(client):
        r = await client.post(f"/api/guilds/{GUILD_ID}/config",
                              headers={"Cookie": f"{SESSION_COOKIE}={remote_token}"},
                              data={"csrf": local._unsign(remote_token, "session")["csrf"], "an_admins": "42"})
        assert r.status == 403  # the cookie is replaced by a fresh local session, so its CSRF token doesn't match

    run(local, scenario)
    assert bot.db.writes == []


def test_server_overview_filters_sorting_and_owner_privacy():
    bot = make_bot()
    bot.db.store[(GUILD_ID, "antinuke")] = {"enabled": True}
    bot.db.store[(GUILD_ID, "logs")] = {"commands": 123, "bans": -1, "ignore": {"users": [10]}}
    server = login_server(bot)
    _, dev_headers = cookie(server, BOT_OWNER)
    _, owner_headers = cookie(server, GUILD_OWNER)

    async def scenario(client):
        r = await client.get("/?sort=members", headers=dev_headers)
        text = await r.text()
        assert text.index("Test Server</a>") < text.index("Other</a>")
        assert "1 configured" in text and "2 of 2 servers shown" in text
        r = await client.get("/?antinuke=on", headers=dev_headers)
        text = await r.text()
        assert "Test Server</a>" in text and "Other</a>" not in text
        r = await client.get("/?q=777", headers=owner_headers)
        text = await r.text()
        assert "Other</a>" not in text and "0 of 1 servers shown" in text
        r = await client.get("/?q=%3Cscript%3E", headers=dev_headers)
        text = await r.text()
        assert 'value="&lt;script&gt;"' in text and '<script>' not in text

    run(server, scenario)


def test_configured_developers_can_access_all_servers(monkeypatch):
    from core import perms
    monkeypatch.setattr(perms, "BOT_DEVELOPERS", [STRANGER])
    bot = make_bot()
    server = login_server(bot)
    _, headers = cookie(server, STRANGER)

    async def scenario(client):
        r = await client.get("/api/guilds", headers=headers)
        assert {g["id"] for g in await r.json()} == {str(GUILD_ID), str(OTHER_GUILD_ID)}
        assert (await client.get(f"/servers/{OTHER_GUILD_ID}", headers=headers)).status == 200

    run(server, scenario)


def test_invalid_ids_never_partially_save_security_settings():
    bot = make_bot()
    server = login_server(bot)
    token, headers = cookie(server, GUILD_OWNER)
    bot.db.store[(GUILD_ID, "roleblacklist")] = {"roles": [11], "authorized": [22]}

    async def scenario(client):
        for field, value in (("rbl_roles", "33, invalid"), ("an_admins", "nope"),
                             ("log_commands", "invalid"), ("rbl_roles", ",".join(map(str, range(1, 102))))):
            r = await client.post(f"/api/guilds/{GUILD_ID}/config", headers=headers,
                                  data={"csrf": csrf_of(server, token), "rbl_roles": "33", field: value})
            assert r.status == 400 and "Nothing was saved" in await r.text()

    run(server, scenario)
    assert bot.db.store[(GUILD_ID, "roleblacklist")] == {"roles": [11], "authorized": [22]}
    assert bot.db.writes == []


def test_dashboard_preserves_logging_off_state():
    bot = make_bot()
    server = login_server(bot)
    token, headers = cookie(server, GUILD_OWNER)

    async def scenario(client):
        r = await client.post(f"/api/guilds/{GUILD_ID}/config", headers=headers, allow_redirects=False,
                              data={"csrf": csrf_of(server, token), "log_commands": "-1"})
        assert r.status == 302

    run(server, scenario)
    assert bot.db.store[(GUILD_ID, "logs")]["commands"] == -1


def test_overview_filters_authorization_and_explicit_log_channels():
    bot = make_bot()
    bot.is_authorized = lambda gid: gid == GUILD_ID
    bot.db.store[(GUILD_ID, "logs")] = {"commands": 42, "bans": -1, "ignore": {"users": [1]}}
    server = login_server(bot)
    _, headers = cookie(server, BOT_OWNER)

    async def scenario(client):
        r = await client.get("/?authorized=yes&logs=configured", headers=headers)
        text = await r.text()
        assert "Test Server</a>" in text and "Other</a>" not in text
        assert "1 configured" in text
        r = await client.get("/?authorized=no&logs=none", headers=headers)
        text = await r.text()
        assert "Other</a>" in text and "Test Server</a>" not in text
        r = await client.get("/?authorized=no", headers=cookie(server, GUILD_OWNER)[1])
        assert "Other</a>" not in await r.text()

    run(server, scenario)


def test_dashboard_rejects_hard_role_in_soft_blacklist_without_writes():
    bot = make_bot()
    server = login_server(bot)
    token, headers = cookie(server, GUILD_OWNER)
    original = {"roles": [11], "hard_roles": [22], "soft_bypass": {"users": [33]}}
    bot.db.store[(GUILD_ID, "roleblacklist")] = original.copy()

    async def scenario(client):
        response = await client.post(f"/api/guilds/{GUILD_ID}/config", headers=headers,
                                     data={"csrf": csrf_of(server, token), "rbl_roles": "11,22"})
        assert response.status == 400 and "Nothing was saved" in await response.text()

    run(server, scenario)
    assert bot.db.store[(GUILD_ID, "roleblacklist")] == original
    assert bot.db.writes == []


def test_local_dashboard_cannot_read_or_write_role_blacklist_even_with_valid_csrf():
    bot = make_bot()
    bot.db.store[(GUILD_ID, "roleblacklist")] = {"roles": [123456], "authorized": [654321]}
    server = DashboardServer(bot, port=0)

    async def scenario(client):
        page = await client.get(f"/servers/{GUILD_ID}")
        body = await page.text()
        assert page.status == 200 and "123456" not in body and "rbl_roles" not in body
        api = await client.get(f"/api/guilds/{GUILD_ID}/config")
        assert "roleblacklist" not in await api.json()
        session = server._unsign(client.session.cookie_jar.filter_cookies(client.make_url('/'))[SESSION_COOKIE].value, "session")
        response = await client.post(f"/api/guilds/{GUILD_ID}/config",
                                     data={"csrf": session["csrf"], "rbl_roles": "44"})
        assert response.status == 403
    run(server, scenario)
    assert bot.db.store[(GUILD_ID, "roleblacklist")] == {"roles": [123456], "authorized": [654321]}
    assert bot.db.writes == []


def test_local_unrelated_settings_save_does_not_write_role_blacklist():
    bot = make_bot()
    server = DashboardServer(bot, port=0)

    async def scenario(client):
        await client.get("/")
        token = client.session.cookie_jar.filter_cookies(client.make_url('/'))[SESSION_COOKIE].value
        csrf = server._unsign(token, "session")["csrf"]
        response = await client.post(f"/api/guilds/{GUILD_ID}/config", data={"csrf": csrf, "an_admins": "42"}, allow_redirects=False)
        assert response.status == 302
    run(server, scenario)
    assert (GUILD_ID, "roleblacklist") not in bot.db.writes


# ------------------------------------------------------------------ authenticated-only dashboard & authorization

def test_listener_refuses_unauthenticated_and_insecure_modes():
    async def go():
        bot = make_bot()
        for server in (DashboardServer(bot),
                       DashboardServer(bot, public_url="http://example.com", client_id=1, client_secret="s")):
            with pytest.raises(RuntimeError, match="requires Discord login"):
                await server.start()
            assert server.runner is None
    asyncio.run(go())


def test_stranger_is_denied_login_and_owner_loses_access_when_ownership_changes():
    bot = make_bot()
    server = login_server(bot)
    server._fetch_discord_user = AsyncMock(return_value={"id": str(STRANGER), "username": "stranger"})
    _, owner_headers = cookie(server, GUILD_OWNER)

    async def scenario(client):
        r = await client.get("/login", allow_redirects=False)
        state_cookie = r.cookies[STATE_COOKIE].value
        state = server._unsign(state_cookie, "state")["s"]
        r = await client.get(f"/callback?code=abc&state={state}",
                             headers={"Cookie": f"{STATE_COOKIE}={state_cookie}"})
        assert r.status == 403 and SESSION_COOKIE not in r.cookies
        assert (await client.get("/api/guilds", headers=owner_headers)).status == 200
        bot.guilds[0].owner_id = 10000
        assert (await client.get("/api/guilds", headers=owner_headers)).status == 403

    run(server, scenario)


def test_authorization_is_developer_only_csrf_protected_and_requires_confirmation():
    bot = make_bot('<script>server</script>')
    bot.authorized = {GUILD_ID, 9999}
    bot.is_authorized = lambda gid: gid in bot.authorized
    bot.guilds[0].leave = AsyncMock()
    calls = []

    class Auth:
        def __init__(self):
            self._auth_lock = asyncio.Lock()

        async def authorize(self, gid, by, note):
            calls.append(("add", gid, by, note))
            added = gid not in bot.authorized
            bot.authorized.add(gid)
            return added

        async def unauthorize(self, gid):
            calls.append(("remove", gid))
            if gid not in bot.authorized:
                return False
            bot.authorized.remove(gid)
            return True

    auth = Auth()
    bot.get_cog = lambda name: auth if name == "Authorization" else None
    server = login_server(bot)
    dev_token, dev_headers = cookie(server, BOT_OWNER)
    owner_token, owner_headers = cookie(server, GUILD_OWNER)
    base = {"csrf": csrf_of(server, dev_token), "guild_id": str(GUILD_ID)}

    async def scenario(client):
        assert (await client.get("/authorization", headers=owner_headers)).status == 403
        assert (await client.post("/authorization", headers=owner_headers,
                                  data={"csrf": csrf_of(server, owner_token), "action": "authorize",
                                        "guild_id": "12345"})).status == 403
        page = await client.get("/authorization", headers=dev_headers)
        text = await page.text()
        assert "&lt;script&gt;server&lt;/script&gt;" in text and "Not joined" in text
        assert (await client.post("/authorization", headers=dev_headers,
                                  data={**base, "csrf": "wrong", "action": "authorize"})).status == 403
        assert (await client.post("/authorization", headers={**dev_headers, "Origin": "https://evil.example"},
                                  data={**base, "action": "authorize"})).status == 403
        assert (await client.post("/authorization", headers=dev_headers,
                                  data={**base, "action": "revoke"})).status == 403
        assert not calls
        r = await client.post("/authorization", headers=dev_headers,
                              data={"csrf": base["csrf"], "action": "authorize", "guild_id": "12345"})
        assert r.status == 200 and 12345 in bot.authorized
        assert calls[-1] == ("add", 12345, BOT_OWNER, "via dashboard")
        r = await client.post("/authorization", headers=dev_headers, data={**base, "action": "review"})
        text = await r.text()
        confirmation = re.search(r'name="confirmation" value="([^"]+)"', text).group(1)
        assert GUILD_ID in bot.authorized and not bot.guilds[0].leave.await_count
        r = await client.post("/authorization", headers=dev_headers,
                              data={**base, "action": "revoke", "confirmation": confirmation})
        assert r.status == 200 and "bot left" in (await r.text()).lower()
        assert GUILD_ID not in bot.authorized
        bot.guilds[0].leave.assert_awaited_once()
        bot.authorized.add(GUILD_ID)
        r = await client.post("/authorization", headers=dev_headers,
                              data={**base, "action": "revoke", "confirmation": confirmation})
        assert r.status == 403 and GUILD_ID in bot.authorized

    run(server, scenario)


def test_log_selection_rejects_channels_not_in_guild_without_writes():
    bot = make_bot()
    server = login_server(bot)
    token, headers = cookie(server, GUILD_OWNER)

    async def scenario(client):
        page = await client.get(f"/servers/{GUILD_ID}", headers=headers)
        text = await page.text()
        for field in ("log_roles", "log_messages", "log_timeouts", "log_antinuke", "log_server"):
            assert f'name="{field}"' in text
        r = await client.post(f"/api/guilds/{GUILD_ID}/config", headers=headers,
                              data={"csrf": csrf_of(server, token), "log_roles": "999", "rbl_roles": "42"})
        assert r.status == 400 and "Nothing was saved" in await r.text()
        r = await client.post(f"/api/guilds/{GUILD_ID}/config", headers=headers, allow_redirects=False,
                              data={"csrf": csrf_of(server, token), "log_roles": "44", "log_timeouts": "-1"})
        assert r.status == 302

    run(server, scenario)
    assert bot.db.store.get((GUILD_ID, "roleblacklist"), {}) == {}
    assert bot.db.store[(GUILD_ID, "logs")] == {"roles": 44, "timeouts": -1}


def test_revocation_still_blocks_owner_if_leave_fails_and_reports_failure():
    bot = make_bot()
    bot.authorized = {GUILD_ID}
    bot.is_authorized = lambda gid: gid in bot.authorized
    bot.guilds[0].leave = AsyncMock(side_effect=RuntimeError("Discord unavailable"))

    class Auth:
        def __init__(self):
            self._auth_lock = asyncio.Lock()

        async def unauthorize(self, gid):
            bot.authorized.remove(gid)
            return True

    auth = Auth()
    bot.get_cog = lambda name: auth
    server = login_server(bot)
    token, headers = cookie(server, BOT_OWNER)
    _, owner_headers = cookie(server, GUILD_OWNER)

    async def scenario(client):
        assert (await client.get("/api/guilds", headers=owner_headers)).status == 200
        data = {"csrf": csrf_of(server, token), "guild_id": str(GUILD_ID)}
        r = await client.post("/authorization", headers=headers, data={**data, "action": "review"})
        confirmation = re.search(r'name="confirmation" value="([^"]+)"', await r.text()).group(1)
        r = await client.post("/authorization", headers=headers,
                              data={**data, "action": "revoke", "confirmation": confirmation})
        assert r.status == 200 and "leave failed or timed out" in (await r.text()).lower()
        assert (await client.get("/api/guilds", headers=owner_headers)).status == 403
        assert (await client.get(f"/servers/{GUILD_ID}", headers=headers)).status == 200

    run(server, scenario)


def test_revoking_explicit_entry_keeps_automatically_authorized_server():
    bot = make_bot()
    bot.authorized = {GUILD_ID}
    bot.is_authorized = lambda gid: True
    bot.guilds[0].leave = AsyncMock()

    class Auth:
        def __init__(self):
            self._auth_lock = asyncio.Lock()

        async def unauthorize(self, gid):
            bot.authorized.remove(gid)
            return True

    bot.get_cog = lambda name: Auth()
    server = login_server(bot)
    token, headers = cookie(server, BOT_OWNER)

    async def scenario(client):
        data = {"csrf": csrf_of(server, token), "guild_id": str(GUILD_ID)}
        r = await client.post("/authorization", headers=headers, data={**data, "action": "review"})
        confirmation = re.search(r'name="confirmation" value="([^"]+)"', await r.text()).group(1)
        r = await client.post("/authorization", headers=headers,
                              data={**data, "action": "revoke", "confirmation": confirmation})
        assert r.status == 200 and "automatically authorized" in await r.text()
        bot.guilds[0].leave.assert_not_awaited()

    run(server, scenario)


def test_dashboard_authorization_updates_real_cog_and_database():
    from cogs.authorization import Authorization
    from core.db import Database

    async def go():
        bot = make_bot()
        db = Database(":memory:")
        await db.connect()
        bot.db = db
        bot.authorized = set()
        bot.is_authorized = lambda gid: gid in bot.authorized
        cog = Authorization(bot)
        bot.get_cog = lambda name: cog if name == "Authorization" else None
        server = login_server(bot)
        token, headers = cookie(server, BOT_OWNER)
        client = TestClient(TestServer(server.app))
        await client.start_server()
        try:
            data = {"csrf": csrf_of(server, token), "guild_id": "12345"}
            r = await client.post("/authorization", headers=headers, data={**data, "action": "authorize"})
            assert r.status == 200 and bot.authorized == {12345}
            row = await db.fetchone("SELECT * FROM authorized WHERE guild_id=?", 12345)
            assert row["authorized_by"] == BOT_OWNER and row["note"] == "via dashboard"
            r = await client.post("/authorization", headers=headers, data={**data, "action": "review"})
            confirmation = re.search(r'name="confirmation" value="([^"]+)"', await r.text()).group(1)
            r = await client.post("/authorization", headers=headers,
                                  data={**data, "action": "revoke", "confirmation": confirmation})
            assert r.status == 200 and bot.authorized == set()
            assert await db.fetchone("SELECT * FROM authorized WHERE guild_id=?", 12345) is None
        finally:
            await client.close()
            await db.close()

    asyncio.run(go())


def test_dashboard_extended_settings_save_and_preserve_other_configuration():
    bot = make_bot()
    bot.guilds[0].get_role = lambda role_id: SimpleNamespace(id=role_id) if role_id == 44 else None
    notice = AsyncMock()
    bot.get_cog = lambda name: SimpleNamespace(security_notice=notice) if name == "Antinuke" else None
    bot.db.store[(GUILD_ID, "logs")] = {"ignore": {"users": [5], "channels": [6], "other": [7]}, "commands": 44}
    bot.db.store[(GUILD_ID, "antinuke")] = {"enabled": True, "modules": {"ban": {"on": True}},
                                             "whitelist": [8], "whitelist_roles": [9]}
    server = login_server(bot)
    token, headers = cookie(server, GUILD_OWNER)

    async def scenario(client):
        page = await client.get(f"/servers/{GUILD_ID}", headers=headers)
        html = await page.text()
        assert 'name="an_whitelist" value="8"' in html
        assert 'name="log_ignore_users" value="5"' in html
        response = await client.post(f"/api/guilds/{GUILD_ID}/config", headers=headers, allow_redirects=False,
                                     data={"csrf": csrf_of(server, token), "an_whitelist": "11, 11, 12",
                                           "an_whitelist_roles": "44", "log_ignore_users": "22",
                                           "log_ignore_channels": "33", "rbl_reply_mode": "dm"})
        assert response.status == 302

    run(server, scenario)
    logs = bot.db.store[(GUILD_ID, "logs")]
    assert logs == {"ignore": {"users": [22], "channels": [33], "other": [7]}, "commands": 44, "rbl_dm": True}
    an = bot.db.store[(GUILD_ID, "antinuke")]
    assert an["whitelist"] == [11, 12] and an["whitelist_roles"] == [44]
    assert an["enabled"] is True and an["modules"] == {"ban": {"on": True}}
    notice.assert_awaited_once()
    assert notice.call_args.args[1].id == GUILD_OWNER


def test_dashboard_extended_settings_validate_before_writes_and_forbid_everyone():
    bot = make_bot()
    server = login_server(bot)
    token, headers = cookie(server, GUILD_OWNER)

    async def scenario(client):
        for field, value in (("an_whitelist", "bad"), ("log_ignore_users", "12, nope"),
                             ("an_whitelist_roles", str(GUILD_ID)), ("an_whitelist_roles", "999"),
                             ("rbl_reply_mode", "invalid")):
            response = await client.post(f"/api/guilds/{GUILD_ID}/config", headers=headers,
                                         data={"csrf": csrf_of(server, token), "rbl_roles": "11", field: value})
            assert response.status == 400
            assert "Nothing was saved" in await response.text()
        assert bot.db.writes == []
        response = await client.post(f"/api/guilds/{GUILD_ID}/config", headers=headers,
                                     data={"csrf": "wrong", "log_ignore_users": "22", "an_whitelist": "11"})
        assert response.status == 403 and bot.db.writes == []
        # An authenticated owner cannot change another owner's server even with valid CSRF.
        response = await client.post(f"/api/guilds/{OTHER_GUILD_ID}/config", headers=headers,
                                     data={"csrf": csrf_of(server, token), "an_whitelist": "11"})
        assert response.status == 404
        assert bot.db.writes == []

    run(server, scenario)


def test_form_referrer_policy_allows_browser_origin_without_weakening_csrf():
    bot = make_bot()
    bot.authorized = {GUILD_ID}
    bot.get_cog = lambda name: SimpleNamespace() if name == "Authorization" else None
    server = login_server(bot)
    token, headers = cookie(server, BOT_OWNER)
    csrf = csrf_of(server, token)
    same_origin = {**headers, "Origin": "https://veil.example.com", "Sec-Fetch-Site": "same-origin"}

    async def scenario(client):
        for path in ("/", f"/servers/{GUILD_ID}", "/authorization"):
            response = await client.get(path, headers=headers)
            assert response.status == 200
            assert response.headers["Referrer-Policy"] == "same-origin"
        callback = await client.get("/callback?code=private-code&state=invalid", headers=headers)
        assert callback.headers["Referrer-Policy"] == "no-referrer"
        redirect = await client.get("/login", allow_redirects=False)
        assert redirect.headers["Referrer-Policy"] == "no-referrer"

        form = {"csrf": csrf, "an_admins": "42"}
        url = f"/api/guilds/{GUILD_ID}/config"
        for origin, fetch_site in (("null", "same-origin"), ("https://evil.example", "cross-site"),
                                   ("https://veil.example.com", "cross-site")):
            response = await client.post(url, headers={**headers, "Origin": origin, "Sec-Fetch-Site": fetch_site},
                                         data=form, allow_redirects=False)
            assert response.status == 403
        assert bot.db.writes == []
        response = await client.post(url, headers=same_origin, data={**form, "csrf": "wrong"})
        assert response.status == 403 and bot.db.writes == []
        response = await client.post(url, headers=same_origin, data=form, allow_redirects=False)
        assert response.status == 302
        assert bot.db.store[(GUILD_ID, "antinuke")]["admins"] == [42]
        response = await client.post("/authorization", headers=same_origin,
                                     data={"csrf": csrf, "guild_id": str(GUILD_ID), "action": "review"})
        assert response.status == 200 and "Confirm revocation" in await response.text()
        assert response.headers["Referrer-Policy"] == "same-origin"
        response = await client.post("/logout", headers=same_origin, data={"csrf": csrf}, allow_redirects=False)
        assert response.status == 302

    run(server, scenario)


def test_dashboard_layout_preserves_accessibility_and_server_form_controls():
    bot = make_bot(guild_name='<script>alert(1)</script>"')
    server = login_server(bot)
    _, headers = cookie(server, GUILD_OWNER)

    async def scenario(client):
        response = await client.get("/", headers=headers)
        html = await response.text()
        assert '<a class="skip-link" href="#main">Skip to content</a>' in html
        assert '<main id="main">' in html and "Server overview" in html
        assert '<span class="server-initial" aria-hidden="true">&lt;</span>' in html
        assert "<script>alert(1)" not in html
        response = await client.get(f"/servers/{GUILD_ID}", headers=headers)
        html = await response.text()
        assert 'aria-label="Settings sections"' in html
        for fragment in ("#blacklist", "#logging", "#exclusions", "#antinuke", "#trusted"):
            assert f'href="{fragment}"' in html
        for field in ("csrf", "rbl_roles", "rbl_authorized", "rbl_reply_mode", "log_joins", "log_timeouts",
                      "log_ignore_users", "log_ignore_channels", "an_admins", "an_whitelist", "an_whitelist_roles"):
            assert f'name="{field}"' in html
        assert response.headers["Referrer-Policy"] == "same-origin"
        assert "form-action 'self'" in response.headers["Content-Security-Policy"]

    run(server, scenario)
