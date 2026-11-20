"""Single-member Roblox role changes using Open Cloud, with preview and audit journal."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import logging
import re
import secrets
import time
import uuid

import aiohttp

from core.config import settings
from core.dashboard import _esc
from core.dashboard_features import _digest
from core.dashboard_roblox import _authorized, _group_id

log = logging.getLogger('veil.dashboard.roblox')
BASE = 'https://apis.roblox.com/cloud/v2/groups'
MAX_BODY = 256 * 1024


class RobloxError(Exception):
    def __init__(self, status=None):
        self.status = status


def configured(group_id):
    return bool(settings.roblox_open_cloud_api_key and group_id and
                settings.roblox_open_cloud_group_id == group_id)


async def cloud(method, group_id, suffix, *, params=None, body=None):
    """Only caller-constructed fixed endpoints; no cookies, redirects, proxy or retry."""
    if not configured(group_id):
        raise RobloxError()
    timeout = aiohttp.ClientTimeout(total=8)
    async with aiohttp.ClientSession(timeout=timeout, cookie_jar=aiohttp.DummyCookieJar(), trust_env=False,
                                     auto_decompress=False, headers={'Accept-Encoding': 'identity',
                                     'x-api-key': settings.roblox_open_cloud_api_key}) as session:
        async with session.request(method, f'{BASE}/{group_id}/{suffix}', params=params, json=body,
                                   allow_redirects=False) as response:
            if response.status != 200:
                raise RobloxError(response.status)
            if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
                raise RobloxError()
            try:
                raw = await response.content.readexactly(MAX_BODY + 1)
            except asyncio.IncompleteReadError as exc:
                raw = exc.partial
            if len(raw) > MAX_BODY:
                raise RobloxError()
            try:
                result = json.loads(raw)
            except (ValueError, UnicodeError):
                raise RobloxError() from None
            if not isinstance(result, dict):
                raise RobloxError()
            return result


def membership_snapshot(doc, group_id, user_id):
    rows = doc.get('groupMemberships')
    # A filtered lookup must be complete, unique and point to this exact user and group.
    if not isinstance(rows, list) or len(rows) != 1 or doc.get('nextPageToken'):
        raise RobloxError()
    row = rows[0]
    if not isinstance(row, dict) or row.get('user') != f'users/{user_id}':
        raise RobloxError()
    path = row.get('path')
    if not isinstance(path, str) or not re.fullmatch(rf'groups/{group_id}/memberships/[A-Za-z0-9_-]{{1,128}}', path):
        raise RobloxError()
    roles = row.get('roles')
    if not isinstance(roles, list) or len(roles) > 250 or any(
        not isinstance(r, str) or not re.fullmatch(rf'groups/{group_id}/roles/[0-9]{{1,20}}', r) for r in roles
    ) or len(set(roles)) != len(roles):
        raise RobloxError()
    return {'path': path, 'roles': sorted(roles), 'updated': row.get('updateTime')}


async def inspect(group_id, user_id, role_id):
    membership, role = await asyncio.gather(
        cloud('GET', group_id, 'memberships', params={'filter': f"user == 'users/{user_id}'", 'maxPageSize': '2'}),
        cloud('GET', group_id, f'roles/{role_id}'))
    snap = membership_snapshot(membership, group_id, user_id)
    if role.get('path') != f'groups/{group_id}/roles/{role_id}' or not isinstance(role.get('displayName'), str):
        raise RobloxError()
    name = role['displayName']
    if not name or len(name) > 200:
        raise RobloxError()
    return snap, name, _digest(role)


def form(server, guild, user, group_id):
    if not configured(group_id):
        return ('<section class="card"><h2>Manage member roles</h2><p class="hint">Not configured for this group. '
                'A host operator must configure a group-scoped Roblox Open Cloud API key. No key is entered in this dashboard.</p></section>')
    return (f'<section class="card"><h2>Manage member roles</h2><p class="hint">Assign or remove one role. '
            'This does not replace other roles. Review the role permissions in Roblox first; Roblox applies its own hierarchy.</p>'
            f'<form method="post" action="/servers/{guild.id}/roblox/roles">'
            f'<input type="hidden" name="csrf" value="{_esc(user["csrf"])}">'
            '<input type="hidden" name="step" value="preview">'
            '<div class="form-grid"><div class="form-group"><label for="roblox_user">Roblox user ID</label>'
            '<input id="roblox_user" name="user_id" inputmode="numeric" maxlength="19" required></div>'
            '<div class="form-group"><label for="roblox_role">Roblox role ID</label>'
            '<input id="roblox_role" name="role_id" inputmode="numeric" maxlength="19" required></div></div>'
            '<div class="form-group"><label for="roblox_operation">Action</label><select id="roblox_operation" name="operation">'
            '<option value="assign">Assign role</option><option value="unassign">Remove role</option></select></div>'
            '<button class="btn" type="submit">Review change</button></form></section>')


async def handle_role_action(server, request):
    user, guild, error = await _authorized(server, request)
    if error is not None:
        return error
    data = await request.post()
    if not server._csrf_ok(user, data.get('csrf')):
        return server._message('Invalid request', 'Refresh and try again. No role was changed.', 403, user)
    cfg = deepcopy(await server.bot.db.get_config(guild.id, 'roblox_group'))
    group_id = _group_id(str(cfg.get('group_id'))) if isinstance(cfg, dict) and cfg.get('group_id') else None
    if not configured(group_id):
        return server._message('Unavailable', 'Open Cloud is not configured for this connected group. No role was changed.', 503, user)
    step = data.get('step')
    if step == 'preview':
        uid = _group_id(str(data.get('user_id', '')))
        role_id = _group_id(str(data.get('role_id', '')))
        operation = data.get('operation')
        if uid is None or role_id is None or operation not in ('assign', 'unassign'):
            return server._message('Invalid request', 'Enter positive Roblox user and role IDs. No role was changed.', 400, user)
        try:
            snapshot, name, role_digest = await inspect(group_id, uid, role_id)
        except (RobloxError, aiohttp.ClientError, asyncio.TimeoutError, OSError):
            return server._message('Unable to verify', 'Roblox membership or role could not be verified. Check IDs, permissions and API availability. No role was changed.', 503, user)
        if await server._guild_for(request, user) is None or not await server._can_manage_security(user, guild):
            return server._message('Access denied', 'Developer access changed. No role was changed.', 403, user)
        if await server.bot.db.get_config(guild.id, 'roblox_group') != cfg:
            return server._message('Settings changed', 'The connected group changed. Start again; no role was changed.', 409, user)
        role_path = f'groups/{group_id}/roles/{role_id}'
        if (role_path in snapshot['roles']) == (operation == 'assign'):
            return server._message('No change needed', 'The member already has that role, or does not have it for removal. No role was changed.', 409, user)
        now = time.monotonic()
        server._roblox_role_confirmations = {k: v for k, v in server._roblox_role_confirmations.items() if v['expires'] > now}
        if len(server._roblox_role_confirmations) >= 256:
            return server._message('Unavailable', 'Too many pending reviews. Try later. No role was changed.', 503, user)
        token = secrets.token_urlsafe(32)
        server._roblox_role_confirmations[token] = {'uid': user['uid'], 'sid': user['sid'], 'gid': guild.id,
            'group': group_id, 'target': uid, 'role': role_id, 'operation': operation, 'snapshot': snapshot,
            'name': name, 'role_digest': role_digest, 'digest': _digest(cfg), 'expires': now + 300}
        current = ', '.join(r.rsplit('/', 1)[-1] for r in snapshot['roles']) or 'None reported'
        body = (f'<section class="card"><h2>Confirm Roblox role change</h2><p class="hint">Group ID: {_esc(group_id)} · '
                f'User ID: {_esc(uid)} · Current role IDs: {_esc(current)}</p>'
                f'<p class="note">{_esc(operation.title())} role <strong>{_esc(name)}</strong> (ID {_esc(role_id)}). '
                'Other roles are not intentionally changed. Check the role’s permissions in Roblox first.</p>'
                '<p class="hint">The request can succeed even if the response is lost. Do not retry without checking Roblox. '
                'State is checked again but can still change concurrently outside Veil.</p>'
                f'<form method="post" action="/servers/{guild.id}/roblox/roles">'
                f'<input type="hidden" name="csrf" value="{_esc(user["csrf"])}">'
                f'<input type="hidden" name="confirmation" value="{_esc(token)}">'
                '<input type="hidden" name="step" value="confirm">'
                '<button class="btn" type="submit">Confirm role change</button> '
                f'<a class="btn btn-plain" href="/servers/{guild.id}/roblox">Cancel</a></form></section>')
        return server._page('Confirm Roblox role change', body, user, guild=guild, active='roblox')
    if step != 'confirm':
        return server._message('Invalid request', 'No role was changed.', 400, user)
    token = str(data.get('confirmation', ''))
    review = server._roblox_role_confirmations.pop(token, None)
    if (not review or review['uid'] != user['uid'] or review['sid'] != user['sid'] or review['gid'] != guild.id
            or review['group'] != group_id or review['expires'] <= time.monotonic()):
        return server._message('Confirmation expired', 'Review the role change again. No role was changed.', 403, user)
    async with server._roblox_role_locks[group_id % len(server._roblox_role_locks)]:
        try:
            snapshot, name, role_digest = await inspect(group_id, review['target'], review['role'])
        except (RobloxError, aiohttp.ClientError, asyncio.TimeoutError, OSError):
            return server._message('Unable to verify', 'Roblox data could not be rechecked. No role was changed.', 503, user)
        if snapshot != review['snapshot'] or name != review['name'] or role_digest != review['role_digest']:
            return server._message('Membership changed', 'The member or role changed since review. Start again; no role was changed.', 409, user)
        if (await server._guild_for(request, user) is None or not await server._can_manage_security(user, guild)
                or _digest(await server.bot.db.get_config(guild.id, 'roblox_group')) != review['digest']
                or not configured(group_id)):
            return server._message('Access or settings changed', 'Start again; no role was changed.', 403, user)
        action_id = uuid.uuid4().hex
        try:
            await server.bot.db.execute('INSERT INTO roblox_role_actions VALUES(?,?,?,?,?,?,?,?,?)',
                action_id, str(guild.id), str(group_id), str(user['uid']), str(review['target']), str(review['role']),
                review['operation'], 'pending', time.time())
        except Exception as exc:
            log.warning('Roblox action journal unavailable (%s)', type(exc).__name__)
            return server._message('Unavailable', 'Could not record the request. No role was changed.', 503, user)
        # Audit write may have taken time; recheck developer and group configuration immediately before the request.
        if (await server._guild_for(request, user) is None or not await server._can_manage_security(user, guild)
                or _digest(await server.bot.db.get_config(guild.id, 'roblox_group')) != review['digest']
                or not configured(group_id)):
            outcome = 'cancelled'
        else:
            role_path = f'groups/{group_id}/roles/{review["role"]}'
            suffix = snapshot['path'].split('/')[-1] + (':assignRole' if review['operation'] == 'assign' else ':unassignRole')
            try:
                result = await cloud('POST', group_id, f'memberships/{suffix}', body={'role': role_path})
                try:
                    updated = membership_snapshot({'groupMemberships': [result]}, group_id, review['target'])
                    valid = (updated['path'] == snapshot['path'] and
                             (role_path in updated['roles']) == (review['operation'] == 'assign'))
                except RobloxError:
                    valid = False
                outcome = 'confirmed' if valid else 'unknown'
            except RobloxError as exc:
                outcome = 'rejected' if exc.status in (400, 401, 403, 404, 429) else 'unknown'
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
                outcome = 'unknown'
        try:
            await server.bot.db.execute('UPDATE roblox_role_actions SET outcome=? WHERE action_id=?', outcome, action_id)
        except Exception as exc:
            log.warning('Roblox action outcome journal update failed (%s)', type(exc).__name__)
            return server._message('Outcome not fully recorded', 'Check Roblox and the pending action record before retrying.', 503, user)
    log.info('Roblox role action %s by developer %s for guild %s group %s user %s role %s: %s',
             action_id, user['uid'], guild.id, group_id, review['target'], review['role'], outcome)
    if outcome == 'confirmed':
        return server._message('Role change confirmed', 'Roblox returned the requested role state. Review the member in Roblox if needed.', 200, user)
    if outcome == 'cancelled':
        return server._message('Access or settings changed', 'No role request was sent.', 403, user)
    if outcome == 'rejected':
        return server._message('Roblox rejected the request', 'Check the API key, Roblox permissions, hierarchy and rate limits. Review Roblox before retrying.', 400, user)
    return server._message('Outcome unknown', 'Roblox may have applied the request. Check the member in Roblox before retrying; no automatic retry was made.', 503, user)


async def history(server, guild_id, group_id):
    try:
        rows = await server.bot.db.fetchall(
            'SELECT action_id, actor_id, user_id, role_id, operation, outcome, created_at '
            'FROM roblox_role_actions WHERE guild_id=? AND group_id=? ORDER BY created_at DESC LIMIT 20',
            str(guild_id), str(group_id))
    except Exception:
        return '<section class="card"><h2>Recent role actions</h2><p class="hint">Action history unavailable.</p></section>'
    from core.dashboard_control import timestamp
    body = ('<section class="card"><h2>Recent role actions</h2><p class="hint">Latest 20 requests from this Discord server. '
            'Pending or unknown outcomes require manual review in Roblox; this is not Roblox’s complete audit log.</p>'
            '<div class="table-wrap"><table><thead><tr><th>Time</th><th>Developer ID</th><th>User ID</th>'
            '<th>Role ID</th><th>Action</th><th>Outcome</th><th>Reference</th></tr></thead><tbody>')
    for row in rows:
        body += (f'<tr><td>{_esc(timestamp(row["created_at"]))}</td><td>{_esc(row["actor_id"])}</td>'
                 f'<td>{_esc(row["user_id"])}</td><td>{_esc(row["role_id"])}</td><td>{_esc(row["operation"])}</td>'
                 f'<td>{_esc(row["outcome"])}</td><td>{_esc(row["action_id"])}</td></tr>')
    if not rows:
        body += '<tr><td colspan="7">No recorded role requests.</td></tr>'
    return body + '</tbody></table></div></section>'
