"""Real process exit at each save boundary cannot authorize uncertain execution."""

import asyncio
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from langchain_core.messages import AIMessage

from native_application_helpers import async_test
from naiyue_test_agent import application
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.model_access import ModelCatalog
from test_async_application import StreamingToolModel, call
from test_subagent_execution import ChildModel
from test_subagent_restart import options
from test_subagent_continuation_boundaries import delegate


@pytest.mark.parametrize('point', ['claim', 'child_saved', 'parent_saved'])
@async_test
async def test_crash_boundary_never_replays_and_only_parent_confirmed_can_continue(monkeypatch, tmp_path, point):
    workspace = tmp_path / 'work'
    workspace.mkdir()
    repo = Path(__file__).resolve().parents[1]
    result = await asyncio.to_thread(subprocess.run,
        [sys.executable, str(Path(__file__).resolve()), point, str(tmp_path)], cwd=repo,
        env={**os.environ, 'PYTHONPATH': os.pathsep.join((str(repo), str(repo / 'tests')))},
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 91, result.stderr
    info = json.loads((tmp_path / 'crash.json').read_text())
    effect = workspace / 'effect.txt'
    assert effect.exists() is (point != 'claim')
    if effect.exists():
        assert effect.read_text() == 'once' and (tmp_path / 'executions').read_text() == 'write\n'
    root, child = StreamingToolModel(replies=[]), ChildModel(replies=[AIMessage(content='explicit new work')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': root, 'B': child}))
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError('Old tool execution must never be replayed')
    monkeypatch.setattr(importlib.import_module('naiyue_test_agent.tools.write'), '_write', forbidden)
    async with open_async_persistent_application('unused', **options(tmp_path)) as app:
        sid, identity = info['session_id'], info['agent_id']
        events = []
        app.subscribe_events(events.append)
        row = app._storage.get_subagent(sid, identity)
        assert row['state'] == {'claim': 'running', 'child_saved': 'pending_parent', 'parent_saved': 'ready'}[point]
        history = await app.aget_session_history(sid)
        if point == 'parent_saved':
            await app.arestore_session(sid)
        else:
            assert history['state'] == 'running'
            await app.arecover_session(sid, checkpoint_id=history['checkpoint_id'], action='continue')
        assert not root.seen and not child.seen and not events and not calls
        assert app._storage.get_subagent(sid, identity) == row
        assert not app.pending_approval_batches(sid) and app._sessions.require(sid)._run is None
        reply = await delegate(app, sid, root, task='explicit new delegation', identity=identity)
        if point == 'parent_saved':
            assert json.loads(reply.content)['report'] == 'explicit new work' and len(child.seen) == 1
        else:
            assert reply.status == 'error' and 'current state' in reply.content and not child.seen
            assert app._storage.get_subagent(sid, identity) == row
        assert not calls
    assert effect.exists() is (point != 'claim')
    if effect.exists():
        assert (tmp_path / 'executions').read_text() == 'write\n'


@async_test
async def test_restore_approval_cannot_confirm_uncommitted_child(monkeypatch, tmp_path):
    workspace = tmp_path / 'work'
    workspace.mkdir()
    (tmp_path / 'outside.txt').write_text('requires approval')
    repo = Path(__file__).resolve().parents[1]
    result = await asyncio.to_thread(subprocess.run,
        [sys.executable, str(Path(__file__).resolve()), 'root_approval', str(tmp_path)], cwd=repo,
        env={**os.environ, 'PYTHONPATH': os.pathsep.join((str(repo), str(repo / 'tests')))},
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 91, result.stderr
    info = json.loads((tmp_path / 'crash.json').read_text())
    root, child = StreamingToolModel(replies=[]), ChildModel(replies=[])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': root, 'B': child}))
    config = options(tmp_path)
    config['initial_active_tools'] = ['subagent', 'read']
    async with open_async_persistent_application('unused', **config) as app:
        sid, identity = info['session_id'], info['agent_id']
        record = app._storage.get_subagent(sid, identity)
        assert record['state'] == 'pending_parent'
        history = await app.aget_session_history(sid)
        assert history['recovery']['kind'] == 'pending_approval'
        events = []
        app.subscribe_events(events.append)
        await app.arecover_session(sid, checkpoint_id=history['checkpoint_id'], action='restore_approval')
        assert app._storage.get_subagent(sid, identity) == record
        assert app.pending_approval_batches(sid) and not root.seen and not child.seen and not events


async def crash():
    point, folder = sys.argv[1], Path(sys.argv[2])
    root = StreamingToolModel(replies=[call('subagent', {'agent': 'worker', 'task': 'write once'}, 'outer'), AIMessage(content='done')])
    child = ChildModel(replies=[call('write', {'path': 'effect.txt', 'content': 'once'}, 'inner'), AIMessage(content='saved report')])
    if point == 'root_approval':
        root.replies[1] = call('read', {'path': str(folder / 'outside.txt')}, 'outer-read')
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': root, 'B': child}))
        config = options(folder)
        if point == 'root_approval':
            config['initial_active_tools'] = ['subagent', 'read']
        async with open_async_persistent_application('unused', **config) as app:
            sid = await app.acreate_workspace_session('A', folder / 'work')
            def exit_now(record):
                (folder / 'crash.json').write_text(json.dumps({'session_id': sid, 'agent_id': record['agent_id']}))
                os._exit(91)
            save = app._storage.save_subagent
            def saved(record, expected):
                save(record, expected)
                if record['state'] == {'claim': 'running', 'child_saved': 'pending_parent'}.get(point):
                    exit_now(record)
            patch.setattr(app._storage, 'save_subagent', saved)
            finish = app._storage.finish_run
            def finished(*args, **kwargs):
                if point == 'root_approval':
                    exit_now(next(iter(app._storage.get_subagent_references(sid).values())))
                finish(*args, **kwargs)
                if point == 'parent_saved':
                    exit_now(next(iter(app._storage.get_subagent_references(sid).values())))
            patch.setattr(app._storage, 'finish_run', finished)
            module = importlib.import_module('naiyue_test_agent.tools.write')
            write = module._write
            def counted(*args, **kwargs):
                result = write(*args, **kwargs)
                with (folder / 'executions').open('a') as output:
                    output.write('write\n')
                    output.flush()
                    os.fsync(output.fileno())
                return result
            patch.setattr(module, '_write', counted)
            await app.arun(sid, [('user', 'write once')])


if __name__ == '__main__':
    asyncio.run(crash())
