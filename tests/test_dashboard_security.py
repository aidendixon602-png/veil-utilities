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
    guilds[1].get_channel = lambda cid: None
    guilds[1].text_channels = []
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


def test_server_icons_only_use_the_correct_discord_cdn_path():
    bot = make_bot()
    server = login_server(bot)
    _, headers = cookie(server, BOT_OWNER)

    async def scenario(client):
        bot.guilds[0].icon = f"https://cdn.discordapp.com/icons/{GUILD_ID}/a_123abc.png?size=1024"
        response = await client.get("/", headers=headers)
        html = await response.text()
        assert f'<img class="server-icon" src="https://cdn.discordapp.com/icons/{GUILD_ID}/a_123abc.png?size=1024"' in html
        assert 'referrerpolicy="no-referrer"' in html
        for unsafe in (f"https://evil.example/icons/{GUILD_ID}/abc.png",
                       f"https://cdn.discordapp.com.evil.example/icons/{GUILD_ID}/abc.png",
                       f"https://cdn.discordapp.com/icons/{OTHER_GUILD_ID}/abc.png",
                       f'https://cdn.discordapp.com/icons/{GUILD_ID}/abc.png\" onerror=\"alert(1)'):
            bot.guilds[0].icon = unsafe
            response = await client.get("/", headers=headers)
            html = await response.text()
            assert unsafe not in html
            assert '<span class="server-initial" aria-hidden="true">T</span>' in html

    run(server, scenario)


def _feature_revision(html, gid, feature):
    pattern = rf'<form method="post" action="/servers/{gid}/features/{feature}">.*?name="revision" value="([^"]+)"'
    return re.search(pattern, html, re.S).group(1)


def test_feature_controls_auth_csrf_and_foreign_server():
    bot = make_bot()
    server = login_server(bot)
    token, headers = cookie(server, GUILD_OWNER)

    async def scenario(client):
        assert (await client.get(f"/servers/{GUILD_ID}/features", allow_redirects=False)).status == 302
        assert (await client.get(f"/servers/{OTHER_GUILD_ID}/features", headers=headers)).status == 404
        page = await client.get(f"/servers/{GUILD_ID}/features", headers=headers)
        html = await page.text()
        for heading in ("Automod", "Leveling", "Tickets", "Antinuke rules"):
            assert heading in html
        from html.parser import HTMLParser

        class IDCollector(HTMLParser):
            def __init__(self):
                super().__init__()
                self.ids = []

            def handle_starttag(self, tag, attrs):
                self.ids.extend(value for name, value in attrs if name == "id")

        parser = IDCollector()
        parser.feed(html)
        assert len(parser.ids) == len(set(parser.ids))
        rev = _feature_revision(html, GUILD_ID, "levels")
        data = {"csrf": csrf_of(server, token), "revision": rev, "enabled": "on", "voice": "on", "stack": "on",
                "channel": "", "message": "hello"}
        assert (await client.post(f"/servers/{GUILD_ID}/features/levels", headers=headers,
                                  data={**data, "csrf": "wrong"})).status == 403
        assert (await client.post(f"/servers/{GUILD_ID}/features/levels", headers={**headers, "Origin": "https://evil.example"},
                                  data=data)).status == 403
        assert (await client.post(f"/servers/{OTHER_GUILD_ID}/features/levels", headers=headers, data=data)).status == 404
        assert bot.db.writes == []

    run(server, scenario)


