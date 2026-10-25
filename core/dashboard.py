"""Per-server web dashboard for Veil Utilities."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import TYPE_CHECKING

from aiohttp import web

if TYPE_CHECKING:
    from core.bot import Veil

log = logging.getLogger("veil.dashboard")

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Veil Utilities • Dashboard</title>
    <style>
        :root {
            --bg: #0f0d13;
            --card: #18151f;
            --accent: #b10f2e;
            --accent-hover: #d11237;
            --text: #f0edf5;
            --muted: #8c8599;
            --border: #282433;
            --success: #2ecc71;
            --warn: #f39c12;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
        body { background: var(--bg); color: var(--text); padding: 2rem; }
        .container { max-width: 1100px; margin: 0 auto; }
        header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 1.5rem; margin-bottom: 2rem; }
        .logo { font-size: 1.5rem; font-weight: 700; color: var(--accent); }
        .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; margin-bottom: 2rem; }
        .stat-card { background: var(--card); border: 1px solid var(--border); padding: 1.25rem; border-radius: 8px; }
        .stat-val { font-size: 1.8rem; font-weight: 700; color: var(--text); margin-top: 0.25rem; }
        .stat-lbl { font-size: 0.85rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }
        .card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 1.5rem; margin-bottom: 2rem; }
        h2 { font-size: 1.25rem; margin-bottom: 1rem; display: flex; align-items: center; gap: 0.5rem; }
        .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 1rem; }
        .server-box { background: rgba(255,255,255,0.02); border: 1px solid var(--border); border-radius: 6px; padding: 1rem; display: flex; justify-content: space-between; align-items: center; }
        .btn { background: var(--accent); color: white; padding: 0.5rem 1rem; border-radius: 6px; text-decoration: none; font-size: 0.875rem; font-weight: 600; border: none; cursor: pointer; transition: 0.2s; }
        .btn:hover { background: var(--accent-hover); }
        .badge { background: var(--border); color: var(--muted); padding: 0.2rem 0.5rem; border-radius: 4px; font-size: 0.75rem; }
        .form-group { margin-bottom: 1rem; }
        label { display: block; font-size: 0.875rem; color: var(--muted); margin-bottom: 0.35rem; }
        input, select, textarea { width: 100%; padding: 0.6rem 0.8rem; background: var(--bg); border: 1px solid var(--border); border-radius: 6px; color: var(--text); font-size: 0.9rem; }
        input:focus { outline: none; border-color: var(--accent); }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="logo">⚡ Veil Utilities</div>
            <div><span class="badge">Online</span></div>
        </header>

        <div class="stats">
            <div class="stat-card">
                <div class="stat-lbl">Connected Servers</div>
                <div class="stat-val">{guild_count}</div>
            </div>
            <div class="stat-card">
                <div class="stat-lbl">Cached Members</div>
                <div class="stat-val">{member_count}</div>
            </div>
            <div class="stat-card">
                <div class="stat-lbl">Gateway Latency</div>
                <div class="stat-val">{latency}ms</div>
            </div>
            <div class="stat-card">
                <div class="stat-lbl">Uptime</div>
                <div class="stat-val">{uptime}</div>
            </div>
        </div>

        <div class="card">
            <h2>🛡️ Per-Server Configuration</h2>
            <p style="color: var(--muted); margin-bottom: 1.25rem;">Select a server to manage role blacklists, logging channels, antinuke rules, and permissions.</p>
            <div class="grid">
                {server_cards}
            </div>
        </div>
    </div>
</body>
</html>
"""

