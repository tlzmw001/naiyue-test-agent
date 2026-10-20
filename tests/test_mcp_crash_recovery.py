"""A dead parent cannot resurrect a saved child MCP driver or replay its effects."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent import application
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.tools import create_subagent_tool
from test_async_application import StreamingToolModel, call
from test_mcp_agent_tools import NAME, calls
from test_mcp_connections import facts, process_alive, until
from test_mcp_subagents import MCPChildModel, child_settings


def options(folder, point):
    return dict(database_path=folder / 'state.sqlite', tools=[create_subagent_tool()],
        initial_active_tools=['subagent'], system_prompt='', response_format=None,
        settings=child_settings(folder / 'calls.jsonl', mode='Default' if point == 'approval' else 'BypassApprovals'))


@pytest.mark.parametrize('point', ['approval', 'committed'])
def test_reopened_parent_does_not_replay_or_revive_child(monkeypatch, tmp_path, point):
    async def scenario():
        repo = Path(__file__).resolve().parent.parent
        result = await asyncio.to_thread(subprocess.run,
            [sys.executable, str(Path(__file__).resolve()), str(tmp_path), point], cwd=repo,
            env={**os.environ, 'PYTHONPATH': os.pathsep.join((str(repo), str(repo / 'tests')))},
            capture_output=True, text=True, timeout=30)
        assert result.returncode == 91, result.stderr
        assert 'PRIVATE' not in result.stdout + result.stderr
        path = tmp_path / 'calls.jsonl'
        expected = 0 if point == 'approval' else 1
        assert len(calls(path)) == expected
        await until(lambda: all(not process_alive(r['pid']) for r in facts(path) if r['event'] == 'start'))
        sid = json.loads((tmp_path / 'crash.json').read_text())['session_id']
        root = StreamingToolModel(replies=[AIMessage(content='new communication only')])
        child = MCPChildModel(replies=[])
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': root, 'B': child}))
        async with open_async_persistent_application('unused', **options(tmp_path, point)) as app:
            history = await app.aget_session_history(sid)
            assert history['recovery']['kind'] == 'interrupted_execution'
            assert history['recovery']['actions'] == ['continue']
            await app.arecover_session(sid, checkpoint_id=history['checkpoint_id'], action='continue')
            assert app.pending_subagent_approvals(sid) == []
            assert root.seen == child.seen == [] and len(calls(path)) == expected
            messages = await app.aget_messages(sid)
            outer, = [m for m in messages if isinstance(m, ToolMessage)]
            assert outer.tool_call_id == 'outer' and outer.status == 'error'
            assert outer.additional_kwargs['naiyue_recovery']['reason'] == 'result_unconfirmed'
            assert 'may already have taken effect' in outer.content
            result = await app.arun(sid, [('user', 'new communication')])
            assert result['messages'][-1].content == 'new communication only'
            assert child.seen == [] and len(calls(path)) == expected
            assert app._sessions.child_thread_ids(sid) == ()
    asyncio.run(scenario())


async def crash(folder, point):
    root = StreamingToolModel(replies=[call('subagent', {'agent': 'tester', 'task': 'one call'}, 'outer')])
    entered = asyncio.Event()
    async def hold_after_effect(messages):
        if isinstance(messages[-1], ToolMessage):
            entered.set()
            await asyncio.Event().wait()
    child = MCPChildModel(replies=[call(NAME, {}, 'inner')],
                           on_request=hold_after_effect if point == 'committed' else None)
    application.load_model_catalog = lambda _: ModelCatalog('A', {'A': root, 'B': child})
    async with open_async_persistent_application('unused', **options(folder, point)) as app:
        sid = await app.acreate_session('A')
        task = asyncio.create_task(app.arun(sid, [('user', 'child call')]))
        if point == 'approval':
            await until(lambda: bool(app.pending_subagent_approvals(sid)) or task.done())
            assert not task.done() and not calls(folder / 'calls.jsonl')
        else:
            await asyncio.wait_for(entered.wait(), 15)
            assert len(calls(folder / 'calls.jsonl')) == 1
        (folder / 'crash.json').write_text(json.dumps({'session_id': sid}))
        os._exit(91)


if __name__ == '__main__':
    asyncio.run(crash(Path(sys.argv[1]), sys.argv[2]))