def test_feature_controls_save_correct_configs_without_overwriting_other_fields():
    bot = make_bot()
    bot.guilds[0].get_role = lambda rid: SimpleNamespace(id=rid) if rid == 21 else None
    bot.db.store[(GUILD_ID, "filters")] = {"words": ["bad"], "regex": ["pattern"], "links_allow": ["example.com"],
                                           "exempt_roles": [50], "escalate": {"hits": 3}, "timeout": 60}
    bot.db.store[(GUILD_ID, "levels")] = {"enabled": False, "voice": True, "stack": True,
                                          "roles": {"2": 31}, "multipliers": {"9": 2}, "ignored": [17]}
    bot.db.store[(GUILD_ID, "tickets")] = {"support": [20], "max": 1, "legacy": "kept"}
    server = login_server(bot)
    token, headers = cookie(server, GUILD_OWNER)
    csrf = csrf_of(server, token)

    async def revision(client, feature):
        page = await client.get(f"/servers/{GUILD_ID}/features", headers=headers)
        assert page.status == 200
        return _feature_revision(await page.text(), GUILD_ID, feature)

    async def scenario(client):
        data = {"csrf": csrf, "revision": await revision(client, "automod"), "invites": "on", "links": "off",
                "zalgo": "on", "antighostping": "off", "caps": "50", "massmention": "3", "emojis": "0",
                "newlines": "0", "spam_messages": "5", "spam_seconds": "10", "action": "timeout", "timeout": "60"}
        r = await client.post(f"/servers/{GUILD_ID}/features/automod", headers=headers, data=data, allow_redirects=False)
        assert r.status == 302
        filters = bot.db.store[(GUILD_ID, "filters")]
        assert filters["spam"] == [5, 10] and filters["action"] == "timeout" and filters["caps"] == 50
        assert filters["words"] == ["bad"] and filters["regex"] == ["pattern"] and filters["exempt_roles"] == [50]
        assert filters["links_allow"] == ["example.com"] and filters["escalate"] == {"hits": 3}
        data = {"csrf": csrf, "revision": await revision(client, "levels"), "enabled": "on", "voice": "off",
                "stack": "on", "channel": "44", "message": "Level {level}"}
        r = await client.post(f"/servers/{GUILD_ID}/features/levels", headers=headers, data=data, allow_redirects=False)
        assert r.status == 302
        levels = bot.db.store[(GUILD_ID, "levels")]
        assert levels["enabled"] is True and levels["voice"] is False and levels["channel"] == 44
        assert levels["message"] == "Level {level}" and levels["roles"] == {"2": 31}
        assert levels["multipliers"] == {"9": 2} and levels["ignored"] == [17]
        data = {"csrf": csrf, "revision": await revision(client, "tickets"), "category": "", "log": "66",
                "ping": "on", "max": "3", "support": "20,21", "message": "Welcome"}
        r = await client.post(f"/servers/{GUILD_ID}/features/tickets", headers=headers, data=data, allow_redirects=False)
        assert r.status == 302
        tickets = bot.db.store[(GUILD_ID, "tickets")]
        assert tickets["support"] == [20, 21] and tickets["ping"] and tickets["log"] == 66
        assert tickets["max"] == 3 and tickets["legacy"] == "kept"
        assert set(bot.db.writes) == {(GUILD_ID, "filters"), (GUILD_ID, "levels"), (GUILD_ID, "tickets")}

    run(server, scenario)


def test_feature_controls_reject_invalid_and_stale_submissions_without_writes():
    bot = make_bot()
    server = login_server(bot)
    token, headers = cookie(server, GUILD_OWNER)
    csrf = csrf_of(server, token)

    async def scenario(client):
        page = await client.get(f"/servers/{GUILD_ID}/features", headers=headers)
        html = await page.text()
        base = {"csrf": csrf, "revision": _feature_revision(html, GUILD_ID, "automod"), "invites": "on", "links": "on",
                "zalgo": "off", "antighostping": "off", "caps": "30", "massmention": "3", "emojis": "2",
                "newlines": "3", "spam_messages": "2", "spam_seconds": "5", "action": "delete", "timeout": "60"}
        for field, value in (("caps", "29"), ("spam_messages", "1"), ("timeout", "99999999"), ("action", "ban")):
            r = await client.post(f"/servers/{GUILD_ID}/features/automod", headers=headers, data={**base, field: value})
            assert r.status == 400 and "Nothing was saved" in await r.text()
        ticket = {"csrf": csrf, "revision": _feature_revision(html, GUILD_ID, "tickets"), "category": "", "log": "",
                  "ping": "off", "max": "1", "support": str(GUILD_ID), "message": ""}
        r = await client.post(f"/servers/{GUILD_ID}/features/tickets", headers=headers, data=ticket)
        assert r.status == 400 and "@everyone" in await r.text()
        r = await client.post(f"/servers/{GUILD_ID}/features/tickets", headers=headers, data={**ticket, "support": "999"})
        assert r.status == 400
        assert bot.db.writes == []
        bot.db.store[(GUILD_ID, "filters")]["links_allow"] = ["new.example"]
        r = await client.post(f"/servers/{GUILD_ID}/features/automod", headers=headers, data=base)
        assert r.status == 409 and "Nothing was saved" in await r.text()
        assert bot.db.store[(GUILD_ID, "filters")]["links_allow"] == ["new.example"]
        assert bot.db.writes == []

    run(server, scenario)


