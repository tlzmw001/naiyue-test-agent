"""Real process exits at explicit save boundaries, without timing guesses."""

from native_application_helpers import async_test

import asyncio
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent.tools import create_session_read_tool, create_session_write_tool
from test_persistent_sessions import build
from test_session_recovery import unfinished
from test_async_persistent import factory
from test_async_application import StreamingToolModel
from test_application_run_events import ToolModel


def run_child(*args):
    root = Path(__file__).resolve().parent.parent
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join((str(root / 'tests'), str(root)))}
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), *map(str, args)],
        cwd=root, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 91, result.stderr


@pytest.mark.parametrize('native', [False, True])
@pytest.mark.parametrize('point', ['before_cleared', 'cleared', 'during_paired_writes', 'paired',
                                  'finished', 'before_metadata', 'after_metadata'])
@async_test
async def test_crash_at_recovery_boundary_can_reopen_and_finish(monkeypatch, tmp_path, point, native):
    path = tmp_path / 'state.sqlite'
    sid, original, messages = await unfinished(monkeypatch, path)
    checkpoint = original.config['configurable']['checkpoint_id']
    run_child('recover', path, sid, checkpoint, point, str(int(native)))
    async with build(monkeypatch, path) as app:
        storage = app._storage
        history = await app.aget_session_history(sid)
        latest = history['checkpoint_id']
        assert [m for m in history['messages'] if isinstance(m, ToolMessage) and m.tool_call_id == 'a'] == [messages[-1]]
        if point == 'after_metadata':
            assert storage.sessions.get(sid)['state'] == 'ready' and history['recovery']['kind'] == 'ready'
            assert await app.arestore_session(sid) == sid
        else:
            assert storage.sessions.get(sid)['state'] == 'failed' and history['recovery']['actions'] == ['continue']
            if point == 'before_cleared':
                assert latest == checkpoint
            await app.arecover_session(sid, checkpoint_id=latest, action='continue')
        snapshot = await app._agent.aget_state({'configurable': {'thread_id': original.config['configurable']['thread_id']}})
        assert snapshot.next == () and snapshot.tasks == ()
        assert snapshot.values['messages'][:3] == messages
        assert [m.tool_call_id for m in snapshot.values['messages'] if isinstance(m, ToolMessage)] == ['a', 'b']
        assert json.loads(snapshot.metadata['naiyue_recovery'])['phase'] == 'finished'
        archived = [json.loads(r[0])['data'] for r in storage.database.connection.execute(
            'SELECT body_json FROM naiyue_chat_messages WHERE session_id=? ORDER BY sequence', (sid,))]
        assert [m['id'] for m in archived] == [m.id for m in snapshot.values['messages']]
        assert archived[-1]['additional_kwargs']['naiyue_recovery']['reason'] == 'result_unconfirmed'
        writes = storage.database.connection.total_changes
        page = await app.aget_chat_page(sid, limit=1, body_bytes=16384, preview_chars=2048)
        latest, = page['records']
        assert page['has_more'] and latest['message_id'] == snapshot.values['messages'][-1].id
        assert latest['parent_message_id'] == messages[1].id
        assert json.loads(latest['body_json'])['data'] == archived[-1]
        assert storage.database.connection.total_changes == writes


