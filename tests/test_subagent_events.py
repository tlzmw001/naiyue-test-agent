"""Child source views share root ordering but never finish root projections."""

from dataclasses import asdict

from naiyue_test_agent.agent_runtime.events import AgentEventSource, RunEventPublisher
from naiyue_test_agent.contracts.session_events import RunStartedData, RunFinishedData
from naiyue_test_agent.application.conversation.stream_projection import StreamingMessageProjection


def test_live_projection_rebinds_repeated_call_id_and_ignores_previous_delegation_terminal():
    from naiyue_test_agent.web.agent_projection import observe_agent
    agents = {}
    parents = [{'id': name, 'type': 'ai', 'tool_calls': [{'id': 'same'}]} for name in ('first', 'second')]
    source = dict(agent_path='root/tester/A', agent_id='A', agent_run_id='old', parent_run_id='root', parent_tool_call_id='same')
    def send(run, kind, data, parent_messages):
        observe_agent(agents, dict(source={**source, 'agent_run_id': run}, event_type=kind, data=data, timestamp='now'), parent_messages)
    send('old', 'run_started', {'mode': 'message'}, parents[:1])
    send('old', 'message_delta', dict(message_id='old-draft', text='old', index=1), parents[:1])
    send('new', 'run_started', {'mode': 'message'}, parents)
    row = agents[source['agent_path']]
    assert row['parent_batch_id'] == 'second' and row['messages'] == [] and row['tool_states'] == {}
    send('new', 'message_delta', dict(message_id='new-draft', text='new', index=1), parents)
    send('resume', 'run_started', {'mode': 'approval_resume'}, parents)
    assert row['messages'][0]['content'] == 'new'
    send('old', 'run_finished', {'status': 'failed'}, parents[:1])
    assert row['source']['agent_run_id'] == 'resume' and row['status'] == 'running'


def test_source_views_share_sequence_and_keep_child_run_identity():
    events = []
    root = RunEventPublisher('session', 'root-run', (events.append,))
    source = AgentEventSource('root/tester/A', 'A', 'child-run', 'root-run', 'outer-A')
    child = root.for_agent(source)
    root.publish('run_started', RunStartedData('message'))
    child.publish('run_started', RunStartedData('message'))
    child.publish('run_finished', RunFinishedData('completed'))
    root.publish('run_finished', RunFinishedData('completed'))
    assert [event.sequence for event in events] == [1, 2, 3, 4]
    assert all(event.run_id == 'root-run' for event in events)
    assert events[0].source is None and asdict(events[1].source) == asdict(source)
    assert child.run_id == 'child-run' and child.matches('session', 'child-run')
    assert not child.matches('session', 'root-run')
    projection = StreamingMessageProjection()
    for event in events[:3]:
        projection(event)
    assert projection.run_status == {} and not projection.incomplete
    projection(events[-1])
    assert projection.run_status == {'root-run': 'completed'}


from native_application_helpers import async_test
from test_subagent_execution import ChildModel, delegated_app, record_instances
from test_async_application import call
from test_dynamic_tools import definition
from langchain_core.messages import AIMessage
import asyncio


@async_test
async def test_child_events_bind_saved_approval_to_one_root_stream_and_stop_wakes_it(monkeypatch):
    effects, events = [], []
    child = ChildModel(replies=[call('probe', {'value': 'protected'}, 'inner'), AIMessage(content='unused')])
    app, root = await delegated_app(monkeypatch, child, tools=[definition(calls=effects, approved=True)])
    instances = record_instances(monkeypatch, app)
    requested = asyncio.Event()
    def receive(event):
        events.append(event)
        if event.event_type == 'approval_requested' and event.source:
            assert app.runs.pending_approvals(event.session_id)[0]['approval_batch_id'] == event.data.approval_batch_id
            requested.set()
    app.observability.subscribe(receive)
    sid = await app.sessions.create('A')
    task = asyncio.create_task(app.runs.start(sid, [('user', 'start')]))
    try:
        await asyncio.wait_for(requested.wait(), 5)
        batch, = app.runs.pending_approvals(sid)
        assert batch['kind'] == 'live_child' and not task.done() and effects == []
        assert [event.sequence for event in events] == list(range(1, len(events) + 1))
        assert len({event.run_id for event in events}) == 1
        assert any(event.source and event.event_type == 'run_finished' and event.data.status == 'interrupted' for event in events)
        assert not any(not event.source and event.event_type == 'run_finished' for event in events)
        app.runs.stop(sid, run_id=batch['origin_run_id'])
        result = await asyncio.wait_for(task, 5)
        assert result['run_stopped'] and effects == [] and len(child.seen) == 1 and len(root.seen) == 1
        assert app.runs.pending_approvals(sid) == []
        assert events[-1].event_type == 'run_finished' and events[-1].source is None
        assert all(service.observation.publisher.source.agent_path == instances[0]['agent_path'] for service in child.services)
    finally:
        if not task.done(): task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@async_test
async def test_invalid_final_report_publishes_failed_child_instead_of_completed(monkeypatch):
    child = ChildModel(replies=[AIMessage(content='')])
    app, _ = await delegated_app(monkeypatch, child)
    events = []
    app.observability.subscribe(events.append)
    result = await app.runs.start(await app.sessions.create('A'), [('user', 'start')])
    assert next(message for message in result['messages'] if message.type == 'tool').status == 'error'
    terminal = [event.data.status for event in events if event.source and event.event_type == 'run_finished']
    assert terminal == ['failed']


@async_test
async def test_failed_notification_does_not_remove_saved_approval_or_execute_tools(monkeypatch, caplog):
    from test_subagent_approval_resume import approval_notifications, next_approval, submit, cancel_run
    effects = []
    child = ChildModel(replies=[call('probe', {'value': 'once'}, 'inner'), AIMessage(content='done')])
    app, _ = await delegated_app(monkeypatch, child, tools=[definition(calls=effects, approved=True)])
    queue = approval_notifications(monkeypatch, app)
    seen = []
    def fail(event):
        if event.source and event.event_type == 'approval_requested':
            seen.append(event.data.approval_batch_id)
            raise RuntimeError('PRIVATE_OBSERVER_BODY')
    app.observability.subscribe(fail)
    sid = await app.sessions.create('A')
    task = asyncio.create_task(app.runs.start(sid, [('user', 'start')]))
    try:
        batch = await next_approval(app, sid, queue)
        assert seen == [batch['approval_batch_id']] and effects == []
        assert app.runs.pending_approvals(sid) == [batch] and not task.done()
        await submit(app, sid, batch)
        await asyncio.wait_for(task, 5)
        assert effects == [('probe', 'once')]
        assert 'event_observer_failed' in caplog.text and 'PRIVATE_OBSERVER_BODY' not in caplog.text
    finally:
        await cancel_run(task)
