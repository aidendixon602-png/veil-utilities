"""Runtime status checks remain bounded and expose only safe operational summaries."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from core.runtime_health import runtime_health


def test_runtime_health_reports_database_and_subsystem_status_without_node_details():
    music = SimpleNamespace(nodes=[("https://private-node.invalid", "secret")],
                           online_nodes=lambda: [object()])
    dashboard = SimpleNamespace(runner=object())
    bot = SimpleNamespace(db=SimpleNamespace(fetchone=AsyncMock(return_value=(1,))), dashboard=dashboard,
                          auth_required=True, get_cog=lambda name: {"Antinuke": object(), "Music": music}.get(name))

    status = asyncio.run(runtime_health(bot))

    assert status == {"database": "online", "music": "1/1 nodes online",
                      "security": "auth gate on · antinuke loaded", "dashboard": "running"}
    assert "private-node.invalid" not in repr(status) and "secret" not in repr(status)


def test_runtime_health_reports_database_failures_and_missing_services():
    async def fail(_query):
        raise OSError("database offline")

    bot = SimpleNamespace(db=SimpleNamespace(fetchone=fail), get_cog=lambda _name: None)
    status = asyncio.run(runtime_health(bot))
    assert status == {"database": "degraded", "music": "disabled", "security": "auth gate unknown · antinuke not loaded",
                      "dashboard": "disabled"}
