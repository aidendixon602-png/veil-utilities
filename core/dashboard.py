"""Per-server web dashboard for Veil Utilities.

Security model
--------------
* Off unless ``DASHBOARD_ENABLED=true``.
* Without Discord login configured it only listens on 127.0.0.1, and the person at that machine is treated as a
  bot owner. Requests must use a localhost address in the Host header, which blocks DNS-rebinding tricks.
* With ``DISCORD_CLIENT_ID``, ``DISCORD_CLIENT_SECRET`` and ``DASHBOARD_URL`` set, people sign in with Discord
  (``identify`` scope only) and each server is shown only to its owner or to a bot owner. This mirrors the
  Discord commands: only the server owner can change antinuke admins, which this page edits.
* Login cookies are HMAC-signed, every form carries a CSRF token, cross-site POSTs are refused, and every value
  written into HTML is escaped.
"""
from __future__ import annotations

import base64
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

import aiohttp
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
LOG_FIELDS = (("log_commands", "commands"), ("log_bans", "bans"), ("log_joins", "joinleave"))

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
    """Comma-separated Discord IDs -> unique ints (anything that isn't an ID is dropped)."""
    out: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit() and len(part) <= 20 and int(part) not in out:
            out.append(int(part))
    return out[:MAX_IDS]


CSS = """
:root { --bg:#0f0d13; --card:#18151f; --accent:#b10f2e; --accent-hover:#d11237; --text:#f0edf5;
        --muted:#8c8599; --border:#282433; --ok:#2ecc71; --bad:#e74c3c; }
* { box-sizing:border-box; margin:0; padding:0;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
body { background:var(--bg); color:var(--text); padding:2rem 1rem; }
.container { max-width:1000px; margin:0 auto; }
header { display:flex; justify-content:space-between; align-items:center; gap:1rem; flex-wrap:wrap;
         border-bottom:1px solid var(--border); padding-bottom:1.25rem; margin-bottom:2rem; }
.logo { font-size:1.5rem; font-weight:700; color:var(--accent); }
.sub { font-size:1.05rem; margin-top:.25rem; }
.who { display:flex; align-items:center; gap:.75rem; color:var(--muted); font-size:.9rem; }
.stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:1rem; margin-bottom:2rem; }
.stat-card, .card { background:var(--card); border:1px solid var(--border); border-radius:8px; padding:1.25rem; }
.card { margin-bottom:1.5rem; }
.stat-val { font-size:1.6rem; font-weight:700; margin-top:.25rem; }
.stat-lbl, label { font-size:.85rem; color:var(--muted); }
.stat-lbl { text-transform:uppercase; letter-spacing:.05em; }
h2 { font-size:1.2rem; margin-bottom:1rem; }
p.muted { color:var(--muted); margin-bottom:1.25rem; }
.grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:1rem; }
.server-box { border:1px solid var(--border); border-radius:6px; padding:1rem; display:flex;
              justify-content:space-between; align-items:center; gap:1rem; }
.server-name { font-weight:600; overflow-wrap:anywhere; }
.server-meta { font-size:.8rem; color:var(--muted); }
.btn { background:var(--accent); color:#fff; padding:.55rem 1.1rem; border-radius:6px; text-decoration:none;
       font-size:.875rem; font-weight:600; border:none; cursor:pointer; display:inline-block; }
.btn:hover { background:var(--accent-hover); }
.btn-plain { background:var(--border); color:var(--text); }
.form-group { margin-bottom:1rem; }
label { display:block; margin-bottom:.35rem; }
input, select { width:100%; padding:.6rem .8rem; background:var(--bg); border:1px solid var(--border);
                border-radius:6px; color:var(--text); font-size:.9rem; }
input:focus, select:focus { outline:none; border-color:var(--accent); }
.note { padding:.75rem 1rem; border-radius:6px; margin-bottom:1.5rem; border:1px solid var(--border); }
.note.ok { border-color:var(--ok); } .note.bad { border-color:var(--bad); }
.center { text-align:center; padding:3rem 1rem; }
form.inline { display:inline; }
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

        wanted = host or ("0.0.0.0" if self.login_enabled else "127.0.0.1")
        if self.local_mode and not _is_loopback(wanted):
            log.warning("Dashboard: ignoring DASHBOARD_HOST=%s. Without Discord login configured it only listens on "
                        "127.0.0.1 (set DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET and DASHBOARD_URL to make it public)",
                        wanted)
            wanted = "127.0.0.1"
        self.host = wanted

        self._secret = (secret or secrets.token_hex(32)).encode()
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
            client_id=settings.discord_client_id,
            client_secret=settings.discord_client_secret,
            secret=settings.dashboard_secret,
        )

    def _setup_routes(self):
        r = self.app.router
        r.add_get("/", self.handle_home)
        r.add_get("/servers", self.handle_home)
        r.add_get("/servers/{guild_id}", self.handle_server)
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
        try:
            resp = await handler(request)
        except web.HTTPException as exc:
            for k, v in SECURITY_HEADERS.items():
                exc.headers.setdefault(k, v)
            raise
        return self._finish(request, resp)

    def _finish(self, request: web.Request, resp: web.StreamResponse) -> web.StreamResponse:
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
            return bool(await self.bot.is_owner(int(user["uid"])))
        except Exception:
            return False

    async def _guilds_for(self, user: dict) -> list:
        if await self._is_bot_owner(user):
            return list(self.bot.guilds)
        return [g for g in self.bot.guilds if g.owner_id == user["uid"]]

    async def _guild_for(self, request: web.Request, user: dict):
        """The guild in the URL, only if this user may manage it (else None, so nothing is revealed)."""
        try:
            gid = int(request.match_info["guild_id"])
        except (KeyError, ValueError):
            return None
        guild = self.bot.get_guild(gid)
        if guild is None:
            return None
        if guild.owner_id == user["uid"] or await self._is_bot_owner(user):
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
            f'<div class="container"><header><div><div class="logo">⚡ Veil Utilities</div>{sub}</div>{who}</header>'
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
                '<div class="card center"><h2>Sign in to manage your servers</h2>'
                '<p class="muted">You can manage the servers you own that Veil is in.</p>'
                '<a class="btn" href="/login">Log in with Discord</a></div>'))

        guilds = await self._guilds_for(user)
        body = ""
        if await self._is_bot_owner(user):
            all_guilds = list(self.bot.guilds)
            members = sum(getattr(g, "member_count", None) or len(getattr(g, "members", [])) for g in all_guilds)
            latency = self.bot.latency
            latency_ms = int(latency * 1000) if isinstance(latency, (int, float)) and latency == latency else 0
            uptime = int(time.time() - getattr(self.bot, "started_at", time.time()))
            hours, rem = divmod(max(uptime, 0), 3600)
            body += (
                '<div class="stats">'
                f'<div class="stat-card"><div class="stat-lbl">Servers</div><div class="stat-val">{len(all_guilds)}</div></div>'
                f'<div class="stat-card"><div class="stat-lbl">Members</div><div class="stat-val">{members}</div></div>'
                f'<div class="stat-card"><div class="stat-lbl">Gateway latency</div><div class="stat-val">{latency_ms}ms</div></div>'
                f'<div class="stat-card"><div class="stat-lbl">Uptime</div><div class="stat-val">{hours}h {rem // 60}m</div></div>'
                "</div>"
            )
        cards = []
        for g in guilds:
            count = getattr(g, "member_count", None) or len(getattr(g, "members", []))
            cards.append(
                f'<div class="server-box"><div><div class="server-name">{_esc(g.name)}</div>'
                f'<div class="server-meta">{_esc(count)} members • ID {_esc(g.id)}</div></div>'
                f'<a href="/servers/{_esc(g.id)}" class="btn">Configure</a></div>'
            )
        listing = "\n".join(cards) or '<p class="muted">You don\'t own any servers that Veil is in.</p>'
        body += ('<div class="card"><h2>🛡️ Your servers</h2>'
                 '<p class="muted">Manage role blacklists, logging channels and antinuke settings.</p>'
                 f'<div class="grid">{listing}</div></div>')
        return self._page("Dashboard", body, user)

    async def handle_server(self, request: web.Request) -> web.Response:
        user = self._user(request)
        if user is None:
            return self._redirect("/login" if self.login_enabled else "/")
        guild = await self._guild_for(request, user)
        if guild is None:
            return self._message("Server not found", "Veil isn't in that server, or you don't own it.", 404, user)

        rbl = await self.bot.db.get_config(guild.id, "roleblacklist") or {}
        logs_cfg = await self.bot.db.get_config(guild.id, "logs") or {}
        an_cfg = await self._antinuke_cfg(guild.id)

        def ids(values) -> str:
            return _esc(", ".join(str(v) for v in values or []))

        current = an_cfg.get("punishment") or "ban"
        options = "".join(
            f'<option value="{p}"{" selected" if p == current else ""}>{p}</option>' for p in PUNISHMENTS)
        saved = ('<div class="note ok">Settings saved.</div>' if request.query.get("saved") else "")

        body = f"""{saved}
