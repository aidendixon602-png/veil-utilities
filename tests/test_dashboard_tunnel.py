"""Test optional ngrok setup without making network calls or using real tokens."""
import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.config import Settings
from core.dashboard_tunnel import DashboardTunnel, TunnelFailure


class SDK:
    def __init__(self, url="https://dash.ngrok-free.dev"):
        self.listener = SimpleNamespace(url=lambda: url, close=AsyncMock())
        self.endpoint = SimpleNamespace(domain=MagicMock(), listen_and_forward=AsyncMock(return_value=self.listener))
        self.endpoint.domain.return_value = self.endpoint
        self.session = SimpleNamespace(http_endpoint=lambda: self.endpoint, close=AsyncMock())
        self.builder = SimpleNamespace(authtoken=MagicMock(), connect=AsyncMock(return_value=self.session))
        self.builder.authtoken.return_value = self.builder
        self.SessionBuilder = MagicMock(return_value=self.builder)


def test_disabled_by_default_and_token_is_never_in_config_repr_or_summary():
    s = Settings.from_env({"NGROK_AUTHTOKEN": "super-secret-token"})
    assert not s.dashboard_ngrok_enabled
    assert "super-secret-token" not in repr(s)
    assert "super-secret-token" not in s.summary()
    assert "ngrok_authtoken=set" in s.summary()


def test_starts_exact_domain_locally_and_closes_cleanly(monkeypatch):
    sdk = SDK()
    monkeypatch.setitem(sys.modules, "ngrok", sdk)

    async def go():
        tunnel = await DashboardTunnel.open(token="private", public_url="https://dash.ngrok-free.dev",
                                            host="127.0.0.1", port=8080)
        sdk.builder.authtoken.assert_called_once_with("private")
        sdk.endpoint.domain.assert_called_once_with("dash.ngrok-free.dev")
        sdk.endpoint.listen_and_forward.assert_awaited_once_with("http://127.0.0.1:8080")
        await tunnel.close()
        sdk.listener.close.assert_awaited_once()
        sdk.session.close.assert_awaited_once()

    asyncio.run(go())


@pytest.mark.parametrize("token,url,host", [
    (None, "https://dash.ngrok-free.dev", "127.0.0.1"),
    ("token", "https://dash.ngrok-free.dev", "0.0.0.0"),
    ("token", "http://dash.ngrok-free.dev", "127.0.0.1"),
    ("token", "https://dash.ngrok-free.dev:1234", "127.0.0.1"),
    ("token", "https://dash.ngrok-free.dev/path", "127.0.0.1"),
    ("token", "https://user:secret@dash.ngrok-free.dev", "127.0.0.1"),
])
def test_rejects_missing_token_unprotected_binding_and_bad_urls(monkeypatch, token, url, host):
    sdk = SDK()
    monkeypatch.setitem(sys.modules, "ngrok", sdk)
    with pytest.raises(ValueError):
        asyncio.run(DashboardTunnel.open(token=token, public_url=url, host=host, port=8080))
    sdk.SessionBuilder.assert_not_called()


def test_mismatched_domain_and_connection_failure_cleanup(monkeypatch):
    sdk = SDK("https://unexpected.ngrok-free.dev")
    monkeypatch.setitem(sys.modules, "ngrok", sdk)

    async def go():
        with pytest.raises(ValueError, match="different public URL"):
            await DashboardTunnel.open(token="private", public_url="https://dash.ngrok-free.dev",
                                       host="127.0.0.1", port=8080)
        sdk.listener.close.assert_awaited_once()
        sdk.session.close.assert_awaited_once()
        sdk.endpoint.listen_and_forward.side_effect = RuntimeError("failure")
        with pytest.raises(TunnelFailure, match="endpoint creation"):
            await DashboardTunnel.open(token="private", public_url="https://dash.ngrok-free.dev",
                                       host="127.0.0.1", port=8080)
        assert sdk.session.close.await_count == 2

    asyncio.run(go())


def test_tunnel_error_stops_dashboard_but_does_not_leak_token_or_crash_bot(monkeypatch):
    import core.bot as bot_module
    import core.dashboard as dashboard_module

    settings = SimpleNamespace(dashboard_enabled=True, dashboard_ngrok_enabled=True,
                               ngrok_authtoken="super-private-token", dashboard_url="https://dash.ngrok-free.dev")
    monkeypatch.setattr(bot_module, "settings", settings)
    dashboard = SimpleNamespace(host="127.0.0.1", port=8080, start=AsyncMock(), stop=AsyncMock())
    monkeypatch.setattr(dashboard_module.DashboardServer, "from_settings", lambda bot, settings: dashboard)
    monkeypatch.setattr(DashboardTunnel, "open", AsyncMock(side_effect=RuntimeError("super-private-token")))
    warning = MagicMock()
    monkeypatch.setattr(bot_module.log, "warning", warning)
    bot = SimpleNamespace()
    asyncio.run(bot_module.Veil.start_dashboard(bot))
    dashboard.start.assert_awaited_once()
    dashboard.stop.assert_awaited_once()
    assert not hasattr(bot, "dashboard")
    logged = str(warning.call_args)
    assert "super-private-token" not in logged
    assert "bot will continue" in logged


