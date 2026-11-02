"""Keep transient Discord login failures from causing hosting-panel crash loops."""
import asyncio
import importlib.util
import socket
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import aiohttp
import discord
import pytest

import core.config
import core.netcalm


@pytest.fixture
def startup(monkeypatch, tmp_path):
    # Load the real entry point without syncing Git or requiring a real token.
    monkeypatch.setenv("AUTO_SYNC_GIT", "0")
    monkeypatch.setattr(core.netcalm, "install", lambda: None)
    settings = replace(core.config.get_settings(), discord_token="test-token", proxy=None,
                       log_file=str(tmp_path / "veil.log"))
    monkeypatch.setattr(core.config, "_settings", settings)
    spec = importlib.util.spec_from_file_location("veil_startup_test", Path(__file__).resolve().parents[1] / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module
    module.logfile.close()


def _rate_limit():
    return discord.HTTPException(SimpleNamespace(status=429, reason="Too Many Requests"), "rate limited")


def _fake_sessions(monkeypatch, startup, results):
    routes = []
    monkeypatch.setattr(socket, "has_ipv6", True)
    monkeypatch.setattr(aiohttp, "TCPConnector", lambda *, limit, family: family)
    monkeypatch.setattr(startup, "intent_plans", lambda: [(object(), None)])

    class Bot:
        def __init__(self, *, intents, connector):
            routes.append(connector)
            self._restart_requested = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def start(self, token):
            result = results.pop(0)
            if isinstance(result, BaseException):
                raise result

    monkeypatch.setattr(startup, "Veil", Bot)
    return routes


def test_rate_limit_survives_ipv6_dns_failure(startup, monkeypatch, caplog):
    rate_limit = _rate_limit()
    routes = _fake_sessions(monkeypatch, startup, [rate_limit, aiohttp.ClientConnectionError("no IPv6 address")])
    with pytest.raises(discord.HTTPException) as error:
        asyncio.run(startup.run_bot(show_banner=False))
    assert error.value is rate_limit
    assert routes == [socket.AF_INET, socket.AF_INET6]
    assert "IPv6 route could not connect" in caplog.text


def test_network_failure_tries_another_route(startup, monkeypatch):
    routes = _fake_sessions(monkeypatch, startup, [aiohttp.ClientConnectionError("DNS error"), None])
    assert asyncio.run(startup.run_bot(show_banner=False)) is False
    assert routes == [socket.AF_INET, socket.AF_INET6]


def test_both_routes_unavailable_raise_network_error(startup, monkeypatch):
    error = aiohttp.ClientConnectionError("no IPv6 address")
    _fake_sessions(monkeypatch, startup, [asyncio.TimeoutError(), error])
    with pytest.raises(aiohttp.ClientConnectionError) as raised:
        asyncio.run(startup.run_bot(show_banner=False))
    assert raised.value is error


def test_invalid_token_does_not_retry(startup, monkeypatch):
    routes = _fake_sessions(monkeypatch, startup, [discord.LoginFailure("bad token")])
    with pytest.raises(SystemExit, match="Discord rejected DISCORD_TOKEN"):
        asyncio.run(startup.run_bot(show_banner=False))
    assert routes == [socket.AF_INET]


def test_proxy_is_not_tried_twice(startup, monkeypatch):
    startup.settings = replace(startup.settings, proxy="http://proxy.example")
    routes = _fake_sessions(monkeypatch, startup, [_rate_limit()])
    with pytest.raises(discord.HTTPException):
        asyncio.run(startup.run_bot(show_banner=False))
    assert routes == [None]


@pytest.mark.parametrize("failure", [_rate_limit(), aiohttp.ClientConnectionError("DNS error"), asyncio.TimeoutError()])
def test_main_retries_transient_failures(startup, monkeypatch, caplog, failure):
    attempts = []
    sleeps = []

    async def run_bot(show_banner):
        attempts.append(show_banner)
        if len(attempts) == 1:
            raise failure
        return False

    monkeypatch.setattr(startup, "run_bot", run_bot)
    monkeypatch.setattr(startup.time, "sleep", sleeps.append)
    startup.main()
    assert attempts == [True, False]
    assert sum(sleeps) == 60
    assert "Keeping the process alive" in caplog.text


def test_main_does_not_retry_programming_errors(startup, monkeypatch, caplog):
    attempts = []

    async def run_bot(show_banner):
        attempts.append(show_banner)
        raise ValueError("bug")

    monkeypatch.setattr(startup, "run_bot", run_bot)
    startup.main()
    assert attempts == [True]
    assert "Unexpected error in main loop" in caplog.text