<form method="POST" action="/api/guilds/{_esc(guild.id)}/config">
  <input type="hidden" name="csrf" value="{_esc(user["csrf"])}">
  <div class="card"><h2>🚫 Role blacklist (BLED)</h2>
    <div class="form-group"><label for="rbl_roles">Blacklisted role IDs (comma-separated)</label>
      <input id="rbl_roles" type="text" name="rbl_roles" value="{ids(rbl.get("roles"))}"></div>
    <div class="form-group"><label for="rbl_authorized">Authorized user and role IDs (comma-separated)</label>
      <input id="rbl_authorized" type="text" name="rbl_authorized" value="{ids(rbl.get("authorized"))}"></div>
  </div>
  <div class="card"><h2>📜 Logging channels</h2>
    <div class="form-group"><label for="log_commands">Command logs channel ID</label>
      <input id="log_commands" type="text" name="log_commands" value="{_esc(logs_cfg.get("commands") or "")}"></div>
    <div class="form-group"><label for="log_bans">Ban logs channel ID</label>
      <input id="log_bans" type="text" name="log_bans" value="{_esc(logs_cfg.get("bans") or "")}"></div>
    <div class="form-group"><label for="log_joins">Join/leave logs channel ID</label>
      <input id="log_joins" type="text" name="log_joins" value="{_esc(logs_cfg.get("joinleave") or "")}"></div>
  </div>
  <div class="card"><h2>🛡️ Antinuke</h2>
    <div class="form-group"><label for="an_punishment">Default punishment (used by modules
      without their own)</label>
      <select id="an_punishment" name="an_punishment">{options}</select></div>
    <div class="form-group"><label for="an_admins">Antinuke admin user IDs (comma-separated). Admins can configure
      antinuke and are never punished by it.</label>
      <input id="an_admins" type="text" name="an_admins" value="{ids(an_cfg.get("admins"))}"></div>
  </div>
  <div style="text-align:right"><a href="/" class="btn btn-plain">← All servers</a>
    <button type="submit" class="btn">💾 Save</button></div>
