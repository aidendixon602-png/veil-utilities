"""Developer control center views backed by existing bot state and database records."""
from __future__ import annotations

from datetime import datetime, timezone

from aiohttp import web

from core.dashboard import _esc

SECTIONS = {
    'overview': ('Overview', '/', 'overview'),
    'servers': ('Server Management', '/servers', 'general'),
    'security': ('Security Center', '/control/security', 'rules'),
    'moderation': ('Moderation Center', '/control/moderation', 'trusted'),
    'roblox': ('Roblox Security', '/control/roblox', 'roblox'),
    'diagnostics': ('Logs & Diagnostics', '/control/diagnostics', 'logging'),
    'bot': ('Bot Management', '/control/bot', 'automod'),
    'database': ('Database & Backups', '/control/database', 'logging'),
    'settings': ('Developer Settings', '/control/settings', 'access'),
}


def navigation(active: str) -> str:
    from core.dashboard import _dashboard_icon
    out = '<span class="nav-label">DEVELOPER CONTROL CENTER</span>'
    for key, (title, href, icon) in SECTIONS.items():
        selected = ' active" aria-current="page' if key == active else ''
        out += (f'<a class="nav-link{selected}" href="{href}" title="{_esc(title)}" aria-label="{_esc(title)}"><span class="nav-icon" aria-hidden="true">'
                f'{_dashboard_icon(icon)}</span><span class="nav-text">{title}</span></a>')
    return out


def heading(title, description):
    return (f'<div class="page-heading"><div><p class="eyebrow">VEIL / DEVELOPER CONTROL CENTER</p>'
            f'<h1>{_esc(title)}</h1><p>{_esc(description)}</p></div></div>')


def card(title, value, detail):
    return (f'<div class="stat-card"><div class="stat-lbl">{_esc(title)}</div><div class="stat-val">{_esc(value)}</div>'
            f'<div class="stat-foot">{_esc(detail)}</div></div>')


