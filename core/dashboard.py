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
import re
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


CSS = """
:root { color-scheme:dark; --bg:#0b0d17; --surface:#141826; --surface2:#1a2031;
        --border:#30374a; --text:#f5f5fb; --muted:#b0b8ca; --subtle:#929bb0;
        --accent:#a99bff; --rose:#ff8993; }
* { box-sizing:border-box; }
html { scroll-behavior:smooth; }
body { margin:0; background:var(--bg); color:var(--text);
       font:14px/1.55 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
a { color:inherit; text-decoration:none; }
a:hover { color:#fff; }
a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible { outline:2px solid #d4cbff; outline-offset:3px; }
.skip-link { position:absolute; left:-9999px; top:8px; padding:10px 14px; background:#fff; color:#111; z-index:8; }
.skip-link:focus { left:12px; color:#111; }
.shell { min-height:100vh; display:grid; grid-template-columns:224px minmax(0,1fr); }
.sidebar { background:#101321; border-right:1px solid #282d3f; display:flex; flex-direction:column;
           padding:26px 18px; gap:36px; }
.brand { display:flex; align-items:center; gap:11px; color:var(--text); padding:0 5px; }
.brand:hover { color:var(--text); }
.brand-mark { display:grid; place-items:center; width:39px; height:39px; flex:none; border-radius:12px;
              background:#704fcd; border:1px solid #9676ed; box-shadow:0 6px 18px #7452ce30; color:#fff; }
.brand-mark svg { width:23px; height:23px; }
.logo { display:block; font-size:15px; font-weight:750; letter-spacing:-.025em; line-height:1.15; }
.brand-caption { display:block; margin-top:4px; color:var(--subtle); font-size:11px; line-height:1.1; }
.nav-label { display:block; color:#8993a9; font-size:10px; font-weight:700; letter-spacing:.11em; padding:0 12px 10px; }
.nav-link { display:flex; align-items:center; gap:11px; color:#e7e5ff; background:#282846; border:1px solid #454063;
            border-radius:9px; padding:11px 12px; font-size:13px; font-weight:650; }
.nav-link svg { width:17px; height:17px; color:#c6b9ff; flex:none; }
.side-note { margin-top:auto; border:1px solid #30364b; border-radius:12px; padding:16px 14px;
             background:#191d2d; color:var(--muted); font-size:12px; line-height:1.55; }
.side-note strong { display:block; color:#e7e5f2; font-size:12px; margin-bottom:6px; }
.workspace { min-width:0; }
header { height:76px; padding:0 40px; display:flex; align-items:center; justify-content:space-between; gap:20px;
         border-bottom:1px solid #2b3142; background:#101321; }
.top-title { color:var(--muted); font-size:12px; } .top-title b { color:#f2f1f8; font-size:13px; font-weight:650; }
.top-separator { padding:0 9px; color:#677087; }
.who { display:flex; align-items:center; gap:10px; font-size:12px; color:var(--muted); }
.who b { color:var(--text); font-weight:650; } .inline { display:inline; }
.account-dot { display:grid; place-items:center; width:32px; height:32px; border-radius:50%;
               background:#393153; color:#d8cfff; font-size:13px; font-weight:700; }
.container { max-width:1440px; margin:0 auto; padding:42px 40px 72px; }
.sub { display:none; }
.eyebrow { color:#c1b5ff; font-size:11px; font-weight:700; letter-spacing:.09em; text-transform:uppercase; margin:0 0 10px; }
h1,h2,h3,p { margin:0; } h1 { font-size:clamp(27px,3vw,36px); line-height:1.18; letter-spacing:-.04em; font-weight:720; }
h2 { font-size:17px; line-height:1.3; letter-spacing:-.02em; font-weight:700; } h3 { font-size:14px; }
p.muted { color:var(--muted); margin:8px 0 20px; }
.page-heading { display:flex; justify-content:space-between; align-items:flex-end; gap:20px; margin:0 0 28px; }
.page-heading p:not(.eyebrow) { color:var(--muted); margin-top:9px; max-width:650px; }
.stats { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; margin:0 0 26px; }
.stat-card { position:relative; overflow:hidden; min-width:0; padding:20px; border:1px solid var(--border);
             border-radius:13px; background:#161b2a; box-shadow:0 10px 24px #00000012; }
.stat-card:before { content:""; display:block; width:32px; height:3px; background:#a78dff; border-radius:4px; margin-bottom:17px; }
.stat-card:nth-child(2):before { background:#f18c98; } .stat-card:nth-child(3):before { background:#73bfcc; }
.stat-card:nth-child(4):before { background:#e4ba70; }
.stat-lbl { color:var(--muted); font-size:12px; font-weight:550; }
.stat-val { margin-top:5px; font-size:27px; line-height:1.15; font-weight:720; letter-spacing:-.04em; font-variant-numeric:tabular-nums; }
.stat-foot { color:var(--subtle); font-size:11px; margin-top:7px; }
.card { background:var(--surface); border:1px solid var(--border); border-radius:14px; padding:25px; margin-bottom:16px;
        box-shadow:0 10px 25px #00000012; }
.card-head { display:flex; align-items:flex-start; gap:14px; padding-bottom:19px; margin-bottom:22px; border-bottom:1px solid #30374a; }
.section-icon { display:grid; place-items:center; width:38px; height:38px; flex:none; border:1px solid #51486f;
                background:#28243b; border-radius:11px; color:#d8c8ff; font:700 11px ui-monospace,SFMono-Regular,Consolas,monospace; }
.card-head p { color:var(--muted); font-size:13px; margin-top:4px; }
.btn { display:inline-flex; align-items:center; justify-content:center; gap:8px; min-height:40px; padding:10px 16px;
       background:#7254cb; border:1px solid #896ee0; border-radius:8px; color:white;
       font-family:inherit; font-size:12px; line-height:1.2; font-weight:700; cursor:pointer; text-decoration:none; white-space:nowrap; }
.btn:hover { background:#6243ba; border-color:#a38cf5; color:#fff; }
.btn-plain { background:#22283a; border-color:#3c445a; color:#f0f0f8; }
.btn-plain:hover { background:#30384c; border-color:#606a84; }
.form-group { margin-bottom:18px; min-width:0; }
label { display:block; color:#e0e3ed; font-size:12px; font-weight:650; margin-bottom:7px; }
input,select,textarea { display:block; width:100%; min-height:42px; padding:10px 12px; background:#0e1220; color:var(--text);
               border:1px solid #424c63; border-radius:8px; font:inherit; font-size:13px; }
input[type=hidden] { display:none; } input::placeholder { color:#98a2b6; }
input:focus,select:focus { border-color:#b7a8ff; }
.hint { display:block; color:var(--muted); font-size:12px; line-height:1.5; margin-top:7px; }
p.hint + .btn { margin-top:16px; } textarea { resize:vertical; }
.note { background:#252335; border:1px solid #554969; border-radius:9px; color:#e1d7e8; padding:13px 15px; margin-bottom:22px; font-size:13px; }
.note.ok { border-color:#35745e; color:#bcebd1; background:#19332c; }
.note.bad { border-color:#ab535b; }
.center { max-width:620px; margin:65px auto; padding:58px 42px; text-align:center; }
.center h1 { margin:0 0 14px; } .center .btn { margin-top:8px; }
.grid,.form-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:0 20px; }
.server-box { border:1px solid var(--border); border-radius:10px; padding:16px; display:flex; justify-content:space-between; gap:15px; }
.server-name { font-weight:650; overflow-wrap:anywhere; } .server-meta,.table-note { font-size:12px; color:var(--muted); }
.filters { display:flex; flex-wrap:wrap; gap:12px; align-items:end; margin:23px 0 20px; padding:18px; border:1px solid #30384a;
           border-radius:11px; background:#111522; }
.filters .form-group { flex:1 1 120px; margin:0; } .filters .form-group:first-child { flex:2 1 190px; }
.table-note { padding:0 0 5px; } .table-wrap { overflow-x:auto; } table { width:100%; border-collapse:collapse; text-align:left; }
th { color:var(--subtle); font-size:10px; font-weight:700; letter-spacing:.06em; text-transform:uppercase; white-space:nowrap; }
th,td { padding:15px 12px; border-bottom:1px solid #30374a; } td { color:#e0e3ed; font-size:13px; }
td:first-child { min-width:200px; font-weight:650; } td:first-child a:hover { color:#c5b6ff; }
.server-initial,.server-icon { display:inline-grid; vertical-align:middle; place-items:center; width:38px; height:38px; margin-right:12px;
              border-radius:12px; border:1px solid #5c5284; color:#ede8ff; background:#373055; font-size:14px; font-weight:700; }
.server-icon { object-fit:cover; }
td:nth-child(2) { font-variant-numeric:tabular-nums; color:var(--muted); font-size:12px; }
tbody tr:hover { background:#ffffff06; } tbody tr:last-child td { border-bottom:0; }
.tag { display:inline-flex; align-items:center; gap:7px; padding:4px 9px; border-radius:99px; font-size:11px; font-weight:700;
       color:#c0c7d5; background:#262b3a; }
.tag:before { content:""; width:6px; height:6px; border-radius:50%; background:#929baa; }
.tag.on { color:#a9e9c9; background:#193a31; } .tag.on:before { background:#78dba8; }
.tag.off { color:#ffd0c9; background:#482b32; } .tag.off:before { background:#ff9b91; }
#antinuke .section-icon, #blacklist .section-icon { background:#382631; border-color:#754954; color:#ffd1d2; }
#logging .section-icon, #exclusions .section-icon { background:#20333d; border-color:#3e6977; color:#b3e9ef; }
.settings-layout { display:grid; grid-template-columns:190px minmax(0,1fr); gap:24px; align-items:start; }
.section-nav { position:sticky; top:22px; border:1px solid var(--border); background:#141826; border-radius:12px; padding:14px 10px; }
.section-nav span { display:block; padding:4px 10px 10px; color:var(--subtle); font-size:10px; font-weight:700; letter-spacing:.08em; }
.section-nav a { display:block; padding:9px 10px; color:#c9ccda; border-radius:7px; font-size:13px; }
.section-nav a:hover,.section-nav a:focus { background:#29283f; color:#f2edff; }
.settings-actions { display:flex; align-items:center; justify-content:space-between; gap:14px; padding:16px 0; }
.settings-actions p { color:var(--muted); font-size:12px; } .settings-actions .buttons,.heading-actions { display:flex; flex-wrap:wrap; gap:10px; }
.confirm-choice { display:flex; align-items:center; gap:9px; font-weight:500; margin:14px 0; }
.confirm-choice input { width:18px; height:18px; min-height:0; flex:none; }
.rules-heading { border-top:1px solid var(--border); padding-top:22px; margin-top:26px; }
.rule-row { display:grid; grid-template-columns:minmax(125px,1.4fr) repeat(4,minmax(85px,1fr)) auto;
            align-items:end; gap:10px; padding:15px 0; border-bottom:1px solid var(--border); }
.rule-row .form-group { margin:0; } .rule-row .hint { margin-top:2px; }
@media (max-width:1050px) { .rule-row { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .rule-row > div:first-child { grid-column:1 / -1; } }
@media (max-width:1050px) { .shell { grid-template-columns:76px minmax(0,1fr); } .sidebar { padding:24px 12px; }
  .brand { justify-content:center; padding:0; } .brand-copy,.nav-label,.nav-text,.side-note { display:none; }
  .nav-link { justify-content:center; padding:12px; } .settings-layout { grid-template-columns:1fr; gap:18px; }
  .section-nav { position:static; display:flex; flex-wrap:wrap; gap:3px; } .section-nav span { width:100%; } }
@media (max-width:760px) { .shell { display:block; } .sidebar { display:none; } header { height:auto; min-height:66px; padding:15px 20px; }
  .container { padding:30px 20px 50px; } .stats { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .top-title { display:block; } .page-heading { align-items:flex-start; flex-direction:column; } }
@media (max-width:480px) { header { flex-wrap:wrap; } .who { width:100%; justify-content:flex-end; }
  .container { padding:26px 15px 36px; } .card { padding:20px 16px; } .grid,.form-grid { grid-template-columns:1fr; }
  .stat-card { padding:16px; } .stat-val { font-size:23px; } .settings-actions { align-items:stretch; flex-direction:column; }
  .center { margin:35px auto; padding:42px 20px; } }
@media (max-width:760px) {
  .server-table,.server-table tbody { display:block; width:100%; }
  .server-table thead { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden;
                        clip:rect(0,0,0,0); white-space:nowrap; border:0; }
  .server-table tr { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px 12px;
                     border-bottom:1px solid #30374a; padding:18px 0; }
  .server-table tr:last-child { border:0; } .server-table td { display:block; min-width:0; padding:0; border:0; overflow-wrap:anywhere; }
  .server-table td:before { content:attr(data-label); display:block; color:var(--subtle); font-size:10px;
                            font-weight:700; letter-spacing:.04em; text-transform:uppercase; margin-bottom:3px; }
  .server-table td:first-child,.server-table td:last-child { grid-column:1 / -1; }
  .server-table td:first-child:before,.server-table td:last-child:before { display:none; }
  .server-table td:last-child .btn { width:100%; margin-top:3px; }
  .server-table td[colspan] { grid-column:1 / -1; }
}
/* Workspace navigation and feature overview */
.sidebar { gap:28px; }
.sidebar nav { display:grid; gap:3px; }
.nav-label { padding:17px 10px 5px; }
.nav-link { background:transparent; border-color:transparent; color:#b9c0d3; padding:9px 10px; }
.nav-link:hover { background:#20253b; }
.nav-link.active { background:#302a50; border-color:#51467b; color:#fff; }
.nav-icon { display:grid; place-items:center; flex:none; width:19px; color:#c4b4ff; font-size:17px; }
.server-switch { display:flex; align-items:center; min-width:0; padding:10px; gap:8px; margin:0 0 6px; border:1px solid #353c53; border-radius:11px; background:#1a1f31; }
.server-switch .server-icon,.server-switch .server-initial { width:36px; height:36px; margin:0; flex:none; }
.server-switch span:not(.server-initial) { min-width:0; }
.server-switch small,.server-switch strong { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.server-switch small { color:var(--subtle); font-size:9px; letter-spacing:.08em; }
.server-switch strong { font-size:12px; } .server-switch b { margin-left:auto; color:var(--subtle); }
.mobile-nav { display:none; }
.server-hero { position:relative; overflow:hidden; display:flex; justify-content:space-between; gap:20px; padding:36px;
  border:1px solid #514879; border-radius:18px; background:radial-gradient(circle at 85% 0%,#6446aa80,transparent 42%),linear-gradient(115deg,#202345,#191a30 65%); margin-bottom:38px; }
.hero-server { display:flex; align-items:center; gap:16px; min-width:0; } .hero-server > div { min-width:0; } .hero-server h1 { overflow-wrap:anywhere; } .hero-server .server-icon,.hero-server .server-initial { width:58px; height:58px; margin:0; font-size:22px; }
.hero-server p,.hero-copy { color:#c7c7de; } .hero-server p { margin-top:5px; font-size:12px; }
.hero-copy { max-width:530px; margin-top:22px; } .hero-emblem { font-size:88px; line-height:1; color:#bdaaff; opacity:.35; }
.overview-heading { margin-bottom:20px; } .overview-heading h2 { font-size:21px; } .overview-heading p:last-child { color:var(--muted); margin-top:5px; }
.feature-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:14px; }
.feature-tile { display:flex; flex-direction:column; min-width:0; min-height:190px; padding:21px; border:1px solid var(--border); border-radius:14px; background:linear-gradient(145deg,#1a2032,#141827); transition:border-color .15s,transform .15s; }
.feature-tile:hover { border-color:#907de0; transform:translateY(-2px); } .tile-top { display:flex; justify-content:space-between; align-items:center; color:#a49bc7; margin-bottom:18px; }
.tile-icon { display:grid; place-items:center; width:38px; height:38px; border-radius:11px; background:#332b50; color:#d6caff; font-size:11px; font-weight:700; }
.feature-tile:nth-child(3n+2) .tile-icon { background:#342b42; color:#f3bbd7; }
.feature-tile:nth-child(3n) .tile-icon { background:#203b43; color:#a9e1e6; }
.feature-tile h3 { font-size:15px; } .feature-tile p { color:var(--muted); font-size:12px; margin:7px 0 16px; }
.tile-status { color:#c9bcff; font-size:11px; font-weight:700; margin-top:auto; } .overview-note { color:var(--subtle); font-size:12px; margin-top:20px; }
@media (max-width:1050px) { .server-switch { justify-content:center; padding:6px; } .server-switch span:not(.server-initial),.server-switch b { display:none; } .feature-grid { grid-template-columns:repeat(2,minmax(0,1fr)); } }
@media (max-width:760px) { .mobile-nav { display:block; margin:12px 20px 0; border:1px solid var(--border); background:#141826; border-radius:10px; }
 .mobile-nav summary { padding:12px 14px; cursor:pointer; font-weight:650; } .mobile-nav nav { padding:0 10px 10px; }
 .mobile-nav .nav-label,.mobile-nav .nav-text { display:block; } .mobile-nav .nav-link { justify-content:flex-start; } .mobile-nav .server-switch { display:none; }
 .server-hero { padding:25px; } .hero-emblem { font-size:55px; } }
@media (max-width:480px) { .feature-grid { grid-template-columns:1fr; } .feature-tile { min-height:160px; } .hero-emblem { display:none; } }
/* Landing workspace */
:root { --bg:#090b16; --surface:#121625; --border:#30354b; --accent:#b9a4ff; }
body { background:radial-gradient(ellipse at 85% 0%,#211b3f 0%,transparent 36%),#090b16; }
.sidebar { background:#0e1120; border-color:#252a40; }
header { background:#0e1120; border-color:#252a40; }
.container { max-width:1500px; }
.welcome-panel { position:relative; overflow:hidden; display:flex; justify-content:space-between; min-height:248px; border:1px solid #64578d; border-radius:20px; padding:32px 38px; background:radial-gradient(circle at 80% 15%,#7554cc80,transparent 40%),linear-gradient(115deg,#292448,#17182e 74%); box-shadow:0 18px 55px #0003; }
.welcome-copy { position:relative; z-index:1; } .welcome-kicker { display:inline-flex; align-items:center; gap:8px; padding:5px 10px; border:1px solid #9a85d74d; border-radius:100px; color:#ded5ff; background:#ffffff0d; font-size:11px; font-weight:700; letter-spacing:.03em; }
.kicker-dot { width:6px; height:6px; background:#bca7ff; border-radius:50%; box-shadow:0 0 10px #cdbfff; }
.welcome-panel h1 { margin-top:17px; font-size:clamp(30px,3.4vw,45px); line-height:1.08; letter-spacing:-.045em; } .welcome-panel h1 span { color:#cdbdff; }
.welcome-panel p { color:#cbc7e1; margin-top:12px; max-width:520px; }
.welcome-actions { display:flex; flex-wrap:wrap; align-items:center; gap:20px; margin-top:23px; }
.welcome-actions .btn { background:#d0c3ff; border-color:#d0c3ff; color:#21183d; padding:11px 17px; } .welcome-actions .btn:hover { background:white; }
.hero-action { color:#e5dcff; font-size:12px; font-weight:700; } .hero-action:hover { text-decoration:underline; }
.welcome-art { position:relative; align-self:center; width:210px; height:210px; flex:none; display:grid; place-items:center; }
.art-orbit { position:absolute; border:1px solid #d1c0ff33; border-radius:50%; inset:0; } .orbit-two { inset:23px; border-style:dashed; }
.art-shield { display:grid; place-items:center; width:112px; height:112px; border-radius:32px; background:linear-gradient(145deg,#896be1,#3f3174); border:1px solid #e1d6ff55; box-shadow:0 16px 45px #07041166,0 0 55px #aa8bff44; transform:rotate(-8deg); color:#e4daff; }
.art-shield svg { width:60px; transform:rotate(8deg); } .art-spark { position:absolute; color:#c8b7ff; font-size:24px; } .spark-one { top:12px; right:25px; } .spark-two { bottom:14px; left:17px; }
.activity-bar { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); border:1px solid #2d3248; border-radius:14px; background:#141827; margin-top:16px; }
.activity-bar > div { padding:15px 22px; min-width:0; } .activity-bar > div + div { border-left:1px solid #30354a; }
.activity-bar span,.guild-facts span { display:block; color:#929bb2; font-size:10px; font-weight:700; letter-spacing:.07em; }
.activity-bar strong { display:block; color:#f4f1ff; font-size:18px; margin-top:2px; }
.servers-section { margin-top:40px; } .servers-heading { display:flex; align-items:end; justify-content:space-between; gap:20px; }
.servers-heading h2 { font-size:25px; } .servers-heading p:last-child { color:var(--muted); margin-top:5px; }
.server-count { flex:none; border:1px solid #363d54; background:#171c2c; padding:6px 11px; border-radius:100px; color:#bec5d5; font-size:11px; }
.server-search { margin:20px 0; } .search-row { display:flex; gap:10px; align-items:end; }
.search-field { flex:1; min-width:0; } .search-field label { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0,0,0,0); }
.search-field input { min-height:44px; padding-left:15px; background:#141827; } .search-row .btn { min-height:44px; }
.advanced-filters { margin-top:12px; color:#c3b7f1; font-size:12px; } .advanced-filters summary { cursor:pointer; font-weight:700; padding:4px 0; }
.filter-row { display:flex; flex-wrap:wrap; align-items:end; gap:12px; padding:14px; margin-top:8px; background:#141827; border:1px solid var(--border); border-radius:12px; }
.filter-row .form-group { flex:1 1 140px; margin:0; } .filter-row .btn { min-height:42px; }
.guild-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:15px; }
.guild-card { min-width:0; display:flex; flex-direction:column; padding:20px; border:1px solid #30364d; border-radius:16px; background:linear-gradient(150deg,#1b2034,#131727 80%); box-shadow:0 10px 30px #0002; }
.guild-card:hover { border-color:#7565a9; } .guild-card-top { display:flex; align-items:start; justify-content:space-between; gap:10px; }
.guild-card .server-initial,.guild-card .server-icon { width:52px; height:52px; border-radius:16px; margin:0; font-size:19px; }
.guild-access { color:#c8bdf1; background:#373050; border:1px solid #524969; border-radius:99px; padding:5px 9px; font-size:10px; font-weight:700; }
.guild-identity { margin-top:17px; min-width:0; } .guild-identity a { display:block; font-size:16px; font-weight:750; overflow-wrap:anywhere; } .guild-identity a:hover { color:#c7b5ff; }
.guild-identity span { display:block; color:#969fb5; font-size:11px; margin-top:3px; overflow-wrap:anywhere; }
.guild-facts { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; border-top:1px solid #33384d; margin:19px 0 16px; padding-top:15px; }
.guild-facts strong { display:block; font-size:12px; margin-top:5px; overflow-wrap:anywhere; } .guild-facts .fact-on { color:#a6e8c7; } .guild-facts .fact-off { color:#ffb6b7; }
.guild-open { display:flex; justify-content:space-between; gap:8px; margin-top:auto; border:1px solid #4d466b; border-radius:9px; padding:10px 12px; background:#292542; color:#e8e0ff; font-size:12px; font-weight:700; }
.guild-open:hover { background:#3a315f; border-color:#8c79cb; } .empty-servers { border:1px dashed #424961; border-radius:16px; padding:35px; color:var(--muted); background:#141827; } .empty-servers strong { color:var(--text); font-size:15px; } .empty-servers p { margin:7px 0; } .empty-servers a { color:#c5b5ff; font-weight:700; }
@media (max-width:1100px) { .guild-grid { grid-template-columns:repeat(2,minmax(0,1fr)); } }
@media (max-width:760px) { .welcome-panel { padding:26px; min-height:0; } .welcome-art { width:150px; height:150px; } .art-shield { width:88px; height:88px; } .art-shield svg { width:48px; }
 .activity-bar { grid-template-columns:repeat(2,minmax(0,1fr)); } .activity-bar > div:nth-child(3) { border-left:0; } .activity-bar > div:nth-child(n+3) { border-top:1px solid #30354a; }
 .servers-section { margin-top:30px; } }
@media (max-width:540px) { .welcome-art { display:none; } .welcome-panel h1 { font-size:32px; } .guild-grid { grid-template-columns:1fr; } .servers-heading { align-items:start; flex-direction:column; gap:12px; } .search-row { flex-wrap:wrap; } .search-field { flex-basis:100%; } .search-row .btn { flex:1; } }
/* Read-only security and routing summaries */
.health-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; margin-bottom:15px; }
.health-grid > div { border:1px solid var(--border); border-radius:10px; background:#101522; padding:13px; }
.health-grid span,.route-row small { color:var(--muted); font-size:11px; }
.health-grid span { display:block; font-weight:700; letter-spacing:.06em; } .health-grid strong { display:block; margin-top:5px; }
.routes-preview { padding:16px; margin-bottom:22px; border:1px solid var(--border); border-radius:10px; background:#101522; }
.route-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:0 22px; margin-top:12px; }
.route-row { display:grid; grid-template-columns:minmax(80px,1fr) minmax(80px,1fr); gap:3px 10px; border-top:1px solid var(--border); padding:10px 0; }
.route-row strong { overflow-wrap:anywhere; font-size:12px; } .route-row small { grid-column:2; }
.list-edit { border-top:1px solid var(--border); margin-top:20px; padding-top:20px; }
.list-edit:first-of-type { border-top:0; margin-top:0; padding-top:0; } .list-edit h3 { margin-bottom:5px; }
.list-controls { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:10px; align-items:end; margin-top:12px; }
.list-controls .form-group { margin:0; } .list-controls button { min-height:42px; }
@media (max-width:600px) { .route-grid { grid-template-columns:1fr; } .list-controls { grid-template-columns:1fr; } }
@media (prefers-reduced-motion:reduce) { html { scroll-behavior:auto; } .feature-tile { transition:none; } .feature-tile:hover { transform:none; } }
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
        r.add_get("/servers/{guild_id}/overview", self.handle_server_overview)
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
                return self._finish(request, self._message("Access denied", "Only bot developers and owners or administrators of authorized servers may use this dashboard.", 403))
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

    async def _is_discord_admin(self, user: dict, guild) -> bool:
        """Fail closed and fetch fresh membership and role permissions for every authorization decision.

        Member.roles resolves against the gateway cache, so use the fresh member role IDs with freshly
        fetched roles. This also works when the privileged members intent or member cache is unavailable.
        """
        try:
            uid = int(user["uid"])
            fetch_member = getattr(guild, "fetch_member", None)
            fetch_roles = getattr(guild, "fetch_roles", None)
            if not callable(fetch_member) or not callable(fetch_roles):
                return False
            member = await asyncio.wait_for(fetch_member(uid), timeout=5)
            if member.id != uid:
                return False
            member_roles = getattr(member, "_roles", None)
            if member_roles is None:
                return False
            ids = {int(rid) for rid in member_roles} | {guild.id}
            roles = await asyncio.wait_for(fetch_roles(), timeout=5)
            return any(role.id in ids and role.permissions.administrator for role in roles)
        except Exception:
            return False  # missing membership, revoked permissions, API failures and timeouts deny access

    async def _may_use_dashboard(self, user: dict) -> bool:
        if user.get("local"):
            return False
        if await self._is_bot_owner(user):
            return True
        authorized = [g for g in self.bot.guilds if self._server_authorized(g)]
        if any(g.owner_id == user.get("uid") for g in authorized):
            return True
        for guild in authorized:
            if await self._is_discord_admin(user, guild):
                return True
        return False

    def _server_authorized(self, guild) -> bool:
        check = getattr(self.bot, "is_authorized", None)
        return bool(check(guild.id)) if callable(check) else False

    async def _can_manage_rbl(self, user: dict, guild) -> bool:
        if user.get("local"):
            return False
        try:
            uid = int(user["uid"])
            return uid == guild.owner_id or is_bot_developer(uid, self.bot) or await self._is_bot_owner(user)
        except (KeyError, TypeError, ValueError):
            return False

    async def _can_manage_security(self, user: dict, guild) -> bool:
        # Preserve the legacy, non-startable local test mode; real listeners require Discord login.
        return bool(user.get("local")) or await self._can_manage_rbl(user, guild)

    async def _guilds_for(self, user: dict) -> list:
        if await self._is_bot_owner(user):
            return list(self.bot.guilds)
        result = []
        for guild in self.bot.guilds:
            if self._server_authorized(guild) and (guild.owner_id == user["uid"] or await self._is_discord_admin(user, guild)):
                result.append(guild)
        return result

    async def _guild_for(self, request: web.Request, user: dict):
        """Return a server only after fresh role and server-authorization checks."""
        try:
            gid = int(request.match_info["guild_id"])
        except (KeyError, ValueError):
            return None
        guild = self.bot.get_guild(gid)
        if guild is None:
            return None
        if await self._is_bot_owner(user):
            return guild
        if self._server_authorized(guild) and (guild.owner_id == user["uid"] or await self._is_discord_admin(user, guild)):
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
    def _server_navigation(self, guild, active: str, limited: bool = False) -> str:
        base = f"/servers/{guild.id}"
        groups = (
            ("WORKSPACE", (("overview", "Overview", f"{base}/overview", "◈"),
                           ("general", "Server settings", f"{base}/features?section=general", "⚙"))),
            ("SECURITY", (("rules", "Antinuke", f"{base}/features?section=rules", "◇"),
                          ("automod", "Automod", f"{base}/features?section=automod", "✦"),
                          ("logging", "Logging & security", base, "≡"))),
            ("COMMUNITY", (("leveling", "Leveling", f"{base}/features?section=leveling", "↗"),
                           ("tickets", "Tickets", f"{base}/features?section=tickets", "▣"))),
        )
        if limited:
            groups = (
                ("WORKSPACE", (("overview", "Overview", f"{base}/overview", "◈"),)),
                ("MODERATION", (("automod", "Automod", f"{base}/features?section=automod", "✦"),
                                 ("logging", "Logging", f"{base}/features?section=logging", "≡"))),
                ("COMMUNITY", (("leveling", "Leveling", f"{base}/features?section=leveling", "↗"),
                               ("tickets", "Tickets", f"{base}/features?section=tickets", "▣"))),
            )
        nav = f'<a class="server-switch" href="/" title="Switch server">{_server_avatar(guild)}<span><small>SERVER WORKSPACE</small><strong>{_esc(guild.name)}</strong></span><b aria-hidden="true">↕</b></a>'
        for label, links in groups:
            nav += f'<span class="nav-label">{label}</span>'
            for key, text, href, icon in links:
                selected = ' active" aria-current="page' if key == active else ''
                nav += (f'<a class="nav-link{selected}" href="{_esc(href)}" title="{_esc(text)}">'
                        f'<span class="nav-icon" aria-hidden="true">{icon}</span><span class="nav-text">{text}</span></a>')
        return nav

    def _page(self, title: str, body: str, user: dict | None = None, subtitle: str = "", *,
              guild=None, active: str = "", developer: bool = False, limited: bool = False) -> web.Response:
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
        nav = (self._server_navigation(guild, active, limited) if guild else
               '<span class="nav-label">WORKSPACE</span><a class="nav-link active" href="/" aria-current="page">'
               '<span class="nav-icon" aria-hidden="true">◈</span><span class="nav-text">Server overview</span></a>')
        if not guild and developer:
            nav += ('<span class="nav-label">DEVELOPER</span><a class="nav-link" href="/authorization" title="Server authorization">'
                    '<span class="nav-icon" aria-hidden="true">◇</span><span class="nav-text">Authorization</span></a>')
        mobile_nav = (f'<details class="mobile-nav"><summary>Navigate server workspace</summary><nav aria-label="Mobile server navigation">{nav}</nav></details>'
                      if guild else '')
        doc = (
            '<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
            f"<title>Veil Utilities • {_esc(title)}</title><style>{CSS}</style></head><body>"
            '<a class="skip-link" href="#main">Skip to content</a>'
            '<div class="shell"><aside class="sidebar"><a href="/" class="brand">'
            '<span class="brand-mark" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" '
            'stroke="currentColor" stroke-width="1.7"><path d="M12 2.5 20 6v5.4c0 4.6-3.2 7.7-8 10.1-4.8-2.4-8-5.5-8-10.1V6L12 2.5Z"/>'
            '<path d="m8.8 11.5 2.2 2.2 4.6-5"/></svg></span>'
            '<span class="brand-copy"><span class="logo">Veil Utilities</span>'
            '<span class="brand-caption">Server management</span></span></a>'
            f'<nav aria-label="Main navigation">{nav}</nav>'
            '<div class="side-note"><strong>Your workspace</strong>Manage settings for the servers available to your account.</div>'
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
    async def handle_home(self, request: web.Request) -> web.Response:
        user = self._user(request)
        if user is None:
            return self._page("Sign in", (
                '<div class="card center"><p class="eyebrow">Veil Utilities / Dashboard</p>'
                '<h1>Sign in to Veil</h1>'
                '<p class="muted">Sign in with Discord to manage your authorized servers. '
                'Bot developers can access all joined servers.</p>'
                '<a class="btn" href="/login">Log in with Discord →</a></div>'))

        guilds = await self._guilds_for(user)
        is_developer = await self._is_bot_owner(user)
        role = "Developer workspace" if is_developer else "Server owner / administrator workspace"
        access_description = ("Manage every server Veil has joined and review server authorizations."
                              if is_developer else "Manage authorized servers you own or administer, all in one place.")
        authorization_link = ('<a class="hero-action" href="/authorization">Manage authorization <span aria-hidden="true">↗</span></a>'
                              if is_developer and not user.get("local") else "")
        body = (f'<section class="welcome-panel"><div class="welcome-copy"><div class="welcome-kicker"><span class="kicker-dot"></span>{role}</div>'
                f'<h1>Make your server<br><span>feel unstoppable.</span></h1><p>{access_description}</p>'
                f'<div class="welcome-actions"><a href="#servers" class="btn">Explore your servers <span aria-hidden="true">↓</span></a>{authorization_link}</div>'
                '</div><div class="welcome-art" aria-hidden="true"><div class="art-orbit orbit-one"></div><div class="art-orbit orbit-two"></div>'
                '<div class="art-shield"><svg viewBox="0 0 80 90" fill="none"><path d="M40 5 72 19v23c0 19-13 32-32 42C21 74 8 61 8 42V19L40 5Z" stroke="currentColor" stroke-width="3"/><path d="m26 44 9 9 20-22" stroke="currentColor" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/></svg></div>'
                '<span class="art-spark spark-one">✦</span><span class="art-spark spark-two">✧</span></div></section>')
        if is_developer:
            all_guilds = list(self.bot.guilds)
            members = sum(getattr(g, "member_count", None) or len(getattr(g, "members", [])) for g in all_guilds)
            latency = self.bot.latency
            latency_text = (f"{int(latency * 1000)} ms" if isinstance(latency, (int, float))
                            and latency == latency and 0 < latency < float("inf") else "Unavailable")
            uptime = int(time.time() - getattr(self.bot, "started_at", time.time()))
            hours, rem = divmod(max(uptime, 0), 3600)
            body += (f'<section class="activity-bar" aria-label="Bot statistics"><div><span>JOINED SERVERS</span><strong>{len(all_guilds):,}</strong></div>'
                     f'<div><span>MEMBERS</span><strong>{members:,}</strong></div>'
                     f'<div><span>GATEWAY LATENCY</span><strong>{latency_text}</strong></div>'
                     f'<div><span>UPTIME</span><strong>{hours}h {rem // 60}m</strong></div></section>')
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
            security_access = await self._can_manage_security(user, g)
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
            if count is None:
                count = len(getattr(g, "members", []))
            visible.append((g, count, enabled, auth, log_count, security_access))
        if order == "members":
            visible.sort(key=lambda row: (-row[1], row[0].name.casefold(), row[0].id))
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
                f'<div class="guild-facts"><div><span>MEMBERS</span><strong>{_esc(count)}</strong></div>'
                f'{security_fact}'
                f'<div><span>LOG ROUTES</span><strong>{log_count} configured</strong></div></div>'
                f'<a class="guild-open" href="/servers/{_esc(g.id)}/overview" '
                f'aria-label="Configure {_esc(g.name)}">Manage server <span aria-hidden="true">↗</span></a></article>'
            )
        if cards:
            listing = '<div class="guild-grid">' + "".join(cards) + '</div>'
        elif guilds:
            listing = '<div class="empty-servers"><strong>No servers match your filters.</strong><p>Try a different search or clear the filters.</p><a href="/">Clear filters →</a></div>'
        else:
            listing = ('<div class="empty-servers"><strong>No servers available yet.</strong>'
                       '<p>Veil must be in an authorized server you own or administer. '
                       'Bot developers can access every joined server.</p></div>')
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
                 '<form method="GET" action="/" class="server-search">'
                 '<div class="search-row"><div class="search-field"><label for="q">Search servers</label>'
                 f'<input id="q" name="q" value="{_esc(query)}" placeholder="Search by name or server ID"></div>'
                 '<button class="btn" type="submit">Search</button><a class="btn btn-plain" href="/">Clear</a></div>'
                 f'<details class="advanced-filters"{filters_open}><summary>Filter &amp; sort servers</summary><div class="filter-row">'
                 f'<div class="form-group"><label for="antinuke">Antinuke</label><select id="antinuke" name="antinuke">{status_options}</select></div>'
                 f'<div class="form-group"><label for="authorized">Authorization</label><select id="authorized" name="authorized">{auth_options}</select></div>'
                 f'<div class="form-group"><label for="logs">Log channels</label><select id="logs" name="logs">{log_options}</select></div>'
                 f'<div class="form-group"><label for="sort">Sort by</label><select id="sort" name="sort">{sort_options}</select></div>'
                 '<button class="btn btn-plain" type="submit">Apply filters</button></div></details></form>'
                 f'{listing}<p class="overview-note">Log routes count explicitly selected channels; automatic routing is not included.</p></section>')
        return self._page("Dashboard", body, user, developer=is_developer)

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
            ("00", "Server settings", "Set the command prefix used in this server.", f"{base}/features?section=general", "Manage prefix"),
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
        limited = not await self._can_manage_security(user, guild)
        if limited:
            cards = [card for card in cards if card[1] in ("Automod", "Logging channels", "Log exclusions", "Leveling", "Tickets")]
            cards = [(icon, name, desc, f"{base}/features?section=logging" if name in ("Logging channels", "Log exclusions") else href, status)
                     for icon, name, desc, href, status in cards]
        tiles = "".join(
            f'<a class="feature-tile" href="{_esc(href)}"><div class="tile-top"><span class="tile-icon">{icon}</span><span aria-hidden="true">↗</span></div>'
            f'<h3>{_esc(name)}</h3><p>{_esc(desc)}</p><span class="tile-status">{_esc(status)}</span></a>'
            for icon, name, desc, href, status in cards)
        overview_description = ('Everything you need, organized by feature.' if limited else
                                'Everything you need, organized by feature. Logging, trust, and blacklist links open a shared settings form.')
        body = (f'<div class="server-hero"><div><p class="eyebrow">YOUR SERVER WORKSPACE</p>'
                f'<div class="hero-server">{_server_avatar(guild)}<div><h1>{_esc(guild.name)}</h1>'
                f'<p>Server ID · {_esc(guild.id)}</p></div></div>'
                '<p class="hero-copy">Your tools for a safer, better organized community. Choose a feature below to get started.</p></div>'
                '<span class="hero-emblem" aria-hidden="true">✦</span></div>'
                '<div class="overview-heading"><div><p class="eyebrow">YOUR TOOLKIT</p><h2>Manage your server</h2>'
                f'<p>{overview_description}</p></div></div>'
                f'<div class="feature-grid">{tiles}</div>'
                '<p class="overview-note">Log route counts show explicitly selected categories, not automatic routing.</p>')
        return self._page("Server overview", body, user, guild=guild, active="overview", limited=limited)

    async def handle_server(self, request: web.Request) -> web.Response:
        user = self._user(request)
        if user is None:
            return self._redirect("/login" if self.login_enabled else "/")
        guild = await self._guild_for(request, user)
        if guild is None:
            return self._message("Server not found", "Veil isn't in that server, or you don't own it.", 404, user)

        if not await self._can_manage_security(user, guild):
            return self._redirect(f"/servers/{guild.id}/features?section=logging")

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
        from core.dashboard_security_views import log_routes
        routes_preview = log_routes(guild, logs_cfg)
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
      </select><span class="hint">Controls role-blacklist replies; direct messages can fail if the recipient has DMs closed.</span></div>
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
{rbl_form}
  <section class="card" id="logging"><div class="card-head"><span class="section-icon" aria-hidden="true">02</span>
    <div><h2>Logging channels</h2><p>Choose where each type of activity is sent.</p></div></div>
    <div class="note">Auto uses Veil's normal routing. Off disables that direct route; some events can still use other categories as fallbacks. Select a channel to override it.</div>
    {routes_preview}<div class="form-grid">{logs_form}</div>
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
    <div class="buttons"><a href="/" class="btn btn-plain">Cancel</a><button type="submit" class="btn">Save changes</button></div></div>
</form></div>"""

        return self._page(guild.name, body, user, subtitle=f"Server settings: <b>{_esc(guild.name)}</b>",
                          guild=guild, active="logging")

    async def handle_features(self, request: web.Request) -> web.Response:
        from core.dashboard_features import features_page
        return await features_page(self, request)

    async def handle_save_feature(self, request: web.Request) -> web.Response:
        from core.dashboard_features import save_feature
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
            log.warning("Dashboard login failed: %s", e)
            return self._message("Login failed", "Couldn't verify your Discord account. Please try again.", 502)
        name = str(me.get("global_name") or me.get("username") or uid)
        if not await self._may_use_dashboard({"uid": uid, "local": False}):
            resp = self._message("Access denied", "Only bot developers and owners or administrators of authorized servers may use this dashboard.", 403)
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
        if not await self._can_manage_security(user, guild):
            return web.json_response({"error": "owner or developer required"}, status=403)
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
        if not await self._can_manage_security(user, guild):
            return self._message("Access denied", "Only the server owner or a bot developer may change security settings.", 403, user)
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
