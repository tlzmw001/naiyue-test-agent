"""Read saved facts without installing a session or resuming graph tasks."""

from native_application_helpers import async_test, await_diagnostics_idle

import asyncio
from copy import deepcopy

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from naiyue_test_agent.tools import create_session_read_tool
from test_application_run_events import ToolModel
from test_persistent_sessions import build
from test_async_application import StreamingToolModel
from test_async_persistent import factory


def database_facts(storage):
    return tuple(storage.connection.iterdump())


@pytest.mark.parametrize('state', ['running', 'failed', 'interrupted', 'review_required', 'ready'])
@async_test
async def test_reopened_history_is_read_only_and_preserves_saved_messages(monkeypatch, tmp_path, state):
    path = tmp_path / 'state.sqlite'
    async with build(monkeypatch, path) as app:
        storage = app._storage
        sid = await app.acreate_session('A')
        original = (await app.arun(sid, [HumanMessage(content='saved question', id='human')]))['messages']
        row = storage.get(sid)
        storage.finish_run(sid, state, row['checkpoint_id'], '', state)
    model = ToolModel(responses=[])
    async with build(monkeypatch, path, model) as app:
        storage = app._storage
        before = database_facts(storage)
        events = []
        app.subscribe_events(events.append)
        view = await app.aget_session_history(sid)
        assert view['messages'] == original
        assert view['checkpoint_id'] == row['checkpoint_id'] == view['confirmed_checkpoint_id']
        assert view['recovery']['kind'] == ('ready' if state == 'ready' else 'interrupted_execution')
        assert view['recovery']['actions'] == ([] if state == 'ready' else ['continue'])
        assert view['recovery']['unconfirmed_tool_call_ids'] == []
        assert not app._sessions.has_session(sid) and events == [] and model.responses == []
        assert database_facts(storage) == before


@async_test
async def test_history_uses_latest_checkpoint_instead_of_confirmed_pointer(monkeypatch, tmp_path):
    path = tmp_path / 'state.sqlite'
    async with build(monkeypatch, path) as app:
        storage = app._storage
        sid = await app.acreate_session('A')
        await app.arun(sid, [('user', 'old')])
        row = storage.get(sid)
        config = {'configurable': {'thread_id': row['thread_id']}}
        await app._agent.aupdate_state(config, {'messages': [AIMessage(content='new request', id='ai-new', tool_calls=[
            {'id': 'unconfirmed', 'name': 'read', 'args': {'path': 'x'}}])]}, as_node='model')
        storage.finish_run(sid, 'running', row['checkpoint_id'], '', None)
    async with build(monkeypatch, path) as app:
        storage = app._storage
        before = database_facts(storage)
        view = await app.aget_session_history(sid)
        assert view['checkpoint_id'] != view['confirmed_checkpoint_id'] == row['checkpoint_id']
        assert view['messages'][-1].id == 'ai-new'
        assert view['recovery']['unconfirmed_tool_call_ids'] == ['unconfirmed']
        assert database_facts(storage) == before and not app._sessions.has_session(sid)


async def approval_snapshot(monkeypatch, tmp_path):
    root = tmp_path / 'work'
    root.mkdir()
    async with build(monkeypatch, tmp_path / 'approval.sqlite', ToolModel(responses=[AIMessage(content='', tool_calls=[
            {'name': 'read', 'args': {'path': str(tmp_path / 'outside')}, 'id': 'call'}])]),
            [create_session_read_tool()]) as app:
        storage = app._storage
        sid = await app.acreate_workspace_session('A', root)
        await app.arun(sid, [('user', 'read outside')])
        row = storage.get(sid)
        snapshot = await app._agent.aget_state({'configurable': {'thread_id': row['thread_id']}})
        return row, snapshot


