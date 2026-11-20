"""Public Roblox group overview and per-server connection settings."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import hmac
import json
import logging
import time

import aiohttp
from aiohttp import web

from core.dashboard import _esc
from core.dashboard_features import _digest, _revision

log = logging.getLogger("veil.dashboard.roblox")
GROUP_URL = "https://groups.roblox.com/v1/groups/{}"
MAX_RESPONSE = 256 * 1024
MAX_ROLES = 250


def _group_id(raw) -> int | None:
    if not isinstance(raw, str) or not raw.isascii() or not raw.isdigit() or len(raw) > 19:
        return None
    value = int(raw)
    return value if 0 < value < 2**63 else None


async def _read_json(session, url):
    # Fixed Roblox host; never follow a redirect, send credentials, or accept an unbounded body.
    async with session.get(url, allow_redirects=False) as response:
        if response.status != 200 or response.headers.get("Content-Encoding", "identity").lower() != "identity":
            raise ValueError("Roblox did not return data")
        try:
            body = await response.content.readexactly(MAX_RESPONSE + 1)
        except asyncio.IncompleteReadError as exc:
            body = exc.partial
        if len(body) > MAX_RESPONSE:
            raise ValueError("Roblox response too large")
        return json.loads(body)


async def fetch_group(group_id: int) -> dict:
    """Fetch public data only. Missing/malformed data is explicitly shown as unavailable."""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5),
                                     cookie_jar=aiohttp.DummyCookieJar(), trust_env=False, auto_decompress=False,
                                     headers={"Accept-Encoding": "identity"}) as session:
        results = await asyncio.gather(_read_json(session, GROUP_URL.format(group_id)),
                                       _read_json(session, GROUP_URL.format(group_id) + "/roles"),
                                       return_exceptions=True)
    group, roles_doc = results
    result = {"group": None, "roles": None, "truncated": False}
    if (isinstance(group, dict) and group.get("id") == group_id and isinstance(group.get("name"), str)
            and 0 < len(group["name"]) <= 200):
        count = group.get("memberCount")
        result["group"] = {"name": group["name"],
                           "members": count if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else None}
    if (isinstance(roles_doc, dict) and isinstance(roles_doc.get("roles"), list)
            and ("groupId" not in roles_doc or roles_doc["groupId"] == group_id)):
        roles = roles_doc["roles"]
        clean = []
        for role in roles[:MAX_ROLES]:
            if not isinstance(role, dict) or not isinstance(role.get("name"), str) or len(role["name"]) > 200:
                break
            rank, count = role.get("rank"), role.get("memberCount")
            if not isinstance(rank, int) or isinstance(rank, bool) or not 0 <= rank <= 255:
                break
            role_id = role.get("id")
            clean.append({"name": role["name"], "rank": rank,
                          "id": role_id if isinstance(role_id, int) and not isinstance(role_id, bool) and 0 < role_id < 2**63 else None,
                          "members": count if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else None})
        else:
            result["roles"] = sorted(clean, key=lambda r: r["rank"], reverse=True)
            result["truncated"] = len(roles) > MAX_ROLES
    return result


async def cached_group(server, group_id: int) -> dict:
    now = time.monotonic()
    cached = server._roblox_cache.get(group_id)
    if cached and cached[0] > now:
        return cached[1]
    async with server._roblox_locks[group_id % len(server._roblox_locks)]:
        now = time.monotonic()
        cached = server._roblox_cache.get(group_id)
        if cached and cached[0] > now:
            return cached[1]
        try:
            result = await asyncio.wait_for(fetch_group(group_id), timeout=7)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError):
            result = {"group": None, "roles": None, "truncated": False}
        if len(server._roblox_cache) >= 128:
            server._roblox_cache.pop(next(iter(server._roblox_cache)))
        ttl = 60 if result["group"] is not None and result["roles"] is not None else 15
        server._roblox_cache[group_id] = (time.monotonic() + ttl, result)
        return result


async def _authorized(server, request):
    user = server._user(request)
    if user is None:
        return None, None, server._redirect("/login" if server.login_enabled else "/")
    guild = await server._guild_for(request, user)
    if guild is None or not await server._can_manage_security(user, guild):
        return user, None, server._message("Server not found", "Veil isn't in that server, or you cannot manage it.", 404, user)
    return user, guild, None


async def group_page(server, request: web.Request) -> web.Response:
    user, guild, error = await _authorized(server, request)
    if error is not None:
        return error
    cfg = deepcopy(await server.bot.db.get_config(guild.id, "roblox_group"))
    if not isinstance(cfg, dict):
        return server._message("Invalid settings", "Roblox group settings could not be read. No changes were made.", 500, user)
    raw = cfg.get("group_id")
    group_id = _group_id(str(raw)) if raw is not None else None
    rev = _revision(server, guild.id, int(user["uid"]), "roblox_group", cfg)
    data = await cached_group(server, group_id) if group_id else None
    # A network request may have outlived the user's developer access or the stored configuration.
    if await server._guild_for(request, user) is None or not await server._can_manage_security(user, guild):
        return server._message("Access denied", "Your access changed. Please sign in again.", 403, user)
    if await server.bot.db.get_config(guild.id, "roblox_group") != cfg:
        return server._message("Settings changed", "Refresh to see the current group.", 409, user)
    body = ('<div class="page-heading"><div><p class="eyebrow">COMMUNITY · ROBLOX</p>'
            '<h1>Roblox group</h1><p>See public group roles and rank order in one place. '
            'Member role changes require a separately configured Open Cloud key. No member linking or automated enforcement.</p></div></div>')
    if request.query.get("saved") == "1":
        body += '<div class="note ok">Group ID saved. Public data is fetched separately and may be unavailable.</div>'
    body += ('<section class="card"><div class="card-head"><div><h2>Group connection</h2>'
             '<p>Enter a Roblox group ID. Leave it empty to disconnect; this does not change your Roblox group.</p></div></div>'
             f'<form method="post" action="/servers/{guild.id}/roblox">'
             f'<input type="hidden" name="csrf" value="{_esc(user["csrf"])}">'
             f'<input type="hidden" name="revision" value="{_esc(rev)}">'
             '<div class="form-group"><label for="group_id">Roblox group ID</label>'
             f'<input id="group_id" name="group_id" inputmode="numeric" maxlength="19" value="{_esc(raw if raw is not None else "")}" '
             'placeholder="e.g. 1234567"><span class="hint">Only public information is requested from Roblox; no API key is needed. '
             'Saving an ID does not verify group ownership.</span></div><button class="btn" type="submit">Save group</button></form></section>')
    if raw is not None and not group_id:
        body += '<div class="note bad">The saved group ID is invalid. Enter a valid numeric ID or disconnect.</div>'
    elif not group_id:
        body += '<section class="card"><h2>No group connected</h2><p class="hint">Add a group ID above to see its public roles.</p></section>'
    else:
        group, roles = data["group"], data["roles"]
        if group is None:
            body += '<div class="note bad">Group details unavailable. Roblox may be unavailable, rate-limiting requests, or the group ID may not exist. Try again later.</div>'
        else:
            count = group["members"] if group["members"] is not None else "Unavailable"
            body += (f'<section class="card"><p class="eyebrow">GROUP · {_esc(group_id)}</p>'
                     f'<h2>{_esc(group["name"])}</h2><p class="hint">Members: {_esc(count)} · Public data, cached briefly.</p></section>')
        body += '<section class="card"><div class="card-head"><div><h2>Chain of command</h2><p>Public Roblox roles in rank order. Member identities are not loaded.</p></div></div>'
        if roles is None:
            body += '<div class="note bad">Roles unavailable. Try again later; no changes were made.</div>'
        elif not roles:
            body += '<p class="hint">Roblox returned no roles.</p>'
        else:
            body += '<div class="table-wrap"><table><thead><tr><th>Role</th><th>Role ID</th><th>Rank</th><th>Members</th></tr></thead><tbody>'
            for role in roles:
                count = role["members"] if role["members"] is not None else "Unavailable"
                body += f'<tr><td>{_esc(role["name"])}</td><td>{_esc(role.get("id") or "Unavailable")}</td><td>{role["rank"]}</td><td>{_esc(count)}</td></tr>'
            body += '</tbody></table></div>'
            if data["truncated"]:
                body += '<p class="hint">Only the first 250 roles returned by Roblox are shown.</p>'
        body += '</section>'
    from core.dashboard_roblox_roles import form, history
    body += form(server, guild, user, group_id)
    if group_id:
        body += await history(server, guild.id, group_id)
    if await server._guild_for(request, user) is None or not await server._can_manage_security(user, guild):
        return server._message("Access denied", "Your access changed. Please sign in again.", 403, user)
    return server._page("Roblox group", body, user, guild=guild, active="roblox")


async def save_group(server, request: web.Request) -> web.Response:
    user, guild, error = await _authorized(server, request)
    if error is not None:
        return error
    data = await request.post()
    if not server._csrf_ok(user, data.get("csrf")):
        return server._message("Invalid request", "Refresh and try again. Nothing was saved.", 403, user)
    raw = str(data.get("group_id", "")).strip()
    group_id = _group_id(raw) if raw else None
    if raw and group_id is None:
        return server._message("Invalid group ID", "Use a positive numeric Roblox group ID, or leave it empty. Nothing was saved.", 400, user)
    async with server._save_locks[guild.id % len(server._save_locks)]:
        old = await server.bot.db.get_config(guild.id, "roblox_group")
        if not isinstance(old, dict):
            return server._message("Invalid settings", "Roblox group settings could not be read. Nothing was saved.", 500, user)
        revision = server._unsign(data.get("revision"), "feature_revision")
        if (not revision or revision.get("gid") != guild.id or revision.get("uid") != int(user["uid"])
                or revision.get("module") != "roblox_group"
                or not hmac.compare_digest(str(revision.get("digest", "")), _digest(old))):
            return server._message("Settings changed", "Refresh and review the current group. Nothing was saved.", 409, user)
        if await server._guild_for(request, user) is None or not await server._can_manage_security(user, guild):
            return server._message("Access denied", "Your access changed; nothing was saved.", 403, user)
        cfg = deepcopy(old)
        if group_id is None:
            cfg.pop("group_id", None)
        else:
            cfg["group_id"] = group_id
        try:
            await server.bot.db.set_config_committed(guild.id, "roblox_group", cfg)
        except Exception as exc:
            log.warning("Roblox group save not confirmed (%s)", type(exc).__name__)
            return server._message("Save not confirmed", "Refresh and review the group ID before retrying.", 503, user)
    log.info("Dashboard: developer %s set Roblox group %s for guild %s", user["uid"], group_id or "disconnected", guild.id)
    return server._redirect(f"/servers/{guild.id}/roblox?saved=1")
