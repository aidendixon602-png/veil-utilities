"""Roblox Open Cloud role controls: mocked API, real in-memory database, no production actions."""
import asyncio
from dataclasses import replace
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiohttp.test_utils import TestClient, TestServer

from core import dashboard_roblox_roles as roles
from core.db import Database
from test_dashboard_security import BOT_OWNER, GUILD_ID, GUILD_OWNER, cookie, csrf_of, login_server, make_bot


class API:
    def __init__(self):
        self.user_roles = ['groups/123/roles/10']
        self.calls = []
        self.fail_post = None
        self.after_read = None

    async def __call__(self, method, group, suffix, *, params=None, body=None):
        self.calls.append((method, group, suffix, params, body))
        if method == 'GET' and suffix == 'memberships':
            assert params['filter'] == "user == 'users/42'"
            if self.after_read:
                self.after_read()
            return {'groupMemberships': [{'path': 'groups/123/memberships/NDI', 'user': 'users/42',
                                           'roles': list(self.user_roles), 'updateTime': '2026-01-01'}]}
        if method == 'GET' and suffix == 'roles/20':
            return {'path': 'groups/123/roles/20', 'displayName': '<Manager>'}
        if method == 'POST':
            if self.fail_post:
                raise self.fail_post
            role = body['role']
            if suffix.endswith(':assignRole'):
                self.user_roles.append(role)
            else:
                self.user_roles.remove(role)
            return {'path': 'groups/123/memberships/NDI', 'user': 'users/42', 'roles': list(self.user_roles)}
        raise AssertionError(suffix)


def scenario_run(monkeypatch, scenario):
    async def go():
        bot = make_bot()
        bot.db = Database(':memory:')
        await bot.db.connect()
        await bot.db.set_config_committed(GUILD_ID, 'roblox_group', {'group_id': 123})
        server = login_server(bot)
        monkeypatch.setattr(roles, 'settings', replace(roles.settings, roblox_open_cloud_group_id=123,
                                                       roblox_open_cloud_api_key='sensitive-key-123'))
        api = API()
        monkeypatch.setattr(roles, 'cloud', api)
        token, headers = cookie(server, BOT_OWNER)
        client = TestClient(TestServer(server.app))
        await client.start_server()
        try:
            await scenario(client, server, bot, api, token, headers)
        finally:
            await client.close()
            await bot.db.close()
    asyncio.run(go())


def preview_data(server, token, operation='assign'):
    return {'csrf': csrf_of(server, token), 'step': 'preview', 'user_id': '42', 'role_id': '20', 'operation': operation}


async def preview(client, server, token, headers, operation='assign'):
    result = await client.post(f'/servers/{GUILD_ID}/roblox/roles', headers=headers,
                               data=preview_data(server, token, operation))
    assert result.status == 200
    text = await result.text()
    assert '&lt;Manager&gt;' in text and '<Manager>' not in text and 'sensitive-key-123' not in text
    return re.search(r'name="confirmation" value="([^"]+)"', text).group(1)


async def confirm(client, server, token, headers, confirmation):
    return await client.post(f'/servers/{GUILD_ID}/roblox/roles', headers=headers,
                             data={'csrf': csrf_of(server, token), 'step': 'confirm', 'confirmation': confirmation})


def test_assign_remove_confirmation_history_and_one_shot(monkeypatch):
    async def scenario(client, server, bot, api, token, headers):
        confirmation = await preview(client, server, token, headers)
        assert not any(call[0] == 'POST' for call in api.calls)
        result = await confirm(client, server, token, headers, confirmation)
        assert result.status == 200 and 'Role change confirmed' in await result.text()
        assert api.user_roles == ['groups/123/roles/10', 'groups/123/roles/20']
        posts = [call for call in api.calls if call[0] == 'POST']
        assert len(posts) == 1 and posts[0][2:] == ('memberships/NDI:assignRole', None, {'role': 'groups/123/roles/20'})
        assert (await confirm(client, server, token, headers, confirmation)).status == 403
        confirmation = await preview(client, server, token, headers, 'unassign')
        assert (await confirm(client, server, token, headers, confirmation)).status == 200
        assert api.user_roles == ['groups/123/roles/10']
        rows = await bot.db.fetchall('SELECT operation, outcome FROM roblox_role_actions ORDER BY created_at')
        assert [(r['operation'], r['outcome']) for r in rows] == [('assign', 'confirmed'), ('unassign', 'confirmed')]
        history = await roles.history(server, GUILD_ID, 123)
        assert 'confirmed' in history and '42' in history and 'sensitive-key-123' not in history
    scenario_run(monkeypatch, scenario)


def test_denials_stale_group_and_role_changes(monkeypatch):
    async def scenario(client, server, bot, api, token, headers):
        path = f'/servers/{GUILD_ID}/roblox/roles'
        _, owner_headers = cookie(server, GUILD_OWNER)
        assert (await client.post(path, headers=owner_headers, data=preview_data(server, token))).status == 403
        assert (await client.post(path, headers=headers, data={**preview_data(server, token), 'csrf': 'bad'})).status == 403
        assert (await client.post(path, headers={**headers, 'Origin': 'https://evil.invalid'}, data=preview_data(server, token))).status == 403
        assert (await client.post(path, headers=headers, data={**preview_data(server, token), 'role_id': '../foo'})).status == 400
        assert not api.calls
        confirmation = await preview(client, server, token, headers)
        api.user_roles.append('groups/123/roles/99')
        assert (await confirm(client, server, token, headers, confirmation)).status == 409
        assert not [c for c in api.calls if c[0] == 'POST']
        api.user_roles.remove('groups/123/roles/99')
        confirmation = await preview(client, server, token, headers)
        await bot.db.set_config_committed(GUILD_ID, 'roblox_group', {'group_id': 124})
        assert (await confirm(client, server, token, headers, confirmation)).status == 503
        assert not [c for c in api.calls if c[0] == 'POST']
    scenario_run(monkeypatch, scenario)


