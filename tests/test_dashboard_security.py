"""Security tests for the web dashboard: login, per-server access, CSRF, escaping and bind address."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

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

    async def set_config(self, gid, module, data):
        self.store[(gid, module)] = data
        self.writes.append((gid, module))


def make_bot(guild_name="Test Server"):
    guilds = [
        SimpleNamespace(id=GUILD_ID, name=guild_name, owner_id=GUILD_OWNER, member_count=10, members=[]),
        SimpleNamespace(id=OTHER_GUILD_ID, name="Other", owner_id=999, member_count=5, members=[]),
    ]
    return SimpleNamespace(
        guilds=guilds,
        get_guild=lambda gid: next((g for g in guilds if g.id == gid), None),
        is_owner=AsyncMock(side_effect=lambda uid: uid == BOT_OWNER),
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
    assert login_server(bot).host == "0.0.0.0"


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
        assert await r.json() == []
        r = await client.get(f"/servers/{GUILD_ID}", headers=headers)
        assert r.status == 404
        r = await client.get(f"/api/guilds/{GUILD_ID}/config", headers=headers)
        assert r.status == 404
        r = await client.post(f"/api/guilds/{GUILD_ID}/config", headers=headers,
                              data={"csrf": csrf_of(server, token), "an_admins": str(STRANGER)})
        assert r.status == 404

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
            data={"csrf": csrf_of(server, token), "rbl_roles": "11, 22, nope", "rbl_authorized": "33",
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