def test_feature_antinuke_rules_and_disable_confirmation():
    bot = make_bot()
    notice = AsyncMock()
    bot.get_cog = lambda name: SimpleNamespace(security_notice=notice) if name == "Antinuke" else None
    bot.db.store[(GUILD_ID, "antinuke")] = {"enabled": True, "modules": {"ban": {"on": True, "threshold": 5,
                                                                                  "window": 90, "punishment": "strip"}},
                                             "whitelist": [11], "admins": [12]}
    server = login_server(bot)
    token, headers = cookie(server, GUILD_OWNER)
    csrf = csrf_of(server, token)

    async def scenario(client):
        page = await client.get(f"/servers/{GUILD_ID}/features", headers=headers)
        html = await page.text()
        rev = _feature_revision(html, GUILD_ID, "protection")
        data = {"csrf": csrf, "revision": rev, "enabled": "off"}
        r = await client.post(f"/servers/{GUILD_ID}/features/protection", headers=headers, data=data)
        assert r.status == 400 and not bot.db.writes
        r = await client.post(f"/servers/{GUILD_ID}/features/rule", headers=headers,
                              data={"csrf": csrf, "revision": rev, "rule": "ban", "state": "off"}, allow_redirects=False)
        assert r.status == 302
        an = bot.db.store[(GUILD_ID, "antinuke")]
        assert an["enabled"] and an["modules"]["ban"] == {"on": False, "threshold": 5, "window": 90, "punishment": "strip"}
        assert an["whitelist"] == [11] and an["admins"] == [12]
        r = await client.post(f"/servers/{GUILD_ID}/features/protection", headers=headers,
                              data={**data, "confirm_disable": "yes"})
        assert r.status == 409  # stale after changing the rule
        page = await client.get(f"/servers/{GUILD_ID}/features", headers=headers)
        data["revision"] = _feature_revision(await page.text(), GUILD_ID, "protection")
        r = await client.post(f"/servers/{GUILD_ID}/features/protection", headers=headers,
                              data={**data, "confirm_disable": "yes"}, allow_redirects=False)
        assert r.status == 302 and bot.db.store[(GUILD_ID, "antinuke")]["enabled"] is False
        assert notice.await_count == 2
        page = await client.get(f"/servers/{GUILD_ID}/features", headers=headers)
        rev = _feature_revision(await page.text(), GUILD_ID, "protection")
        r = await client.post(f"/servers/{GUILD_ID}/features/protection", headers=headers,
                              data={"csrf": csrf, "revision": rev, "enabled": "on"}, allow_redirects=False)
        assert r.status == 302
        an = bot.db.store[(GUILD_ID, "antinuke")]
        assert an["enabled"] is True and an["modules"]["ban"]["on"] is False
        assert "botadd" in an["modules"]  # enabling uses the bot's actual default rules

    run(server, scenario)


def test_feature_rule_threshold_window_and_punishment_validation():
    bot = make_bot()
    bot.db.store[(GUILD_ID, "antinuke")] = {"enabled": True, "modules": {"ban": {"on": True, "command": False}},
                                             "punishment": "ban"}
    server = login_server(bot)
    token, headers = cookie(server, GUILD_OWNER)

    async def scenario(client):
        page = await client.get(f"/servers/{GUILD_ID}/features", headers=headers)
        rev = _feature_revision(await page.text(), GUILD_ID, "rule")
        data = {"csrf": csrf_of(server, token), "revision": rev, "rule": "ban", "state": "on",
                "threshold": "4", "window": "120", "punishment": "kick"}
        for patch in ({"threshold": "0"}, {"window": "86401"}, {"punishment": "ban-everyone"},
                      {"rule": "permissions", "punishment": "ban"}, {"rule": "botadd", "threshold": "5"}):
            r = await client.post(f"/servers/{GUILD_ID}/features/rule", headers=headers, data={**data, **patch})
            assert r.status == 400
            assert not bot.db.writes
        r = await client.post(f"/servers/{GUILD_ID}/features/rule", headers=headers, data=data, allow_redirects=False)
        assert r.status == 302
        mod = bot.db.store[(GUILD_ID, "antinuke")]["modules"]["ban"]
        assert mod == {"on": True, "command": False, "threshold": 4, "window": 120, "punishment": "kick"}
        page = await client.get(f"/servers/{GUILD_ID}/features", headers=headers)
        data.update(revision=_feature_revision(await page.text(), GUILD_ID, "rule"), threshold="", window="", punishment="")
        r = await client.post(f"/servers/{GUILD_ID}/features/rule", headers=headers, data=data, allow_redirects=False)
        assert r.status == 302
        assert bot.db.store[(GUILD_ID, "antinuke")]["modules"]["ban"] == {"on": True, "command": False}

    run(server, scenario)


def test_feature_rule_page_shows_effective_migrations_without_writing_on_get():
    bot = make_bot()
    original = {"enabled": True, "modules": {"roleinvite": {"on": True, "threshold": 1}}}
    bot.db.store[(GUILD_ID, "antinuke")] = json.loads(json.dumps(original))
    server = login_server(bot)
    token, headers = cookie(server, GUILD_OWNER)

    async def scenario(client):
        page = await client.get(f"/servers/{GUILD_ID}/features", headers=headers)
        html = await page.text()
        assert 'id="threshold_roleinvite"' in html and 'value="10"' in html
        assert bot.db.store[(GUILD_ID, "antinuke")] == original and not bot.db.writes
        revision = _feature_revision(html, GUILD_ID, "rule")
        response = await client.post(f"/servers/{GUILD_ID}/features/rule", headers=headers, allow_redirects=False,
                                     data={"csrf": csrf_of(server, token), "revision": revision,
                                           "rule": "ban", "state": "on"})
        assert response.status == 302
        assert bot.db.store[(GUILD_ID, "antinuke")]["modules"]["roleinvite"]["threshold"] == 10
        assert bot.db.store[(GUILD_ID, "antinuke")]["rules_v3"] is True

    run(server, scenario)