def test_bot_starts_tunnel_only_after_dashboard_is_listening(monkeypatch):
    import core.bot as bot_module
    import core.dashboard as dashboard_module

    settings = SimpleNamespace(dashboard_enabled=True, dashboard_ngrok_enabled=True,
                               ngrok_authtoken="private", dashboard_url="https://dash.ngrok-free.dev")
    monkeypatch.setattr(bot_module, "settings", settings)
    dashboard = SimpleNamespace(host="127.0.0.1", port=8080, start=AsyncMock(), stop=AsyncMock())
    monkeypatch.setattr(dashboard_module.DashboardServer, "from_settings", lambda bot, settings: dashboard)
    tunnel = SimpleNamespace(close=AsyncMock())

    async def open_tunnel(**kwargs):
        dashboard.start.assert_awaited_once()
        assert kwargs == {"token": "private", "public_url": "https://dash.ngrok-free.dev",
                          "host": "127.0.0.1", "port": 8080}
        return tunnel

    monkeypatch.setattr(DashboardTunnel, "open", open_tunnel)
    bot = SimpleNamespace()
    asyncio.run(bot_module.Veil.start_dashboard(bot))
    assert bot.dashboard is dashboard and bot.dashboard_tunnel is tunnel
    dashboard.stop.assert_not_awaited()


def test_cancellation_during_endpoint_setup_closes_session(monkeypatch):
    sdk = SDK()
    monkeypatch.setitem(sys.modules, "ngrok", sdk)

    async def cancel(*args):
        raise asyncio.CancelledError()

    sdk.endpoint.listen_and_forward.side_effect = cancel

    async def go():
        with pytest.raises(asyncio.CancelledError):
            await DashboardTunnel.open(token="private", public_url="https://dash.ngrok-free.dev",
                                       host="127.0.0.1", port=8080)
        sdk.session.close.assert_awaited_once()

    asyncio.run(go())


def test_sdk_error_codes_and_connection_stage_are_safe_and_actionable(monkeypatch):
    sdk = SDK()
    sdk.builder.connect.side_effect = ValueError("ERR_NGROK_105: authtoken is super-private-token; URL secret.example")
    monkeypatch.setitem(sys.modules, "ngrok", sdk)

    async def go():
        with pytest.raises(TunnelFailure) as caught:
            await DashboardTunnel.open(token="super-private-token", public_url="https://dash.ngrok-free.dev",
                                       host="127.0.0.1", port=8080)
        assert str(caught.value) == "session connection: ERR_NGROK_105"
        assert caught.value.__cause__ is None
        assert "super-private-token" not in str(caught.value)
        assert "secret.example" not in str(caught.value)
        sdk.builder.connect.side_effect = ValueError("super-private-token TLS handshake error")
        with pytest.raises(TunnelFailure, match="session connection: TLS handshake failed"):
            await DashboardTunnel.open(token="super-private-token", public_url="https://dash.ngrok-free.dev",
                                       host="127.0.0.1", port=8080)
        sdk.builder.connect.side_effect = ValueError("ERR_NGROK_105")
        # Do not disclose any token even if its content happens to look like an ngrok error code.
        with pytest.raises(TunnelFailure, match="no ngrok error code returned"):
            await DashboardTunnel.open(token="ERR_NGROK_105", public_url="https://dash.ngrok-free.dev",
                                       host="127.0.0.1", port=8080)

    asyncio.run(go())


def test_bot_logs_safe_tunnel_failure_category(monkeypatch):
    import core.bot as bot_module
    import core.dashboard as dashboard_module

    monkeypatch.setattr(bot_module, "settings", SimpleNamespace(
        dashboard_enabled=True, dashboard_ngrok_enabled=True, ngrok_authtoken="private-secret",
        dashboard_url="https://dash.ngrok-free.dev"))
    dashboard = SimpleNamespace(host="127.0.0.1", port=8080, start=AsyncMock(), stop=AsyncMock())
    monkeypatch.setattr(dashboard_module.DashboardServer, "from_settings", lambda bot, settings: dashboard)
    monkeypatch.setattr(DashboardTunnel, "open", AsyncMock(side_effect=TunnelFailure("session connection: ERR_NGROK_105")))
    warning = MagicMock()
    monkeypatch.setattr(bot_module.log, "warning", warning)
    asyncio.run(bot_module.Veil.start_dashboard(SimpleNamespace()))
    assert "session connection: ERR_NGROK_105" in str(warning.call_args)
    assert "private-secret" not in str(warning.call_args)
    dashboard.stop.assert_awaited_once()