def test_unknown_rejected_and_journal_failure_do_not_retry(monkeypatch):
    async def scenario(client, server, bot, api, token, headers):
        confirmation = await preview(client, server, token, headers)
        api.fail_post = asyncio.TimeoutError()
        response = await confirm(client, server, token, headers, confirmation)
        assert response.status == 503 and 'Outcome unknown' in await response.text()
        assert len([c for c in api.calls if c[0] == 'POST']) == 1
        assert (await bot.db.fetchone('SELECT outcome FROM roblox_role_actions'))['outcome'] == 'unknown'
        api.fail_post = roles.RobloxError(403)
        confirmation = await preview(client, server, token, headers)
        assert (await confirm(client, server, token, headers, confirmation)).status == 400
        rows = await bot.db.fetchall('SELECT outcome FROM roblox_role_actions ORDER BY created_at')
        assert [r['outcome'] for r in rows] == ['unknown', 'rejected']
        confirmation = await preview(client, server, token, headers)
        bot.db.execute = AsyncMock(side_effect=OSError('private error'))
        response = await confirm(client, server, token, headers, confirmation)
        assert response.status == 503 and len([c for c in api.calls if c[0] == 'POST']) == 2
    scenario_run(monkeypatch, scenario)


def test_revoked_access_and_malformed_membership(monkeypatch):
    async def scenario(client, server, bot, api, token, headers):
        confirmation = await preview(client, server, token, headers)
        bot.application_info.return_value = SimpleNamespace(owner=SimpleNamespace(id=999), team=None)
        assert (await confirm(client, server, token, headers, confirmation)).status == 403
        assert not [c for c in api.calls if c[0] == 'POST']
    scenario_run(monkeypatch, scenario)
    invalid = {'groupMemberships': [{'path': 'groups/123/memberships/NDI', 'user': 'users/42', 'role': 'groups/123/roles/20'}]}
    import pytest
    with pytest.raises(roles.RobloxError):
        roles.membership_snapshot(invalid, 123, 42)


def test_cloud_http_security_and_bounds(monkeypatch):
    import json
    import pytest

    seen = []
    class Stream:
        def __init__(self, data):
            self.data = data
        async def readexactly(self, n):
            assert n == roles.MAX_BODY + 1
            if len(self.data) < n:
                raise asyncio.IncompleteReadError(self.data, n)
            return self.data[:n]

    class Response:
        status = 200
        headers = {}
        data = b'{}'
        async def __aenter__(self):
            self.content = Stream(self.data)
            return self
        async def __aexit__(self, *args):
            pass

    response = Response()
    class Session:
        def __init__(self, **kwargs):
            seen.append(('session', kwargs))
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        def request(self, *args, **kwargs):
            seen.append(('request', args, kwargs))
            return response

    async def run():
        monkeypatch.setattr(roles, 'settings', replace(roles.settings, roblox_open_cloud_group_id=123,
                                                       roblox_open_cloud_api_key='secret'))
        monkeypatch.setattr(roles.aiohttp, 'ClientSession', Session)
        assert await roles.cloud('POST', 123, 'memberships/NDI:assignRole', body={'role': 'groups/123/roles/20'}) == {}
        assert seen[0][1]['trust_env'] is False and seen[0][1]['auto_decompress'] is False
        assert seen[0][1]['headers']['x-api-key'] == 'secret'
        assert seen[1][1][1] == 'https://apis.roblox.com/cloud/v2/groups/123/memberships/NDI:assignRole'
        assert seen[1][2]['allow_redirects'] is False
        with pytest.raises(roles.RobloxError):
            await roles.cloud('GET', 124, 'roles/20')
        response.status = 302
        with pytest.raises(roles.RobloxError):
            await roles.cloud('GET', 123, 'roles/20')
        response.status = 200
        response.data = b'x' * (roles.MAX_BODY + 1)
        with pytest.raises(roles.RobloxError):
            await roles.cloud('GET', 123, 'roles/20')
        response.data = json.dumps({'ok': True}).encode()
        response.headers = {'Content-Encoding': 'gzip'}
        with pytest.raises(roles.RobloxError):
            await roles.cloud('GET', 123, 'roles/20')
    asyncio.run(run())


def test_config_redacts_roblox_secret():
    from core.config import Settings
    settings = Settings.from_env({'ROBLOX_OPEN_CLOUD_GROUP_ID': '123', 'ROBLOX_OPEN_CLOUD_API_KEY': 'private-roblox-key'})
    assert settings.roblox_open_cloud_group_id == 123
    assert 'private-roblox-key' not in repr(settings)
    assert 'private-roblox-key' not in settings.summary()


def test_reject_membership_path_injection_and_wrong_group():
    import pytest
    for path in ('groups/124/memberships/NDI', 'groups/123/memberships/../roles/20',
                 'groups/123/memberships/NDI:unassignRole', 'groups/123/memberships/%2f'):
        with pytest.raises(roles.RobloxError):
            roles.membership_snapshot({'groupMemberships': [{'path': path, 'user': 'users/42', 'roles': []}]}, 123, 42)