def test_feature_controls_escape_config_values_and_enforce_revision_user_and_guild():
    bot = make_bot()
    bot.db.store[(GUILD_ID, "tickets")] = {"support": [], "max": 1, "message": '</textarea><script>alert(1)</script>'}
    server = login_server(bot)
    owner_token, owner_headers = cookie(server, GUILD_OWNER)
    dev_token, dev_headers = cookie(server, BOT_OWNER)

    async def scenario(client):
        page = await client.get(f"/servers/{GUILD_ID}/features", headers=owner_headers)
        html = await page.text()
        assert '&lt;/textarea&gt;&lt;script&gt;alert(1)&lt;/script&gt;' in html
        assert '<script>alert(1)</script>' not in html
        revision = _feature_revision(html, GUILD_ID, "levels")
        data = {"csrf": csrf_of(server, dev_token), "revision": revision, "enabled": "on", "voice": "on",
                "stack": "on", "channel": "", "message": "hello"}
        r = await client.post(f"/servers/{GUILD_ID}/features/levels", headers=dev_headers, data=data)
        assert r.status == 409 and not bot.db.writes  # signed to the owner, not the developer
        r = await client.post(f"/servers/{OTHER_GUILD_ID}/features/levels", headers=dev_headers, data=data)
        assert r.status == 409 and not bot.db.writes  # also tied to its original guild
        r = await client.post(f"/servers/{GUILD_ID}/features/levels", headers=owner_headers,
                              data={**data, "csrf": csrf_of(server, owner_token), "revision": revision + 'x'})
        assert r.status == 409 and not bot.db.writes

    run(server, scenario)


def test_server_workspace_navigation_access_and_real_status():
    bot = make_bot('<script>alert("server")</script>')
    bot.db.store[(GUILD_ID, "antinuke")] = {"enabled": True}
    bot.db.store[(GUILD_ID, "levels")] = {"enabled": False}
    bot.db.store[(GUILD_ID, "logs")] = {"joinleave": 44, "commands": -1}
    server = login_server(bot)
    _, owner_headers = cookie(server, GUILD_OWNER)
    _, developer_headers = cookie(server, BOT_OWNER)

    async def scenario(client):
        path = f"/servers/{GUILD_ID}/overview"
        assert (await client.get(path, allow_redirects=False)).status == 302
        assert (await client.get(f"/servers/{OTHER_GUILD_ID}/overview", headers=owner_headers)).status == 404
        assert (await client.get(f"/servers/{OTHER_GUILD_ID}/overview", headers=developer_headers)).status == 200
        response = await client.get(path, headers=owner_headers)
        html = await response.text()
        assert response.status == 200 and 'aria-label="Mobile server navigation"' in html
        assert "<script>" not in html and "&lt;script&gt;" in html
        assert "Protection on" in html and "Leveling off" in html and "1 explicit log route" in html
        for section in ("automod", "leveling", "tickets", "rules"):
            assert f"?section={section}" in html
        assert bot.db.writes == []

    run(server, scenario)


def test_feature_pages_are_focused_and_keep_safe_save_redirects():
    bot = make_bot()
    server = login_server(bot)
    token, headers = cookie(server, GUILD_OWNER)

    async def scenario(client):
        url = f"/servers/{GUILD_ID}/features"
        for section, feature in (("automod", "automod"), ("leveling", "levels"),
                                 ("tickets", "tickets"), ("rules", "protection")):
            page = await client.get(f"{url}?section={section}", headers=headers)
            html = await page.text()
            assert page.status == 200
            assert f'action="{url}/{feature}"' in html
            assert f'name="return_section" value="{section}"' in html
            if section != "automod":
                assert f'action="{url}/automod"' not in html
            assert 'aria-current="page"' in html
        assert (await client.get(f"{url}?section=not-real", headers=headers)).status == 404
        assert (await client.get(f"/servers/{OTHER_GUILD_ID}/features?section=tickets", headers=headers)).status == 404
        page = await client.get(f"{url}?section=leveling", headers=headers)
        revision = _feature_revision(await page.text(), GUILD_ID, "levels")
        data = {"csrf": csrf_of(server, token), "revision": revision, "enabled": "on", "voice": "on", "stack": "on",
                "channel": "", "message": "hello", "return_section": "leveling"}
        response = await client.post(f"{url}/levels", headers=headers, data=data, allow_redirects=False)
        assert response.status == 302
        assert response.headers["Location"] == f"{url}?saved=1&section=leveling#leveling"
        # The selected-page form retains the same stale-revision protection.
        assert (await client.post(f"{url}/levels", headers=headers, data=data)).status == 409
        page = await client.get(f"{url}?section=leveling", headers=headers)
        data["revision"] = _feature_revision(await page.text(), GUILD_ID, "levels")
        data["return_section"] = "//evil.example"
        response = await client.post(f"{url}/levels", headers=headers, data=data, allow_redirects=False)
        assert response.headers["Location"] == f"{url}?saved=1#leveling"

    run(server, scenario)


