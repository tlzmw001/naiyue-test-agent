"""Required child/root persistence and cleanup remain separate from MCP success."""

import asyncio

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent import application
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.tools import create_subagent_tool
from test_async_application import StreamingToolModel, call
from test_mcp_agent_tools import NAME, calls
from test_mcp_connections import facts, process_alive
from test_mcp_subagents import MCPChildModel, child_settings, make_child_app
from test_subagent_execution import record_instances


def test_child_checkpoint_failure_after_mcp_effect_returns_error_without_replay(monkeypatch, tmp_path, caplog):
    async def scenario():
        path = tmp_path / 'child-save.jsonl'
        root = StreamingToolModel(replies=[call('subagent', {'agent': 'tester', 'task': 'call once'}, 'outer'), AIMessage(content='root handled save failure')])
        child = MCPChildModel(replies=[call(NAME, {}), AIMessage(content='must not become final report')])
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': root, 'B': child}))
        options = dict(database_path=tmp_path / 'state.sqlite', tools=[create_subagent_tool()],
            initial_active_tools=['subagent'], system_prompt='', response_format=None,
            settings=child_settings(path, mode='BypassApprovals'))
        async with open_async_persistent_application('unused', **options) as app:
            instances = record_instances(monkeypatch, app)
            backend = app.runs._agent.checkpointer
            original = backend.aput
            attempts = []
            async def fail(config, *args, **kwargs):
                if config['configurable']['thread_id'].startswith('subagent:') and calls(path):
                    attempts.append('failed')
                    raise OSError('PRIVATE-MCP-CHECKPOINT')
                return await original(config, *args, **kwargs)
            monkeypatch.setattr(backend, 'aput', fail)
            sid = await app.sessions.create('A')
            result = await app.runs.start(sid, [('user', 'child call')])
            outer, = [m for m in result['messages'] if isinstance(m, ToolMessage)]
            assert outer.status == 'error' and 'checkpoint' in outer.content.lower()
            assert result['messages'][-1].content == 'root handled save failure'
            assert attempts and len(calls(path)) == 1
            assert instances[0]['status'] == 'failed' and instances[0]['settled']
            assert not process_alive(calls(path)[0]['pid'])
            assert (await app.sessions.status(sid))['state'] == 'ready'
            assert 'PRIVATE-MCP-CHECKPOINT' not in str(result) + caplog.text
    asyncio.run(scenario())


def test_distinct_child_cleanup_failure_is_reported_and_application_still_closes(monkeypatch, tmp_path):
    from naiyue_test_agent.mcp_client.resources import MCPResourceError
    async def scenario():
        path = tmp_path / 'cleanup.jsonl'
        holder = {}
        async def inject_after_effect(messages):
            if not isinstance(messages[-1], ToolMessage): return
            owner, = [owner for key, owner in holder['app'].sessions._sessions.mcp_resources._owners.items()
                      if key[1] != 'root']
            close = owner.close
            async def fail():
                await close()
                raise MCPResourceError('synthetic_child_cleanup_failed')
            monkeypatch.setattr(owner, 'close', fail)
        child = MCPChildModel(replies=[call(NAME, {}), AIMessage(content='report')], on_request=inject_after_effect)
        app, _ = await make_child_app(monkeypatch, path, child, mode='BypassApprovals')
        holder['app'] = app
        instances = record_instances(monkeypatch, app)
        sid = await app.sessions.create('A')
        with pytest.raises(ExceptionGroup):
            await app.runs.start(sid, [('user', 'cleanup fault')])
        assert len(calls(path)) == 1 and not process_alive(calls(path)[0]['pid'])
        assert instances[0]['status'] == 'failed' and instances[0]['settled']
        with pytest.raises(ExceptionGroup): await app.aclose()
        assert app._lifetime._closed
        assert all(not process_alive(r['pid']) for r in facts(path) if r['event'] == 'start')
    asyncio.run(scenario())
