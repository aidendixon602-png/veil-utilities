"""Render-level regressions for the dashboard UI.

These complement the security tests: they check that every page renders with awkward
configuration data, that the markup is valid enough to navigate (no duplicate ids, no
stray "None" labels), and that the single stylesheet covers every class the pages emit.
"""
import re
from html.parser import HTMLParser

from core.dashboard_assets import CSS
from test_dashboard_security import BOT_OWNER, GUILD_ID, cookie, login_server, make_bot, run

PAGES = (
    "/", "/servers", "/authorization",
    "/control/security", "/control/moderation", "/control/roblox", "/control/diagnostics",
    "/control/bot", "/control/database", "/control/settings",
    f"/servers/{GUILD_ID}", f"/servers/{GUILD_ID}/overview", f"/servers/{GUILD_ID}/features",
    f"/servers/{GUILD_ID}/roblox",
    f"/servers/{GUILD_ID}/features?section=rules", f"/servers/{GUILD_ID}/features?section=automod",
    f"/servers/{GUILD_ID}/features?section=logging", f"/servers/{GUILD_ID}/features?section=general",
    f"/servers/{GUILD_ID}/features?section=leveling", f"/servers/{GUILD_ID}/features?section=tickets",
    f"/control/security?server={GUILD_ID}", f"/control/moderation?server={GUILD_ID}&page=2",
)

# Values a hand-edited database, an interrupted migration or an older version could hold.
GARBAGE = {
    "settings": {"disabled": "ban, kick", "bot_channels": "not-a-list"},
    "roleblacklist": {"roles": "not-a-list", "authorized": None, "hard_roles": "x"},
    "logs": {"messages": "abc", "ignore": "bad", "rbl_dm": "yes"},
    "antinuke": {"enabled": "yes", "modules": {"ban": "on"}, "admins": {}, "whitelist": None},
    "levels": {"enabled": "maybe", "channel": "x"},
    "tickets": {"support": "nope", "max": "x"},
    "filters": {"caps": "lots", "links": {"x": 1}},
    "roblox_group": {"group_id": "x"},
}


class _Ids(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name == "id" and value:
                self.ids.append(value)


def _duplicate_ids(html):
    parser = _Ids()
    parser.feed(html)
    seen, dups = set(), []
    for value in parser.ids:
        if value in seen and value not in dups:
            dups.append(value)
        seen.add(value)
    return dups


def _collect(pages, collect):
    bot = make_bot()
    server = login_server(bot)
    _, headers = cookie(server, BOT_OWNER)

    async def scenario(client):
        for path in pages:
            response = await client.get(path, headers=headers)
            assert response.status == 200, (path, response.status)
            await collect(path, await response.text())

    run(server, scenario)


def test_every_page_renders_with_malformed_config():
    """A bad config value must degrade the page, not return HTTP 500."""
    bot = make_bot()
    bot.db.store.update({(GUILD_ID, module): value for module, value in GARBAGE.items()})
    server = login_server(bot)
    _, headers = cookie(server, BOT_OWNER)

    async def scenario(client):
        for path in PAGES:
            response = await client.get(path, headers=headers)
            assert response.status == 200, (path, response.status, (await response.text())[:200])

    run(server, scenario)


def test_pages_have_unique_ids_and_no_stray_none_labels():
    results = {}

    async def collect(path, html):
        results[path] = html

    _collect(PAGES, collect)
    for path, html in results.items():
        assert not _duplicate_ids(html), f"{path}: duplicate element ids {_duplicate_ids(html)}"
        assert not re.search(r">\s*None\s*<", html), f"{path}: literal 'None' rendered as a label"


def test_every_rendered_class_is_styled():
    defined = set(re.findall(r"\.([a-zA-Z][\w-]*)", CSS))
    used = set()

    async def collect(_path, html):
        for attribute in re.findall(r'class="([^"]*)"', html):
            used.update(attribute.split())

    _collect(PAGES, collect)
    missing = sorted(used - defined)
    assert not missing, f"classes used by pages but missing from the stylesheet: {missing}"


def test_uptime_never_reports_the_epoch():
    """started_at=0 must read as unavailable, not "498747h" (the age of 1970)."""
    for value in (0.0, None, "yesterday"):
        bot = make_bot()
        bot.started_at = value
        server = login_server(bot)
        _, headers = cookie(server, BOT_OWNER)
        pages = {}

        async def scenario(client, bot=bot):
            pages["servers"] = await (await client.get("/servers", headers=headers)).text()
            pages["overview"] = await (await client.get("/control/overview", headers=headers)).text()

        run(server, scenario)
        for name, html in pages.items():
            assert "Unavailable" in html, (value, name)
            assert "498747h" not in html and "h 18m" not in html


def test_stylesheet_is_one_system():
    """Guard against stacking another theme layer that silently recolours earlier rules."""
    assert CSS.count(":root {") == 1
    assert CSS.count("@media") < 20
    assert "prefers-reduced-motion:reduce" in CSS
    assert ":focus-visible" in CSS


def test_header_breadcrumb_and_active_navigation():
    results = {}

    async def collect(path, html):
        results[path] = html

    _collect((f"/servers/{GUILD_ID}/features?section=rules", "/servers"), collect)
    rules_page = results[f"/servers/{GUILD_ID}/features?section=rules"]
    assert '<nav class="top-title" aria-label="Breadcrumb">' in rules_page
    assert 'class="crumb-server"' in rules_page
    assert 'class="nav-link active" aria-current="page" href="/servers/' in rules_page
    # The features page highlights the matching server section, not the first link.
    assert re.search(r'class="nav-link active"[^>]*href="[^"]*section=rules"', rules_page)
    # The global list highlights All servers instead of leaving everything unselected.
    assert 'aria-current="page" href="/servers"' in results["/servers"]
