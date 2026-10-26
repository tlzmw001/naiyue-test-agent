"""One user capability exposes delegation and bounded root-only discovery."""

from runtime_context_helpers import typed_scope
import json
import asyncio

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from native_application_helpers import async_test
from naiyue_test_agent import application
from scripts.component_fixtures import open_component_application
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.tools import subagent
from naiyue_test_agent.tools.settings import SubAgentOutputSettings
from test_async_application import call
from test_subagent_execution import ChildModel
from test_subagent_restart import options
from test_subagent_continuation_boundaries import delegate


def test_discovery_defaults_and_strict_limits():
    settings = SubAgentOutputSettings()
    assert (settings.max_list_items, settings.max_list_bytes, settings.task_preview_chars) == (20, 8192, 160)
    for field in ('max_list_items', 'max_list_bytes', 'task_preview_chars'):
        for bad in (0, -1, True, '2', 1.5):
            with pytest.raises(ValueError):
                SubAgentOutputSettings(**{field: bad})


@async_test
async def test_selected_subagent_exposes_list_executes_it_and_reopen_needs_no_selection_migration(monkeypatch, tmp_path):
    root, child = ChildModel(replies=[]), ChildModel(replies=[AIMessage(content='saved')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': root, 'B': child}))
    config = options(tmp_path)
    config['tools'].append(subagent.create_subagent_list_tool())
    async with open_component_application('unused', **config) as app:
        sid = await app.host.sessions.create('A')
        identity = json.loads((await delegate(app, sid, root)).content)['agent_id']
        assert app.host.sessions.active_tools(sid) == ('subagent',)
        saved_selection = app.host.sessions._persistence.storage.sessions.get(sid)['active_tool_names_json']
    async with open_component_application('unused', **config) as app:
        await app.host.sessions.restore(sid)
        root.replies.extend([call('subagent_list', {}, 'list'), AIMessage(content='found')])
        result = await app.host.runs.start(sid, [('user', 'find earlier worker')])
        feedback = next(m for m in reversed(result['messages']) if isinstance(m, ToolMessage))
        assert feedback.status == 'success'
        row, = json.loads(feedback.content)['items']
        assert row['agent_id'] == identity and row['resume_available'] is True and row['resume_reason'] is None
        assert set(root.bound[-1]) == {'subagent', 'subagent_list'}
        assert app.host.sessions._persistence.storage.sessions.get(sid)['active_tool_names_json'] == saved_selection and len(child.seen) == 1
        await app.host.sessions.select_tools(sid, [])
        root.replies.extend([call('subagent_list', {}, 'denied'), AIMessage(content='disabled')])
        result = await app.host.runs.start(sid, [('user', 'disabled')])
        denied = next(m for m in reversed(result['messages']) if isinstance(m, ToolMessage))
        assert denied.status == 'error' and denied.additional_kwargs['naiyue_not_executed'] == 'tool_not_active'
        assert result['tool_execution']['tool_names'] == [] and len(child.seen) == 1
        with pytest.raises(ValueError, match='subagent'):
            await app.host.sessions.select_tools(sid, ['subagent_list'])


@async_test
async def test_list_configuration_paging_foreign_cursor_and_complete_json_budget(monkeypatch, tmp_path):
    root, child = ChildModel(replies=[]), ChildModel(replies=[AIMessage(content='one'), AIMessage(content='two')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': root, 'B': child}))
    config = options(tmp_path)
    config['tools'].append(subagent.create_subagent_list_tool())
    config['settings'] = config['settings'].model_copy(update={'tools': config['settings'].tools.model_copy(update={
        'subagent': SubAgentOutputSettings(max_list_items=1, max_list_bytes=500, task_preview_chars=3)})})
    async with open_component_application('unused', **config) as app:
        sid = await app.host.sessions.create('A')
        first = json.loads((await delegate(app, sid, root, task='第一份任务')).content)['agent_id']
        second = json.loads((await delegate(app, sid, root, task='第二份任务')).content)['agent_id']
        async def query(args):
            root.replies.extend([call('subagent_list', args, 'list'), AIMessage(content='listed')])
            result = await app.host.runs.start(sid, [('user', 'list')])
            return next(m for m in reversed(result['messages']) if isinstance(m, ToolMessage))
        page = await query({})
        assert len(page.content.encode()) <= 500
        assert json.loads(page.content) == {'items': [dict(agent_id=second, agent='worker', task='第二份',
            status='completed', resume_available=True, resume_reason=None)], 'next_before': second, 'truncated': True}
        last = json.loads((await query({'before': second})).content)
        assert last['items'][0]['agent_id'] == first and last['next_before'] is None and not last['truncated']
        for args in ({'limit': 2}, {'before': 'f' * 64}):
            assert (await query(args)).status == 'error'
        other = await app.host.sessions.create('A')
        root.replies.extend([call('subagent_list', {'before': first}, 'foreign'), AIMessage(content='denied')])
        denied = await app.host.runs.start(other, [('user', 'foreign cursor')])
        assert next(m for m in reversed(denied['messages']) if isinstance(m, ToolMessage)).status == 'error'
        assert len(child.seen) == 2


@pytest.mark.parametrize('budget', [1, 80, 260, 360, 500])
def test_complete_json_budget_never_produces_an_empty_page_loop(budget):
    from langchain_core.tools import ToolException
    from naiyue_test_agent.application.subagents.subagent_queries import bounded_list
    records = [dict(agent_id=char * 64, agent='worker', task='中文😀' * 100, status='completed',
        resume_available=True, resume_reason=None) for char in ('a', 'b')]
    owned = {r['agent_id']: {'created_at': str(i)} for i, r in enumerate(records)}
    settings = SubAgentOutputSettings(max_list_bytes=budget, task_preview_chars=1000)
    # 260 bytes cannot hold the fixed item plus the full 64-character next cursor.
    if budget <= 260:
        with pytest.raises(ToolException, match='budget'):
            bounded_list(records, owned, settings)
    else:
        first = bounded_list(records, owned, settings)
        assert len(first.encode()) <= budget
        page = json.loads(first)
        assert page['items'] and page['items'][0]['agent_id'] == 'b' * 64
        assert page['next_before'] == 'b' * 64 and page['truncated']
        last = json.loads(bounded_list(records, owned, settings, before=page['next_before']))
        assert last['items'][0]['agent_id'] == 'a' * 64 and last['next_before'] is None


@pytest.mark.parametrize('state,reason', [('ready', None), ('pending_parent', 'pending_parent'), ('running', 'running'),
    ('retired', 'retired'), ('failed', 'failed'), ('stopped', 'stopped')])
def test_candidate_status_is_metadata_only_and_does_not_claim_execution_admission(state, reason):
    from naiyue_test_agent.application.subagents.subagent_queries import annotate
    from naiyue_test_agent.application.subagents.subagent_records import role_fingerprint
    from test_subagent_execution import role
    spec = role()
    row = dict(agent_id='a' * 64, agent='tester', task='work', status='completed')
    record = dict(state=state, last_parent_run_id='old', role_fingerprint=role_fingerprint(spec), model_id='B')
    catalog = ModelCatalog('B', {'B': ChildModel(replies=[])})
    actual, = annotate([row], {row['agent_id']: record}, [spec], catalog, 'new')
    assert actual['resume_reason'] == reason and actual['resume_available'] is (reason is None)
    assert annotate([row], {}, [spec], catalog)[0]['resume_reason'] == 'legacy_instance'
    if state == 'ready':
        assert annotate([row], {row['agent_id']: record}, [], catalog)[0]['resume_reason'] == 'role_unavailable'
        record['role_fingerprint'] = 'changed'
        assert annotate([row], {row['agent_id']: record}, [spec], catalog)[0]['resume_reason'] == 'role_changed'


def test_list_projection_is_root_and_publisher_scope_only():
    from naiyue_test_agent.agent_runtime.events import RunEventPublisher
    from naiyue_test_agent.agent_runtime.invocation_context import make_invocation_context
    from naiyue_test_agent.agent_runtime.run_scope import bind_run_scope
    query = lambda **_: None
    with bind_run_scope(typed_scope(publisher=RunEventPublisher('s', 'r', ()), list_subagents=query)):
        assert make_invocation_context('s', 'r', None, None, ()).scope.capabilities.list_subagents is query
        for sid, run, path in [('other', 'r', 'root'), ('s', 'old', 'root'), ('s', 'r', 'root/tester/a')]:
            with pytest.raises(ValueError, match='identity'):
                make_invocation_context(sid, run, None, None, (), agent_path=path)


def test_list_limits_load_from_example_and_custom_settings(tmp_path):
    from pathlib import Path
    from naiyue_test_agent.settings import load_settings
    actual = load_settings(Path(__file__).parents[1] / 'docs/examples/settings.example.toml').tools.subagent
    assert (actual.max_list_items, actual.max_list_bytes, actual.task_preview_chars) == (20, 8192, 160)
    path = tmp_path / 'settings.toml'
    path.write_text('[tools.subagent]\nmax_list_items=2\nmax_list_bytes=400\ntask_preview_chars=9\n')
    actual = load_settings(path).tools.subagent
    assert (actual.max_list_items, actual.max_list_bytes, actual.task_preview_chars) == (2, 400, 9)


@async_test
async def test_context_preview_counts_the_same_companion_definition_as_the_actual_request(monkeypatch, tmp_path):
    root, child = ChildModel(replies=[AIMessage(content='done')]), ChildModel(replies=[])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': root, 'B': child}))
    config = options(tmp_path)
    config['tools'].append(subagent.create_subagent_list_tool())
    async with open_component_application('unused', **config) as app:
        sid = await app.host.sessions.create('A')
        preview = await app.host.history.context(sid)
        assert next(row['count'] for row in preview['current']['estimate']['groups'] if row['id'] == 'tool_definitions') == 2
        await app.host.runs.start(sid, [('user', 'hello')])
        assert set(root.bound[-1]) == {'subagent', 'subagent_list'}


@async_test
async def test_stale_child_execution_stop_cannot_stop_the_next_delegation_in_the_same_parent(monkeypatch):
    from naiyue_test_agent.application.errors import SessionBusyError
    from test_subagent_execution import delegated_app, record_instances
    entered, release = asyncio.Event(), asyncio.Event()
    root = ChildModel(replies=[call('subagent', {'agent': 'tester', 'task': 'first'}, 'same'),
        AIMessage(content='placeholder'), AIMessage(content='root done')])
    child = ChildModel(replies=[AIMessage(content='first report'), AIMessage(content='second report')])
    async def next_delegation(messages):
        if isinstance(messages[-1], ToolMessage):
            identity = json.loads(messages[-1].content)['agent_id']
            root.replies[0] = call('subagent', {'agent': 'tester', 'task': 'second', 'agent_id': identity}, 'same')
    async def hold_second(messages):
        if len(child.seen) == 1:
            entered.set()
            await release.wait()
    root.on_request, child.on_request = next_delegation, hold_second
    app, _ = await delegated_app(monkeypatch, child, root=root)
    instances = record_instances(monkeypatch, app)
    sid = await app.host.sessions.create('A')
    task = asyncio.create_task(app.host.runs.start(sid, [('user', 'two steps')]))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        old, current = instances
        for binding in ({'agent_run_id': old['run_id']}, {'approval_batch_id': 'old-approval'}):
            with pytest.raises(SessionBusyError, match='different child'):
                app.host.runs.stop(sid, run_id=current['parent_run_id'], agent_path=current['agent_path'], **binding)
        assert not current['control'].requested
        root.on_request = None
        release.set()
        assert (await asyncio.wait_for(task, 5))['messages'][-1].content == 'root done'
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