SERVER_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Veil Utilities • {guild_name}</title>
    <style>
        :root {{
            --bg: #0f0d13;
            --card: #18151f;
            --accent: #b10f2e;
            --accent-hover: #d11237;
            --text: #f0edf5;
            --muted: #8c8599;
            --border: #282433;
            --success: #2ecc71;
            --warn: #f39c12;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
        body {{ background: var(--bg); color: var(--text); padding: 2rem; }}
        .container {{ max-width: 900px; margin: 0 auto; }}
        header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 1.5rem; margin-bottom: 2rem; }}
        .logo {{ font-size: 1.5rem; font-weight: 700; color: var(--accent); }}
        .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 1.5rem; margin-bottom: 1.5rem; }}
        h2 {{ font-size: 1.2rem; margin-bottom: 1rem; color: var(--text); }}
        .form-group {{ margin-bottom: 1rem; }}
        label {{ display: block; font-size: 0.85rem; color: var(--muted); margin-bottom: 0.35rem; }}
        input, textarea {{ width: 100%; padding: 0.6rem 0.8rem; background: var(--bg); border: 1px solid var(--border); border-radius: 6px; color: var(--text); font-size: 0.9rem; }}
        .btn {{ background: var(--accent); color: white; padding: 0.6rem 1.2rem; border-radius: 6px; text-decoration: none; font-size: 0.9rem; font-weight: 600; border: none; cursor: pointer; }}
        .btn:hover {{ background: var(--accent-hover); }}
        .btn-back {{ background: var(--border); color: var(--text); margin-right: 0.5rem; }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div>
                <div class="logo">⚡ Veil Utilities</div>
                <div style="font-size: 1.1rem; margin-top: 0.25rem;">Server Settings: <b>{guild_name}</b></div>
            </div>
            <div>
                <a href="/" class="btn btn-back">← All Servers</a>
            </div>
        </header>

        <form method="POST" action="/api/guilds/{guild_id}/config">
            <div class="card">
                <h2>🚫 Role Blacklist (BLED) Configuration</h2>
                <div class="form-group">
                    <label>Blacklisted Role IDs (comma-separated):</label>
                    <input type="text" name="rbl_roles" value="{rbl_roles}">
                </div>
                <div class="form-group">
                    <label>Authorized User & Role Bypass IDs (comma-separated):</label>
                    <input type="text" name="rbl_authorized" value="{rbl_authorized}">
                </div>
            </div>

            <div class="card">
                <h2>📜 Logging Channels</h2>
                <div class="form-group">
                    <label>Command Logs Channel ID:</label>
                    <input type="text" name="log_commands" value="{log_commands}">
                </div>
                <div class="form-group">
                    <label>Ban Logs Channel ID:</label>
                    <input type="text" name="log_bans" value="{log_bans}">
                </div>
                <div class="form-group">
                    <label>Join/Leave Logs Channel ID:</label>
                    <input type="text" name="log_joins" value="{log_joins}">
                </div>
            </div>

            <div class="card">
                <h2>🛡️ Antinuke Protections</h2>
                <div class="form-group">
                    <label>Antinuke Action (kick / ban / strip):</label>
                    <input type="text" name="an_punishment" value="{an_punishment}">
                </div>
                <div class="form-group">
                    <label>Whitelisted Admin User IDs (comma-separated):</label>
                    <input type="text" name="an_admins" value="{an_admins}">
                </div>
            </div>

            <div style="text-align: right;">
                <button type="submit" class="btn">💾 Save Configuration</button>
            </div>
        </form>
    </div>
</body>
</html>
"""


class DashboardServer:
    def __init__(self, bot: "Veil", port: int = 8080):
        self.bot = bot
        self.port = port
        self.app = web.Application()
        self.runner: web.AppRunner | None = None
        self._setup_routes()

    def _setup_routes(self):
        self.app.router.add_get("/", self.handle_home)
        self.app.router.add_get("/servers", self.handle_home)
        self.app.router.add_get("/servers/{guild_id}", self.handle_server)
        self.app.router.add_get("/api/guilds", self.handle_api_guilds)
        self.app.router.add_get("/api/guilds/{guild_id}/config", self.handle_api_get_config)
        self.app.router.add_post("/api/guilds/{guild_id}/config", self.handle_api_post_config)

    async def handle_home(self, request: web.Request) -> web.Response:
        guilds = list(self.bot.guilds)
        member_count = sum(len(getattr(g, "members", [])) for g in guilds)
        uptime_secs = int(time.time() - getattr(self.bot, "started_at", time.time()))
        mins, secs = divmod(uptime_secs, 60)
        hours, mins = divmod(mins, 60)
        uptime_str = f"{hours}h {mins}m"

        cards = []
        for g in guilds:
            cards.append(f"""
            <div class="server-box">
                <div>
                    <div style="font-weight: 600;">{g.name}</div>
                    <div style="font-size: 0.8rem; color: var(--muted);">{len(g.members)} members • ID {g.id}</div>
                </div>
                <a href="/servers/{g.id}" class="btn">Configure</a>
            </div>
            """)
        cards_html = "\n".join(cards) if cards else "<p style='color: var(--muted);'>No servers connected yet.</p>"

        html = HTML_TEMPLATE.format(
            guild_count=len(guilds),
            member_count=member_count,
            latency=int(self.bot.latency * 1000) if self.bot.latency == self.bot.latency else 0,
            uptime=uptime_str,
            server_cards=cards_html,
        )
        return web.Response(text=html, content_type="text/html")

    async def handle_server(self, request: web.Request) -> web.Response:
        gid = int(request.match_info["guild_id"])
        guild = self.bot.get_guild(gid)
        if not guild:
            return web.Response(text="Guild not found", status=404)

        rbl = await self.bot.db.get_config(gid, "roleblacklist")
        logging_cfg = await self.bot.db.get_config(gid, "logging")
        an_cfg = await self.bot.db.get_config(gid, "antinuke")

        html = SERVER_TEMPLATE.format(
            guild_id=gid,
            guild_name=guild.name,
            rbl_roles=", ".join(str(r) for r in rbl.get("roles", [])),
            rbl_authorized=", ".join(str(a) for a in rbl.get("authorized", [])),
            log_commands=logging_cfg.get("channels", {}).get("commands", ""),
            log_bans=logging_cfg.get("channels", {}).get("bans", ""),
            log_joins=logging_cfg.get("channels", {}).get("joinleave", ""),
            an_punishment=an_cfg.get("action", "kick"),
            an_admins=", ".join(str(a) for a in an_cfg.get("admins", [])),
        )
        return web.Response(text=html, content_type="text/html")

    async def handle_api_guilds(self, request: web.Request) -> web.Response:
        guilds = [{"id": g.id, "name": g.name, "member_count": len(g.members)} for g in self.bot.guilds]
        return web.json_response(guilds)

    async def handle_api_get_config(self, request: web.Request) -> web.Response:
        gid = int(request.match_info["guild_id"])
        rbl = await self.bot.db.get_config(gid, "roleblacklist")
        logging_cfg = await self.bot.db.get_config(gid, "logging")
        an_cfg = await self.bot.db.get_config(gid, "antinuke")
        pl_cfg = await self.bot.db.get_config(gid, "permlevels")
        return web.json_response({
            "roleblacklist": rbl,
            "logging": logging_cfg,
            "antinuke": an_cfg,
            "permlevels": pl_cfg,
        })

    async def handle_api_post_config(self, request: web.Request) -> web.Response:
        gid = int(request.match_info["guild_id"])
        data = await request.post()

        # Update Role Blacklist
        rbl = await self.bot.db.get_config(gid, "roleblacklist")
        if "rbl_roles" in data:
            roles = [int(x.strip()) for x in data["rbl_roles"].split(",") if x.strip().isdigit()]
            rbl["roles"] = roles
        if "rbl_authorized" in data:
            auth = [int(x.strip()) for x in data["rbl_authorized"].split(",") if x.strip().isdigit()]
            rbl["authorized"] = auth
        await self.bot.db.set_config(gid, "roleblacklist", rbl)

        # Update Logging Channels
        logging_cfg = await self.bot.db.get_config(gid, "logging")
        channels = logging_cfg.setdefault("channels", {})
        if "log_commands" in data and data["log_commands"].strip().isdigit():
            channels["commands"] = int(data["log_commands"].strip())
        if "log_bans" in data and data["log_bans"].strip().isdigit():
            channels["bans"] = int(data["log_bans"].strip())
        if "log_joins" in data and data["log_joins"].strip().isdigit():
            channels["joinleave"] = int(data["log_joins"].strip())
        await self.bot.db.set_config(gid, "logging", logging_cfg)

        # Update Antinuke
        an_cfg = await self.bot.db.get_config(gid, "antinuke")
        if "an_punishment" in data and data["an_punishment"].strip():
            an_cfg["action"] = data["an_punishment"].strip().lower()
        if "an_admins" in data:
            admins = [int(x.strip()) for x in data["an_admins"].split(",") if x.strip().isdigit()]
            an_cfg["admins"] = admins
        await self.bot.db.set_config(gid, "antinuke", an_cfg)

        raise web.HTTPFound(f"/servers/{gid}")

    async def start(self):
        try:
            self.runner = web.AppRunner(self.app)
            await self.runner.setup()
            site = web.TCPSite(self.runner, "0.0.0.0", self.port)
            await site.start()
            log.info("Per-server Web Dashboard running at http://0.0.0.0:%d", self.port)
        except Exception as e:
            log.warning("Web dashboard could not bind to port %d: %s", self.port, e)

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()