@async_test
async def test_real_approval_is_candidate_only_and_no_tool_was_run(monkeypatch, tmp_path):
    from naiyue_test_agent.application.sessions.session_recovery import classify_recovery
    row, snapshot = await approval_snapshot(monkeypatch, tmp_path)
    result = classify_recovery(row, snapshot)
    assert result == {'kind': 'pending_approval', 'reason': 'saved_pre_tool_approval',
                      'actions': [], 'unconfirmed_tool_call_ids': ['call']}
    assert not any(isinstance(m, ToolMessage) for m in snapshot.values['messages'])


@pytest.mark.parametrize('change', ['node', 'namespace', 'batch', 'args', 'result', 'error', 'marker', 'tool_result', 'extra_task'])
@async_test
async def test_approval_classification_requires_exact_saved_shape(monkeypatch, tmp_path, change):
    from naiyue_test_agent.application.sessions.session_recovery import classify_recovery
    row, original = await approval_snapshot(monkeypatch, tmp_path)
    snapshot = original._replace(values=deepcopy(original.values), config=deepcopy(original.config))
    task = snapshot.tasks[0]
    if change == 'node':
        snapshot = snapshot._replace(next=('other',), tasks=(task._replace(name='other'),))
    elif change == 'namespace':
        snapshot.config['configurable']['checkpoint_ns'] = 'child'
    elif change == 'batch':
        snapshot.values['tool_execution']['batch_id'] = 'wrong'
    elif change == 'args':
        snapshot.values['tool_execution']['calls'][0]['args'] = {'path': 'changed'}
    elif change in ('result', 'error'):
        snapshot = snapshot._replace(tasks=(task._replace(**{change: {} if change == 'result' else 'PRIVATE'}),))
    elif change == 'marker':
        snapshot = snapshot._replace(metadata={**snapshot.metadata, 'naiyue_recovery': '{"phase":"cleared"}'})
    elif change == 'tool_result':
        snapshot.values['messages'].append(ToolMessage(content='already saved', id='result', tool_call_id='call'))
    else:
        snapshot = snapshot._replace(tasks=(task, task))
    assert classify_recovery(row, snapshot)['kind'] != 'pending_approval'


@pytest.mark.parametrize('state,kind', [('ready', 'ready'), ('running', 'unavailable')])
@async_test
async def test_no_checkpoint_distinguishes_never_run_from_missing(monkeypatch, tmp_path, state, kind):
    path = tmp_path / 'state.sqlite'
    async with build(monkeypatch, path) as app:
        storage = app._storage
        sid = await app.acreate_session('A')
        storage.finish_run(sid, state, None, '', None)
    async with build(monkeypatch, path) as app:
        view = await app.aget_session_history(sid)
        assert view['messages'] == [] and view['checkpoint_id'] is None
        assert view['recovery']['kind'] == kind


@pytest.mark.parametrize('failure', ['read', 'identity', 'model', 'tools', 'mode', 'workspace', 'loaded'])
@async_test
async def test_unavailable_history_retains_only_safe_readable_facts(monkeypatch, tmp_path, caplog, failure):
    path = tmp_path / 'state.sqlite'
    async def verify(app):
        storage = app._storage
        await await_diagnostics_idle(app)
        if failure == 'read':
            async def broken(*_a):
                raise ValueError('PRIVATE_CHECKPOINT')
            monkeypatch.setattr(app._agent, 'aget_state', broken)
        elif failure == 'identity':
            snapshot = await app._agent.aget_state({'configurable': {'thread_id': storage.get(sid)['thread_id']}})
            snapshot.config['configurable']['thread_id'] = 'different'
            async def snapshot_read(*_a):
                return snapshot
            monkeypatch.setattr(app._agent, 'aget_state', snapshot_read)
        elif failure in ('model', 'tools', 'mode', 'workspace'):
            query, value = {
                'model': ('UPDATE naiyue_sessions SET model_id=? WHERE session_id=?', 'removed'),
                'tools': ('UPDATE naiyue_sessions SET active_tool_names_json=? WHERE session_id=?', '["removed"]'),
                'mode': ('UPDATE naiyue_sessions SET permission_mode=? WHERE session_id=?', 'removed'),
                'workspace': ('UPDATE naiyue_sessions SET workspace_root=? WHERE session_id=?', str(tmp_path / 'missing')),
            }[failure]
            storage.connection.execute(query, (value, sid))
            storage.connection.commit()
        before = database_facts(storage)
        view = await app.aget_session_history(sid)
        assert view['recovery']['kind'] == 'unavailable' and view['recovery']['actions'] == []
        assert [m.content for m in view['messages']] == ([] if failure in ('read', 'identity') else ['saved', 'answer'])
        assert 'PRIVATE' not in caplog.text and database_facts(storage) == before
        assert app._sessions.has_session(sid) == (failure == 'loaded')
    async with build(monkeypatch, path) as first:
        sid = await first.acreate_session('A')
        await first.arun(sid, [('user', 'saved')])
        if failure == 'loaded':
            await verify(first)
    if failure != 'loaded':
        async with build(monkeypatch, path) as app:
            await verify(app)


