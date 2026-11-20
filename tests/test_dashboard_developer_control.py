"""Developer-only dashboard security and read-only control center."""
import asyncio
import discord
from types import SimpleNamespace
from unittest.mock import AsyncMock

from core.dashboard import SESSION_COOKIE, STATE_COOKIE
from core.db import Database
from test_dashboard_security import BOT_OWNER, GUILD_ID, GUILD_OWNER, STRANGER, cookie, csrf_of, login_server, make_bot, run


def test_server_owner_admin_and_stranger_denied_all_routes():
    bot = make_bot()
    server = login_server(bot)
    async def scenario(client):
        for uid in (GUILD_OWNER, STRANGER):
            token, headers = cookie(server, uid)
            for path in ('/', '/servers', '/authorization', '/control/security', '/control/moderation',
                         '/control/roblox', '/control/database', '/api/guilds',
                         f'/servers/{GUILD_ID}/overview', f'/servers/{GUILD_ID}/features',
                         f'/servers/{GUILD_ID}/roblox', f'/api/guilds/{GUILD_ID}/config'):
                assert (await client.get(path, headers=headers, allow_redirects=False)).status == 403
            for path in ('/authorization', f'/servers/{GUILD_ID}/roblox',
                         f'/servers/{GUILD_ID}/features/general', f'/api/guilds/{GUILD_ID}/config'):
                assert (await client.post(path, headers=headers, data={'csrf': csrf_of(server, token)})).status == 403
        assert not bot.db.writes
    run(server, scenario)


def test_oauth_developer_only_logout_revocation_and_cookies():
    bot = make_bot()
    server = login_server(bot)
    server._fetch_discord_user = AsyncMock(return_value={'id': str(GUILD_OWNER), 'username': 'owner'})
    async def scenario(client):
        for uid in (GUILD_OWNER, BOT_OWNER):
            server._fetch_discord_user.return_value = {'id': str(uid), 'username': 'name'}
            response = await client.get('/login', allow_redirects=False)
            state_cookie = response.cookies[STATE_COOKIE].value
            state = server._unsign(state_cookie, 'state')['s']
            response = await client.get(f'/callback?code=hidden&state={state}', allow_redirects=False,
                                        headers={'Cookie': f'{STATE_COOKIE}={state_cookie}'})
            if uid == GUILD_OWNER:
                assert response.status == 403 and SESSION_COOKIE not in response.cookies
                continue
            assert response.status == 302
            session = response.cookies[SESSION_COOKIE]
            assert session['secure'] and session['httponly'] and session['samesite'] == 'Lax'
            token = session.value
            headers = {'Cookie': f'{SESSION_COOKIE}={token}'}
            assert (await client.get('/', headers=headers)).status == 200
            assert (await client.post('/logout', headers=headers, data={'csrf': 'bad'})).status == 403
            assert (await client.post('/logout', headers=headers, data={'csrf': csrf_of(server, token)},
                                      allow_redirects=False)).status == 302
            assert (await client.get('/api/guilds', headers=headers)).status == 401
    run(server, scenario)


def test_developer_rechecked_on_each_request_and_fails_closed():
    bot = make_bot()
    server = login_server(bot)
    _, headers = cookie(server, BOT_OWNER)
    async def scenario(client):
        assert (await client.get('/control/bot', headers=headers)).status == 200
        bot.application_info.return_value = SimpleNamespace(owner=SimpleNamespace(id=99), team=None)
        assert (await client.get('/control/bot', headers=headers)).status == 403
        assert (await client.get(f'/api/guilds/{GUILD_ID}/config', headers=headers)).status == 403
        bot.application_info.side_effect = OSError('secret')
        assert (await client.get('/control/bot', headers=headers)).status == 403
    run(server, scenario)


