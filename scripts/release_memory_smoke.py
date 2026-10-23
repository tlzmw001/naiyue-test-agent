"""Real memory acceptance shared by source tests and the installed product gate."""

from contextlib import asynccontextmanager
import json
import re
from unittest.mock import patch


PREFERENCE = 'Use .venv for tests from now on.'


def verify_memory_report(report):
    """Reject a green installation report that omitted the actual memory chain."""
    try:
        flags = ('status', 'overview', 'memory_saved', 'task_queries', 'first_turn_index',
                 'independent_usage', 'history_reopen')
        valid = all(report[key] == 'passed' for key in flags)
        counts = dict(root_model_calls=3, worker_model_calls=1, memory_records=1,
            overview_version=1, memory_input_tokens=31, cold_model_calls=0, cold_tasks=0, provider_requests=0)
        valid &= all(type(report[key]) is int and report[key] == value for key, value in counts.items())
        valid &= isinstance(report['task_id'], str) and re.fullmatch('[0-9a-f]{32}', report['task_id']) is not None
        roots = report['root_run_ids']
        valid &= isinstance(roots, list) and len(roots) == len(set(roots)) == 2
        valid &= all(isinstance(value, str) and value for value in roots)
        valid &= report['task_id'] not in roots and report['origin_run_id'] in roots
        if not valid:
            raise ValueError('Memory acceptance facts are missing or inconsistent')
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError('Memory acceptance facts are missing or inconsistent') from error