@async_test
async def test_native_history_matches_reopened_and_does_not_install_or_call_models(monkeypatch, tmp_path):
    path = tmp_path / 'state.sqlite'
    async def scenario():
        async with factory(monkeypatch, path, StreamingToolModel(replies=[AIMessage(content='saved')])) as app:
            sid = await app.acreate_session('A')
            await app.arun(sid, [('user', 'question')])
            storage = app._storage
            row = storage.get(sid)
            storage.finish_run(sid, 'failed', row['checkpoint_id'], '', 'failed')
        model = StreamingToolModel(replies=[])
        async with factory(monkeypatch, path, model) as app:
            before = database_facts(app._storage)
            view = await app.aget_session_history(sid)
            assert not app._sessions.has_session(sid) and model.seen == []
            assert database_facts(app._storage) == before
        return sid, view
    sid, asynchronous = await scenario()
    async with build(monkeypatch, path) as app:
        reopened = await app.aget_session_history(sid)
    assert reopened == asynchronous


@pytest.mark.parametrize('corruption', ['values', 'messages', 'message_id', 'call_id', 'orphan', 'duplicate_result'])
@async_test
async def test_malformed_history_has_explicit_unavailable_reason(monkeypatch, tmp_path, corruption):
    from naiyue_test_agent.application.sessions.session_recovery import classify_recovery
    row, original = await approval_snapshot(monkeypatch, tmp_path)
    snapshot = original._replace(values=deepcopy(original.values))
    messages = snapshot.values['messages']
    if corruption == 'values':
        snapshot = snapshot._replace(values=[])
    elif corruption == 'messages':
        snapshot.values['messages'] = ['undecoded']
    elif corruption == 'message_id':
        messages[-1].id = messages[0].id
    elif corruption == 'call_id':
        messages[-1].tool_calls[0]['id'] = None
    else:
        result = ToolMessage(content='saved', id='r', tool_call_id='unknown' if corruption == 'orphan' else 'call')
        messages.append(result)
        if corruption == 'duplicate_result':
            messages.append(result.model_copy(update={'id': 'r2'}))
    result = classify_recovery(row, snapshot)
    assert result['kind'] == 'unavailable' and result['actions'] == []
    assert all(isinstance(identity, str) and identity for identity in result['unconfirmed_tool_call_ids'])


@async_test
async def test_reopened_real_approval_never_prepares_or_executes_tools_or_auto(monkeypatch, tmp_path):
    import importlib
    from naiyue_test_agent.agent_runtime.tool_execution.permissions import AutoPermissionReviewer
    from naiyue_test_agent.tools.permissions import FilePermission
    row, original = await approval_snapshot(monkeypatch, tmp_path)
    reached = []
    def forbidden(*_a, **_k):
        reached.append(True)
        raise AssertionError('History must only read')
    async with build(monkeypatch, tmp_path / 'approval.sqlite', ToolModel(responses=[]), [create_session_read_tool()]) as app:
        storage = app._storage
        before = database_facts(storage)
        for name in ('ainvoke', 'aupdate_state'):
            monkeypatch.setattr(app._agent, name, forbidden)
        monkeypatch.setattr(FilePermission, 'prepare', forbidden)
        monkeypatch.setattr(AutoPermissionReviewer, 'areview', forbidden)
        monkeypatch.setattr(AutoPermissionReviewer, 'areview', forbidden)
        monkeypatch.setattr(importlib.import_module('naiyue_test_agent.tools.read'), '_read', forbidden)
        view = await app.aget_session_history(row['session_id'])
        assert view['recovery']['kind'] == 'pending_approval' and view['messages'] == original.values['messages']
        assert reached == [] and not app._sessions.has_session(row['session_id'])
        assert database_facts(storage) == before


