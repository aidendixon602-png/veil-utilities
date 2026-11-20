"""Per-server web dashboard for Veil Utilities.

Security model
--------------
* Off unless ``DASHBOARD_ENABLED=true``.
* A real listener requires HTTPS and Discord login; it never starts in unauthenticated local mode.
* Discord OAuth identifies the user. Only Veil bot developers may use any dashboard page or API;
  server ownership, Discord Administrator and bot permission levels never grant dashboard access.
* Login cookies are HMAC-signed, every form carries a CSRF token, cross-site POSTs are refused, and every value
  written into HTML is escaped.
"""
from __future__ import annotations

import asyncio
import base64
from copy import deepcopy
import hashlib
import hmac
import html
import ipaddress
import json
import logging
import re
import secrets
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode, urlsplit

from core.dashboard_assets import CSS, DASHBOARD_SCRIPT
from core import perms
from core.config import settings

import aiohttp
import discord
from aiohttp import web

if TYPE_CHECKING:
    from core.bot import Veil
    from core.config import Settings

log = logging.getLogger("veil.dashboard")

DISCORD_API = "https://discord.com/api/v10"
DISCORD_AUTHORIZE = "https://discord.com/oauth2/authorize"

SESSION_COOKIE = "veil_session"
STATE_COOKIE = "veil_oauth_state"
SESSION_TTL = 12 * 3600
STATE_TTL = 10 * 60

PUNISHMENTS = ("ban", "kick", "strip")
MAX_IDS = 100
LOCAL_HOSTNAMES = {"127.0.0.1", "localhost", "::1"}
# typed request-storage key on newer aiohttp, plain string on older versions
NEW_SESSION_KEY: Any = web.RequestKey("veil_new_session", str) if hasattr(web, "RequestKey") else "veil_new_session"
LOG_FIELDS = (("log_joins", "joinleave"), ("log_commands", "commands"), ("log_server", "server"),
              ("log_antinuke", "antinuke"), ("log_messages", "messages"), ("log_members", "members"),
              ("log_roles", "roles"), ("log_channels", "channels"), ("log_voice", "voice"),
              ("log_bans", "bans"), ("log_timeouts", "timeouts"))

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; script-src 'self'; img-src https://cdn.discordapp.com; "
        "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


# Fixed inline icons avoid external resources, font-glyph gaps and user-controlled markup.
_ICON_PATHS = {
    "overview": '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    "access": '<path d="M5 11h14v10H5z"/><path d="M8 11V8a4 4 0 0 1 8 0v3m-4 4v2"/>',
    "general": '<circle cx="12" cy="12" r="3"/><path d="M12 2v3m0 14v3M2 12h3m14 0h3M5 5l2 2m10 10 2 2M19 5l-2 2M7 17l-2 2"/>',
    "rules": '<path d="M12 2 20 5v6c0 5-3 8-8 11-5-3-8-6-8-11V5l8-3Z"/><path d="m9 12 2 2 4-5"/>',
    "automod": '<path d="m12 2 1.8 6.2L20 10l-6.2 1.8L12 18l-1.8-6.2L4 10l6.2-1.8L12 2ZM19 15l.7 2.3L22 18l-2.3.7L19 21l-.7-2.3L16 18l2.3-.7L19 15Z"/>',
    "logging": '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/>',
    "exclusions": '<circle cx="12" cy="12" r="9"/><path d="m6 6 12 12"/>',
    "trusted": '<circle cx="12" cy="8" r="3"/><path d="M5 21v-2a7 7 0 0 1 14 0v2"/>',
    "leveling": '<path d="M4 19h16M6 16v-3m6 3V9m6 7V5"/><path d="m5 9 5-4 4 2 5-4"/>',
    "tickets": '<path d="M3 7h18v3a2 2 0 0 0 0 4v3H3v-3a2 2 0 0 0 0-4V7Z"/><path d="M12 8v2m0 4v2"/>',
    "roblox": '<path d="M5 5 18 2l3 13-13 3L5 5Z"/><path d="m11 8 4-1 1 4-4 1-1-4Z"/>',
    "blacklist": '<path d="M12 3 3 20h18L12 3Z"/><path d="M12 9v4m0 3v.1"/>',
}


def _dashboard_icon(name: str) -> str:
    paths = _ICON_PATHS.get(name, _ICON_PATHS["overview"])
    return ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" '
            f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{paths}</svg>')


def _server_avatar(guild) -> str:
    """Render an actual Discord CDN icon when available; never trust an arbitrary image URL."""
    icon = getattr(guild, "icon", None)
    if icon:
        url = str(icon)
        pattern = rf"https://cdn\.discordapp\.com/icons/{int(guild.id)}/[a-zA-Z0-9_]+\.(?:png|jpg|jpeg|webp|gif)(?:\?size=\d+)?"
        if re.fullmatch(pattern, url):
            return f'<img class="server-icon" src="{_esc(url)}" alt="" loading="lazy" referrerpolicy="no-referrer">'
    return f'<span class="server-initial" aria-hidden="true">{_esc(guild.name[:1].upper() or "?")}</span>'


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def _parse_ids(raw: str) -> list[int]:
    """Parse a list without silently deleting invalid entries or truncating existing settings."""
    out: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isascii() or not part.isdigit() or len(part) > 20 or not 0 < int(part) < 2**64:
            raise ValueError("IDs must be positive Discord IDs separated by commas")
        value = int(part)
        if value not in out:
            out.append(value)
            if len(out) > MAX_IDS:
                raise ValueError(f"Use at most {MAX_IDS} IDs")
    return out