async def memory_scenario(root, Model):
    """Only pass a disposable directory; use no environment or provider settings."""
    from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
    from naiyue_test_agent import application
    from naiyue_test_agent.application.async_persistent import open_async_persistent_application
    from naiyue_test_agent.application.memory.memory_overview_store import overview
    from naiyue_test_agent.model_access import ModelCatalog
    from naiyue_test_agent.settings import AppSettings
    from naiyue_test_agent.tools.memory import create_memory_tool, MemoryInput
    from naiyue_test_agent.workspaces import prepare_workspace

    directory = root.resolve(strict=True) / 'memory-acceptance'
    directory.mkdir()
    workspace = directory / 'work'
    workspace.mkdir()
    database = directory / 'state.sqlite'
    settings = AppSettings.model_validate({'storage': {'data_dir': str(directory / 'data')},
        'memory': {'auto_enabled': False, 'model_id': 'memory'}})
    location = prepare_workspace(workspace, data_dir=directory / 'data', database_path=database)
    generated = dict(overview=dict(goal='Installed memory acceptance', progress='Saved preference',
        blockers='', next_step='Use the saved test runner'), proposals=[dict(scope='workspace',
        kind='user', title='Test runner', body=PREFERENCE, subject='test runner',
            basis='user_declaration', intent='activate', reason='Explicit persistent user preference',
        sources=[dict(source_key='1:0', quote=PREFERENCE)])])
    parent = Model(replies=[AIMessage(content='', tool_calls=[dict(name='memory',
        args=dict(action='refresh_overview'), id='installed-refresh')]),
        AIMessage(content='Request accepted'), AIMessage(content='Preference recalled')])
    worker = Model(replies=[AIMessage(content=json.dumps(generated),
        usage_metadata=dict(input_tokens=31, output_tokens=13, total_tokens=44))])

    @asynccontextmanager
    async def opened():
        with patch.object(application, 'load_model_catalog',
                lambda _: ModelCatalog('root', {'root': parent, 'memory': worker})):
            async with open_async_persistent_application('unused', database_path=database,
                    tools=[create_memory_tool()], system_prompt='', response_format=None, settings=settings,
                    workspace_binding={'workspace_id': location.workspace_id, 'root_path': workspace}) as app:
                yield app

    async with opened() as app:
        events = []
        app.observability.subscribe(events.append)
        sid = await app.sessions.create_workspace('root', workspace)
        result = await app.runs.start(sid, [('user', PREFERENCE)])
        assert result['messages'][-1].content == 'Request accepted'
        receipt, = [json.loads(message.content) for message in result['messages']
            if isinstance(message, ToolMessage) and message.tool_call_id == 'installed-refresh']
        assert receipt['status'] == 'accepted'
        task_id = receipt['task_id']
        origin = app.sessions._persistence.storage.sessions.get(sid)['run_id']
        assert app.sessions._persistence.storage.sessions.get(sid)['state'] == 'ready'
        await app.sessions._sessions.memory_jobs.wait_idle()
        task = await app.tasks.output(task_id, session_id=sid, workspace_root=workspace)
        assert task['result_status'] == 'completed' and task['cleanup_confirmed'] and task['kind'] == 'memory'
        assert json.loads(task['output'])['memory_saved'] == 1
        listing = await app.tasks.list(session_id=sid, workspace_root=workspace)
        assert listing['active_count'] == 0 and listing['items'][0]['task_id'] == task_id
        saved = app.sessions._persistence.storage.memory.overview(sid)
        assert saved['overview_status'] == 'generated' and saved['overview'] == generated['overview']
        request = MemoryInput(action='list', scope='workspace').model_dump(mode='json')
        memories = app.sessions._persistence._memory.query(sid, app.sessions._persistence.storage.sessions.get(sid)['project_id'], request)['items']
        assert len(memories) == 1 and memories[0]['kind'] == 'user' and memories[0]['body'] == PREFERENCE
        usage = await app.observability.usage(sid, model_limit=20)
        memory_usage, = [row for row in usage['activities'] if row['id'] == 'memory']
        assert memory_usage['recorded_calls'] == 1
        assert memory_usage['metrics']['input_tokens'] == dict(known_sum='31', reported_calls=1, unknown_calls=0)
        next_sid = await app.sessions.create_workspace('root', workspace)
        await app.runs.start(next_sid, [('user', 'continue')])
        system, = [message.content for message in parent.seen[-1] if isinstance(message, SystemMessage)]
        assert PREFERENCE in system and 'Saved memory index' in system
        assert 'Other session directory' in system and sid in system and generated['overview']['goal'] in system
        root_ids = sorted({event.run_id for event in events})
        assert task_id not in root_ids and len(root_ids) == 2 and origin in root_ids
        assert len(parent.seen) == 3 and len(worker.seen) == 1
        assert not parent.replies and not worker.replies
        root_calls, worker_calls = len(parent.seen), len(worker.seen)
        memory_tokens = int(memory_usage['metrics']['input_tokens']['known_sum'])
        contents = [message.content for message in await app.history.messages(sid)]
        overview_version = saved['overview_version']

    parent, worker = Model(replies=[]), Model(replies=[])
    async with opened() as app:
        history = await app.history.session(sid)
        assert [message.content for message in history['messages']] == contents
        assert app.sessions._persistence.storage.memory.overview(sid)['overview_version'] == overview_version
        diagnostics = await app.observability.events(sid, task_id)
        assert diagnostics['summary']['mode'] == 'memory' and diagnostics['summary']['origin_run_id'] == origin
        kinds = [event['event_type'] for event in diagnostics['events']]
        assert kinds.count('model_call_started') == kinds.count('model_call_finished') == 1
        assert not parent.seen and not worker.seen and not app.sessions._sessions.memory_jobs._entries
    report = {key: 'passed' for key in ('status', 'overview', 'memory_saved', 'task_queries',
        'first_turn_index', 'independent_usage', 'history_reopen')}
    report.update(task_id=task_id, origin_run_id=origin, root_run_ids=root_ids, root_model_calls=root_calls,
        worker_model_calls=worker_calls, memory_records=len(memories), overview_version=overview_version,
        memory_input_tokens=memory_tokens, cold_model_calls=len(parent.seen) + len(worker.seen),
        cold_tasks=len(app.sessions._sessions.memory_jobs._entries), provider_requests=0)
    verify_memory_report(report)
    return report
