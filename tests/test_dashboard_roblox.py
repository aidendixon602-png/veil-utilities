"""Read-only Roblox integration, without contacting Roblox or Discord."""
import asyncio
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core import dashboard_roblox as roblox
from test_dashboard_security import (
    BOT_OWNER, GUILD_ID, GUILD_OWNER, OTHER_GUILD_ID, STRANGER,
    cookie, csrf_of, login_server, make_bot, run,
)


def revision(html):
    match = re.search(r'name="revision" value="([^"]+)"', html)
    assert match
    return match.group(1)


def test_connect_disconnect_csrf_stale_and_authorization(monkeypatch):
    bot = make_bot()
    server = login_server(bot)
    fetch = AsyncMock(return_value={"group": {"name": '<script>bad</script>', "members": 52},
                                    "roles": [{"name": '<b>Boss</b>', "rank": 255, "members": None}],
                                    "truncated": False})
    monkeypatch.setattr(roblox, "fetch_group", fetch)
    token, headers = cookie(server, BOT_OWNER)
    _, stranger = cookie(server, STRANGER)
    _, developer = cookie(server, BOT_OWNER)
    path = f"/servers/{GUILD_ID}/roblox"

    async def scenario(client):
        assert (await client.get(path, allow_redirects=False)).status == 302
        assert (await client.get(path, headers=stranger)).status == 403
        _, owner = cookie(server, GUILD_OWNER)
        assert (await client.get(f"/servers/{OTHER_GUILD_ID}/roblox", headers=owner)).status == 403
        assert (await client.get(f"/servers/{OTHER_GUILD_ID}/roblox", headers=developer)).status == 200
        page = await client.get(path, headers=headers)
        html = await page.text()
        assert 'No group connected' in html and 'Roblox group' in html
        rev = revision(html)
        form = {"csrf": csrf_of(server, token), "revision": rev, "group_id": "123"}
        assert (await client.post(path, headers={**headers, "Origin": "https://evil.example"}, data=form)).status == 403
        assert (await client.post(path, headers=headers, data={**form, "csrf": "bad"})).status == 403
        for invalid in ["0", "-1", "１２３", "1/roles", "9223372036854775808", "1" * 20]:
            assert (await client.post(path, headers=headers, data={**form, "group_id": invalid})).status == 400
        assert bot.db.writes == []
        response = await client.post(path, headers=headers, data=form, allow_redirects=False)
        assert response.status == 302
        assert bot.db.store[(GUILD_ID, "roblox_group")] == {"group_id": 123}
        assert (await client.post(path, headers=headers, data=form)).status == 409
        page = await client.get(path, headers=headers)
        html = await page.text()
        assert '&lt;script&gt;bad&lt;/script&gt;' in html and '<script>bad</script>' not in html
        assert '&lt;b&gt;Boss&lt;/b&gt;' in html and 'Unavailable' in html and '52' in html
        await client.get(path, headers=headers)
        assert fetch.await_count == 1
        form.update(revision=revision(html), group_id="")
        response = await client.post(path, headers=headers, data=form, allow_redirects=False)
        assert response.status == 302
        assert bot.db.store[(GUILD_ID, "roblox_group")] == {}
        assert 'No group connected' in await (await client.get(path, headers=headers)).text()

    run(server, scenario)


def test_fetch_parsing_partial_failure_and_order(monkeypatch):
    async def read(session, url):
        if url.endswith('/roles'):
            return {"groupId": 123, "roles": [{"name": "Member", "rank": 1, "memberCount": 9},
                                               {"name": "Owner", "rank": 255, "memberCount": 1}]}
        return {"id": 123, "name": "Group", "memberCount": 10}

    monkeypatch.setattr(roblox, "_read_json", read)
    data = asyncio.run(roblox.fetch_group(123))
    assert data['group'] == {"name": "Group", "members": 10}
    assert [r['rank'] for r in data['roles']] == [255, 1]

    async def failed_group(session, url):
        if not url.endswith('/roles'):
            raise ValueError("HTTP failure")
        return {"groupId": 123, "roles": [{"name": "Member", "rank": 1}]}

    monkeypatch.setattr(roblox, "_read_json", failed_group)
    data = asyncio.run(roblox.fetch_group(123))
    assert data['group'] is None and data['roles'][0]['members'] is None

    async def bad_roles(session, url):
        return {"groupId": 999, "roles": []} if url.endswith('/roles') else {"id": 456, "name": "wrong"}

    monkeypatch.setattr(roblox, "_read_json", bad_roles)
    data = asyncio.run(roblox.fetch_group(123))
    assert data['group'] is None and data['roles'] is None


