"""SSRF-safe fetching, music helpers, antinuke permission diffs, colour parsing, warn/case guards."""
import asyncio
import socket
from types import SimpleNamespace

import discord
import pytest

from cogs.antinuke import gained_dangerous, overwrite_gained
from cogs.moderation import Moderation
from cogs.music import listeners, parse_time
from core.net import BlockedURL, PublicResolver, check_url, is_public_ip, make_public_session
from core.utils import Colors, UserError, parse_color


# ------------------------------------------------------------------ core/net.py
@pytest.mark.parametrize("ip, ok", [
    ("8.8.8.8", True), ("1.1.1.1", True), ("2606:4700:4700::1111", True),
    ("127.0.0.1", False), ("10.0.0.5", False), ("192.168.1.1", False), ("172.16.0.1", False),
    ("169.254.169.254", False), ("0.0.0.0", False), ("100.64.0.1", False), ("::1", False),
    ("fe80::1", False), ("fc00::1", False), ("::ffff:127.0.0.1", False), ("224.0.0.1", False),
    ("not-an-ip", False),
])
def test_is_public_ip(ip, ok):
    assert is_public_ip(ip) is ok


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:2333/v4/info", "http://[::1]/", "http://169.254.169.254/latest/meta-data/",
    "http://localhost:8080/x.png", "http://panel.local/a.png", "http://10.1.2.3/a.gif",
    "file:///etc/passwd", "ftp://example.com/a.png", "http://[::ffff:7f00:1]/",
])
def test_check_url_blocks_private_and_odd_schemes(url):
    with pytest.raises(BlockedURL):
        check_url(url)


def test_check_url_allows_public_hostnames_and_ips():
    assert check_url("https://cdn.discordapp.com/emojis/1.png").host == "cdn.discordapp.com"
    assert check_url("http://8.8.8.8/x.png").host == "8.8.8.8"


class FakeResolver:
    def __init__(self, hosts):
        self.hosts = hosts

    async def resolve(self, host, port=0, family=socket.AF_INET):
        return [{"hostname": host, "host": h, "port": port, "family": family, "proto": 0, "flags": 0}
                for h in self.hosts]

    async def close(self):
        pass


def test_resolver_drops_private_answers():
    async def go():
        r = PublicResolver(FakeResolver(["10.0.0.1", "93.184.216.34"]))
        assert [i["host"] for i in await r.resolve("example.com", 80)] == ["93.184.216.34"]
        with pytest.raises(OSError):  # DNS rebinding to an internal address
            await PublicResolver(FakeResolver(["127.0.0.1"])).resolve("evil.example", 80)
    asyncio.run(go())


def test_public_session_refuses_literal_private_ip():
    async def go():
        session = make_public_session()
        try:
            with pytest.raises(BlockedURL):
                async with session.get("http://127.0.0.1:1/"):
                    pass
        finally:
            await session.close()
    asyncio.run(go())


# ------------------------------------------------------------------ parse_color
@pytest.mark.parametrize("text, value", [
    ("#ff0000", 0xFF0000), ("ff4d6d", 0xFF4D6D), ("0xb10f2e", 0xB10F2E), ("#f0a", 0xFF00AA),
    ("red", 0xF04747), ("#FFFFFFFF", Colors.main), ("zzz", Colors.main), ("", Colors.main),
])
def test_parse_color(text, value):
    assert parse_color(text) == value
    assert 0 <= parse_color(text) <= 0xFFFFFF


# ------------------------------------------------------------------ music
@pytest.mark.parametrize("text, secs", [("90", 90), ("1:30", 90), ("1:02:03", 3723), ("0:05", 5)])
def test_parse_time(text, secs):
    assert parse_time(text) == secs


@pytest.mark.parametrize("text", ["abc", "1:2:3:4", "-5", "1:-1", ""])
def test_parse_time_rejects_garbage(text):
    with pytest.raises(UserError):
        parse_time(text)


def test_listeners_ignore_bots_and_deafened():
    def m(bot=False, deaf=False):
        return SimpleNamespace(bot=bot, voice=SimpleNamespace(deaf=False, self_deaf=deaf))
    a, b, c = m(), m(bot=True), m(deaf=True)
    player = SimpleNamespace(channel=SimpleNamespace(members=[a, b, c]))
    assert listeners(player) == [a]
    assert listeners(SimpleNamespace(channel=None)) == []


# ------------------------------------------------------------------ antinuke diffs
P = discord.Permissions


def test_gained_dangerous_uses_bits_not_value():
    # Manage Server (0x20) -> Administrator (0x8): the number goes *down* but it's a huge escalation
    assert gained_dangerous(P(manage_guild=True), P(administrator=True))
    assert gained_dangerous(None, P(ban_members=True))
    assert gained_dangerous(P.none(), P(manage_roles=True, send_messages=True))
    assert not gained_dangerous(P(ban_members=True), P(ban_members=True, send_messages=True))
    assert not gained_dangerous(P(administrator=True), P.none())
    assert not gained_dangerous(P.none(), None)


def test_overwrite_gained():
    before = SimpleNamespace(allow=P(send_messages=True))
    assert overwrite_gained(before, SimpleNamespace(allow=P(send_messages=True, manage_roles=True)))
    assert overwrite_gained(SimpleNamespace(), SimpleNamespace(allow=P(manage_webhooks=True)))
    assert not overwrite_gained(before, SimpleNamespace(allow=P(send_messages=True, attach_files=True)))
    assert not overwrite_gained(before, SimpleNamespace())


# ------------------------------------------------------------------ warn guards
def _ctx(author_id, top, owner_id=1):
    return SimpleNamespace(author=SimpleNamespace(id=author_id, top_role=top),
                           guild=SimpleNamespace(owner_id=owner_id))


def test_mods_cannot_clear_their_own_or_a_higher_members_warnings():
    check = Moderation._check_warn_target
    with pytest.raises(UserError):
        check(_ctx(5, 3), SimpleNamespace(id=5), "clear warnings for")
    higher = discord.Object(9)
    check(_ctx(5, 3), higher, "clear warnings for")  # not a member (left the server): allowed
    check(_ctx(1, 0), SimpleNamespace(id=1), "clear warnings for")  # server owner: anything goes