</form>"""
        return self._page(guild.name, body, user, subtitle=f"Server settings: <b>{_esc(guild.name)}</b>")

    # ------------------------------------------------------------------ Discord login
    async def handle_login(self, request: web.Request) -> web.Response:
        if not self.login_enabled:
            return self._redirect("/")
        state = secrets.token_urlsafe(24)
        query = urlencode({
            "client_id": self.client_id, "redirect_uri": self.redirect_uri, "response_type": "code",
            "scope": "identify", "state": state, "prompt": "none",
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
        return web.json_response({
            "roleblacklist": await self.bot.db.get_config(guild.id, "roleblacklist") or {},
            "logs": await self.bot.db.get_config(guild.id, "logs") or {},
            "antinuke": await self._antinuke_cfg(guild.id),
            "permlevels": await self.bot.db.get_config(guild.id, "permlevels") or {},
        })

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

        rbl = await self.bot.db.get_config(gid, "roleblacklist") or {}
        if "rbl_roles" in data:
            rbl["roles"] = _parse_ids(str(data["rbl_roles"]))
        if "rbl_authorized" in data:
            rbl["authorized"] = _parse_ids(str(data["rbl_authorized"]))
        await self.bot.db.set_config(gid, "roleblacklist", rbl)

        logs_cfg = await self.bot.db.get_config(gid, "logs") or {}
        for field, key in LOG_FIELDS:
            if field not in data:
                continue
            value = str(data[field]).strip()
            if not value:
                logs_cfg[key] = None  # same as `log set <type>` with no channel: back to auto
            elif value.isdigit() and len(value) <= 20:
                logs_cfg[key] = int(value)
        await self.bot.db.set_config(gid, "logs", logs_cfg)

        an_cfg = await self._antinuke_cfg(gid)
        an_cfg.pop("action", None)  # written by older dashboard versions; the antinuke never read it
        punishment = str(data.get("an_punishment", "")).strip().lower()
        if punishment in PUNISHMENTS:
            an_cfg["punishment"] = punishment
        if "an_admins" in data:
            an_cfg["admins"] = _parse_ids(str(data["an_admins"]))
        await self.bot.db.set_config(gid, "antinuke", an_cfg)

        log.info("Dashboard: %s (%s) updated settings for guild %s", user.get("name"), user.get("uid"), gid)
        return self._redirect(f"/servers/{gid}?saved=1")

    # ------------------------------------------------------------------ lifecycle
    async def start(self):
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
