"""Optional ngrok tunnel for a loopback-only dashboard, using the ngrok Python SDK."""
from __future__ import annotations

from urllib.parse import urlsplit


class DashboardTunnel:
    def __init__(self, session, listener):
        self.session = session
        self.listener = listener

    @classmethod
    async def open(cls, *, token: str | None, public_url: str | None, host: str, port: int):
        if not token:
            raise ValueError("NGROK_AUTHTOKEN is required")
        # A tunnel must never depend on a publicly reachable, unencrypted dashboard listener.
        if host != "127.0.0.1":
            raise ValueError("ngrok requires the dashboard to bind to 127.0.0.1")
        parts = urlsplit(public_url or "")
        if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.port
                or parts.path not in ("", "/") or parts.query or parts.fragment):
            raise ValueError("ngrok requires a public HTTPS origin without a port or path")
        import ngrok

        session = await ngrok.SessionBuilder().authtoken(token).connect()
        listener = None
        try:
            listener = await session.http_endpoint().domain(parts.hostname).listen_and_forward(f"http://127.0.0.1:{port}")
            # Never silently publish to a different domain than the OAuth redirect.
            if listener.url().rstrip("/").lower() != f"https://{parts.hostname}".lower():
                raise ValueError("ngrok returned a different public URL")
            return cls(session, listener)
        except BaseException:
            try:
                if listener is not None:
                    await listener.close()
            finally:
                await session.close()
            raise

    async def close(self):
        try:
            await self.listener.close()
        finally:
            await self.session.close()