def test_landing_workspace_owner_developer_and_revoked_server_access():
    bot = make_bot()
    bot.guilds[1].owner_id = GUILD_OWNER
    bot.is_authorized = lambda gid: gid == GUILD_ID
    bot.db.store[(GUILD_ID, "antinuke")] = {"enabled": True}
    bot.db.store[(GUILD_ID, "logs")] = {"commands": 123}
    server = login_server(bot)
    _, owner_headers = cookie(server, GUILD_OWNER)
    _, developer_headers = cookie(server, BOT_OWNER)

    async def scenario(client):
        owner = await (await client.get("/", headers=owner_headers)).text()
        assert "Server owner / administrator workspace" in owner and "Test Server</a>" in owner
        assert "Other</a>" not in owner and "Developer workspace" not in owner
        assert 'href="/authorization"' not in owner and "JOINED SERVERS" not in owner
        assert 'class="guild-card"' in owner and "1 configured" in owner
        developer = await (await client.get("/", headers=developer_headers)).text()
        assert "Developer workspace" in developer and "Other</a>" in developer
        assert "Not authorized" in developer and 'href="/authorization"' in developer
        filtered = await (await client.get("/?authorized=no", headers=developer_headers)).text()
        assert "Other</a>" in filtered and "Test Server</a>" not in filtered
        assert '<details class="advanced-filters" open>' in filtered
        assert (await client.get(f"/servers/{OTHER_GUILD_ID}/overview", headers=owner_headers)).status == 404
        assert (await client.get(f"/servers/{OTHER_GUILD_ID}/overview", headers=developer_headers)).status == 200
        # Access is checked again even after an owner opened the dashboard.
        bot.is_authorized = lambda gid: gid == OTHER_GUILD_ID
        assert (await client.get(f"/servers/{GUILD_ID}/overview", headers=owner_headers)).status == 404
        assert (await client.get(f"/servers/{GUILD_ID}/features?section=automod", headers=owner_headers)).status == 404
        assert (await client.get(f"/servers/{GUILD_ID}/overview", headers=developer_headers)).status == 200
        assert bot.db.writes == []

    run(server, scenario)


def test_workspace_links_to_existing_security_and_community_controls():
    bot = make_bot()
    server = login_server(bot)
    _, headers = cookie(server, GUILD_OWNER)

    async def scenario(client):
        page = await client.get(f"/servers/{GUILD_ID}/overview", headers=headers)
        html = await page.text()
        for anchor in ("logging", "exclusions", "trusted", "blacklist"):
            assert f'href="/servers/{GUILD_ID}#{anchor}"' in html
        assert "shared settings form" in html
        for section in ("rules", "automod", "leveling", "tickets"):
            assert f'features?section={section}' in html
        assert bot.db.writes == []

    run(server, scenario)


def test_general_prefix_form_preserves_other_settings_and_enforces_safety():
    bot = make_bot()
    bot.default_prefix = ","
    bot.db.store[(GUILD_ID, "settings")] = {"prefix": "!", "other_setting": {"retained": True}}
    server = login_server(bot)
    owner_token, owner_headers = cookie(server, GUILD_OWNER)
    dev_token, dev_headers = cookie(server, BOT_OWNER)
    url = f"/servers/{GUILD_ID}/features"

    async def scenario(client):
        page = await client.get(f"{url}?section=general", headers=owner_headers)
        html = await page.text()
        assert 'name="prefix" value="!"' in html
        revision = _feature_revision(html, GUILD_ID, "general")
        data = {"csrf": csrf_of(server, owner_token), "revision": revision, "prefix": "?", "return_section": "general"}
        for invalid in ("", "x" * 11, "a b", "a\nb"):
            assert (await client.post(f"{url}/general", headers=owner_headers, data={**data, "prefix": invalid})).status == 400
        assert (await client.post(f"{url}/general", headers=owner_headers, data={**data, "csrf": "bad"})).status == 403
        assert (await client.post(f"{url}/general", headers={**owner_headers, "Origin": "https://evil.example"}, data=data)).status == 403
        assert (await client.post(f"/servers/{OTHER_GUILD_ID}/features/general", headers=owner_headers, data=data)).status == 404
        assert not bot.db.writes
        result = await client.post(f"{url}/general", headers=owner_headers, data=data, allow_redirects=False)
        assert result.status == 302 and result.headers["Location"] == f"{url}?saved=1&section=general#general"
        assert bot.db.store[(GUILD_ID, "settings")] == {"prefix": "?", "other_setting": {"retained": True}}
        assert (await client.post(f"{url}/general", headers=owner_headers, data=data)).status == 409
        page = await client.get(f"{url}?section=general", headers=dev_headers)
        revision = _feature_revision(await page.text(), GUILD_ID, "general")
        result = await client.post(f"{url}/general", headers=dev_headers,
                                   data={**data, "csrf": csrf_of(server, dev_token), "revision": revision, "prefix": ";"})
        assert result.status == 200 and bot.db.store[(GUILD_ID, "settings")]["prefix"] == ";"

    run(server, scenario)