@async_test
async def test_file_commit_without_tool_result_is_retained_and_never_replayed(monkeypatch, tmp_path):
    path = tmp_path / 'state.sqlite'
    root = tmp_path / 'work'
    root.mkdir()
    (root / 'a.txt').write_text('actual read input')
    run_child('write', path, root)
    sid = (tmp_path / 'session-id').read_text()
    assert (root / 'b.txt').read_text() == 'committed effect'
    assert (tmp_path / 'executions').read_text() == 'write\n'
    reached = []
    def forbidden(*_a, **_kw):
        reached.append(True)
        raise AssertionError('Old file tools must not replay')
    tools = [create_session_read_tool(), create_session_write_tool()]
    async with build(monkeypatch, path, tools=tools) as app:
        storage = app._storage
        history = await app.aget_session_history(sid)
        assert history['state'] == 'running' and 'b' in history['recovery']['unconfirmed_tool_call_ids']
        monkeypatch.setattr(importlib.import_module('naiyue_test_agent.tools.write'), '_write', forbidden)
        monkeypatch.setattr(importlib.import_module('naiyue_test_agent.tools.read'), '_read', forbidden)
        events = []
        app.subscribe_events(events.append)
        await app.arecover_session(sid, checkpoint_id=history['checkpoint_id'], action='continue')
        assert events == [] and reached == []
        assert (root / 'b.txt').read_text() == 'committed effect'
        snapshot = await app._agent.aget_state({'configurable': {'thread_id': storage.sessions.get(sid)['thread_id']}})
        result = next(m for m in snapshot.values['messages'] if isinstance(m, ToolMessage) and m.tool_call_id == 'b')
        assert result.status == 'error' and 'may already have taken effect' in result.content
        writes = storage.database.connection.total_changes
        page = await app.aget_chat_page(sid, limit=1, body_bytes=16384, preview_chars=2048)
        latest, = page['records']
        assert latest['message_id'] == result.id and latest['parent_message_id'] == 'ai'
        assert json.loads(latest['body_json'])['data']['additional_kwargs']['naiyue_recovery']['reason'] == 'result_unconfirmed'
        assert storage.database.connection.total_changes == writes and reached == []
        assert (await app.arun(sid, [('user', 'new input')]))['messages'][-1].content == 'answer'
        assert reached == [] and (tmp_path / 'executions').read_text() == 'write\n'


def crash(point, wanted):
    if point == wanted:
        os._exit(91)


def install_exit_points(patch, app, point, native):
    graph = app._agent
    method = graph.aupdate_state
    async def update(config, values, *, as_node):
        phase = json.loads(config['metadata']['naiyue_recovery'])['phase']
        crash('before_' + phase, point)
        result = await method(config, values, as_node=as_node)
        crash(phase, point)
        return result
    patch.setattr(graph, 'aupdate_state', update)
    saver = graph.checkpointer
    put = saver.aput
    async def save(config, *args, **kwargs):
        if json.loads(config.get('metadata', {}).get('naiyue_recovery', '{}')).get('phase') == 'paired':
            crash('during_paired_writes', point)
        return await put(config, *args, **kwargs)
    patch.setattr(saver, 'aput', save)
    storage = app._storage
    finish = storage.runs.finish_run
    def metadata(*args):
        crash('before_metadata', point)
        result = finish(*args)
        crash('after_metadata', point)
        return result
    patch.setattr(storage.runs, 'finish_run', metadata)


async def child():
    mode, path, *args = sys.argv[1:]
    with pytest.MonkeyPatch.context() as patch:
        if mode == 'recover':
            sid, checkpoint, point, native = args
            if native == '1':
                async def execute():
                    async with factory(patch, Path(path), StreamingToolModel(replies=[])) as app:
                        install_exit_points(patch, app, point, True)
                        await app.arecover_session(sid, checkpoint_id=checkpoint, action='continue')
                await execute()
            else:
                async with build(patch, Path(path)) as app:
                    storage = app._storage
                    install_exit_points(patch, app, point, False)
                    await app.arecover_session(sid, checkpoint_id=checkpoint, action='continue')
        else:
            root = Path(args[0])
            module = importlib.import_module('naiyue_test_agent.tools.write')
            original = module._write
            def committed(*a, **kw):
                result = original(*a, **kw)
                with (root.parent / 'executions').open('a') as output:
                    output.write('write\n')
                    output.flush()
                    os.fsync(output.fileno())
                os._exit(91)
            patch.setattr(module, '_write', committed)
            model = ToolModel(responses=[AIMessage(content='', id='ai', tool_calls=[
                {'name': 'read', 'args': {'path': 'a.txt'}, 'id': 'a'},
                {'name': 'write', 'args': {'path': 'b.txt', 'content': 'committed effect'}, 'id': 'b'}])])
            async with build(patch, Path(path), model, [create_session_read_tool(), create_session_write_tool()]) as app:
                storage = app._storage
                sid = await app.acreate_workspace_session('A', root)
                (root.parent / 'session-id').write_text(sid)
                await app.arun(sid, [('user', 'fixture request')])


if __name__ == '__main__':
    asyncio.run(child())