def test_team_member_and_rate_limit(monkeypatch):
    bot = make_bot()
    bot.is_owner = AsyncMock(return_value=True)
    bot.application_info.return_value = SimpleNamespace(owner=SimpleNamespace(id=999),
                                                        team=SimpleNamespace(members=[SimpleNamespace(id=STRANGER, membership_state=discord.TeamMembershipState.accepted)]))
    server = login_server(bot)
    _, headers = cookie(server, STRANGER)
    async def scenario(client):
        assert (await client.get('/control/settings', headers=headers)).status == 200
        bot.application_info.return_value.team.members[0].membership_state = discord.TeamMembershipState.invited
        assert (await client.get('/control/settings', headers=headers)).status == 403
        monkeypatch.setattr(server, '_rate_allowed', lambda key, limit: False)
        assert (await client.get('/login')).status == 429
    run(server, scenario)


def test_control_views_real_cases_and_no_leaked_reasons():
    async def go():
        from aiohttp.test_utils import TestClient, TestServer
        bot = make_bot()
        bot.db = Database(':memory:')
        await bot.db.connect()
        await bot.db.execute('INSERT INTO cases VALUES(?,?,?,?,?,?,?)', GUILD_ID, 1, 10, 20, 'warn', 'secret reason', 1000)
        await bot.db.execute('INSERT INTO cases VALUES(?,?,?,?,?,?,?)', 777, 2, 11, 21, 'ban', 'other secret', 1000)
        server = login_server(bot)
        _, headers = cookie(server, BOT_OWNER)
        client = TestClient(TestServer(server.app))
        await client.start_server()
        try:
            for section in ('security', 'moderation', 'roblox', 'diagnostics', 'bot', 'database', 'settings'):
                result = await client.get(f'/control/{section}', headers=headers)
                assert result.status == 200, section
            result = await client.get(f'/control/moderation?server={GUILD_ID}', headers=headers)
            html = await result.text()
            assert 'warn' in html and 'secret reason' not in html and 'other secret' not in html and '>ban<' not in html
            assert 'SQLite' in await (await client.get('/control/database', headers=headers)).text()
            assert (await client.get('/control/not-a-section', headers=headers)).status == 404
        finally:
            await client.close()
            await bot.db.close()
    asyncio.run(go())


def test_configured_identity_is_independent_of_discord_and_other_sessions(monkeypatch):
    from dataclasses import replace
    import core.dashboard as dashboard
    bot = make_bot()
    server = login_server(bot)
    monkeypatch.setattr(dashboard, 'settings', replace(dashboard.settings, owner_ids=frozenset({STRANGER})))
    bot.application_info.side_effect = OSError('offline')
    first, first_headers = cookie(server, STRANGER)
    _, second_headers = cookie(server, STRANGER)
    async def scenario(client):
        assert (await client.get('/api/guilds', headers=first_headers)).status == 200
        assert (await client.post('/logout', headers=first_headers, data={'csrf': csrf_of(server, first)},
                                  allow_redirects=False)).status == 302
        assert (await client.get('/api/guilds', headers=first_headers)).status == 401
        assert (await client.get('/api/guilds', headers=second_headers)).status == 200
        monkeypatch.setattr(dashboard, 'settings', replace(dashboard.settings, owner_ids=frozenset()))
        assert (await client.get('/api/guilds', headers=second_headers)).status == 403
    run(server, scenario)


def test_old_signed_cookie_and_actual_rate_bucket_are_denied():
    import time
    bot = make_bot()
    server = login_server(bot)
    old = server._sign({'t': 'session', 'uid': BOT_OWNER, 'name': 'legacy', 'csrf': 'x',
                        'local': False, 'exp': int(time.time()) + 1000})
    async def scenario(client):
        headers = {'Cookie': f'{SESSION_COOKIE}={old}'}
        assert (await client.get('/api/guilds', headers=headers)).status == 401
        for _ in range(59):
            assert (await client.get('/login', allow_redirects=False)).status == 302
        assert (await client.get('/login', allow_redirects=False)).status == 429
    run(server, scenario)