def test_antinuke_readiness_is_scoped_and_read_only():
    bot = make_bot()
    guild = bot.guilds[0]
    guild.me = SimpleNamespace(guild_permissions=discord.Permissions(view_audit_log=True, manage_roles=True))
    bot.intents = SimpleNamespace(members=False)
    original = {"enabled": True, "modules": {"ban": {"on": True}, "roleinvite": {"on": True, "threshold": 1}}}
    bot.db.store[(GUILD_ID, "antinuke")] = json.loads(json.dumps(original))
    server = login_server(bot)
    _, owner_headers = cookie(server, GUILD_OWNER)
    _, dev_headers = cookie(server, BOT_OWNER)

    async def scenario(client):
        url = f"/servers/{GUILD_ID}/features?section=rules"
        html = await (await client.get(url, headers=owner_headers)).text()
        assert "Protection readiness" in html and "Missing permissions:" in html
        assert "Manage Channels" in html and "Server Members intent is off" in html
        assert "do not guarantee protection" in html
        assert bot.db.store[(GUILD_ID, "antinuke")] == original and not bot.db.writes
        assert (await client.get(f"/servers/{OTHER_GUILD_ID}/features?section=rules", headers=owner_headers)).status == 404
        assert (await client.get(f"/servers/{OTHER_GUILD_ID}/features?section=rules", headers=dev_headers)).status == 200
        guild.me = None
        html = await (await client.get(url, headers=owner_headers)).text()
        assert "Bot permissions could not be checked" in html

    run(server, scenario)


def test_log_preview_uses_saved_routing_and_timeout_event_fallback():
    bot = make_bot()
    guild = bot.guilds[0]

    def channel(cid, name):
        ch = MagicMock(spec=discord.TextChannel)
        ch.id, ch.name, ch.position, ch.category = cid, name, cid, None
        return ch

    join = channel(44, "join-logs")
    mod = channel(66, "mod-logs")
    general = channel(77, "midtown-utilities-logging")
    guild.text_channels = [join, mod, general]
    guild.get_channel = lambda cid: next((c for c in guild.text_channels if c.id == cid), None)
    bot.db.store[(GUILD_ID, "logs")] = {"joinleave": 44, "timeouts": -1, "roles": -1, "commands": 999}
    server = login_server(bot)
    _, headers = cookie(server, GUILD_OWNER)

    async def scenario(client):
        html = await (await client.get(f"/servers/{GUILD_ID}", headers=headers)).text()
        assert 'Joins &amp; leaves</span><strong>#join-logs</strong><small>Selected · shared' in html
        assert 'Timeouts</span><strong>#mod-logs</strong><small>Event fallback; direct route off · shared' in html
        assert 'Roles</span><strong>No destination</strong><small>Off' in html
        assert 'Messages</span><strong>#midtown-utilities-logging</strong><small>Automatic' in html
        assert 'Commands</span><strong>No destination</strong><small>No route found' in html
        assert 'Unavailable channel (999)' in html and not bot.db.writes

    run(server, scenario)


def test_advanced_automod_entries_validate_and_require_explicit_removal():
    bot = make_bot()
    original = {"words": ["old", '<script>'], "regex": ["safe"], "links_allow": ["old.example"],
                "invites": True, "exempt_roles": [90], "legacy": {"keep": True}}
    bot.db.store[(GUILD_ID, "filters")] = json.loads(json.dumps(original))
    server = login_server(bot)
    token, headers = cookie(server, GUILD_OWNER)
    _, dev_headers = cookie(server, BOT_OWNER)
    csrf = csrf_of(server, token)
    path = f"/servers/{GUILD_ID}/features"

    async def revision(client):
        html = await (await client.get(f"{path}?section=automod", headers=headers)).text()
        assert "&lt;script&gt;" in html and "<script>" not in html
        return _feature_revision(html, GUILD_ID, "automod-entry")

    async def scenario(client):
        rev = await revision(client)
        base = {"csrf": csrf, "revision": rev, "list_key": "words", "operation": "add", "entry": "New*", "return_section": "automod"}
        for patch in ({"entry": ""}, {"entry": "x" * 101}, {"entry": "x\ny"}, {"list_key": "legacy"},
                      {"list_key": "regex", "entry": "["}, {"list_key": "links_allow", "entry": "bad..domain"}):
            assert (await client.post(f"{path}/automod-entry", headers=headers, data={**base, **patch})).status == 400
        assert (await client.post(f"{path}/automod-entry", headers=headers, data={**base, "csrf": "bad"})).status == 403
        assert (await client.post(f"{path}/automod-entry", headers={**headers, "Origin": "https://evil.example"}, data=base)).status == 403
        assert (await client.post(f"/servers/{OTHER_GUILD_ID}/features/automod-entry", headers=headers, data=base)).status == 404
        assert (await client.post(f"{path}/automod-entry", headers=dev_headers, data=base)).status == 403
        assert not bot.db.writes
        r = await client.post(f"{path}/automod-entry", headers=headers, data=base, allow_redirects=False)
        assert r.status == 302 and "section=automod" in r.headers["Location"]
        assert bot.db.store[(GUILD_ID, "filters")]["words"] == ["old", '<script>', "new*"]
        assert bot.db.store[(GUILD_ID, "filters")]["legacy"] == {"keep": True}
        assert (await client.post(f"{path}/automod-entry", headers=headers, data=base)).status == 409
        rev = await revision(client)
        removal = {**base, "revision": rev, "operation": "remove", "index": "0"}
        assert (await client.post(f"{path}/automod-entry", headers=headers, data=removal)).status == 400
        assert bot.db.store[(GUILD_ID, "filters")]["words"][0] == "old"
        r = await client.post(f"{path}/automod-entry", headers=headers,
                              data={**removal, "confirm_remove": "yes"}, allow_redirects=False)
        assert r.status == 302 and bot.db.store[(GUILD_ID, "filters")]["words"] == ['<script>', "new*"]
        rev = await revision(client)
        r = await client.post(f"{path}/automod-entry", headers=headers, allow_redirects=False,
                              data={**base, "revision": rev, "list_key": "links_allow", "entry": "https://www.Example.com/path"})
        assert r.status == 302 and "example.com" in bot.db.store[(GUILD_ID, "filters")]["links_allow"]

    run(server, scenario)


