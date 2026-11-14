"""Optional ngrok tunnel for a loopback-only dashboard, using the ngrok Python SDK."""
from __future__ import annotations

import re
from urllib.parse import urlsplit


class TunnelFailure(ValueError):
    """A safe diagnostic; never contains the raw SDK exception or credentials."""


def _sdk_failure(stage: str, error: Exception, token: str) -> TunnelFailure:
    # ngrok exceptions may include the authtoken, URL, or other credentials. Only retain a recognised
    # error-code pattern or a fixed connection category, never the original message.
    detail = str(error).replace(token, "[redacted]") if token else str(error)
    code = re.search(r"\bERR_NGROK_\d{1,6}\b", detail)
    if code:
        category = code.group(0)
    elif "tls handshake" in detail.lower():
        category = "TLS handshake failed"
    elif "dns" in detail.lower() or "resolve" in detail.lower():
        category = "DNS resolution failed"
    else:
        category = "no ngrok error code returned"
    return TunnelFailure(f"{stage}: {category}")


class DashboardTunnel:
    def __init__(self, session, listener):
        self.session = session
        self.listener = listener

    @classmethod
    async def open(cls, *, token: str | None, public_url: str | None, host: str, port: int):
        if not token:
            raise TunnelFailure("configuration: NGROK_AUTHTOKEN is missing")
        # A tunnel must never depend on a publicly reachable, unencrypted dashboard listener.
        if host != "127.0.0.1":
            raise TunnelFailure("configuration: dashboard host must be 127.0.0.1")
        try:
            parts = urlsplit(public_url or "")
            valid = (parts.scheme == "https" and parts.hostname and not parts.username and not parts.password
                     and not parts.port and parts.path in ("", "/") and not parts.query and not parts.fragment)
        except ValueError:
            valid = False
        if not valid:
            raise TunnelFailure("configuration: DASHBOARD_URL must be an HTTPS origin without a port or path")
        try:
            import ngrok
        except ImportError:
            raise TunnelFailure("dependency: ngrok SDK is not installed") from None
        try:
            session = await ngrok.SessionBuilder().authtoken(token).connect()
        except Exception as exc:
            raise _sdk_failure("session connection", exc, token) from None
        listener = None
        try:
            try:
                listener = await session.http_endpoint().domain(parts.hostname).listen_and_forward(f"http://127.0.0.1:{port}")
            except Exception as exc:
                raise _sdk_failure("endpoint creation", exc, token) from None
            # Never silently publish to a different domain than the OAuth redirect.
            if listener.url().rstrip("/").lower() != f"https://{parts.hostname}".lower():
                raise TunnelFailure("endpoint verification: ngrok returned a different public URL")
            return cls(session, listener)
        except BaseException:
            # Cleanup failures must not replace the safe error with an SDK exception containing secrets.
            try:
                if listener is not None:
                    await listener.close()
            except Exception:
                pass
            try:
                await session.close()
            except Exception:
                pass
            raise

    async def close(self):
        try:
            await self.listener.close()
        finally:
            await self.session.close()
