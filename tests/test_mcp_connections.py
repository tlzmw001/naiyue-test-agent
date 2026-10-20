"""Real stdio/HTTP discovery and resource cleanup using the official SDK."""

import asyncio
from contextlib import asynccontextmanager
import json
import logging
import os
from pathlib import Path
import subprocess
import sys

import pytest


FIXTURE = Path(__file__).with_name('mcp_discovery_fixture.py')


def facts(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


async def until(predicate):
    async with asyncio.timeout(15):
        while not predicate():
            await asyncio.sleep(.02)


def process_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def stdio_settings(path, mode='normal', **extra):
    from naiyue_test_agent.mcp_client.settings import MCPServerSettings
    return MCPServerSettings(enabled=True, transport='stdio', command=sys.executable,
        args=[str(FIXTURE), '--audit', str(path), '--mode', mode], **extra)


@asynccontextmanager
async def http_server(path, mode='normal', json_response=False, *, legacy=False):
    arguments = [sys.executable, str(FIXTURE), '--audit', str(path), '--http', '--mode', mode]
    if json_response:
        arguments.append('--json-response')
    if legacy:
        arguments.append('--legacy')
    with path.with_suffix('.stderr').open('w') as output:
        proc = subprocess.Popen(arguments, stdout=output, stderr=output)
        try:
            await until(lambda: any(f['event'] == 'ready' for f in facts(path)) or proc.poll() is not None)
            assert proc.poll() is None, path.with_suffix('.stderr').read_text()[-2000:]
            port = next(f['port'] for f in facts(path) if f['event'] == 'ready')
            yield f'http://127.0.0.1:{port}/mcp/'
        finally:
            proc.terminate()
            await asyncio.to_thread(proc.wait, 15)


@pytest.mark.parametrize('mode', ['normal', 'empty', 'invalid'])
def test_real_stdio_discovery_cleanup_and_no_call(tmp_path, monkeypatch, caplog, mode):
    from naiyue_test_agent.mcp_client.discovery import discover_server, MCPDiscoveryError
    path = tmp_path / 'stdio.jsonl'
    monkeypatch.setenv('OPENAI_API_KEY', 'MUST-NOT-INHERIT')
    monkeypatch.setenv('MCP_FIXTURE_SOURCE', 'explicit-value')
    server = stdio_settings(path, mode, env={'FIXTURE_VALUE': '${MCP_FIXTURE_SOURCE}'})
    async def scenario():
        if mode == 'invalid':
            with pytest.raises(MCPDiscoveryError):
                await discover_server('local', server)
        else:
            catalog = await discover_server('local', server)
            assert len(catalog.tools) == (0 if mode == 'empty' else 2)
        audit = facts(path)
        assert not process_alive(audit[0]['pid'])
        assert not audit[0]['inherited_key'] and audit[0]['explicit'] == 'explicit-value'
        assert not any(item['event'] == 'call' for item in audit)
    with caplog.at_level(logging.DEBUG):
        asyncio.run(scenario())
    assert 'PRIVATE-STDERR' not in caplog.text and 'MUST-NOT-INHERIT' not in caplog.text


@pytest.mark.parametrize('json_response', [False, True])
@pytest.mark.parametrize('mode', ['normal', 'legacy', 'empty', 'invalid', 'auth'])
def test_real_http_discovery_cleanup_and_no_call(tmp_path, monkeypatch, caplog, mode, json_response):
    from naiyue_test_agent.mcp_client.discovery import discover_server, MCPDiscoveryError
    from naiyue_test_agent.mcp_client.settings import MCPServerSettings
    from naiyue_test_agent.tools.web_settings import NetworkSettings
    path = tmp_path / 'http.jsonl'
    from naiyue_test_agent.mcp_client.connections import _BudgetTransport
    closed = []
    original_close = _BudgetTransport.aclose
    async def close(transport):
        await original_close(transport)
        closed.append(True)
    monkeypatch.setattr(_BudgetTransport, 'aclose', close)
    async def scenario():
        async with http_server(path, mode, json_response) as url:
            server = MCPServerSettings(enabled=True, transport='streamable_http', url=url)
            if mode in ('invalid', 'auth'):
                with pytest.raises(MCPDiscoveryError):
                    await discover_server('local', server, NetworkSettings(mode='direct'))
            else:
                catalog = await discover_server('local', server, NetworkSettings(mode='direct'))
                assert len(catalog.tools) == (0 if mode == 'empty' else 2)
            assert closed == [True]
            if mode == 'legacy':
                assert any(row['event'] == 'delete' for row in facts(path))
            else:
                assert not any(row['event'] == 'delete' for row in facts(path))
        assert not any(row['event'] == 'call' for row in facts(path))
    with caplog.at_level(logging.DEBUG):
        asyncio.run(scenario())
    assert 'PRIVATE-AUTH-BODY' not in caplog.text
    assert '/mcp/' not in caplog.text


def test_stdio_deadline_and_cancellation_close_process(tmp_path):
    from naiyue_test_agent.mcp_client.discovery import discover_server, MCPDiscoveryError
    async def scenario():
        path = tmp_path / 'timeout.jsonl'
        # Leave startup a wide window; synchronize the cancellation branch on list.
        with pytest.raises(MCPDiscoveryError):
            await discover_server('local', stdio_settings(path, 'wait', connect_timeout_seconds=2))
        assert not process_alive(facts(path)[0]['pid'])
        path = tmp_path / 'cancel.jsonl'
        task = asyncio.create_task(discover_server('local', stdio_settings(path, 'wait')))
        await until(lambda: any(row['event'] == 'list' for row in facts(path)))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not process_alive(facts(path)[0]['pid'])
    asyncio.run(scenario())


def test_http_parse_budget_rejects_before_catalog(tmp_path):
    from naiyue_test_agent.mcp_client.discovery import discover_server, MCPDiscoveryError
    from naiyue_test_agent.mcp_client.settings import MCPServerSettings
    from naiyue_test_agent.tools.web_settings import NetworkSettings
    async def scenario():
        path = tmp_path / 'budget.jsonl'
        async with http_server(path, 'large', True) as url:
            with pytest.raises(MCPDiscoveryError):
                await discover_server('local', MCPServerSettings(enabled=True, transport='streamable_http',
                    url=url, max_response_bytes=4096), NetworkSettings(mode='direct'))
        assert not any(row['event'] == 'call' for row in facts(path))
    asyncio.run(scenario())