def test_response_size_redirect_and_fixed_urls(monkeypatch):
    class Content:
        def __init__(self, body):
            self.body = body

        async def readexactly(self, n):
            if len(self.body) < n:
                raise asyncio.IncompleteReadError(self.body, n)
            return self.body[:n]

    class Response:
        def __init__(self, status, body):
            self.status, self.content = status, Content(body)
            self.headers = {}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    class Session:
        def __init__(self, response):
            self.response = response

        def get(self, url, **kwargs):
            assert kwargs == {"allow_redirects": False}
            return self.response

    assert asyncio.run(roblox._read_json(Session(Response(200, b'{"ok":true}')), 'url')) == {"ok": True}
    with pytest.raises(ValueError):
        asyncio.run(roblox._read_json(Session(Response(302, b'{}')), 'url'))
    with pytest.raises(ValueError):
        asyncio.run(roblox._read_json(Session(Response(200, b'x' * (roblox.MAX_RESPONSE + 1))), 'url'))
    assert roblox.GROUP_URL.format(123) == 'https://groups.roblox.com/v1/groups/123'


def test_failure_states_revocation_and_uncertain_save(monkeypatch):
    bot = make_bot()
    server = login_server(bot)
    bot.db.store[(GUILD_ID, 'roblox_group')] = {'group_id': 123}
    fetch = AsyncMock(side_effect=asyncio.TimeoutError)
    monkeypatch.setattr(roblox, 'fetch_group', fetch)
    token, headers = cookie(server, BOT_OWNER)
    path = f'/servers/{GUILD_ID}/roblox'

    async def scenario(client):
        response = await client.get(path, headers=headers)
        text = await response.text()
        assert response.status == 200 and 'Group details unavailable' in text and 'Roles unavailable' in text
        form = {'csrf': csrf_of(server, token), 'revision': revision(text), 'group_id': '456'}
        bot.db.set_config_committed = AsyncMock(side_effect=OSError('secret'))
        response = await client.post(path, headers=headers, data=form)
        assert response.status == 503 and 'Save not confirmed' in await response.text()
        assert bot.db.store[(GUILD_ID, 'roblox_group')] == {'group_id': 123}
        bot.application_info.return_value = SimpleNamespace(owner=SimpleNamespace(id=999), team=None)
        assert (await client.get(path, headers=headers)).status in (403, 404)
        assert (await client.post(path, headers=headers, data=form)).status in (403, 404)
        assert bot.db.set_config_committed.await_count == 1

    run(server, scenario)


def test_concurrent_saves_and_access_revoked_during_lookup(monkeypatch):
    bot = make_bot()
    server = login_server(bot)
    token, headers = cookie(server, BOT_OWNER)
    path = f'/servers/{GUILD_ID}/roblox'

    async def scenario(client):
        page = await client.get(path, headers=headers)
        form = {'csrf': csrf_of(server, token), 'revision': revision(await page.text()), 'group_id': '123'}
        responses = await asyncio.gather(*(client.post(path, headers=headers, data=form, allow_redirects=False)
                                           for _ in range(2)))
        assert sorted(r.status for r in responses) == [302, 409]
        assert bot.db.writes == [(GUILD_ID, 'roblox_group')]

        async def revoke(group_id):
            bot.application_info.return_value = SimpleNamespace(owner=SimpleNamespace(id=999), team=None)
            return {'group': {'name': 'private view', 'members': 1}, 'roles': [], 'truncated': False}

        monkeypatch.setattr(roblox, 'fetch_group', revoke)
        response = await client.get(path, headers=headers)
        assert response.status == 403 and 'private view' not in await response.text()

    run(server, scenario)


def test_malformed_and_truncated_roles(monkeypatch):
    async def read(session, url):
        if url.endswith('/roles'):
            return {'roles': [{'name': 'Member', 'rank': 1, 'memberCount': -1}] * 251}
        return {'id': 123, 'name': 'Group', 'memberCount': True}

    monkeypatch.setattr(roblox, '_read_json', read)
    data = asyncio.run(roblox.fetch_group(123))
    assert data['group']['members'] is None
    assert len(data['roles']) == 250 and data['truncated']
    assert data['roles'][0]['members'] is None

    async def malformed(session, url):
        if url.endswith('/roles'):
            return {'roles': [{'name': 'bad', 'rank': True}]}
        return {'id': 123, 'name': 'Group'}

    monkeypatch.setattr(roblox, '_read_json', malformed)
    assert asyncio.run(roblox.fetch_group(123))['roles'] is None