class DashboardServer:
    def __init__(
        self,
        bot: "Veil",
        port: int = 8080,
        *,
        host: str | None = None,
        public_url: str | None = None,
        client_id: int | str | None = None,
        client_secret: str | None = None,
        secret: str | None = None,
    ):
        self.bot = bot
        self.port = port
        self.login_enabled = bool(client_id and client_secret and public_url)
        self.local_mode = not self.login_enabled
        self.client_id = str(client_id) if client_id else None
        self.client_secret = client_secret
        self.public_url = public_url.rstrip("/") if public_url else None
        self.redirect_uri = f"{self.public_url}/callback" if self.public_url else None
        self.secure_cookies = bool(self.public_url and self.public_url.startswith("https://"))

        # Default to loopback even with login: only a trusted TLS proxy should reach the app directly.
        wanted = host or "127.0.0.1"
        if self.local_mode and not _is_loopback(wanted):
            log.warning("Dashboard: ignoring DASHBOARD_HOST=%s. Without Discord login configured it only listens on "
                        "127.0.0.1 (set DISCORD_CLIENT_SECRET and DASHBOARD_URL to make it public)",
                        wanted)
            wanted = "127.0.0.1"
        self.host = wanted

        self._secret = (secret or secrets.token_hex(32)).encode()
        self._pending_revocations: dict[str, tuple[int, int, float]] = {}
        self._sessions: dict[str, tuple[int, int]] = {}
        self._request_limits: dict[str, list[float]] = {}
        self._save_locks = [asyncio.Lock() for _ in range(64)]
        self._roblox_locks = [asyncio.Lock() for _ in range(32)]
        self._roblox_cache: dict[int, tuple[float, dict]] = {}
        self._roblox_role_confirmations: dict[str, dict] = {}
        self._roblox_role_locks = [asyncio.Lock() for _ in range(32)]
        self._http_session: aiohttp.ClientSession | None = None
        self.app = web.Application(middlewares=[self._security_middleware])
        self.runner: web.AppRunner | None = None
        self._setup_routes()

    @classmethod
    def from_settings(cls, bot: "Veil", settings: "Settings") -> "DashboardServer":
        return cls(
            bot,
            port=settings.dashboard_port,
            host=settings.dashboard_host,
            public_url=settings.dashboard_url,
            client_id=settings.discord_client_id or getattr(bot, "application_id", None),
            client_secret=settings.discord_client_secret,
            secret=settings.dashboard_secret,
        )

    def _setup_routes(self):
        r = self.app.router
        r.add_get("/assets/dashboard.js", self.handle_script)
        r.add_get("/", self.handle_control_overview)
        r.add_get("/control/{section}", self.handle_control)
        r.add_get("/servers", self.handle_home)
        r.add_get("/servers/{guild_id}", self.handle_server)
        r.add_get("/servers/{guild_id}/overview", self.handle_server_overview)
        r.add_get("/servers/{guild_id}/roblox", self.handle_roblox)
        r.add_post("/servers/{guild_id}/roblox", self.handle_roblox_save)
        r.add_post("/servers/{guild_id}/roblox/roles", self.handle_roblox_role)
        r.add_get("/servers/{guild_id}/features", self.handle_features)
        r.add_post("/servers/{guild_id}/features/{feature}", self.handle_save_feature)
        r.add_get("/authorization", self.handle_authorization)
        r.add_post("/authorization", self.handle_authorization_action)
        r.add_get("/login", self.handle_login)
        r.add_get("/callback", self.handle_callback)
        r.add_post("/logout", self.handle_logout)
        r.add_get("/api/guilds", self.handle_api_guilds)
        r.add_get("/api/guilds/{guild_id}/config", self.handle_api_get_config)
        r.add_post("/api/guilds/{guild_id}/config", self.handle_api_post_config)

    # ------------------------------------------------------------------ signed cookies
    def _sign(self, payload: dict) -> str:
        body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
        mac = hmac.new(self._secret, body.encode(), hashlib.sha256).hexdigest()
        return f"{body}.{mac}"

    def _unsign(self, token: str | None, kind: str) -> dict | None:
        if not token or "." not in token:
            return None
        body, mac = token.rsplit(".", 1)
        expected = hmac.new(self._secret, body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(mac, expected):
            return None
        try:
            payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        except (ValueError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("t") != kind or payload.get("exp", 0) < time.time():
            return None
        return payload

    def make_session(self, uid: int, name: str, *, local: bool = False) -> str:
        now = int(time.time())
        self._sessions = {sid: entry for sid, entry in self._sessions.items() if entry[1] > now}
        if len(self._sessions) >= 4096:
            raise RuntimeError("Dashboard session capacity reached")
        sid = secrets.token_urlsafe(32)
        expiry = now + SESSION_TTL
        self._sessions[sid] = (int(uid), expiry)
        return self._sign({
            "t": "session", "uid": int(uid), "name": name, "local": local, "sid": sid,
            "csrf": secrets.token_urlsafe(24), "exp": expiry,
        })

    def _set_cookie(self, resp: web.StreamResponse, name: str, value: str, max_age: int) -> None:
        resp.set_cookie(name, value, max_age=max_age, path="/", httponly=True, samesite="Lax",
                        secure=self.secure_cookies)

    def _user(self, request: web.Request) -> dict | None:
        """Accept authenticated Discord sessions only; local mode never grants access."""
        if not self.login_enabled:
            return None
        user = self._unsign(request.cookies.get(SESSION_COOKIE), "session")
        if not user or user.get("local"):
            return None
        sid = user.get("sid")
        if not isinstance(sid, str) or self._sessions.get(sid) != (user.get("uid"), user.get("exp")):
            return None
        return user

    # ------------------------------------------------------------------ middleware
    def _origin(self, request: web.Request) -> str:
        if self.public_url:
            parts = urlsplit(self.public_url)
            return f"{parts.scheme}://{parts.netloc}"
        return f"{request.scheme}://{request.host}"

    def _rate_allowed(self, key: str, limit: int) -> bool:
        now = time.monotonic()
        self._request_limits = {k: [t for t in times if t > now - 60]
                                for k, times in self._request_limits.items() if times and times[-1] > now - 60}
        if key not in self._request_limits and len(self._request_limits) >= 1024:
            return False
        bucket = self._request_limits.setdefault(key, [])
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True

    @web.middleware
    async def _security_middleware(self, request: web.Request, handler):
        try:
            if self.local_mode:
                hostname = urlsplit(f"//{request.host or ''}").hostname or ""
                if hostname not in LOCAL_HOSTNAMES:
                    return self._finish(request, web.Response(
                        status=421, text=f"Open the dashboard at http://127.0.0.1:{self.port}"))
            user = self._user(request)
            if request.path not in {"/assets/dashboard.js", "/logout"}:
                key = f"user:{user['uid']}" if user else f"peer:{request.remote or 'unknown'}"
                if not self._rate_allowed(key, 240 if user else 60):
                    return self._finish(request, web.Response(status=429, text="Too many requests; try again later.",
                                                              headers={"Retry-After": "60"}))
            if request.method == "POST":
                origin = request.headers.get("Origin")
                fetch_site = request.headers.get("Sec-Fetch-Site")
                if (origin and origin != self._origin(request)) or fetch_site == "cross-site":
                    return self._finish(request, web.Response(status=403, text="Cross-site request refused"))
            # Recheck developer status on every request.
            if self.login_enabled and request.path not in {"/login", "/callback", "/logout"}:
                user = self._user(request)
                if user and not await self._may_use_dashboard(user):
                    log.warning("Dashboard access denied: user %s", user["uid"])
                    return self._finish(request, self._message("Access denied", "Only verified bot developers may use this dashboard.", 403))
            resp = await handler(request)
        except web.HTTPException as exc:
            for k, v in SECURITY_HEADERS.items():
                exc.headers.setdefault(k, v)
            raise
        except Exception as exc:
            # Errors can contain OAuth codes, tokens or private settings.
            log.error("Dashboard request failed (%s)", type(exc).__name__)
            if request.path.startswith("/api/"):
                resp = web.json_response({"error": "request could not be completed"}, status=500)
            else:
                resp = self._message("Request failed", "The request could not be completed. Refresh and verify settings before retrying.", 500)
        return self._finish(request, resp)

    def _finish(self, request: web.Request, resp: web.StreamResponse) -> web.StreamResponse:
        # With no-referrer, browsers can send Origin: null on same-origin form POSTs.
        # Form pages need same-origin so those POSTs carry their real Origin; referrers
        # are still withheld from other origins. Keep OAuth callbacks at no-referrer
        # so their one-time codes are not sent even to same-origin pages.
        if request.path != "/callback" and resp.content_type == "text/html":
            resp.headers.setdefault("Referrer-Policy", "same-origin")
        for k, v in SECURITY_HEADERS.items():
            resp.headers.setdefault(k, v)
        if request.path != "/assets/dashboard.js":
            user = self._user(request)
            # Do not log URLs, query strings, cookies, OAuth codes, form fields, or exception messages.
            log.info("Dashboard request: user=%s method=%s status=%s", user["uid"] if user else "anonymous",
                     request.method, resp.status)
        token = request.get(NEW_SESSION_KEY)
        if token:
            self._set_cookie(resp, SESSION_COOKIE, token, SESSION_TTL)
        return resp

    # ------------------------------------------------------------------ access control
    async def _is_bot_owner(self, user: dict) -> bool:
        """Use Veil's developer identities, refreshing Discord-discovered identities.

        Explicitly configured/built-in developers remain authorized until their local
        configuration changes. Application owners and team members must still be
        present in fresh Discord application info.
        """
        if user.get("local"):
            return False
        sid = user.get("sid")
        if sid is not None and (self._sessions.get(sid) != (user.get("uid"), user.get("exp"))
                                or user.get("exp", 0) <= time.time()):
            return False
        try:
            uid = int(user["uid"])
            if uid in perms.BOT_DEVELOPERS or uid in (settings.owner_ids or ()):
                return True
            info = await asyncio.wait_for(self.bot.application_info(), timeout=5)
            owner = getattr(getattr(info, "owner", None), "id", None)
            team = getattr(info, "team", None)
            members = getattr(team, "members", ()) if team is not None else ()
            return uid == owner or any(getattr(member, "id", None) == uid and
                                       getattr(member, "membership_state", None) == discord.TeamMembershipState.accepted
                                       for member in members)
        except Exception:
            return False

    async def _may_use_dashboard(self, user: dict) -> bool:
        return await self._is_bot_owner(user)

    async def _can_manage_rbl(self, user: dict, guild) -> bool:
        return await self._is_bot_owner(user)

    async def _can_manage_security(self, user: dict, guild) -> bool:
        return await self._is_bot_owner(user)

    async def _guilds_for(self, user: dict) -> list:
        return list(self.bot.guilds) if await self._is_bot_owner(user) else []

    async def _guild_for(self, request: web.Request, user: dict):
        """Only developers can access a joined server, including by a direct URL."""
        try:
            gid = int(request.match_info["guild_id"])
        except (KeyError, ValueError):
            return None
        guild = self.bot.get_guild(gid)
        return guild if guild is not None and await self._is_bot_owner(user) else None

    async def _antinuke_cfg(self, gid: int) -> dict:
        """Load antinuke config the same way the antinuke cog does, so a first read caches the full defaults."""
        from cogs.antinuke import DEFAULT
        return await self.bot.db.get_config(gid, "antinuke", DEFAULT)

    @staticmethod
    def _csrf_ok(user: dict, submitted: str | None) -> bool:
        return bool(submitted) and hmac.compare_digest(str(submitted), str(user.get("csrf", "")))

    # ------------------------------------------------------------------ page helpers
    def _server_navigation(self, guild, active: str) -> str:
        base = f"/servers/{guild.id}"
        groups = (
            ("WORKSPACE", (("overview", "Overview", f"{base}/overview", "◈"),
                           ("general", "Server settings", f"{base}/features?section=general", "⚙"),
                           ("access", "Command access", f"{base}/features?section=general#command-access", "⚙"))),
            ("SECURITY", (("rules", "Antinuke", f"{base}/features?section=rules", "◇"),
                          ("automod", "Automod", f"{base}/features?section=automod", "✦"),
                          ("logging", "Logging & security", base, "≡"))),
            ("COMMUNITY", (("leveling", "Leveling", f"{base}/features?section=leveling", "↗"),
                           ("tickets", "Tickets", f"{base}/features?section=tickets", "▣"),
                           ("roblox", "Roblox group", f"{base}/roblox", "◇"))),
        )
        nav = f'<a class="server-switch" href="/" title="Switch server">{_server_avatar(guild)}<span><small>SERVER WORKSPACE</small><strong>{_esc(guild.name)}</strong></span><b aria-hidden="true">↕</b></a>'
        for label, links in groups:
            nav += f'<span class="nav-label">{label}</span>'
            for key, text, href, _icon in links:
                selected = ' active" aria-current="page' if key == active else ''
                nav += (f'<a class="nav-link{selected}" href="{_esc(href)}" title="{_esc(text)}">'
                        f'<span class="nav-icon" aria-hidden="true">{_dashboard_icon(key)}</span><span class="nav-text">{text}</span></a>')
        return nav

    def _page(self, title: str, body: str, user: dict | None = None, subtitle: str = "", *,
              guild=None, active: str = "", developer: bool = False) -> web.Response:
        who = ""
        if user and not user.get("local"):
            who = (f'<div class="who"><span class="account-dot" aria-hidden="true">'
                   f'{_esc(str(user.get("name") or "?")[:1].upper())}</span>'
                   f'<span>Signed in as <b>{_esc(user.get("name"))}</b></span>'
                   f'<form class="inline" method="POST" action="/logout">'
                   f'<input type="hidden" name="csrf" value="{_esc(user.get("csrf"))}">'
                   f'<button class="btn btn-plain" type="submit">Log out</button></form></div>')
        elif user:
            who = '<div class="who">Local access (127.0.0.1)</div>'
        sub = f'<div class="sub">{subtitle}</div>' if subtitle else ""
        from core.dashboard_control import navigation
        nav = navigation(active) if user else ''
        if guild and user:
            nav += self._server_navigation(guild, active)
        mobile_nav = (f'<details class="mobile-nav"><summary>Navigate control center</summary><nav aria-label="Mobile navigation">{nav}</nav></details>'
                      if user else '')
        doc = (
            '<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
            f"<title>Veil Utilities • {_esc(title)}</title><style>{CSS}</style>"
            '<script src="/assets/dashboard.js" defer></script></head><body>'
            '<a class="skip-link" href="#main">Skip to content</a>'
            '<div class="shell"><aside class="sidebar"><a href="/" class="brand">'
            '<span class="brand-mark" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" '
            'stroke="currentColor" stroke-width="1.7"><path d="M12 2.5 20 6v5.4c0 4.6-3.2 7.7-8 10.1-4.8-2.4-8-5.5-8-10.1V6L12 2.5Z"/>'
            '<path d="m8.8 11.5 2.2 2.2 4.6-5"/></svg></span>'
            '<span class="brand-copy"><span class="logo">Veil Utilities</span>'
            '<span class="brand-caption">Developer control center</span></span></a>'
            f'<details class="sidebar-navigation" open><summary>Navigation</summary><nav aria-label="Main navigation">{nav}</nav></details>'
            '<div class="side-note"><strong>Your workspace</strong>Verified developer access only.</div>'
            '</aside><div class="workspace"><header>'
            f'<div class="top-title"><a href="/">Veil Utilities</a><span class="top-separator">/</span><b>{_esc(title)}</b>{sub}</div>{who}'
            f'</header>{mobile_nav}<div class="container"><main id="main">{body}</main></div></div></div></body></html>'
        )
        return web.Response(text=doc, content_type="text/html")

    def _message(self, title: str, text: str, status: int = 200, user: dict | None = None) -> web.Response:
        resp = self._page(title, f'<div class="card center"><h2>{_esc(title)}</h2>'
                                 f'<p class="muted">{_esc(text)}</p><a class="btn" href="/">Back</a></div>', user)
        resp.set_status(status)
        return resp

    @staticmethod
    def _redirect(location: str) -> web.Response:
        return web.Response(status=302, headers={"Location": location})

    # ------------------------------------------------------------------ pages
    async def handle_script(self, request: web.Request) -> web.Response:
        return web.Response(text=DASHBOARD_SCRIPT, content_type="application/javascript")

    async def handle_control_overview(self, request: web.Request) -> web.Response:
        if self._user(request) is None:
            return await self.handle_home(request)
        from core.dashboard_control import page
        return await page(self, request, "overview")

    async def handle_control(self, request: web.Request) -> web.Response:
        from core.dashboard_control import page
        return await page(self, request)

    async def handle_home(self, request: web.Request) -> web.Response:
        user = self._user(request)
        if user is None:
            return self._page("Sign in", (
                '<div class="card center"><p class="eyebrow">Veil Utilities / Dashboard</p>'
                '<h1>Sign in to Veil</h1>'
                '<p class="muted">Sign in with Discord. This control center is restricted to verified Veil bot developers.</p>'
                '<a class="btn" href="/login">Log in with Discord →</a></div>'))

        guilds = await self._guilds_for(user)
        is_developer = await self._is_bot_owner(user)
        role = "Developer workspace"
        access_description = "Manage every server Veil has joined and review server authorizations."
        authorization_link = ('<a class="hero-action" href="/authorization">Manage authorization <span aria-hidden="true">↗</span></a>'
                              if is_developer and not user.get("local") else "")
        body = (f'<section class="welcome-panel"><div class="welcome-copy"><div class="welcome-kicker"><span class="kicker-dot"></span>{role}</div>'
                f'<h1>Your servers, in one place.</h1><p>{access_description}</p>'
                f'<div class="welcome-actions"><a href="#servers" class="btn">Browse servers <span aria-hidden="true">↓</span></a>{authorization_link}</div>'
                f'</div><div class="welcome-aside" aria-hidden="true"><span class="workspace-symbol">{_dashboard_icon("rules")}</span>'
                '<span>VEIL UTILITIES<small>SERVER WORKSPACE</small></span></div></section>')
        if is_developer:
            all_guilds = list(self.bot.guilds)
            counts = [getattr(g, "member_count", None) for g in all_guilds]
            known = [count for count in counts if isinstance(count, int) and not isinstance(count, bool) and count >= 0]
            members_text = f"{sum(known):,}" if known or not counts else "Unavailable"
            member_label = "MEMBERS (PARTIAL)" if len(known) != len(counts) else "MEMBERS"
            latency = self.bot.latency
            latency_text = (f"{int(latency * 1000)} ms" if isinstance(latency, (int, float))
                            and latency == latency and 0 < latency < float("inf") else "Unavailable")
            uptime = int(time.time() - getattr(self.bot, "started_at", time.time()))
            hours, rem = divmod(max(uptime, 0), 3600)
            body += (f'<section class="activity-bar" aria-label="Bot statistics"><div><span>JOINED SERVERS</span><strong>{len(all_guilds):,}</strong></div>'
                     f'<div><span>{member_label}</span><strong>{members_text}</strong></div>'
                     f'<div><span>GATEWAY LATENCY</span><strong>{latency_text}</strong></div>'
                     f'<div><span>UPTIME</span><strong>{hours}h {rem // 60}m</strong></div></section>')
        # Search and filters are evaluated after the developer access check.
        query = request.query.get("q", "").strip()[:100]
        status = request.query.get("antinuke", "any")
        if status not in ("any", "on", "off"):
            status = "any"
        order = request.query.get("sort", "name")
        if order not in ("name", "members"):
            order = "name"
        authorization = request.query.get("authorized", "any")
        if authorization not in ("any", "yes", "no"):
            authorization = "any"
        log_filter = request.query.get("logs", "any")
        if log_filter not in ("any", "configured", "none"):
            log_filter = "any"
        from cogs.logging import KINDS

        visible = []
        for g in guilds:
            if query and query.casefold() not in g.name.casefold() and query not in str(g.id):
                continue
            an = await self._antinuke_cfg(g.id)
            enabled = bool(an.get("enabled"))
            security_access = True  # The inventory is developer-only; recheck before responding.
            if status != "any" and (not security_access or enabled != (status == "on")):
                continue
            is_authorized = getattr(self.bot, "is_authorized", None)
            auth = ("yes" if is_authorized(g.id) else "no") if callable(is_authorized) else "unknown"
            if authorization != "any" and auth != authorization:
                continue
            logs = await self.bot.db.get_config(g.id, "logs") or {}
            log_count = sum(isinstance(logs.get(key), int) and not isinstance(logs.get(key), bool) and logs[key] > 0
                            for key in KINDS)
            if log_filter != "any" and bool(log_count) != (log_filter == "configured"):
                continue
            count = getattr(g, "member_count", None)
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                count = None
            visible.append((g, count, enabled, auth, log_count, security_access))
        if order == "members":
            visible.sort(key=lambda row: (row[1] is None, -(row[1] or 0), row[0].name.casefold(), row[0].id))
        else:
            visible.sort(key=lambda row: (row[0].name.casefold(), row[0].id))

        def option(value, label, selected):
            return f'<option value="{value}"{" selected" if selected else ""}>{label}</option>'

        cards = []
        for g, count, enabled, auth, log_count, security_access in visible:
            state = "on" if enabled else "off"
            access = {"yes": "Authorized", "no": "Not authorized", "unknown": "Authorization unknown"}[auth]
            security_fact = (f'<div><span>ANTINUKE</span><strong class="fact-{state}">{state.title()}</strong></div>'
                             if security_access else "")
            cards.append(
                f'<article class="guild-card"><div class="guild-card-top">{_server_avatar(g)}'
                f'<span class="guild-access">{_esc(access)}</span></div>'
                f'<div class="guild-identity"><a href="/servers/{_esc(g.id)}/overview">{_esc(g.name)}</a>'
                f'<span>Server ID · {_esc(g.id)}</span></div>'
                f'<div class="guild-facts"><div><span>MEMBERS</span><strong>{_esc(f"{count:,}" if count is not None else "Unavailable")}</strong></div>'
                f'{security_fact}'
                f'<div><span>LOG ROUTES</span><strong>{log_count} configured</strong></div></div>'
                f'<a class="guild-open" href="/servers/{_esc(g.id)}/overview" '
                f'aria-label="Configure {_esc(g.name)}">Manage server <span aria-hidden="true">↗</span></a></article>'
            )
        if cards:
            listing = '<div class="guild-grid">' + "".join(cards) + '</div>'
        elif guilds:
            listing = '<div class="empty-servers"><strong>No servers match your filters.</strong><p>Try a different search or clear the filters.</p><a href="/servers">Clear filters →</a></div>'
        else:
            listing = ('<div class="empty-servers"><strong>No servers available yet.</strong>'
                       '<p>Veil has not joined a server yet.</p></div>')
        status_options = "".join(option(v, label, status == v) for v, label in
                                 (("any", "Any"), ("on", "On"), ("off", "Off")))
        sort_options = "".join(option(v, label, order == v) for v, label in
                               (("name", "Name"), ("members", "Members")))
        auth_options = "".join(option(v, label, authorization == v) for v, label in
                               (("any", "Any"), ("yes", "Authorized"), ("no", "Not authorized")))
        log_options = "".join(option(v, label, log_filter == v) for v, label in
                              (("any", "Any"), ("configured", "Configured"), ("none", "None configured")))
        filters_open = ' open' if any((status != "any", authorization != "any", log_filter != "any", order != "name")) else ''
        body += (f'<section class="servers-section" id="servers"><div class="servers-heading"><div>'
                 '<p class="eyebrow">YOUR WORKSPACE</p><h2>Your servers</h2>'
                 '<p>Select a server to manage its protection and features.</p></div>'
                 f'<span class="server-count">{len(visible)} of {len(guilds)} servers shown</span></div>'
                 '<form method="GET" action="/servers" class="server-search">'
                 '<div class="search-row"><div class="search-field"><label for="q">Search servers</label>'
                 f'<input id="q" name="q" value="{_esc(query)}" placeholder="Search by name or server ID"></div>'
                 '<button class="btn" type="submit">Search</button><a class="btn btn-plain" href="/servers">Clear</a></div>'
                 f'<details class="advanced-filters"{filters_open}><summary>Filter &amp; sort servers</summary><div class="filter-row">'
                 f'<div class="form-group"><label for="antinuke">Antinuke</label><select id="antinuke" name="antinuke">{status_options}</select></div>'
                 f'<div class="form-group"><label for="authorized">Authorization</label><select id="authorized" name="authorized">{auth_options}</select></div>'
                 f'<div class="form-group"><label for="logs">Log channels</label><select id="logs" name="logs">{log_options}</select></div>'
                 f'<div class="form-group"><label for="sort">Sort by</label><select id="sort" name="sort">{sort_options}</select></div>'
                 '<button class="btn btn-plain" type="submit">Apply filters</button></div></details></form>'
                 f'{listing}<p class="overview-note">Log routes count explicitly selected channels; automatic routing is not included.</p></section>')
        if not await self._is_bot_owner(user):
            return self._message("Access denied", "Developer access changed.", 403)
        return self._page("Server Management", body, user, active="servers", developer=is_developer)

    async def _authorization_user(self, request: web.Request) -> dict | None:
        user = self._user(request)
        if not user or user.get("local") or not await self._is_bot_owner(user):
            return None
        return user

    async def handle_authorization(self, request: web.Request) -> web.Response:
        user = await self._authorization_user(request)
        if user is None:
            return self._message("Access denied", "Only bot developers can manage server authorization.", 403)
        ids = sorted(getattr(self.bot, "authorized", set()))
        rows = []
        for gid in ids:
            guild = self.bot.get_guild(gid)
            name = guild.name if guild else "Not joined"
            rows.append(f'<tr><td>{_esc(name)}</td><td>{_esc(gid)}</td><td>'
                        f'<form method="post" action="/authorization" class="inline">'
                        f'<input type="hidden" name="csrf" value="{_esc(user["csrf"])}">'
                        f'<input type="hidden" name="guild_id" value="{_esc(gid)}">'
                        '<button class="btn btn-plain" name="action" value="review">Review revocation</button>'
                        '</form></td></tr>')
        listing = "".join(rows) or '<tr><td colspan="3">No explicitly authorized servers.</td></tr>'
        body = (f'<div class="card"><h2>Server authorization</h2>'
                '<p class="muted">Only bot developers can change this list. You can authorize a server ID before '
                'the bot joins it. Developer-owned servers may be authorized automatically.</p>'
                '<form method="post" action="/authorization" class="filters">'
                f'<input type="hidden" name="csrf" value="{_esc(user["csrf"])}">'
                '<div class="form-group"><label for="guild_id">Discord server ID</label>'
                '<input id="guild_id" name="guild_id" required inputmode="numeric"></div>'
                '<label class="confirm-choice"><input type="checkbox" name="confirm_authorize" value="yes"> I verified the server ID and want to authorize it.</label>'
                '<button class="btn" name="action" value="authorize">Authorize</button></form>'
                f'<div class="table-wrap"><table><thead><tr><th>Server</th><th>ID</th><th></th></tr></thead>'
                f'<tbody>{listing}</tbody></table></div></div><a href="/">← Back to servers</a>')
        return self._page("Server authorization", body, user)

    async def handle_authorization_action(self, request: web.Request) -> web.Response:
        user = await self._authorization_user(request)
        if user is None:
            return self._message("Access denied", "Only bot developers can manage server authorization.", 403)
        data = await request.post()
        if not self._csrf_ok(user, data.get("csrf")):
            return self._message("Session expired", "Please refresh the page and try again.", 403, user)
        try:
            ids = _parse_ids(str(data.get("guild_id", "")))
            if len(ids) != 1:
                raise ValueError("Enter one server ID")
            gid = ids[0]
        except ValueError:
            return self._message("Invalid server ID", "Enter one positive Discord server ID.", 400, user)
        action = data.get("action")
        cog = self.bot.get_cog("Authorization")
        if cog is None:
            return self._message("Unavailable", "The authorization module is not loaded; no changes were made.", 503, user)
        if action == "authorize":
            if data.get("confirm_authorize") != "yes":
                return self._message("Confirmation required", "Confirm the server ID before authorizing it. Nothing was saved.", 400, user)
            if not await self._is_bot_owner(user):
                return self._message("Access denied", "Developer access changed. Nothing was saved.", 403, user)
            try:
                added = await cog.authorize(gid, int(user["uid"]), "via dashboard")
            except Exception as exc:
                log.warning("Dashboard authorization could not be confirmed (%s)", type(exc).__name__)
                return self._message("Action not confirmed", "Check the current authorization list before retrying.", 503, user)
            log.info("Dashboard: developer %s authorized guild %s (added=%s)", user["uid"], gid, added)
            return self._message("Server authorized" if added else "Already authorized",
                                 f"Server {gid} {'is authorized' if added else 'is already on the list'}. If the bot has left, invite it again.", user=user)
        if action == "review":
            if gid not in self.bot.authorized:
                return self._message("Not authorized", "That server is not on the explicit authorization list.", 404, user)
            guild = self.bot.get_guild(gid)
            name = guild.name if guild else "Not joined"
            now = time.monotonic()
            self._pending_revocations = {k: v for k, v in self._pending_revocations.items() if v[2] > now}
            confirmation = secrets.token_urlsafe(24)
            self._pending_revocations[confirmation] = (user["uid"], gid, now + 300)
            body = (f'<div class="card"><h2>Confirm revocation</h2><p class="muted">'
                    f'Revoke {_esc(name)} (<code>{_esc(gid)}</code>)? If the bot is in the server and it is '
                    'no longer automatically authorized, it will attempt to leave. This may interrupt service.</p>'
                    '<form method="post" action="/authorization">'
                    f'<input type="hidden" name="csrf" value="{_esc(user["csrf"])}">'
                    f'<input type="hidden" name="guild_id" value="{_esc(gid)}">'
                    f'<input type="hidden" name="confirmation" value="{_esc(confirmation)}">'
                    '<button class="btn" name="action" value="revoke">Confirm revocation</button> '
                    '<a class="btn btn-plain" href="/authorization">Cancel</a></form></div>')
            return self._page("Confirm revocation", body, user)
        if action == "revoke":
            confirmation = self._pending_revocations.pop(str(data.get("confirmation", "")), None)
            if (not confirmation or confirmation[0] != user["uid"] or confirmation[1] != gid
                    or confirmation[2] <= time.monotonic()):
                return self._message("Confirmation required", "Review this revocation before confirming it.", 403, user)
            if not await self._is_bot_owner(user):
                return self._message("Access denied", "Developer access changed. Nothing was saved.", 403, user)
            try:
                removed = await cog.unauthorize(gid)
            except Exception as exc:
                log.warning("Dashboard revocation could not be confirmed (%s)", type(exc).__name__)
                return self._message("Action not confirmed", "Check the current authorization list before retrying.", 503, user)
            if not removed:
                return self._message("Not authorized", "That server is not on the explicit authorization list.", 404, user)
            log.info("Dashboard: developer %s revoked authorization for guild %s", user["uid"], gid)
            guild = self.bot.get_guild(gid)
            detail = "The bot is not currently in that server."
            if guild:
                # Serialize the final check with authorization, just like the join rejection path.
                async with cog._auth_lock:
                    if not await self._is_bot_owner(user):
                        detail = "Authorization was revoked, but developer access changed before the leave; the bot did not attempt to leave. Review the server in Discord."
                    elif self.bot.is_authorized(gid):
                        detail = "The bot stayed because this server is still automatically authorized or authorization is disabled."
                    else:
                        try:
                            await asyncio.wait_for(guild.leave(), timeout=15)
                            detail = "The bot left the server."
                        except Exception as exc:
                            log.warning("Dashboard: failed to leave revoked guild %s (%s)", gid, type(exc).__name__)
                            detail = "The leave failed or timed out; check Discord and the bot logs, then remove it manually if needed."
            return self._message("Authorization revoked", detail, user=user)
        return self._message("Invalid action", "No changes were made.", 400, user)

    async def handle_server_overview(self, request: web.Request) -> web.Response:
        user = self._user(request)
        if user is None:
            return self._redirect("/login" if self.login_enabled else "/")
        guild = await self._guild_for(request, user)
        if guild is None:
            return self._message("Server not found", "Veil isn't in that server, or you cannot manage it.", 404, user)
        from cogs.logging import KINDS
        from cogs.levels import DEFAULT as LEVEL_DEFAULT

        an = await self._antinuke_cfg(guild.id)
        logs = await self.bot.db.get_config(guild.id, "logs") or {}
        levels = await self.bot.db.get_config(guild.id, "levels", LEVEL_DEFAULT)
        count = sum(isinstance(logs.get(key), int) and not isinstance(logs.get(key), bool) and logs[key] > 0
                    for key in KINDS)
        state = "on" if an.get("enabled") else "off"
        level_state = "on" if levels.get("enabled") else "off"
        base = f"/servers/{guild.id}"
        cards = [
            ("00", "Server settings", "Configure the prefix, command access and disabled commands.", f"{base}/features?section=general", "Manage command access"),
            ("01", "Antinuke", "Protection rules, thresholds and responses.", f"{base}/features?section=rules", f"Protection {state}"),
            ("02", "Automod", "Message filters and moderation actions.", f"{base}/features?section=automod", "Configure filters"),
            ("03", "Logging channels", "Choose where each category of activity is sent.", f"{base}#logging", f"{count} explicit log {'route' if count == 1 else 'routes'}"),
            ("04", "Log exclusions", "Choose which users or channels are excluded from supported events.", f"{base}#exclusions", "Review exclusions"),
            ("05", "Trusted identities", "Review users and roles exempt from antinuke enforcement.", f"{base}#trusted", "Review trust"),
            ("06", "Leveling", "XP settings and level-up announcements.", f"{base}/features?section=leveling", f"Leveling {level_state}"),
            ("07", "Tickets", "Support roles, ticket settings and transcripts.", f"{base}/features?section=tickets", "Configure tickets"),
        ]
        if await self._can_manage_rbl(user, guild):
            cards.append(("08", "Role blacklist", "Manage restricted roles and blacklist bypasses.", f"{base}#blacklist", "Review blacklist"))
        if not await self._can_manage_security(user, guild):
            return self._message("Access denied", "Your access changed. Please sign in again.", 403, user)
        tile_icons = {"Server settings": "general", "Antinuke": "rules", "Automod": "automod", "Logging channels": "logging",
                      "Log exclusions": "exclusions", "Trusted identities": "trusted", "Leveling": "leveling",
                      "Tickets": "tickets", "Role blacklist": "blacklist"}
        tiles = "".join(
            f'<a class="feature-tile" href="{_esc(href)}"><div class="tile-top"><span class="tile-icon">{_dashboard_icon(tile_icons.get(name, "overview"))}</span><span aria-hidden="true">↗</span></div>'
            f'<h3>{_esc(name)}</h3><p>{_esc(desc)}</p><span class="tile-status">{_esc(status)}</span></a>'
            for _icon, name, desc, href, status in cards)
        overview_description = 'Everything you need, organized by feature. Logging, trust, and blacklist links open a shared settings form.'
        security_summary = f'<div class="summary-row"><span>Antinuke</span><strong>{_esc(state.title())}</strong></div>'
        body = (f'<div class="server-hero"><div><p class="eyebrow">YOUR SERVER WORKSPACE</p>'
                f'<div class="hero-server">{_server_avatar(guild)}<div><h1>{_esc(guild.name)}</h1>'
                f'<p>Server ID · {_esc(guild.id)}</p></div></div>'
                '<p class="hero-copy">Your tools for a safer, better organized community. Choose a feature below to get started.</p></div>'
                '</div><div class="workspace-grid"><div>'
                '<div class="overview-heading"><div><p class="eyebrow">YOUR TOOLKIT</p><h2>Manage your server</h2>'
                f'<p>{overview_description}</p></div></div>'
                '<section class="setup-guide" aria-labelledby="setup-title"><div><p class="eyebrow">GET STARTED</p>'
                '<h2 id="setup-title">A safer setup, step by step</h2>'
                '<p>Start with the features you need. Settings and permissions can change independently.</p></div>'
                f'<ol><li><a href="{base}/features?section=rules">Review protection readiness</a><span>Check Discord permissions and hierarchy.</span></li>'
                f'<li><a href="{base}#logging">Choose log destinations</a><span>Review routing and exclusions before relying on alerts.</span></li>'
                f'<li><a href="{base}/features?section=automod">Configure message filters</a><span>Test actions in a safe channel before enabling broadly.</span></li></ol></section>'
                f'<div class="feature-grid">{tiles}</div>'
                '<p class="overview-note">Log route counts show explicitly selected categories, not automatic routing.</p></div>'
                '<aside class="workspace-summary" aria-label="Workspace summary"><p class="eyebrow">AT A GLANCE</p>'
                '<h2>Workspace overview</h2><p>A quick view of your saved configuration.</p>'
                f'<div class="summary-row"><span>Access</span><strong>Developer only</strong></div>'
                f'{security_summary}<div class="summary-row"><span>Leveling</span><strong>{_esc(level_state.title())}</strong></div>'
                f'<div class="summary-row"><span>Explicit log routes</span><strong>{count}</strong></div>'
                '<p class="summary-note">Settings are a snapshot, not a guarantee of Discord permissions or delivery.</p></aside></div>')
        return self._page("Server overview", body, user, guild=guild, active="overview")

    async def handle_server(self, request: web.Request) -> web.Response:
        user = self._user(request)
        if user is None:
            return self._redirect("/login" if self.login_enabled else "/")
        guild = await self._guild_for(request, user)
        if guild is None:
            return self._message("Server not found", "Veil isn't in that server, or you don't own it.", 404, user)

        if not await self._can_manage_security(user, guild):
            return self._message("Access denied", "Your access changed. Please sign in again.", 403, user)

        can_rbl = await self._can_manage_rbl(user, guild)
        if not can_rbl:
            return self._message("Access denied", "Your access changed. Please sign in again.", 403, user)
        rbl = (await self.bot.db.get_config(guild.id, "roleblacklist") or {}) if can_rbl else {}
        logs_cfg = await self.bot.db.get_config(guild.id, "logs") or {}
        an_cfg = await self._antinuke_cfg(guild.id)

        def ids(values) -> str:
            return _esc(", ".join(str(v) for v in values or []))

        from core.dashboard_features import _revision
        revision = _revision(self, guild.id, int(user["uid"]), "combined_security",
                             {"roleblacklist": rbl, "logs": logs_cfg, "antinuke": an_cfg})
        current = an_cfg.get("punishment") or "ban"
        options = "".join(
            f'<option value="{p}"{" selected" if p == current else ""}>{p}</option>' for p in PUNISHMENTS)
        saved = ('<div class="note ok" role="status">Settings saved.</div>' if request.query.get("saved") else "")

        from cogs.logging import KINDS, other_bots_channel
        from core.dashboard_security_views import log_routes
        routes_preview = log_routes(guild, logs_cfg)
        channels = sorted([ch for ch in getattr(guild, "text_channels", []) if not other_bots_channel(ch)],
                          key=lambda ch: (ch.position, ch.id))
        log_forms = []
        for field, key in LOG_FIELDS:
            current_log = logs_cfg.get(key)
            value = str(current_log) if current_log is not None else ""
            choices = [("", "Automatic"), ("-1", "Off")]
            choices.extend((str(ch.id), f"#{ch.name}") for ch in channels)
            if value and value not in {v for v, _ in choices}:
                choices.append((value, f"Unavailable or excluded channel ({value})"))
            options_html = "".join(f'<option value="{_esc(v)}"{" selected" if v == value else ""}>{_esc(label)}</option>'
                                   for v, label in choices)
            log_forms.append(f'<div class="form-group"><label for="{field}">{_esc(KINDS[key].label)}</label>'
                             f'<select id="{field}" name="{field}">{options_html}</select></div>')
        logs_form = "".join(log_forms)
        ignored = logs_cfg.get("ignore") or {}

        rbl_form = f'''<section class="card" id="blacklist"><div class="card-head"><span class="section-icon" aria-hidden="true">01</span>
    <div><h2>Role blacklist</h2><p>Control which roles are restricted and who may bypass the blacklist.</p></div></div>
    <div class="form-grid"><div class="form-group"><label for="rbl_roles">Blacklisted role IDs</label>
      <input id="rbl_roles" type="text" name="rbl_roles" value="{ids(rbl.get("roles"))}" placeholder="Role IDs, separated by commas">
      <span class="hint">Ordinary blacklist only. Hard-blacklisted roles are managed separately.</span></div>
    <div class="form-group"><label for="rbl_authorized">Authorized users and roles</label>
      <input id="rbl_authorized" type="text" name="rbl_authorized" value="{ids(rbl.get("authorized"))}" placeholder="User or role IDs, separated by commas">
      <span class="hint">These IDs can bypass the ordinary role blacklist.</span></div></div>
    <div class="form-group"><label for="rbl_reply_mode">Role blacklist reply location</label>
      <select id="rbl_reply_mode" name="rbl_reply_mode">
        <option value="channel"{' selected' if not logs_cfg.get('rbl_dm') else ''}>In the channel</option>
        <option value="dm"{' selected' if logs_cfg.get('rbl_dm') else ''}>Direct message</option>
      </select><span class="hint">Controls role-blacklist replies except rbl list, which always appears in the invoking channel; direct messages can fail if the recipient has DMs closed.</span></div>
  </section>''' if can_rbl else ""
        body = f"""<div class="page-heading"><div><p class="eyebrow">Servers / Settings</p>
  <h1>{_esc(guild.name)}</h1><p>Manage the blacklist, logging, and antinuke settings for this server.</p></div>
  <div class="heading-actions"><a class="btn btn-plain" href="/">← All servers</a>
    <a class="btn" href="/servers/{_esc(guild.id)}/features">More feature controls →</a></div></div>
{saved}<div class="settings-layout"><nav class="section-nav" aria-label="Settings sections">
  <span>ON THIS PAGE</span>{'<a href="#blacklist">Role blacklist</a>' if can_rbl else ''}
  <a href="#logging">Logging channels</a><a href="#exclusions">Log exclusions</a><a href="#antinuke">Antinuke</a>
  <a href="#trusted">Trusted identities</a></nav>
<form method="POST" action="/api/guilds/{_esc(guild.id)}/config">
  <input type="hidden" name="csrf" value="{_esc(user["csrf"])}">
  <input type="hidden" name="revision" value="{_esc(revision)}">
{rbl_form}
  <section class="card" id="logging"><div class="card-head"><span class="section-icon" aria-hidden="true">02</span>
    <div><h2>Logging channels</h2><p>Choose where each type of activity is sent.</p></div></div>
    <div class="note">Unset routes may use explicitly approved shared destinations; Off disables the category. Existing IDs require review before logging resumes. Check channel privacy and access.</div>
    {routes_preview}<div class="form-grid">{logs_form}</div>
    <label class="confirm-choice"><input type="checkbox" name="confirm_logs" value="yes"> I reviewed these logging destinations and their access permissions.</label>
  </section>
  <section class="card" id="exclusions"><div class="card-head"><span class="section-icon" aria-hidden="true">03</span>
    <div><h2>Log exclusions</h2><p>Exclude activity from specific channels or users when the event supports it.</p></div></div>
    <div class="form-grid"><div class="form-group"><label for="log_ignore_channels">Ignored channel IDs</label>
      <input id="log_ignore_channels" name="log_ignore_channels" value="{ids(ignored.get("channels"))}" placeholder="Channel IDs, separated by commas">
      <span class="hint">Events that include a source channel will not be logged for these channels.</span></div>
    <div class="form-group"><label for="log_ignore_users">Ignored user IDs</label>
      <input id="log_ignore_users" name="log_ignore_users" value="{ids(ignored.get("users"))}" placeholder="User IDs, separated by commas">
      <span class="hint">Events that include a user will not be logged for these users.</span></div></div>
  </section>
  <section class="card" id="antinuke"><div class="card-head"><span class="section-icon" aria-hidden="true">04</span>
    <div><h2>Antinuke</h2><p>Manage the default response and the people who administer protection.</p></div></div>
    <div class="note">Protection is currently <strong>{'on' if an_cfg.get("enabled") else 'off'}</strong>.
      Enable or disable protection and manage individual rules in <a href="/servers/{_esc(guild.id)}/features?section=rules">Antinuke rules</a>.</div>
    <div class="form-grid"><div class="form-group"><label for="an_punishment">Default punishment</label>
      <select id="an_punishment" name="an_punishment">{options}</select><span class="hint">Used by modules without their own punishment.</span></div>
    <div class="form-group"><label for="an_admins">Antinuke admin user IDs</label>
      <input id="an_admins" type="text" name="an_admins" value="{ids(an_cfg.get("admins"))}" placeholder="User IDs, separated by commas">
      <span class="hint">Admins can configure antinuke and are exempt from punishment.</span></div></div>
  </section>
  <section class="card" id="trusted"><div class="card-head"><span class="section-icon" aria-hidden="true">05</span>
    <div><h2>Trusted identities</h2><p>Antinuke exclusions for trusted users and roles.</p></div></div>
    <div class="note">Trusted users and members with trusted roles are exempt from antinuke enforcement; trusted bots are also exempt from bot-add detection and quarantine. Add only identities you trust.</div>
    <div class="form-grid"><div class="form-group"><label for="an_whitelist">Trusted user IDs</label>
      <input id="an_whitelist" name="an_whitelist" value="{ids(an_cfg.get("whitelist"))}" placeholder="User IDs, separated by commas"></div>
    <div class="form-group"><label for="an_whitelist_roles">Trusted role IDs</label>
      <input id="an_whitelist_roles" name="an_whitelist_roles" value="{ids(an_cfg.get("whitelist_roles"))}" placeholder="Role IDs, separated by commas"></div></div>
  </section>
  <div class="settings-actions"><p>Changes apply after you save. Review security exemptions carefully.</p>
    <label class="confirm-choice"><input type="checkbox" name="confirm_security" value="yes"> I have reviewed changes to security roles, admins and exemptions.</label>
    <div class="buttons"><a href="/" class="btn btn-plain">Cancel</a><button type="submit" class="btn">Save changes</button></div></div>
</form></div>"""

        return self._page(guild.name, body, user, subtitle=f"Server settings: <b>{_esc(guild.name)}</b>",
                          guild=guild, active="logging")

    async def handle_roblox(self, request: web.Request) -> web.Response:
        from core.dashboard_roblox import group_page
        return await group_page(self, request)

    async def handle_roblox_save(self, request: web.Request) -> web.Response:
        from core.dashboard_roblox import save_group
        return await save_group(self, request)

    async def handle_roblox_role(self, request: web.Request) -> web.Response:
        from core.dashboard_roblox_roles import handle_role_action
        return await handle_role_action(self, request)

    async def handle_features(self, request: web.Request) -> web.Response:
        from core.dashboard_features import features_page
        return await features_page(self, request)

    async def handle_save_feature(self, request: web.Request) -> web.Response:
        from core.dashboard_features import save_feature
        try:
            gid = int(request.match_info["guild_id"])
        except (KeyError, ValueError):
            return self._message("Server not found", "Server not found.", 404)
        async with self._save_locks[gid % len(self._save_locks)]:
            return await save_feature(self, request)

    # ------------------------------------------------------------------ Discord login
    async def handle_login(self, request: web.Request) -> web.Response:
        if not self.login_enabled:
            return self._redirect("/")
        state = secrets.token_urlsafe(24)
        query = urlencode({
            "client_id": self.client_id, "redirect_uri": self.redirect_uri, "response_type": "code",
            "scope": "identify", "state": state,
        })
        resp = self._redirect(f"{DISCORD_AUTHORIZE}?{query}")
        self._set_cookie(resp, STATE_COOKIE, self._sign({"t": "state", "s": state, "exp": int(time.time()) + STATE_TTL}),
                         STATE_TTL)
        return resp

    async def handle_callback(self, request: web.Request) -> web.Response:
        if not self.login_enabled:
            return self._redirect("/")
        if request.query.get("error"):
            return self._message("Login cancelled", "Discord login was cancelled.", 400)
        saved = self._unsign(request.cookies.get(STATE_COOKIE), "state")
        state, code = request.query.get("state", ""), request.query.get("code", "")
        if not saved or not state or not code or not hmac.compare_digest(state, str(saved.get("s", ""))):
            return self._message("Login expired", "That login link is invalid or expired. Please try again.", 400)
        try:
            me = await self._fetch_discord_user(code)
            uid = int(me["id"])
        except Exception as e:
            log.warning("Dashboard login failed (%s)", type(e).__name__)
            return self._message("Login failed", "Couldn't verify your Discord account. Please try again.", 502)
        name = str(me.get("global_name") or me.get("username") or uid)
        if not await self._may_use_dashboard({"uid": uid, "local": False}):
            log.warning("Dashboard login denied: user %s", uid)
            resp = self._message("Access denied", "Only verified bot developers may use this dashboard.", 403)
            resp.del_cookie(STATE_COOKIE, path="/")
            return resp
        resp = self._redirect("/")
        self._set_cookie(resp, SESSION_COOKIE, self.make_session(uid, name), SESSION_TTL)
        resp.del_cookie(STATE_COOKIE, path="/")
        log.info("Dashboard login: user %s", uid)
        return resp

    async def _fetch_discord_user(self, code: str) -> dict:
        """Exchange the OAuth2 code for the user's identity. The access token is used once and never stored."""
        http = self._client()
        form = {
            "grant_type": "authorization_code", "code": code, "redirect_uri": self.redirect_uri,
            "client_id": self.client_id, "client_secret": self.client_secret,
        }
        async with http.post(f"{DISCORD_API}/oauth2/token", data=form) as r:
            if r.status != 200:
                raise RuntimeError(f"token exchange returned HTTP {r.status}")
            token = (await r.json())["access_token"]
        async with http.get(f"{DISCORD_API}/users/@me", headers={"Authorization": f"Bearer {token}"}) as r:
            if r.status != 200:
                raise RuntimeError(f"/users/@me returned HTTP {r.status}")
            return await r.json()

    def _client(self) -> aiohttp.ClientSession:
        shared = getattr(self.bot, "session", None)
        if isinstance(shared, aiohttp.ClientSession) and not shared.closed:
            return shared
        if self._http_session is None or self._http_session.closed:
            self._http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        return self._http_session

    async def handle_logout(self, request: web.Request) -> web.Response:
        user = self._user(request)
        data = await request.post()
        if user is None or not self._csrf_ok(user, data.get("csrf")):
            return self._message("Session expired", "Please refresh the page and try again.", 403)
        self._sessions.pop(user["sid"], None)
        log.info("Dashboard logout: user %s", user["uid"])
        resp = self._redirect("/")
        resp.del_cookie(SESSION_COOKIE, path="/")
        return resp

    # ------------------------------------------------------------------ JSON API
    async def handle_api_guilds(self, request: web.Request) -> web.Response:
        user = self._user(request)
        if user is None:
            return web.json_response({"error": "login required"}, status=401)
        guilds = await self._guilds_for(user)
        return web.json_response([
            {"id": str(g.id), "name": g.name,
             "member_count": getattr(g, "member_count", None)}
            for g in guilds
        ])

    async def handle_api_get_config(self, request: web.Request) -> web.Response:
        user = self._user(request)
        if user is None:
            return web.json_response({"error": "login required"}, status=401)
        guild = await self._guild_for(request, user)
        if guild is None:
            return web.json_response({"error": "not found"}, status=404)
        if not await self._can_manage_security(user, guild):
            return web.json_response({"error": "developer required"}, status=403)
        result = {
            "logs": await self.bot.db.get_config(guild.id, "logs") or {},
            "antinuke": await self._antinuke_cfg(guild.id),
            "permlevels": await self.bot.db.get_config(guild.id, "permlevels") or {},
        }
        if await self._can_manage_rbl(user, guild):
            result["roleblacklist"] = await self.bot.db.get_config(guild.id, "roleblacklist") or {}
            from core.dashboard_features import _revision
            snapshot = {key: result[key] for key in ("roleblacklist", "logs", "antinuke")}
            result["revision"] = _revision(self, guild.id, int(user["uid"]), "combined_security", snapshot)
        return web.json_response(result)

    async def handle_api_post_config(self, request: web.Request) -> web.Response:
        try:
            gid = int(request.match_info["guild_id"])
        except (KeyError, ValueError):
            return web.json_response({"error": "not found"}, status=404)
        async with self._save_locks[gid % len(self._save_locks)]:
            return await self._save_combined_config(request)

    async def _save_combined_config(self, request: web.Request) -> web.Response:
        user = self._user(request)
        if user is None:
            return web.json_response({"error": "login required"}, status=401)
        guild = await self._guild_for(request, user)
        if guild is None:
            return self._message("Server not found", "Veil isn't in that server, or you don't own it.", 404, user)
        if not await self._can_manage_security(user, guild):
            return self._message("Access denied", "Only bot developers may change dashboard security settings.", 403, user)
        data = await request.post()
        if not self._csrf_ok(user, data.get("csrf")):
            return self._message("Session expired", "Please refresh the page and try again.", 403, user)
        gid = guild.id
        rbl_fields = {"rbl_roles", "rbl_authorized"} & set(data)
        if (rbl_fields or "rbl_reply_mode" in data) and not await self._can_manage_rbl(user, guild):
            return self._message("Access denied", "Developer login is required for role blacklist settings.", 403, user)

        from core.dashboard_features import _digest
        rbl = deepcopy(await self.bot.db.get_config(gid, "roleblacklist") or {})
        logs_cfg = deepcopy(await self.bot.db.get_config(gid, "logs") or {})
        an_cfg = deepcopy(await self._antinuke_cfg(gid))
        revision = self._unsign(data.get("revision"), "feature_revision")
        snapshot = {"roleblacklist": rbl, "logs": logs_cfg, "antinuke": an_cfg}
        if (not revision or revision.get("gid") != gid or revision.get("uid") != int(user["uid"])
                or revision.get("module") != "combined_security"
                or not hmac.compare_digest(str(revision.get("digest", "")), _digest(snapshot))):
            return self._message("Settings changed", "Refresh the page and review the latest settings. Nothing was saved.", 409, user)

        # Validate every submitted list before modifying cached settings or making any writes.
        lists = {}
        for field in ("rbl_roles", "rbl_authorized", "an_admins", "an_whitelist", "an_whitelist_roles",
                      "log_ignore_channels", "log_ignore_users"):
            if field in data:
                try:
                    lists[field] = _parse_ids(str(data[field]))
                except ValueError as exc:
                    return self._message("Invalid settings", f"{field}: {exc}. Nothing was saved.", 400, user)
        if "rbl_reply_mode" in data and data["rbl_reply_mode"] not in ("channel", "dm"):
            return self._message("Invalid settings", "Invalid role blacklist reply location. Nothing was saved.", 400, user)
        if "an_whitelist_roles" in lists:
            old_roles = set(an_cfg.get("whitelist_roles") or [])
            for role_id in lists["an_whitelist_roles"]:
                if role_id == guild.id:
                    return self._message("Invalid settings", "The @everyone role cannot be trusted. Nothing was saved.", 400, user)
                if role_id not in old_roles:
                    get_role = getattr(guild, "get_role", None)
                    if not callable(get_role) or get_role(role_id) is None:
                        return self._message("Invalid settings", "Trusted roles must belong to this server. Nothing was saved.", 400, user)
        for field, key in LOG_FIELDS:
            if field in data:
                value = str(data[field]).strip()
                if value and value != "-1":
                    if (not value.isascii() or not value.isdigit() or len(value) > 20
                            or not 0 < int(value) < 2**64):
                        return self._message("Invalid settings", f"{field}: invalid channel ID. Nothing was saved.", 400, user)
                    get_channel = getattr(guild, "get_channel", None)
                    channel = get_channel(int(value)) if callable(get_channel) else None
                    if int(value) != logs_cfg.get(key) and not isinstance(channel, discord.TextChannel):
                        return self._message("Invalid settings", f"{field}: choose a text channel in this server. Nothing was saved.", 400, user)
                    from cogs.logging import can_post, other_bots_channel
                    if int(value) != logs_cfg.get(key) and other_bots_channel(channel) and data.get("confirm_logs") != "yes":
                        return self._message("Invalid settings", f"{field}: Ghost Vanguard channels are excluded from Veil logging. Nothing was saved.", 400, user)
                    if data.get("confirm_logs") == "yes" and isinstance(channel, discord.TextChannel) and not can_post(channel):
                        return self._message("Invalid settings", f"{field}: Veil needs View Channel, Send Messages and Embed Links. Nothing was saved.", 400, user)
                    if int(value) != logs_cfg.get(key) and not can_post(channel):
                        return self._message("Invalid settings", f"{field}: Veil cannot post there. Nothing was saved.", 400, user)

        if "log_ignore_channels" in lists:
            old_ignore = logs_cfg.get("ignore") or {}
            if not isinstance(old_ignore, dict):
                return self._message("Invalid settings", "Log exclusions have an unexpected format. Nothing was saved.", 400, user)
            old_channels = set(old_ignore.get("channels") or [])
            get_channel = getattr(guild, "get_channel", None)
            if any(not callable(get_channel) or get_channel(cid) is None
                   for cid in set(lists["log_ignore_channels"]) - old_channels):
                return self._message("Invalid settings", "New excluded channels must belong to this server. Nothing was saved.", 400, user)
        if "rbl_roles" in lists:
            old_roles = set(rbl.get("roles") or [])
            get_role = getattr(guild, "get_role", None)
            for role_id in set(lists["rbl_roles"]) - old_roles:
                if role_id == guild.id or not callable(get_role) or get_role(role_id) is None:
                    return self._message("Invalid settings", "Blacklisted roles must belong to this server and cannot be @everyone. Nothing was saved.", 400, user)
        for field, key in (("an_admins", "admins"), ("an_whitelist", "whitelist"), ("rbl_authorized", "authorized")):
            if field not in lists:
                continue
            old = set((rbl if field == "rbl_authorized" else an_cfg).get(key) or [])
            for target_id in set(lists[field]) - old:
                role = guild.get_role(target_id) if field == "rbl_authorized" and callable(getattr(guild, "get_role", None)) else None
                if role is not None and target_id != guild.id:
                    continue
                if target_id == guild.id:
                    return self._message("Invalid settings", "@everyone cannot receive a security exemption. Nothing was saved.", 400, user)
                try:
                    member = await asyncio.wait_for(guild.fetch_member(target_id), timeout=5)
                    if member.id != target_id:
                        raise ValueError("incorrect member")
                except Exception:
                    return self._message("Invalid settings", "New security users must be verified members of this server. Nothing was saved.", 400, user)
        security_changed = any(field in lists and set(lists[field]) != set(config.get(key) or [])
                               for field, config, key in (("rbl_roles", rbl, "roles"), ("rbl_authorized", rbl, "authorized"),
                                                          ("an_admins", an_cfg, "admins"), ("an_whitelist", an_cfg, "whitelist"),
                                                          ("an_whitelist_roles", an_cfg, "whitelist_roles")))
        if security_changed and data.get("confirm_security") != "yes":
            return self._message("Confirmation required", "Confirm the security changes before saving. Nothing was saved.", 400, user)
        if "an_punishment" in data and str(data["an_punishment"]).strip().lower() not in PUNISHMENTS:
            return self._message("Invalid settings", "Choose a valid punishment. Nothing was saved.", 400, user)

        if rbl_fields:
            if "rbl_roles" in data and set(lists["rbl_roles"]) & set(rbl.get("hard_roles", [])):
                return self._message("Invalid settings", "A role cannot be on both the ordinary and hard blacklists. Nothing was saved.", 400, user)
            if "rbl_roles" in data:
                rbl["roles"] = lists["rbl_roles"]
            if "rbl_authorized" in data:
                rbl["authorized"] = lists["rbl_authorized"]
        for field, key in LOG_FIELDS:
            if field not in data:
                continue
            value = str(data[field]).strip()
            new_value = int(value) if value else None
            logs_cfg[key] = new_value
            approvals = logs_cfg.get("approved_routes")
            if data.get("confirm_logs") == "yes" and isinstance(new_value, int) and new_value > 0:
                get_channel = getattr(guild, "get_channel", None)
                channel = get_channel(new_value) if callable(get_channel) else None
                if isinstance(channel, discord.TextChannel):
                    if not isinstance(approvals, dict):
                        approvals = logs_cfg["approved_routes"] = {}
                    approvals[key] = new_value
            elif isinstance(approvals, dict) and (new_value is None or new_value == -1):
                approvals.pop(key, None)
                if not approvals:
                    logs_cfg.pop("approved_routes", None)
        if "rbl_reply_mode" in data:
            logs_cfg["rbl_dm"] = data["rbl_reply_mode"] == "dm"
        for field, key in (("log_ignore_channels", "channels"), ("log_ignore_users", "users")):
            if field in data:
                if not isinstance(logs_cfg.get("ignore"), dict):
                    logs_cfg["ignore"] = {}
                logs_cfg["ignore"][key] = lists[field]

        trust_changed = any(field in lists and set(lists[field]) != set(an_cfg.get(key) or [])
                            for field, key in (("an_whitelist", "whitelist"),
                                               ("an_whitelist_roles", "whitelist_roles")))
        an_cfg.pop("action", None)  # written by older dashboard versions; the antinuke never read it
        punishment = str(data.get("an_punishment", "")).strip().lower()
        if punishment in PUNISHMENTS:
            an_cfg["punishment"] = punishment
        if "an_admins" in data:
            an_cfg["admins"] = lists["an_admins"]
        for field, key in (("an_whitelist", "whitelist"), ("an_whitelist_roles", "whitelist_roles")):
            if field in data:
                an_cfg[key] = lists[field]
        documents = {"logs": logs_cfg, "antinuke": an_cfg}
        if rbl_fields:
            documents["roleblacklist"] = rbl
        if await self._guild_for(request, user) is None:
            return self._message("Access denied", "Your access changed; nothing was saved.", 403, user)
        latest = {"roleblacklist": await self.bot.db.get_config(gid, "roleblacklist") or {},
                  "logs": await self.bot.db.get_config(gid, "logs") or {}, "antinuke": await self._antinuke_cfg(gid)}
        if not hmac.compare_digest(str(revision.get("digest", "")), _digest(latest)):
            return self._message("Settings changed", "Refresh the page and review the latest settings. Nothing was saved.", 409, user)
        try:
            await self.bot.db.set_configs_committed(gid, documents)
        except Exception as exc:
            log.warning("Dashboard combined save could not be confirmed (%s)", type(exc).__name__)
            return self._message("Save not confirmed", "The database did not confirm the save. Refresh and review the current settings before retrying.", 503, user)
        if trust_changed:
            # Match the Discord command's best-effort owner/security-log notification.
            try:
                get_cog = getattr(self.bot, "get_cog", None)
                cog = get_cog("Antinuke") if callable(get_cog) else None
                if cog:
                    actor = discord.Object(id=int(user["uid"]))
                    await cog.security_notice(guild, actor, "updated the antinuke trusted users or roles via the dashboard")
            except Exception:
                log.warning("Dashboard: could not send antinuke trust-change notice for guild %s", gid)

        log.info("Dashboard: user %s updated settings for guild %s", user.get("uid"), gid)
        return self._redirect(f"/servers/{gid}?saved=1")

    # ------------------------------------------------------------------ lifecycle
    async def start(self):
        if not self.login_enabled or not self.secure_cookies:
            raise RuntimeError("Dashboard requires Discord login and an HTTPS public URL; refusing to listen")
        if not _is_loopback(self.host):
            raise RuntimeError("Dashboard backend must bind to loopback behind a trusted HTTPS proxy")
        self.runner = web.AppRunner(self.app, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, self.host, self.port)
        await site.start()
        where = self.public_url or f"http://127.0.0.1:{self.port}"
        log.info("Web dashboard listening on %s:%d (%s) at %s", self.host, self.port,
                 "Discord login" if self.login_enabled else "localhost only", where)

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()
            self.runner = None
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()