def test_advanced_automod_exemptions_reject_foreign_targets_and_preserve_existing():
    bot = make_bot()
    bot.db.store[(GUILD_ID, "filters")] = {"exempt_roles": [90], "exempt_channels": [], "invites": True}
    guild = bot.guilds[0]
    guild.roles = [SimpleNamespace(id=21, name="Helpers")]
    guild.get_role = lambda rid: next((r for r in guild.roles if r.id == rid), None)
    server = login_server(bot)
    token, headers = cookie(server, GUILD_OWNER)
    csrf = csrf_of(server, token)
    path = f"/servers/{GUILD_ID}/features"

    async def get_revision(client):
        html = await (await client.get(f"{path}?section=automod", headers=headers)).text()
        assert "Helpers" in html
        return _feature_revision(html, GUILD_ID, "automod-exempt")

    async def scenario(client):
        html = await (await client.get(f"{path}?section=automod", headers=headers)).text()
        assert "Unavailable (90)" in html
        rev = await get_revision(client)
        data = {"csrf": csrf, "revision": rev, "list_key": "exempt_roles", "target": "21", "operation": "add"}
        for target in (str(GUILD_ID), "9999", "21,90", "bad"):
            assert (await client.post(f"{path}/automod-exempt", headers=headers, data={**data, "target": target})).status == 400
        assert not bot.db.writes
        r = await client.post(f"{path}/automod-exempt", headers=headers, data=data, allow_redirects=False)
        assert r.status == 302 and bot.db.store[(GUILD_ID, "filters")]["exempt_roles"] == [90, 21]
        assert bot.db.store[(GUILD_ID, "filters")]["invites"] is True
        rev = await get_revision(client)
        removal = {**data, "revision": rev, "target": "90", "operation": "remove"}
        assert (await client.post(f"{path}/automod-exempt", headers=headers, data=removal)).status == 400
        r = await client.post(f"{path}/automod-exempt", headers=headers,
                              data={**removal, "confirm_remove": "yes"}, allow_redirects=False)
        assert r.status == 302 and bot.db.store[(GUILD_ID, "filters")]["exempt_roles"] == [21]
        rev = await get_revision(client)
        r = await client.post(f"{path}/automod-exempt", headers=headers, allow_redirects=False,
                              data={**data, "revision": rev, "list_key": "exempt_channels", "target": "44"})
        assert r.status == 302 and bot.db.store[(GUILD_ID, "filters")]["exempt_channels"] == [44]

    run(server, scenario)


def test_advanced_automod_developer_session_and_access_recheck():
    bot = make_bot()
    server = login_server(bot)
    token, dev_headers = cookie(server, BOT_OWNER)
    _, owner_headers = cookie(server, GUILD_OWNER)

    async def scenario(client):
        path = f"/servers/{OTHER_GUILD_ID}/features"
        assert (await client.get(f"{path}?section=automod", headers=owner_headers)).status == 404
        html = await (await client.get(f"{path}?section=automod", headers=dev_headers)).text()
        revision = _feature_revision(html, OTHER_GUILD_ID, "automod-entry")
        data = {"csrf": csrf_of(server, token), "revision": revision, "operation": "add", "list_key": "words", "entry": "hello"}
        response = await client.post(f"{path}/automod-entry", headers=dev_headers, data=data, allow_redirects=False)
        assert response.status == 302 and bot.db.store[(OTHER_GUILD_ID, "filters")]["words"] == ["hello"]
        assert (await client.post(f"{path}/automod-entry", headers=owner_headers, data=data)).status == 404

    run(server, scenario)


