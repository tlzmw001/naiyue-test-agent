"""Runtime scope consumers use the child snapshot and original human intent."""

import json

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables.config import ensure_config

from native_application_helpers import async_test, build_application
from naiyue_test_agent import application
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.tools import SubAgentSpec, ToolSettings, create_subagent_tool
from naiyue_test_agent.tools.permissions import PermissionMechanismError
from test_async_application import StreamingToolModel, call
from test_auto_permission_review import model as reviewer_model, verdict
from test_dynamic_tools import definition
from test_subagent_execution import ChildModel, delegated_app, record_instances, role


@async_test
async def test_child_batch_receives_parent_selected_concurrency(monkeypatch):
    limits = []
    child = ChildModel(replies=[AIMessage(content='done')])
    async def inspect_config(_messages):
        limits.append(ensure_config()['max_concurrency'])
    child.on_request = inspect_config
    app, _ = await delegated_app(monkeypatch, child)
    await app.arun(await app.acreate_session('A'), [('user', 'start')], max_concurrency=1)
    assert limits == [1]


@async_test
async def test_auto_reviews_original_human_request_not_delegated_task(monkeypatch):
    child = ChildModel(replies=[call('probe', {'value': 'actual'}, 'inner'), AIMessage(content='done')])
    root = StreamingToolModel(replies=[call('subagent', {'agent': 'tester', 'task': 'DELEGATED_NOT_CONSENT'}, 'delegate'),
                                       AIMessage(content='done')])
    fast, detail = reviewer_model([verdict()]), reviewer_model([])
    catalog = ModelCatalog('A', {'A': root, 'B': child, 'fast': fast, 'detail': detail})
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: catalog)
    settings = ToolSettings(subagents=(role(),), permissions={'mode': 'Auto', 'auto': {
        'fast_model_id': 'fast', 'detailed_model_id': 'detail'}})
    effects = []
    app = await build_application('unused', tools=[create_subagent_tool(), definition(calls=effects, approved=True)],
                                  initial_active_tools=['subagent'], system_prompt='', response_format=None, settings=settings)
    result = await app.arun(await app.acreate_session('A'), [('user', 'ORIGINAL_HUMAN_REQUEST')])
    assert result['messages'][-1].content == 'done' and effects == [('probe', 'actual')]
    assert len(fast.seen) == 1 and detail.seen == []
    data = json.loads(fast.seen[0][-1].content)
    assert [item['content'] for item in data['human_messages']] == ['ORIGINAL_HUMAN_REQUEST']
    assert 'DELEGATED_NOT_CONSENT' not in fast.seen[0][-1].content


@async_test
async def test_child_checkpoint_and_parent_report_share_sqlite_without_sharing_thread(monkeypatch, tmp_path):
    child = ChildModel(replies=[AIMessage(content='child report')])
    root = StreamingToolModel(replies=[call('subagent', {'agent': 'tester', 'task': 'child task'}, 'delegate'),
                                       AIMessage(content='root done')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': root, 'B': child}))
    async with open_async_persistent_application('unused', database_path=tmp_path / 'shared.sqlite',
            tools=[create_subagent_tool(), definition()], initial_active_tools=['subagent'],
            system_prompt='', response_format=None, settings=ToolSettings(subagents=(role(),))) as app:
        instances = record_instances(monkeypatch, app)
        sid = await app.acreate_session('A')
        await app.arun(sid, [('user', 'root task')])
        instance, = instances
        child_graph = app._sessions._subagents['tester']['agent']
        assert child_graph.checkpointer.backend is app._agent.checkpointer
        snapshot = await child_graph.aget_state({'configurable': {'thread_id': instance['child_thread_id']}})
        assert [message.content for message in snapshot.values['messages']] == ['child task', 'child report']
        assert not snapshot.next and not snapshot.interrupts
        rows = await app.alist_sessions()
        assert len(rows) == 1 and rows[0]['session_id'] == sid and rows[0]['state'] == 'ready'
        parent = await app.aget_messages(sid)
        assert [message.content for message in parent] == ['root task', '', 'child report', 'root done']
        assert next(message for message in parent if isinstance(message, ToolMessage)).tool_call_id == 'delegate'


@async_test
async def test_subagent_scope_is_used_by_model_selection_and_compaction(monkeypatch):
    from naiyue_test_agent.agent_runtime import context_compaction
    child = ChildModel(replies=[AIMessage(content='done')])
    app, _ = await delegated_app(monkeypatch, child)
    seen = []
    compact = context_compaction._acompact_messages
    async def record(**kwargs):
        seen.append((kwargs['context']['agent_path'], kwargs['model_id'], kwargs['model']))
        return await compact(**kwargs)
    monkeypatch.setattr(context_compaction, '_acompact_messages', record)
    await app.arun(await app.acreate_session('A'), [('user', 'start')])
    child_scopes = [item for item in seen if item[0] != 'root']
    assert len(child_scopes) == 1 and child_scopes[0][1:] == ('B', child)


@async_test
async def test_max_calls_omitted_does_not_install_an_implicit_small_limit(monkeypatch):
    child = ChildModel(replies=[call('probe', {'value': str(index)}, f'inner-{index}') for index in range(3)]
                               + [AIMessage(content='done')])
    app, _ = await delegated_app(monkeypatch, child)
    result = await app.arun(await app.acreate_session('A'), [('user', 'start')])
    assert len(child.seen) == 4
    assert next(message for message in result['messages'] if isinstance(message, ToolMessage)).status == 'success'


@async_test
async def test_mismatched_parent_permit_cannot_start_child(monkeypatch):
    child = ChildModel(replies=[])
    app, _ = await delegated_app(monkeypatch, child)
    original = app._sessions.prepare_subagent_instance
    def mismatch(*args, **kwargs):
        context = kwargs['parent_context']
        kwargs['parent_context'] = {**context, '_execution_permit': {
            **context['_execution_permit'], 'args': {'agent': 'other', 'task': 'different'},
        }}
        return original(*args, **kwargs)
    monkeypatch.setattr(app._sessions, 'prepare_subagent_instance', mismatch)
    with pytest.raises(PermissionMechanismError, match='permit'):
        await app.arun(await app.acreate_session('A'), [('user', 'start')])
    assert child.seen == [] and app._sessions._subagent_instances == {}
