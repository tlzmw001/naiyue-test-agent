"""Credential-free MCP gate used by the existing installed-product smoke worker."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch


NAME = 'mcp__local__echo'


def process_alive(pid):
    if os.name != 'nt':
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:
            return False
        raise OSError('Unable to verify MCP fixture process')
    try:
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
            raise OSError('Unable to read MCP fixture process status')
        return code.value == 259
    finally:
        kernel.CloseHandle(handle)


def _write_config(path, server):
    lines = ['[mcp.servers.local]']
    lines.extend(f'{name} = {json.dumps(value)}' for name, value in server.items())
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


async def mcp_scenario(root, model_type):
    from langchain_core.messages import AIMessage, ToolMessage
    from naiyue_test_agent import application
    from naiyue_test_agent.application.async_application import build_async_local_application
    from naiyue_test_agent.application.async_persistent import open_async_persistent_application
    from naiyue_test_agent.model_access import ModelCatalog
    from naiyue_test_agent.settings import AppSettings
    from naiyue_test_agent.tools import create_subagent_tool, SubAgentSpec

    root = Path(root).resolve()
    audit, config = root / 'mcp-audit.jsonl', root / 'mcp-settings.toml'
    fixture = Path(__file__).resolve().parents[1] / 'tests' / 'mcp_discovery_fixture.py'
    assert fixture.is_file(), 'SDK fixture missing from release verification checkout'
    def records():
        return [json.loads(line) for line in audit.read_text(encoding='utf-8').splitlines()] if audit.exists() else []
    def calls():
        return [row for row in records() if row['event'] == 'call']
    def invoke(name, args, identity):
        return AIMessage(content='', tool_calls=[{'name': name, 'args': args, 'id': identity}])
    def outcome(result, identity):
        message, = [m for m in result['messages'] if isinstance(m, ToolMessage) and m.tool_call_id == identity]
        return message.artifact['outcome']

    disabled_model = model_type(replies=[AIMessage(content='disabled-ok')])
    with patch.object(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': disabled_model})), \
            patch('naiyue_test_agent.mcp_client.connections.open_connection', side_effect=AssertionError('disabled MCP IO')):
        app = await build_async_local_application('unused', tools=[], system_prompt='', response_format=None,
                                                  settings=AppSettings())
        try:
            sid = await app.acreate_session('A')
            result = await app.arun(sid, [('user', 'disabled')])
            assert result['messages'][-1].content == 'disabled-ok' and not records()
            assert not app._sessions.mcp_resources._idle_handles
        finally:
            await app.aclose()

    server = {'enabled': True, 'transport': 'stdio', 'command': sys.executable,
              'args': [str(fixture), '--audit', str(audit), '--mode', 'tools'], 'allowed_tools': ['echo']}
    _write_config(config, server)
    listed = subprocess.run([sys.executable, '-m', 'naiyue_test_agent.cli', 'mcp', 'list', '--settings', str(config)],
                            capture_output=True, text=True, check=True, timeout=30)
    directory = json.loads(listed.stdout)
    assert directory['servers'][0]['status'] == 'ok' and len(directory['servers'][0]['tools']) == 2
    assert not calls()

    requests = [('one', NAME, {}, 'root one'), ('two', NAME, {}, 'root two'),
        ('outer', 'subagent', {'agent': 'tester', 'task': 'two calls'}, 'child returned'),
        ('error', NAME, {'text': 'error'}, 'error handled'), ('drop', NAME, {'text': 'drop'}, 'unknown handled'),
        ('stale', NAME, {}, 'stale skipped'), ('on', NAME, {}, 're-enabled'),
        ('reject', NAME, {}, 'rejected'), ('ttl', NAME, {}, 'after ttl')]
    replies = []
    for identity, name, args, text in requests:
        replies.extend([invoke(name, args, identity), AIMessage(content=text)])
    model = model_type(replies=replies)
    child = model_type(replies=[invoke(NAME, {}, 'child-one'), invoke(NAME, {}, 'child-two'), AIMessage(content='child done')])
    catalog = ModelCatalog('A', {'A': model, 'B': child})
    options = dict(database_path=root / 'mcp.sqlite', tools=[create_subagent_tool()], initial_active_tools=[],
                   system_prompt='', response_format=None)
    with patch.object(application, 'load_model_catalog', lambda _: catalog):
        async with open_async_persistent_application('unused', settings=AppSettings(), **options) as app:
            resources = app._sessions.mcp_resources
            now = [100.0]
            resources._clock = lambda: now[0]
            sid = await app.acreate_workspace_session('A', root)
            report = await app.arefresh_mcp(config)
            assert report['servers'][0]['status'] == 'enabled' and app.get_active_tools(sid) == () and not calls()
            role = SubAgentSpec('tester', 'Installed MCP check', 'Use explicitly selected tools', (NAME,), model_id='B')
            await app.aregister_subagent(role)
            await app.aset_active_tools(sid, [NAME, 'subagent'])
            pending = await app.arun(sid, [('user', 'one')])
            assert pending['approval_requests'] and not calls()
            await app.aresume_tool_approval(sid, ['approve'], remember_approved=['one'])
            await app.arun(sid, [('user', 'two')])
            assert [r['count'] for r in calls()] == [1, 2] and calls()[0]['pid'] == calls()[1]['pid']
            await app.arun(sid, [('user', 'child')])
            assert [r['count'] for r in calls()][2:] == [1, 2]
            assert calls()[2]['pid'] == calls()[3]['pid'] != calls()[0]['pid']
            assert not process_alive(calls()[2]['pid']), 'MCP fixture process leaked'
            known = await app.arun(sid, [('user', 'error')])
            unknown = await app.arun(sid, [('user', 'drop')])
            assert outcome(known, 'error') == 'tool_error' and outcome(unknown, 'drop') == 'outcome_unknown'
            assert len(calls()) == 6
            server['allowed_tools'] = ['echo', 'other']
            _write_config(config, server)
            await app.arefresh_mcp(config)
            pending = await app.arun(sid, [('user', 'stale')])
            assert pending['approval_requests'] and len(calls()) == 6
            server['enabled'] = False
            _write_config(config, server)
            await app.arefresh_mcp(config)
            result = await app.aresume_tool_approval(sid, ['approve'])
            assert outcome(result, 'stale') == 'not_executed' and len(calls()) == 6
            server.update(enabled=True, idle_timeout_seconds=.2)
            _write_config(config, server)
            await app.arefresh_mcp(config)
            await app.arun(sid, [('user', 'on')])
            await app.aresume_tool_approval(sid, ['approve'])
            owner, = resources._owners.values()
            await app.arun(sid, [('user', 'pending')])
            now[0] = 1000
            assert not owner.task.done() and not resources._idle_handles
            await app.aresume_tool_approval(sid, ['reject'])
            assert next(iter(resources._idle_handles.values()))[2] == 1000.2
            now[0] = 1000.2
            async with asyncio.timeout(10):
                await asyncio.shield(owner.task)
                while resources._idle_tasks:
                    await asyncio.sleep(0)
            assert not resources._owners
            await app.arun(sid, [('user', 'ttl')])
            await app.aresume_tool_approval(sid, ['approve'])
            assert len(calls()) == 8 and [r['count'] for r in calls()][-2:] == [1, 1]
            assert calls()[-1]['pid'] != calls()[-2]['pid']
        seen = len(model.seen) + len(child.seen)
        reopened = AppSettings.model_validate({'mcp': {'servers': {'local': server}}})
        async with open_async_persistent_application('unused', settings=reopened, **options) as app:
            history = await app.aget_session_history(sid)
            assert any(message.content == 'after ttl' for message in history['messages'])
            assert len(calls()) == 8 and len(model.seen) + len(child.seen) == seen
    assert all(not process_alive(row['pid']) for row in records() if row['event'] == 'start'), 'MCP fixture process leaked'
    return {'disabled_zero_io': 'passed', 'list_zero_tool_calls': 'passed', 'runtime_add_and_select': 'passed',
        'root_reuse_counts': [1, 2], 'child_reuse_counts': [1, 2], 'known_error_and_unknown': 'passed',
        'disabled_pending_not_executed': 'passed', 'ttl_rebuild': 'passed', 'cold_history_no_replay': 'passed',
        'business_calls': 8, 'process_cleanup': 'passed', 'provider_requests': 0}