def test_administrator_scoped_access_writes_and_revocation():
    from core.dashboard import LOG_FIELDS

    bot = make_bot()
    guild = bot.guilds[0]
    role_id = 42
    member = SimpleNamespace(id=STRANGER, _roles=[role_id])
    admin_role = SimpleNamespace(id=role_id, permissions=discord.Permissions(administrator=True))
    guild.fetch_member = AsyncMock(return_value=member)
    guild.fetch_roles = AsyncMock(return_value=[admin_role])
    bot.db.store[(GUILD_ID, "antinuke")] = {"enabled": True, "whitelist": [1234]}
    bot.db.store[(GUILD_ID, "roleblacklist")] = {"roles": [5678]}
    bot.db.store[(GUILD_ID, "logs")] = {"commands": 44, "unknown": "preserve", "ignore": {"users": [9], "extra": [88]}}
    server = login_server(bot)
    token, headers = cookie(server, STRANGER)
    path = f"/servers/{GUILD_ID}"

    async def scenario(client):
        home = await (await client.get("/", headers=headers)).text()
        assert "Test Server</a>" in home and "Other</a>" not in home
        assert '<span>ANTINUKE</span>' not in home
        assert "Test Server</a>" not in await (await client.get("/?antinuke=on", headers=headers)).text()
        overview = await (await client.get(f"{path}/overview", headers=headers)).text()
        assert "Automod" in overview and "Tickets" in overview and "Logging channels" in overview
        assert "Antinuke" not in overview and "Trusted identities" not in overview and "Role blacklist" not in overview
        for section in ("automod", "logging", "leveling", "tickets"):
            page = await client.get(f"{path}/features?section={section}", headers=headers)
            assert page.status == 200
            html = await page.text()
            assert 'section=rules' not in html and 'id="an_whitelist"' not in html
        for section in ("rules", "general", "garbage"):
            assert (await client.get(f"{path}/features?section={section}", headers=headers)).status == 403
        assert (await client.get(path, headers=headers, allow_redirects=False)).status == 302
        assert (await client.get(f"/api/guilds/{GUILD_ID}/config", headers=headers)).status == 403
        assert (await client.post(f"/api/guilds/{GUILD_ID}/config", headers=headers, data={"an_whitelist": "1"})).status == 403
        assert (await client.get("/authorization", headers=headers)).status == 403
        assert (await client.get(f"/servers/{OTHER_GUILD_ID}/overview", headers=headers)).status == 404
        for feature in ("general", "rule", "protection", "authorization"):
            assert (await client.post(f"{path}/features/{feature}", headers=headers,
                                      data={"csrf": csrf_of(server, token)})).status == 403
        for section, feature, fields in (
            ("automod", "automod", {"invites": "on", "links": "off", "zalgo": "off", "antighostping": "off",
                                    "caps": "0", "massmention": "0", "emojis": "0", "newlines": "0",
                                    "spam_messages": "0", "spam_seconds": "10", "action": "delete", "timeout": "60"}),
            ("leveling", "levels", {"enabled": "on", "voice": "off", "stack": "on", "channel": "", "message": "hi"}),
            ("tickets", "tickets", {"category": "", "log": "", "ping": "off", "max": "2", "support": "", "message": "hi"}),
        ):
            html = await (await client.get(f"{path}/features?section={section}", headers=headers)).text()
            data = {**fields, "csrf": csrf_of(server, token), "revision": _feature_revision(html, GUILD_ID, feature),
                    "an_whitelist": "1", "rbl_roles": "1"}
            assert (await client.post(f"{path}/features/{feature}", headers=headers, data=data, allow_redirects=False)).status == 302
        html = await (await client.get(f"{path}/features?section=logging", headers=headers)).text()
        revision = _feature_revision(html, GUILD_ID, "logging")
        data = {field: "" for field, _ in LOG_FIELDS}
        data.update({"csrf": csrf_of(server, token), "revision": revision, "log_commands": "44",
                     "log_ignore_channels": "55, 66", "log_ignore_users": "77", "an_whitelist": "1",
                     "rbl_roles": "1", "return_section": "logging"})
        assert (await client.post(f"{path}/features/logging", headers={**headers, "Origin": "https://evil.example"}, data=data)).status == 403
        assert (await client.post(f"{path}/features/logging", headers=headers, data={**data, "csrf": "bad"})).status == 403
        assert (await client.post(f"{path}/features/logging", headers=headers, data=data, allow_redirects=False)).status == 302
        logs = bot.db.store[(GUILD_ID, "logs")]
        assert logs["commands"] == 44 and logs["unknown"] == "preserve"
        assert logs["ignore"] == {"users": [77], "channels": [55, 66], "extra": [88]}
        assert bot.db.store[(GUILD_ID, "antinuke")]["whitelist"] == [1234]
        assert bot.db.store[(GUILD_ID, "roleblacklist")]["roles"] == [5678]
        assert (await client.post(f"{path}/features/logging", headers=headers, data=data)).status == 409
        # Fresh roles are used: removal of Administrator immediately denies the old session.
        admin_role.permissions = discord.Permissions.none()
        assert (await client.get(f"{path}/features?section=logging", headers=headers)).status == 403
        assert (await client.post(f"{path}/features/logging", headers=headers, data=data)).status == 403
        admin_role.permissions = discord.Permissions(administrator=True)
        guild.fetch_member.side_effect = RuntimeError("Discord unavailable")
        assert (await client.get(f"{path}/overview", headers=headers)).status == 403
        guild.fetch_member.side_effect = None
        bot.is_authorized = lambda gid: gid != GUILD_ID
        assert (await client.get(f"{path}/overview", headers=headers)).status == 403

    run(server, scenario)