@async_test
async def test_history_preserves_artifacts_and_pairs_reused_call_ids_by_batch(monkeypatch, tmp_path):
    path = tmp_path / 'state.sqlite'
    messages = [HumanMessage(content='saved', id='h')]
    for index in (1, 2):
        messages.extend([
            AIMessage(content='', id=f'a{index}', tool_calls=[{'name': 'custom', 'args': {}, 'id': 'same'}]),
            ToolMessage(content=[{'type': 'text', 'text': f'result{index}'}], id=f'r{index}',
                tool_call_id='same', artifact={'record': [index]}, status='success')])
    async with build(monkeypatch, path) as app:
        storage = app._storage
        sid = await app.acreate_session('A')
        row = storage.get(sid)
        await app._agent.aupdate_state({'configurable': {'thread_id': row['thread_id']}}, {'messages': messages}, as_node='tools')
        storage.finish_run(sid, 'failed', None, '', 'failed')
    async with build(monkeypatch, path) as app:
        view = await app.aget_session_history(sid)
        assert view['messages'] == messages
        assert view['messages'][-1].artifact == {'record': [2]}
        assert view['recovery']['kind'] == 'interrupted_execution'
        assert view['recovery']['unconfirmed_tool_call_ids'] == []


@async_test
async def test_latest_history_projects_completed_pending_writes_without_replay(monkeypatch, tmp_path):
    from typing import Annotated, TypedDict
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.graph.message import add_messages

    class State(TypedDict):
        messages: Annotated[list, add_messages]

    async def scenario():
        path = tmp_path / 'state.sqlite'
        done = asyncio.Event()
        calls = []
        async def fast(_state):
            calls.append('fast')
            done.set()
            return {'messages': [ToolMessage(content='saved result', tool_call_id='fast', id='result')]}
        async def fail(_state):
            await done.wait()
            calls.append('fail')
            raise RuntimeError('PRIVATE_EXECUTION')
        async with build(monkeypatch, path) as app:
            storage = app._storage
            sid = await app.acreate_session('A')
            row = storage.get(sid)
            config = {'configurable': {'thread_id': row['thread_id']}}
            builder = StateGraph(State)
            for name, function in (('fast', fast), ('fail', fail)):
                builder.add_node(name, function)
                builder.add_edge(START, name)
                builder.add_edge(name, END)
            async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
                graph = builder.compile(checkpointer=saver)
                with pytest.raises(RuntimeError, match='PRIVATE_EXECUTION'):
                    await graph.ainvoke({'messages': [AIMessage(content='', id='ai', tool_calls=[
                        {'name': 'fast', 'args': {}, 'id': 'fast'}, {'name': 'fail', 'args': {}, 'id': 'fail'}])]}, config)
            storage.finish_run(sid, 'failed', None, '', 'failed')
        async with build(monkeypatch, path) as app:
            storage = app._storage
            async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
                graph = builder.compile(checkpointer=saver)
                before = database_facts(storage)
                view = await app._persistence.ahistory(sid, graph)
                assert [m.content for m in view['messages']] == ['', 'saved result']
                assert view['recovery']['kind'] == 'interrupted_execution' and not app._sessions.has_session(sid)
                assert view['recovery']['unconfirmed_tool_call_ids'] == ['fail']
                assert database_facts(storage) == before and calls == ['fast', 'fail']
    await scenario()
