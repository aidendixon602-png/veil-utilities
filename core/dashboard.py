"""Per-server web dashboard for Veil Utilities.

Security model
--------------
* Off unless ``DASHBOARD_ENABLED=true``.
* A real listener requires HTTPS and Discord login; it never starts in unauthenticated local mode.
* With ``DISCORD_CLIENT_SECRET`` and ``DASHBOARD_URL`` set (the client ID comes from the bot itself), people sign in with Discord
  (``identify`` scope only) and each server is shown only to its owner or to a bot developer. This mirrors the
  Discord commands: only the server owner can change antinuke admins, which this page edits.
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
import secrets
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode, urlsplit

from core.perms import is_bot_developer

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
SESSION_TTL = 7 * 24 * 3600
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
        "default-src 'none'; style-src 'unsafe-inline'; img-src https://cdn.discordapp.com; "
        "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


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


CSS = """
:root { color-scheme:dark; --bg:#090c14; --surface:#111622; --raised:#161d2b; --border:#293244;
        --text:#eef2fa; --muted:#a5afc1; --subtle:#78869b; --accent:#a78bfa; --blue:#8dbcfb; }
* { box-sizing:border-box; }
html { scroll-behavior:smooth; }
body { margin:0; background:radial-gradient(ellipse at 50% -20%,#20213c 0,transparent 48%),var(--bg);
       color:var(--text); font:14px/1.55 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
a { color:#c4b5fd; text-decoration:none; } a:hover { color:#e0d6ff; text-decoration:underline; }
a:focus-visible, button:focus-visible, input:focus-visible, select:focus-visible { outline:3px solid #c4b5fd; outline-offset:2px; }
.container { max-width:1440px; margin:auto; padding:0 44px 60px; }
header { display:flex; align-items:center; justify-content:space-between; gap:20px; min-height:84px;
         border-bottom:1px solid var(--border); margin-bottom:42px; }
.brand { display:flex; align-items:center; gap:12px; color:var(--text); text-decoration:none; }
.brand:hover { text-decoration:none; color:var(--text); }
.brand-mark { display:grid; place-items:center; width:39px; height:39px; border:1px solid #7158ba; border-radius:12px;
              color:#e2d7ff; background:linear-gradient(145deg,#7052c9,#32255e); font-size:19px; font-weight:800; }
.logo { font-weight:750; letter-spacing:-.03em; font-size:17px; line-height:1.2; }
.brand-caption { color:var(--subtle); font-size:10px; font-weight:700; letter-spacing:.13em; text-transform:uppercase; }
.sub { color:var(--muted); margin-top:4px; font-size:12px; }
.who { display:flex; align-items:center; gap:12px; color:var(--muted); font-size:13px; }
.who b { color:var(--text); } .inline { display:inline; }
.eyebrow { color:var(--accent); font-size:11px; font-weight:750; letter-spacing:.14em; text-transform:uppercase; margin:0 0 8px; }
h1 { font-size:clamp(27px,4vw,37px); letter-spacing:-.045em; line-height:1.15; margin:0 0 10px; }
h2 { font-size:17px; letter-spacing:-.02em; margin:0 0 7px; } h3 { font-size:14px; margin:0 0 8px; }
p { margin:0; } p.muted { color:var(--muted); margin:0 0 20px; }
.page-heading { display:flex; justify-content:space-between; align-items:end; gap:20px; margin-bottom:28px; }
.page-heading p:not(.eyebrow) { color:var(--muted); max-width:650px; }
.stats { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; margin:0 0 28px; }
.stat-card,.card { background:linear-gradient(145deg,#151b29,#101520); border:1px solid var(--border); border-radius:14px; }
.stat-card { padding:20px 22px; min-width:0; } .stat-lbl { color:var(--muted); font-size:12px; font-weight:600; }
.stat-val { font-size:27px; font-weight:730; letter-spacing:-.04em; margin-top:7px; line-height:1.2; }
.card { padding:24px; margin-bottom:16px; } .card-head { display:flex; gap:14px; align-items:flex-start; margin-bottom:20px; }
.section-icon { display:grid; place-items:center; flex:none; width:39px; height:39px; background:#292440; border:1px solid #44365f;
                border-radius:11px; font-size:17px; } .card-head p { color:var(--muted); font-size:13px; }
.btn { display:inline-flex; justify-content:center; align-items:center; gap:8px; background:#8465df; border:1px solid #9b80ef;
       color:white; border-radius:9px; padding:10px 16px; font:inherit; font-size:13px; font-weight:700; cursor:pointer;
       text-decoration:none; white-space:nowrap; } .btn:hover { background:#977cec; color:white; text-decoration:none; }
.btn-plain { background:#1b2231; border-color:#344054; color:#e2e8f3; } .btn-plain:hover { background:#273247; }
.form-group { margin-bottom:17px; min-width:0; } label { display:block; color:#d7dfec; font-size:12px; font-weight:650; margin-bottom:7px; }
input,select { width:100%; min-height:43px; padding:10px 12px; color:var(--text); background:#0b101a; border:1px solid #354054;
               border-radius:8px; font:inherit; } input[type=hidden] { display:none; }
input:focus,select:focus { border-color:#a78bfa; } input::placeholder { color:#79879b; }
.hint { display:block; color:var(--muted); font-size:12px; margin-top:6px; }
.note { padding:13px 16px; margin-bottom:18px; border:1px solid #3c4c65; border-radius:10px; color:#d8e5fa; background:#182337; }
.note.ok { border-color:#2f765d; color:#b2f3cf; background:#122b24; } .note.bad { border-color:#9d4f5a; }
.center { text-align:center; padding:70px 24px; } .center .btn { margin-top:5px; }
.grid,.form-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:0 18px; }
.server-box { border:1px solid var(--border); border-radius:10px; padding:16px; display:flex; justify-content:space-between; gap:15px; }
.server-name { font-weight:700; overflow-wrap:anywhere; } .server-meta,.table-note { font-size:12px; color:var(--muted); }
.filters { display:flex; flex-wrap:wrap; gap:12px; align-items:end; margin:22px 0; }
.filters .form-group { flex:1 1 140px; margin:0; } .filters .form-group:first-child { flex:2 1 210px; }
.table-wrap { overflow-x:auto; } table { width:100%; border-collapse:collapse; text-align:left; }
th { color:var(--subtle); font-size:10px; font-weight:750; letter-spacing:.08em; text-transform:uppercase; white-space:nowrap; }
th,td { padding:15px 12px; border-bottom:1px solid #252e3d; } td { color:#d8dfeb; font-size:13px; }
td:first-child { min-width:180px; font-weight:650; } tbody tr:hover { background:#ffffff05; } tbody tr:last-child td { border-bottom:0; }
.tag { display:inline-flex; padding:3px 9px; border-radius:99px; font-size:11px; font-weight:700; background:#242a37; color:#abb5c5; }
.tag.on { background:#15352e; color:#84dfb3; } .tag.off { color:#b0b8c7; }
.settings-layout { display:grid; grid-template-columns:210px minmax(0,1fr); gap:24px; align-items:start; }
.section-nav { position:sticky; top:22px; padding:12px; border:1px solid var(--border); background:#111723; border-radius:12px; }
.section-nav span { display:block; padding:8px 10px; color:var(--subtle); font-size:10px; font-weight:750; letter-spacing:.12em; }
.section-nav a { display:block; padding:10px; color:#c4cede; border-radius:7px; font-size:13px; font-weight:600; }
.section-nav a:hover { background:#232b3b; text-decoration:none; }
.settings-actions { display:flex; align-items:center; justify-content:space-between; gap:14px; padding:18px 0; }
.settings-actions p { color:var(--muted); font-size:12px; } .settings-actions .buttons { display:flex; gap:10px; }
@media (max-width:900px) { .container { padding:0 24px 40px; } .stats { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .settings-layout { grid-template-columns:1fr; } .section-nav { position:static; display:flex; flex-wrap:wrap; gap:3px; }
  .section-nav span { width:100%; } }
@media (max-width:600px) { .container { padding:0 16px 35px; } header { margin-bottom:30px; flex-wrap:wrap; padding:15px 0; }
  .who { width:100%; justify-content:space-between; } .page-heading { align-items:flex-start; flex-direction:column; }
  .card { padding:18px; } .grid,.form-grid { grid-template-columns:1fr; } .stat-card { padding:16px; }
  .stat-val { font-size:23px; } .settings-actions { align-items:stretch; flex-direction:column; } }
"""


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
        r.add_get("/", self.handle_home)
        r.add_get("/servers", self.handle_home)
        r.add_get("/servers/{guild_id}", self.handle_server)
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
        return self._sign({
            "t": "session", "uid": int(uid), "name": name, "local": local,
            "csrf": secrets.token_urlsafe(24), "exp": int(time.time()) + SESSION_TTL,
        })

    def _set_cookie(self, resp: web.StreamResponse, name: str, value: str, max_age: int) -> None:
        resp.set_cookie(name, value, max_age=max_age, path="/", httponly=True, samesite="Lax",
                        secure=self.secure_cookies)

    def _user(self, request: web.Request) -> dict | None:
        """The signed-in user, or (localhost-only mode) a fresh local session."""
        user = self._unsign(request.cookies.get(SESSION_COOKIE), "session")
        if user and bool(user.get("local")) != self.local_mode:
            user = None  # a cookie from the other mode is never valid
        if user is None and self.local_mode:
            token = self.make_session(0, "Local", local=True)
            request[NEW_SESSION_KEY] = token
            user = self._unsign(token, "session")
        return user

    # ------------------------------------------------------------------ middleware
    def _origin(self, request: web.Request) -> str:
        if self.public_url:
            parts = urlsplit(self.public_url)
            return f"{parts.scheme}://{parts.netloc}"
        return f"{request.scheme}://{request.host}"

    @web.middleware
    async def _security_middleware(self, request: web.Request, handler):
        if self.local_mode:
            hostname = urlsplit(f"//{request.host or ''}").hostname or ""
            if hostname not in LOCAL_HOSTNAMES:
                return self._finish(request, web.Response(
                    status=421, text=f"Open the dashboard at http://127.0.0.1:{self.port}"))
        if request.method == "POST":
            origin = request.headers.get("Origin")
            fetch_site = request.headers.get("Sec-Fetch-Site")
            if (origin and origin != self._origin(request)) or fetch_site == "cross-site":
                return self._finish(request, web.Response(status=403, text="Cross-site request refused"))
        # Recheck every request so losing ownership or developer status revokes an existing session.
        if self.login_enabled and request.path not in {"/login", "/callback", "/logout"}:
            user = self._user(request)
            if user and not await self._may_use_dashboard(user):
                return self._finish(request, self._message("Access denied", "Only server owners and bot developers may use this dashboard.", 403))
        try:
            resp = await handler(request)
        except web.HTTPException as exc:
            for k, v in SECURITY_HEADERS.items():
                exc.headers.setdefault(k, v)
            raise
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
        token = request.get(NEW_SESSION_KEY)
        if token:
            self._set_cookie(resp, SESSION_COOKIE, token, SESSION_TTL)
        return resp

    # ------------------------------------------------------------------ access control
    async def _is_bot_owner(self, user: dict) -> bool:
        if user.get("local"):
            return True
        try:
            if is_bot_developer(int(user["uid"]), self.bot):
                return True
            return bool(await self.bot.is_owner(int(user["uid"])))
        except Exception:
            return False

    async def _may_use_dashboard(self, user: dict) -> bool:
        if user.get("local"):
            return False
        if await self._is_bot_owner(user):
            return True
        return any(g.owner_id == user.get("uid") and self._server_authorized(g) for g in self.bot.guilds)

    def _server_authorized(self, guild) -> bool:
        check = getattr(self.bot, "is_authorized", None)
        return bool(check(guild.id)) if callable(check) else False

    async def _can_manage_rbl(self, user: dict, guild) -> bool:
        # Local mode has no verified Discord identity, even though it may manage other settings.
        if user.get("local"):
            return False
        try:
            uid = int(user["uid"])
            return uid == guild.owner_id or is_bot_developer(uid, self.bot) or await self._is_bot_owner(user)
        except (KeyError, TypeError, ValueError):
            return False

    async def _guilds_for(self, user: dict) -> list:
        if await self._is_bot_owner(user):
            return list(self.bot.guilds)
        return [g for g in self.bot.guilds if g.owner_id == user["uid"] and self._server_authorized(g)]

    async def _guild_for(self, request: web.Request, user: dict):
        """The guild in the URL, only if this user may manage it (else None, so nothing is revealed)."""
        try:
            gid = int(request.match_info["guild_id"])
        except (KeyError, ValueError):
            return None
        guild = self.bot.get_guild(gid)
        if guild is None:
            return None
        if await self._is_bot_owner(user) or (guild.owner_id == user["uid"] and self._server_authorized(guild)):
            return guild
        return None

    async def _antinuke_cfg(self, gid: int) -> dict:
        """Load antinuke config the same way the antinuke cog does, so a first read caches the full defaults."""
        from cogs.antinuke import DEFAULT
        return await self.bot.db.get_config(gid, "antinuke", DEFAULT)

    @staticmethod
    def _csrf_ok(user: dict, submitted: str | None) -> bool:
        return bool(submitted) and hmac.compare_digest(str(submitted), str(user.get("csrf", "")))

    # ------------------------------------------------------------------ page helpers
    def _page(self, title: str, body: str, user: dict | None = None, subtitle: str = "") -> web.Response:
        who = ""
        if user and not user.get("local"):
            who = (f'<div class="who">Signed in as <b>{_esc(user.get("name"))}</b>'
                   f'<form class="inline" method="POST" action="/logout">'
                   f'<input type="hidden" name="csrf" value="{_esc(user.get("csrf"))}">'
                   f'<button class="btn btn-plain" type="submit">Log out</button></form></div>')
        elif user:
            who = '<div class="who">Local access (127.0.0.1)</div>'
        sub = f'<div class="sub">{subtitle}</div>' if subtitle else ""
        doc = (
            '<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
            f"<title>Veil Utilities • {_esc(title)}</title><style>{CSS}</style></head><body>"
            f'<div class="container"><header><div><a href="/" class="brand"><span class="brand-mark">V</span>'
            f'<span><span class="logo">Veil Utilities</span><br><span class="brand-caption">Control center</span></span></a>{sub}</div>{who}</header>'
            f"{body}</div></body></html>"
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
    async def handle_home(self, request: web.Request) -> web.Response:
        user = self._user(request)
        if user is None:
            return self._page("Sign in", (
                '<div class="card center"><p class="eyebrow">Veil control center</p>'
                '<h1>Security, in one place.</h1>'
                '<p class="muted">Sign in with Discord to manage your authorized servers. '
                'Bot developers can access all joined servers.</p>'
                '<a class="btn" href="/login">Log in with Discord →</a></div>'))

        guilds = await self._guilds_for(user)
        body = ('<div class="page-heading"><div><p class="eyebrow">Overview</p>'
                '<h1>Your servers, at a glance.</h1><p>Manage security and logging for the servers available to you.</p>'
                '</div></div>')
        if await self._is_bot_owner(user):
            all_guilds = list(self.bot.guilds)
            members = sum(getattr(g, "member_count", None) or len(getattr(g, "members", [])) for g in all_guilds)
            latency = self.bot.latency
            latency_ms = int(latency * 1000) if isinstance(latency, (int, float)) and latency == latency else 0
            uptime = int(time.time() - getattr(self.bot, "started_at", time.time()))
            hours, rem = divmod(max(uptime, 0), 3600)
            if not user.get("local"):
                body += ('<p style="margin-bottom:1rem"><a class="btn btn-plain" href="/authorization">'
                         'Server authorization</a></p>')
            body += (
                '<div class="stats">'
                f'<div class="stat-card"><div class="stat-lbl">Servers</div><div class="stat-val">{len(all_guilds)}</div></div>'
                f'<div class="stat-card"><div class="stat-lbl">Members</div><div class="stat-val">{members}</div></div>'
                f'<div class="stat-card"><div class="stat-lbl">Gateway latency</div><div class="stat-val">{latency_ms}ms</div></div>'
                f'<div class="stat-card"><div class="stat-lbl">Uptime</div><div class="stat-val">{hours}h {rem // 60}m</div></div>'
                "</div>"
            )
        # Search and filters are evaluated on the server, after the access check. No guild
        # names or settings for other owners are sent to the browser.
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
            if status != "any" and enabled != (status == "on"):
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
            if count is None:
                count = len(getattr(g, "members", []))
            visible.append((g, count, enabled, auth, log_count))
        if order == "members":
            visible.sort(key=lambda row: (-row[1], row[0].name.casefold(), row[0].id))
        else:
            visible.sort(key=lambda row: (row[0].name.casefold(), row[0].id))

        def option(value, label, selected):
            return f'<option value="{value}"{" selected" if selected else ""}>{label}</option>'

        rows = []
        for g, count, enabled, auth, log_count in visible:
            state = "on" if enabled else "off"
            rows.append(
                f'<tr><td><a href="/servers/{_esc(g.id)}">{_esc(g.name)}</a></td>'
                f'<td>{_esc(g.id)}</td><td>{_esc(count)}</td>'
                f'<td><span class="tag {state}">{state.title()}</span></td>'
                f'<td>{auth.title()}</td><td>{log_count} configured</td>'
                f'<td><a class="btn btn-plain" href="/servers/{_esc(g.id)}">Configure</a></td></tr>'
            )
        listing = "".join(rows) or '<tr><td colspan="7">No servers match your filters.</td></tr>'
        status_options = "".join(option(v, label, status == v) for v, label in
                                 (("any", "Any"), ("on", "On"), ("off", "Off")))
        sort_options = "".join(option(v, label, order == v) for v, label in
                               (("name", "Name"), ("members", "Members")))
        auth_options = "".join(option(v, label, authorization == v) for v, label in
                               (("any", "Any"), ("yes", "Authorized"), ("no", "Not authorized")))
        log_options = "".join(option(v, label, log_filter == v) for v, label in
                              (("any", "Any"), ("configured", "Configured"), ("none", "None configured")))
        body += (f'<div class="card"><h2>🛡️ Your servers</h2>'
                 '<p class="muted">Manage role blacklists, logging channels and antinuke settings. '
                 'Log counts show explicitly configured channels, not automatic routing.</p>'
                 '<form method="GET" action="/" class="filters">'
                 f'<div class="form-group"><label for="q">Search name or ID</label>'
                 f'<input id="q" name="q" value="{_esc(query)}"></div>'
                 f'<div class="form-group"><label for="antinuke">Antinuke</label>'
                 f'<select id="antinuke" name="antinuke">{status_options}</select></div>'
                 f'<div class="form-group"><label for="authorized">Authorization</label>'
                 f'<select id="authorized" name="authorized">{auth_options}</select></div>'
                 f'<div class="form-group"><label for="logs">Log channels</label>'
                 f'<select id="logs" name="logs">{log_options}</select></div>'
                 f'<div class="form-group"><label for="sort">Sort by</label>'
                 f'<select id="sort" name="sort">{sort_options}</select></div>'
                 '<button class="btn" type="submit">Apply filters</button>'
                 '<a class="btn btn-plain" href="/">Clear</a></form>'
                 f'<p class="table-note">{len(visible)} of {len(guilds)} servers shown</p>'
                 '<div class="table-wrap"><table><thead><tr><th>Server</th><th>ID</th><th>Members</th>'
                 '<th>Antinuke</th><th>Authorized</th><th>Logs</th><th></th></tr></thead>'
                 f'<tbody>{listing}</tbody></table></div></div>')
        return self._page("Dashboard", body, user)

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
            added = await cog.authorize(gid, int(user["uid"]), "via dashboard")
            log.info("Dashboard: developer %s authorized guild %s (added=%s)", user["uid"], gid, added)
            return self._message("Server authorized" if added else "Already authorized",
                                 f"Server {gid} {'can now invite the bot' if added else 'is already on the list'}.", user=user)
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
            removed = await cog.unauthorize(gid)
            if not removed:
                return self._message("Not authorized", "That server is not on the explicit authorization list.", 404, user)
            log.info("Dashboard: developer %s revoked authorization for guild %s", user["uid"], gid)
            guild = self.bot.get_guild(gid)
            detail = "The bot is not currently in that server."
            if guild:
                # Serialize the final check with authorization, just like the join rejection path.
                async with cog._auth_lock:
                    if self.bot.is_authorized(gid):
                        detail = "The bot stayed because this server is still automatically authorized or authorization is disabled."
                    else:
                        try:
                            await asyncio.wait_for(guild.leave(), timeout=15)
                            detail = "The bot left the server."
                        except Exception:
                            log.exception("Dashboard: failed to leave revoked guild %s", gid)
                            detail = "The leave failed or timed out; check Discord and the bot logs, then remove it manually if needed."
            return self._message("Authorization revoked", detail, user=user)
        return self._message("Invalid action", "No changes were made.", 400, user)

    async def handle_server(self, request: web.Request) -> web.Response:
        user = self._user(request)
        if user is None:
            return self._redirect("/login" if self.login_enabled else "/")
        guild = await self._guild_for(request, user)
        if guild is None:
            return self._message("Server not found", "Veil isn't in that server, or you don't own it.", 404, user)

        can_rbl = await self._can_manage_rbl(user, guild)
        rbl = (await self.bot.db.get_config(guild.id, "roleblacklist") or {}) if can_rbl else {}
        logs_cfg = await self.bot.db.get_config(guild.id, "logs") or {}
        an_cfg = await self._antinuke_cfg(guild.id)

        def ids(values) -> str:
            return _esc(", ".join(str(v) for v in values or []))

        current = an_cfg.get("punishment") or "ban"
        options = "".join(
            f'<option value="{p}"{" selected" if p == current else ""}>{p}</option>' for p in PUNISHMENTS)
        saved = ('<div class="note ok">Settings saved.</div>' if request.query.get("saved") else "")

        from cogs.logging import KINDS
        channels = sorted(getattr(guild, "text_channels", []), key=lambda ch: (ch.position, ch.id))
        log_forms = []
        for field, key in LOG_FIELDS:
            current_log = logs_cfg.get(key)
            value = str(current_log) if current_log is not None else ""
            choices = [("", "Auto"), ("-1", "Off")]
            choices.extend((str(ch.id), f"#{ch.name}") for ch in channels)
            if value and value not in {v for v, _ in choices}:
                choices.append((value, f"Unavailable channel ({value})"))
            options_html = "".join(f'<option value="{_esc(v)}"{" selected" if v == value else ""}>{_esc(label)}</option>'
                                   for v, label in choices)
            log_forms.append(f'<div class="form-group"><label for="{field}">{_esc(KINDS[key].label)}</label>'
                             f'<select id="{field}" name="{field}">{options_html}</select></div>')
        logs_form = "".join(log_forms)
        ignored = logs_cfg.get("ignore") or {}

        rbl_form = f'''<section class="card" id="blacklist"><div class="card-head"><span class="section-icon">⛔</span>
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
      </select><span class="hint">Controls role-blacklist replies; direct messages can fail if the recipient has DMs closed.</span></div>
  </section>''' if can_rbl else ""
        body = f"""<div class="page-heading"><div><p class="eyebrow">Server settings</p>
  <h1>{_esc(guild.name)}</h1><p>Configure how Veil protects your server and routes its activity logs.</p></div>
  <a class="btn btn-plain" href="/">← All servers</a></div>
{saved}<div class="settings-layout"><nav class="section-nav" aria-label="Settings sections">
  <span>ON THIS PAGE</span>{'<a href="#blacklist">Role blacklist</a>' if can_rbl else ''}
  <a href="#logging">Logging channels</a><a href="#exclusions">Log exclusions</a><a href="#antinuke">Antinuke</a>
  <a href="#trusted">Trusted identities</a></nav>
<form method="POST" action="/api/guilds/{_esc(guild.id)}/config">
  <input type="hidden" name="csrf" value="{_esc(user["csrf"])}">
{rbl_form}
  <section class="card" id="logging"><div class="card-head"><span class="section-icon">▤</span>
    <div><h2>Logging channels</h2><p>Choose where each type of activity is sent.</p></div></div>
    <div class="note">Auto uses Veil's normal routing. Off disables that category. Select a channel to override it.</div>
    <div class="form-grid">{logs_form}</div>
  </section>
  <section class="card" id="exclusions"><div class="card-head"><span class="section-icon">◌</span>
    <div><h2>Log exclusions</h2><p>Exclude activity from specific channels or users when the event supports it.</p></div></div>
    <div class="form-grid"><div class="form-group"><label for="log_ignore_channels">Ignored channel IDs</label>
      <input id="log_ignore_channels" name="log_ignore_channels" value="{ids(ignored.get("channels"))}" placeholder="Channel IDs, separated by commas">
      <span class="hint">Events that include a source channel will not be logged for these channels.</span></div>
    <div class="form-group"><label for="log_ignore_users">Ignored user IDs</label>
      <input id="log_ignore_users" name="log_ignore_users" value="{ids(ignored.get("users"))}" placeholder="User IDs, separated by commas">
      <span class="hint">Events that include a user will not be logged for these users.</span></div></div>
  </section>
  <section class="card" id="antinuke"><div class="card-head"><span class="section-icon">◇</span>
    <div><h2>Antinuke</h2><p>Manage the default response and the people who administer protection.</p></div></div>
    <div class="note">Protection is currently <strong>{'on' if an_cfg.get("enabled") else 'off'}</strong>.
      Enable or disable protection and manage individual rules using Veil's Discord commands.</div>
    <div class="form-grid"><div class="form-group"><label for="an_punishment">Default punishment</label>
      <select id="an_punishment" name="an_punishment">{options}</select><span class="hint">Used by modules without their own punishment.</span></div>
    <div class="form-group"><label for="an_admins">Antinuke admin user IDs</label>
      <input id="an_admins" type="text" name="an_admins" value="{ids(an_cfg.get("admins"))}" placeholder="User IDs, separated by commas">
      <span class="hint">Admins can configure antinuke and are exempt from punishment.</span></div></div>
  </section>
  <section class="card" id="trusted"><div class="card-head"><span class="section-icon">☆</span>
    <div><h2>Trusted identities</h2><p>Antinuke exclusions for trusted users and roles.</p></div></div>
    <div class="note">Trusted users and members with trusted roles are exempt from antinuke enforcement; trusted bots are also exempt from bot-add detection and quarantine. Add only identities you trust.</div>
    <div class="form-grid"><div class="form-group"><label for="an_whitelist">Trusted user IDs</label>
      <input id="an_whitelist" name="an_whitelist" value="{ids(an_cfg.get("whitelist"))}" placeholder="User IDs, separated by commas"></div>
    <div class="form-group"><label for="an_whitelist_roles">Trusted role IDs</label>
      <input id="an_whitelist_roles" name="an_whitelist_roles" value="{ids(an_cfg.get("whitelist_roles"))}" placeholder="Role IDs, separated by commas"></div></div>
  </section>
  <div class="settings-actions"><p>Changes apply after you save. Review security exemptions carefully.</p>
    <div class="buttons"><a href="/" class="btn btn-plain">Cancel</a><button type="submit" class="btn">Save changes</button></div></div>
</form></div>"""

        return self._page(guild.name, body, user, subtitle=f"Server settings: <b>{_esc(guild.name)}</b>")

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
            log.warning("Dashboard login failed: %s", e)
            return self._message("Login failed", "Couldn't verify your Discord account. Please try again.", 502)
        name = str(me.get("global_name") or me.get("username") or uid)
        if not await self._may_use_dashboard({"uid": uid, "local": False}):
            resp = self._message("Access denied", "Only server owners and bot developers may use this dashboard.", 403)
            resp.del_cookie(STATE_COOKIE, path="/")
            return resp
        resp = self._redirect("/")
        self._set_cookie(resp, SESSION_COOKIE, self.make_session(uid, name), SESSION_TTL)
        resp.del_cookie(STATE_COOKIE, path="/")
        log.info("Dashboard login: %s (%s)", name, uid)
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
             "member_count": getattr(g, "member_count", None) or len(getattr(g, "members", []))}
            for g in guilds
        ])

    async def handle_api_get_config(self, request: web.Request) -> web.Response:
        user = self._user(request)
        if user is None:
            return web.json_response({"error": "login required"}, status=401)
        guild = await self._guild_for(request, user)
        if guild is None:
            return web.json_response({"error": "not found"}, status=404)
        result = {
            "logs": await self.bot.db.get_config(guild.id, "logs") or {},
            "antinuke": await self._antinuke_cfg(guild.id),
            "permlevels": await self.bot.db.get_config(guild.id, "permlevels") or {},
        }
        if await self._can_manage_rbl(user, guild):
            result["roleblacklist"] = await self.bot.db.get_config(guild.id, "roleblacklist") or {}
        return web.json_response(result)

    async def handle_api_post_config(self, request: web.Request) -> web.Response:
        user = self._user(request)
        if user is None:
            return web.json_response({"error": "login required"}, status=401)
        guild = await self._guild_for(request, user)
        if guild is None:
            return self._message("Server not found", "Veil isn't in that server, or you don't own it.", 404, user)
        data = await request.post()
        if not self._csrf_ok(user, data.get("csrf")):
            return self._message("Session expired", "Please refresh the page and try again.", 403, user)
        gid = guild.id
        rbl_fields = {"rbl_roles", "rbl_authorized"} & set(data)
        if (rbl_fields or "rbl_reply_mode" in data) and not await self._can_manage_rbl(user, guild):
            return self._message("Access denied", "Discord owner or developer login is required for role blacklist settings.", 403, user)

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
        an_cfg = deepcopy(await self._antinuke_cfg(gid))
        if "an_whitelist_roles" in lists:
            old_roles = set(an_cfg.get("whitelist_roles") or [])
            for role_id in lists["an_whitelist_roles"]:
                if role_id == guild.id:
                    return self._message("Invalid settings", "The @everyone role cannot be trusted. Nothing was saved.", 400, user)
                if role_id not in old_roles:
                    get_role = getattr(guild, "get_role", None)
                    if not callable(get_role) or get_role(role_id) is None:
                        return self._message("Invalid settings", "Trusted roles must belong to this server. Nothing was saved.", 400, user)
        logs_cfg = deepcopy(await self.bot.db.get_config(gid, "logs") or {})
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

        if rbl_fields:
            rbl = deepcopy(await self.bot.db.get_config(gid, "roleblacklist") or {})
            if "rbl_roles" in data and set(lists["rbl_roles"]) & set(rbl.get("hard_roles", [])):
                return self._message("Invalid settings", "A role cannot be on both the ordinary and hard blacklists. Nothing was saved.", 400, user)
            if "rbl_roles" in data:
                rbl["roles"] = lists["rbl_roles"]
            if "rbl_authorized" in data:
                rbl["authorized"] = lists["rbl_authorized"]
            await self.bot.db.set_config_committed(gid, "roleblacklist", rbl)
        for field, key in LOG_FIELDS:
            if field not in data:
                continue
            value = str(data[field]).strip()
            if not value:
                logs_cfg[key] = None  # same as `log set <type>` with no channel: back to auto
            else:
                logs_cfg[key] = int(value)
        if "rbl_reply_mode" in data:
            logs_cfg["rbl_dm"] = data["rbl_reply_mode"] == "dm"
        for field, key in (("log_ignore_channels", "channels"), ("log_ignore_users", "users")):
            if field in data:
                if not isinstance(logs_cfg.get("ignore"), dict):
                    logs_cfg["ignore"] = {}
                logs_cfg["ignore"][key] = lists[field]
        await self.bot.db.set_config(gid, "logs", logs_cfg)

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
        await self.bot.db.set_config_committed(gid, "antinuke", an_cfg)
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

        log.info("Dashboard: %s (%s) updated settings for guild %s", user.get("name"), user.get("uid"), gid)
        return self._redirect(f"/servers/{gid}?saved=1")

    # ------------------------------------------------------------------ lifecycle
    async def start(self):
        if not self.login_enabled or not self.secure_cookies:
            raise RuntimeError("Dashboard requires Discord login and an HTTPS public URL; refusing to listen")
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
