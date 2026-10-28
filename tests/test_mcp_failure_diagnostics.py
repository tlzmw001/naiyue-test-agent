"""MCP connection failures expose safe leaf facts, never SDK exception bodies."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize(('error_type', 'code'), [(FileNotFoundError, 2), (PermissionError, 13)])
def test_owner_failure_preserves_original_group_and_logs_safe_leaf(monkeypatch, caplog, error_type, code):
    from naiyue_test_agent.mcp_client import connections
    from naiyue_test_agent.mcp_client.owner import ConnectionOwner
    from naiyue_test_agent.mcp_client.resources import MCPResourceError
    original = ExceptionGroup('PRIVATE_GROUP', [error_type(code, 'PRIVATE_PATH')])
    @asynccontextmanager
    async def connect(*args, **kwargs):
        raise original
        yield
    monkeypatch.setattr(connections, 'open_connection', connect)
    async def scenario():
        owner = ConnectionOwner('local', SimpleNamespace(connect_timeout_seconds=5), None, None, 'identity')
        await owner.task
        with pytest.raises(MCPResourceError, match='mcp_connect_failed'):
            await owner.ready
        assert owner.failure is original
        await owner.close()
    asyncio.run(scenario())
    assert error_type.__name__ in caplog.text and str(code) in caplog.text
    assert 'connect' in caplog.text and 'PRIVATE' not in caplog.text
