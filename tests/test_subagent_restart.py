"""A real crash leaves child history, but recovery never replays delegation."""

import asyncio
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from native_application_helpers import async_test
from naiyue_test_agent import application
from scripts.component_fixtures import open_component_application
from naiyue_test_agent.storage.errors import SessionPersistenceError
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.tools import SubAgentSpec, create_subagent_tool, create_session_read_tool, create_session_write_tool
from naiyue_test_agent.settings import AppSettings
from test_async_application import StreamingToolModel, call
from test_subagent_execution import ChildModel, record_instances


def options(folder):
    return dict(database_path=folder / 'state.sqlite', tools=[create_subagent_tool(),
        create_session_read_tool(), create_session_write_tool()], initial_active_tools=['subagent'],
        system_prompt='', response_format=None, settings=AppSettings(subagents=(
            SubAgentSpec('worker', 'Inspect or write the requested file', 'Report the result.',
                         ('read', 'write'), model_id='B'),)))


@pytest.mark.parametrize('point', ['approval', 'committed'])
@async_test
async def test_restart_pairs_root_delegation_without_replaying_child_or_reviving_approval(monkeypatch, tmp_path, point):
    workspace = tmp_path / 'work'
    workspace.mkdir()
    (tmp_path / 'outside.txt').write_text('not approved', encoding='utf-8')
    repo = Path(__file__).resolve().parent.parent
    result = await asyncio.to_thread(subprocess.run,
        [sys.executable, str(Path(__file__).resolve()), point, str(tmp_path)], cwd=repo,
        env={**os.environ, 'PYTHONPATH': os.pathsep.join((str(repo), str(repo / 'tests')))},
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 91, result.stderr
    info = json.loads((tmp_path / 'crash.json').read_text())
    effect = workspace / 'effect.txt'
    assert effect.exists() is (point == 'committed')
    if point == 'committed':
        assert effect.read_text() == 'committed once'
        assert (tmp_path / 'executions').read_text() == 'write\n'
    root = StreamingToolModel(replies=[AIMessage(content='new communication only')])
    child = ChildModel(replies=[])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': root, 'B': child}))
    async with open_component_application('unused', **options(tmp_path)) as app:
        sid = info['session_id']
        events, reached = [], []
        app.host.observability.subscribe(events.append)
        def forbidden(*_args, **_kwargs):
            reached.append(True)
            raise AssertionError('Recovery must not execute an old child tool')
        monkeypatch.setattr(importlib.import_module('naiyue_test_agent.tools.read'), '_read', forbidden)
        monkeypatch.setattr(importlib.import_module('naiyue_test_agent.tools.write'), '_write', forbidden)
        history = await app.host.history.session(sid)
        assert history['state'] == 'running'
        assert history['recovery']['kind'] == 'interrupted_execution'
        assert history['recovery']['unconfirmed_tool_call_ids'] == ['outer']
        row = app.host.sessions._persistence.storage.sessions.get(sid)
        config = {'configurable': {'thread_id': row['thread_id']}}
        source = await app.host.runs._agent.aget_state(config)
        child_graph = app.host.sessions._sessions._subagents['worker']['agent']
        child_config = {'configurable': {'thread_id': info['child_thread_id']}}
        original_child = await child_graph.aget_state(child_config)
        assert bool(original_child.interrupts) is (point == 'approval')
        with pytest.raises(SessionPersistenceError, match='session_recovery_blocked'):
            await app.host.sessions.recover(sid, checkpoint_id=history['checkpoint_id'], action='restore_approval')
        recovered = await app.host.sessions.recover(sid, checkpoint_id=history['checkpoint_id'], action='continue')
        assert recovered['state'] == 'ready'
        assert not reached and not root.seen and not child.seen and not events
        storage = app.host.sessions._persistence.storage
        child_head, = [r for r in storage.conversation.chat_heads(sid) if r['agent_path'] != 'root']
        assert child_head['thread_id'] == info['child_thread_id']
        archived_child = [json.loads(r[0])['data'] for r in storage.database.connection.execute(
            'SELECT body_json FROM naiyue_chat_messages WHERE session_id=? AND thread_id=? ORDER BY sequence',
            (sid, info['child_thread_id']))]
        assert [r['id'] for r in archived_child] == [m.id for m in original_child.values['messages']]
        assert any(r['tool_calls'] for r in archived_child if r['type'] == 'ai')
        assert all(not (owner._runtime.run.subagent_instances if owner._runtime.run else {}) for owner in app.host.sessions._sessions._sessions.values()) and app.host.runs.pending_approvals(sid) == []
        saved = await app.host.runs._agent.aget_state(config)
        assert not saved.next and not saved.tasks and saved.values['tool_execution'] == {}
        assert saved.values['messages'][:-1] == source.values['messages']
        feedback = saved.values['messages'][-1]
        assert isinstance(feedback, ToolMessage) and feedback.name == 'subagent'
        assert feedback.tool_call_id == 'outer' and feedback.status == 'error'
        assert feedback.additional_kwargs['naiyue_recovery']['reason'] == 'result_unconfirmed'
        assert 'not automatically replayed' in feedback.content
        writes = storage.database.connection.total_changes
        root_page = await app.host.history.page(sid, limit=1, body_bytes=16384, preview_chars=2048)
        assert root_page['records'][0]['message_id'] == feedback.id
        records = await app.host.history.chat_subagents(sid)
        child_id = info['child_thread_id'].removeprefix('subagent:')
        assert any(row['agent_id'] == child_id for row in records)
        child_page = await app.host.history.page(sid, agent_id=child_id, limit=1, body_bytes=16384, preview_chars=2048)
        assert child_page['has_more']
        assert child_page['records'][0]['message_id'] == original_child.values['messages'][-1].id
        assert storage.database.connection.total_changes == writes and not root.seen and not child.seen and not reached
        unchanged = await child_graph.aget_state(child_config)
        assert unchanged.config == original_child.config and unchanged.values == original_child.values
        assert (await app.host.sessions.list())[0]['state'] == 'ready'
        following = await app.host.runs.start(sid, [('user', 'continue discussing without executing anything')])
        assert following['messages'][-1].content == 'new communication only'
        assert len(root.seen) == 1 and not child.seen and not reached
    if point == 'committed':
        assert effect.read_text() == 'committed once'
        assert (tmp_path / 'executions').read_text() == 'write\n'
    else:
        assert not effect.exists() and not (tmp_path / 'executions').exists()


async def crash_child():
    point, folder = sys.argv[1], Path(sys.argv[2])
    root = StreamingToolModel(replies=[call('subagent', {'agent': 'worker', 'task': point}, 'outer')])
    child_call = (call('read', {'path': str(folder / 'outside.txt')}, 'inner') if point == 'approval'
                  else call('write', {'path': 'effect.txt', 'content': 'committed once'}, 'inner'))
    child = ChildModel(replies=[child_call])
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': root, 'B': child}))
        async with open_component_application('unused', **options(folder)) as app:
            instances = record_instances(patch, app)
            sid = await app.host.sessions.create_workspace('A', folder / 'work')
            def exit_now():
                (folder / 'crash.json').write_text(json.dumps({'session_id': sid,
                    'child_thread_id': instances[0]['child_thread_id']}), encoding='utf-8')
                os._exit(91)
            if point == 'approval':
                from naiyue_test_agent.application.subagents import session_subagents
                announce = session_subagents.announce_approval
                def saved(observation, batch):
                    announce(observation, batch)
                    exit_now()
                patch.setattr(session_subagents, 'announce_approval', saved)
            else:
                module = importlib.import_module('naiyue_test_agent.tools.write')
                write = module._write
                def committed(*args, **kwargs):
                    write(*args, **kwargs)
                    with (folder / 'executions').open('a') as output:
                        output.write('write\n')
                        output.flush()
                        os.fsync(output.fileno())
                    exit_now()
                patch.setattr(module, '_write', committed)
            await app.host.runs.start(sid, [('user', 'perform the delegated task')])


if __name__ == '__main__':
    asyncio.run(crash_child())