def timestamp(value):
    try:
        return datetime.fromtimestamp(float(value), timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    except (TypeError, ValueError, OverflowError, OSError):
        return 'Unavailable'


def selector(guilds, selected, section):
    options = '<option value="">Choose a server</option>'
    for guild in sorted(guilds, key=lambda g: (g.name.casefold(), g.id)):
        options += (f'<option value="{guild.id}"{" selected" if guild.id == selected else ""}>'
                    f'{_esc(guild.name)} · {guild.id}</option>')
    return (f'<form class="control-picker" method="get" action="/control/{section}">'
            f'<div class="form-group"><label for="server">Discord server</label><select id="server" name="server">{options}</select></div>'
            '<button class="btn" type="submit">Open server</button></form>')


def select_guild(server, request):
    raw = request.query.get('server', '')
    if not raw:
        return None, None
    if not raw.isascii() or not raw.isdigit() or len(raw) > 20:
        return None, 'Invalid server selection.'
    guild = server.bot.get_guild(int(raw))
    return (guild, None) if guild else (None, 'The bot is not in that server.')


async def overview(server, user):
    import time
    guilds = list(server.bot.guilds)
    check = getattr(server.bot, 'is_authorized', None)
    auth = str(sum(bool(check(g.id)) for g in guilds)) if callable(check) else 'Unavailable'
    ready = getattr(server.bot, 'is_ready', None)
    state = ('Ready' if ready() else 'Not ready') if callable(ready) else 'Unavailable'
    started = getattr(server.bot, 'started_at', None)
    if isinstance(started, (int, float)) and not isinstance(started, bool) and 0 < started <= time.time():
        minutes = int((time.time() - started) / 60)
        uptime = f'{minutes // 60}h {minutes % 60}m'
    else:
        uptime = 'Unavailable'
    try:
        row = await server.bot.db.fetchone("SELECT COUNT(*) AS count FROM security_incidents WHERE state IN ('open','containing')")
        incidents = str(row['count'])
    except Exception:
        incidents = 'Unavailable'
    return (heading('Developer overview', 'An operational starting point for your Veil deployment.') +
            '<div class="stats">' + card('Gateway status', state, 'Local bot readiness, not an external health check') +
            card('Bot uptime', uptime, 'Since this bot process started') + card('Joined servers', str(len(guilds)), 'Gateway cache') +
            card('Authorized servers', auth, 'Joined servers currently authorized') +
            card('Open incident records', incidents, 'Stored open/containing records; not a live alert count') + '</div>' +
            '<div class="control-grid"><section class="card"><div class="card-head"><div><h2>Workspace</h2>'
            '<p>Manage your servers using the existing Veil configuration.</p></div></div>'
            '<div class="control-links"><a href="/servers">Server inventory <span>↗</span></a>'
            '<a href="/authorization">Server authorization <span>↗</span></a>'
            '<a href="/control/security">Security center <span>↗</span></a>'
            '<a href="/control/roblox">Roblox groups <span>↗</span></a></div></section>'
            '<section class="card"><div class="card-head"><div><h2>Operational context</h2>'
            '<p>Read before taking action</p></div></div><p class="hint">Protection is best effort. Discord permissions, '
            'hierarchy, audit delivery and API availability affect detection and recovery. '
            'Roblox monitoring, backups and recovery are not implemented.</p>'
            '<p class="hint">Live API health, recent developer activity and backup history are not available here.</p></section></div>')


async def page(server, request: web.Request, section: str | None = None) -> web.Response:
    user = server._user(request)
    if not user:
        return server._redirect('/login')
    if not await server._is_bot_owner(user):
        return server._message('Access denied', 'Only verified bot developers can use this dashboard.', 403)
    section = section or request.match_info.get('section')
    if section not in SECTIONS or section == 'servers':
        raise web.HTTPNotFound()
    title = SECTIONS[section][0]
    if section == 'overview':
        body = await overview(server, user)
    elif section in ('security', 'moderation', 'roblox', 'diagnostics'):
        guild, error = select_guild(server, request)
        body = heading(title, 'Select a joined server to inspect the information available in Veil.')
        body += '<section class="card">' + selector(server.bot.guilds, guild.id if guild else None, section)
        if error:
            body += f'<div class="note bad">{_esc(error)}</div>'
        elif not guild:
            body += '<p class="hint">Choose a server to continue.</p>'
        else:
            gid = guild.id
            body += f'<h2>{_esc(guild.name)}</h2><p class="hint">Server ID: {gid}</p>'
            if section == 'security':
                body += (f'<div class="control-links"><a href="/servers/{gid}/features?section=rules">Antinuke and trust settings ↗</a>'
                         f'<a href="/servers/{gid}">Logging and security settings ↗</a></div>'
                         '<p class="hint">Anti-raid management and incident recovery are not available in this dashboard. '
                         'Use existing Discord commands where supported. Settings do not guarantee protection.</p>')
                body += await incidents(server, gid)
            elif section == 'roblox':
                body += (f'<div class="control-links"><a href="/servers/{gid}/roblox">Open Roblox group connection ↗</a></div>'
                         '<p class="hint">Public group metadata only. Group monitoring, raid detection, lockdown, '
                         'backups and recovery are not implemented; no credentials are stored.</p>')
            elif section == 'diagnostics':
                body += (f'<div class="control-links"><a href="/servers/{gid}">Configure log routing ↗</a></div>'
                         '<p class="hint">Discord log channels remain the source for event logs. A searchable log archive '
                         'and API error history are not available in this dashboard.</p>')
            else:
                body += await cases(server, request, gid)
        body += '</section>'
    elif section == 'bot':
        cogs = getattr(server.bot, 'cogs', None)
        names = sorted(str(name) for name in cogs) if cogs is not None else None
        shown = ''.join(f'<li>{_esc(name)}</li>' for name in names or [])
        body = (heading(title, 'Inspect bot process state without exposing host controls.') +
                '<section class="card"><h2>Loaded cogs</h2>' +
                (f'<ul>{shown}</ul>' if shown else '<p class="hint">No loaded cogs reported.</p>' if names is not None
                 else '<p class="hint">Cog inventory unavailable.</p>') +
                '<p class="hint">Restart, shutdown, shell access and maintenance controls are not exposed here.</p></section>')
    elif section == 'database':
        from core.db import Database, PostgresDatabase
        db = server.bot.db
        kind = 'SQLite' if isinstance(db, Database) else 'PostgreSQL' if isinstance(db, PostgresDatabase) else 'Unknown'
        try:
            await db.fetchone('SELECT 1 AS ok')
            status = 'Query succeeded'
        except Exception:
            status = 'Query unavailable'
        body = (heading(title, 'Read-only database connectivity and backup information.') +
                f'<section class="card"><h2>Database</h2><p class="hint">Backend: {_esc(kind)} · {_esc(status)}. '
                'This query is not a full integrity check.</p><p class="hint">Database backups and restore controls are not '
                'available in the dashboard. Use your established operator backup procedure.</p></section>')
    else:
        body = (heading(title, 'Your developer session and access boundaries.') +
                f'<section class="card"><h2>Current session</h2><p class="hint">Discord user ID: {_esc(user["uid"])} · '
                'Expires within 12 hours. Logging out revokes this session in this process.</p>'
                '<p class="hint">Developer identities come from bot configuration and Discord application ownership or team membership. '
                'They cannot be changed here. Session inventory and remote session revocation are not available.</p></section>')
    if not await server._is_bot_owner(user):
        return server._message('Access denied', 'Developer access changed.', 403)
    return server._page(title, body, user, active=section, developer=True)


async def cases(server, request, guild_id):
    raw_page = request.query.get('page', '1')
    page_num = min(int(raw_page), 10000) if raw_page.isascii() and raw_page.isdigit() and len(raw_page) <= 5 else 1
    page_num = max(page_num, 1)
    try:
        rows = await server.bot.db.fetchall(
            'SELECT case_num, user_id, mod_id, action, created_at FROM cases WHERE guild_id=? '
            'ORDER BY case_num DESC LIMIT ? OFFSET ?', guild_id, 26, (page_num - 1) * 25)
    except Exception:
        return '<div class="note bad">Case history is unavailable. No action was taken.</div>'
    html = ('<h2>Moderation cases</h2><p class="hint">Stored case records only; not all Discord moderation actions create a case. '
            'Reasons are omitted from this view.</p><div class="table-wrap"><table><thead><tr>'
            '<th>Case</th><th>Action</th><th>Target ID</th><th>Moderator ID</th><th>Created</th></tr></thead><tbody>')
    for row in rows[:25]:
        html += (f'<tr><td>{_esc(row["case_num"])}</td><td>{_esc(row["action"])}</td><td>{_esc(row["user_id"])}</td>'
                 f'<td>{_esc(row["mod_id"])}</td><td>{_esc(timestamp(row["created_at"]))}</td></tr>')
    if not rows:
        html += '<tr><td colspan="5">No stored cases on this page.</td></tr>'
    html += '</tbody></table></div><div class="control-pagination">'
    if page_num > 1:
        html += f'<a class="btn btn-plain" href="?server={guild_id}&amp;page={page_num - 1}">Previous</a>'
    if len(rows) > 25 and page_num < 10000:
        html += f'<a class="btn btn-plain" href="?server={guild_id}&amp;page={page_num + 1}">Next</a>'
    return html + '</div><p class="hint">Moderation actions and bulk operations are not exposed here.</p>'


async def incidents(server, guild_id):
    try:
        rows = await server.bot.db.fetchall(
            'SELECT incident_id, kind, severity, state, created_at FROM security_incidents WHERE guild_id=? '
            'ORDER BY created_at DESC LIMIT 25', str(guild_id))
    except Exception:
        return '<div class="note bad">Security incident history is unavailable.</div>'
    html = ('<h2>Recent incident records</h2><p class="hint">Latest 25 records for this server; '
            'incident state does not prove whether a threat is ongoing. Details and delivery state are not shown.</p>'
            '<div class="table-wrap"><table><thead><tr><th>Incident ID</th><th>Kind</th><th>Severity</th>'
            '<th>Stored state</th><th>Created</th></tr></thead><tbody>')
    for row in rows:
        html += (f'<tr><td>{_esc(row["incident_id"])}</td><td>{_esc(row["kind"])}</td>'
                 f'<td>{_esc(row["severity"])}</td><td>{_esc(row["state"])}</td>'
                 f'<td>{_esc(timestamp(row["created_at"]))}</td></tr>')
    if not rows:
        html += '<tr><td colspan="5">No stored incidents.</td></tr>'
    return html + '</tbody></table></div>'
