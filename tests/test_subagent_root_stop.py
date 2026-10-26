"""Stopping a paused root batch uses control resume, never fabricated rejection."""

import pytest
from langchain_core.messages import ToolMessage
from native_application_helpers import async_test, build_application
from test_async_application import StreamingToolModel, call
from test_dynamic_tools import definition
from naiyue_test_agent import application
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.agent_runtime.run_control import RunControl, stop_tool_message


@pytest.mark.parametrize('streaming', [False, True])
@async_test
async def test_stop_saved_root_batch_pairs_unexecuted_calls_without_model_or_grant(monkeypatch, streaming):
    effects, events = [], []
    model = StreamingToolModel(replies=[call('probe', {'value': 'never'}, 'one')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    app = await build_application('unused', tools=[definition(calls=effects, approved=True)], system_prompt='', response_format=None)
    app.host.observability.subscribe(events.append)
    sid = await app.host.sessions.create('A')
    await app.host.runs.start(sid, [('user', 'ask')])
    batch, = app.host.runs.pending_approvals(sid)
    kwargs = dict(approval_batch_id=batch['approval_batch_id'], origin_run_id=batch['origin_run_id'])
    with pytest.raises(ValueError):
        await app.host.runs.stop_approval(sid, **{**kwargs, 'approval_batch_id': 'stale'})
    assert app.host.runs.pending_approvals(sid) == [batch]
    if streaming:
        received = [event async for event in app.host.runs.stream_stop_approval(sid, **kwargs)]
        assert received[-1].event_type == 'run_finished' and received[-1].data.status == 'stopped'
    else:
        result = await app.host.runs.stop_approval(sid, **kwargs)
        assert result['run_stopped'] is True
    messages = await app.host.history.messages(sid)
    feedback, = [message for message in messages if isinstance(message, ToolMessage)]
    assert feedback.tool_call_id == 'one' and feedback.status == 'error'
    assert feedback.additional_kwargs['naiyue_not_executed'] == 'user_stop'
    assert 'stopped' in feedback.content and 'reject' not in feedback.content.lower()
    assert len(model.seen) == 1 and effects == [] and app.host.runs.pending_approvals(sid) == []
    terminal = [event for event in events if event.event_type == 'run_finished']
    assert [event.data.status for event in terminal] == ['interrupted', 'stopped']
    assert terminal[0].run_id != terminal[1].run_id
    assert not any(event.event_type == 'control_applied' and event.data.kind == 'approval' for event in events)
    with pytest.raises(ValueError): await app.host.runs.stop_approval(sid, **kwargs)


@pytest.mark.parametrize('reason, expected', [('user', 'user stopped'), ('failure', 'owning run failed'), ('close', 'application is closing')])
def test_stop_reason_is_trusted_first_source_and_fixed_feedback(reason, expected):
    control = RunControl()
    control.request(reason)
    control.request('user')
    assert control.reason == reason
    message = stop_tool_message({'id': 'one', 'name': 'probe'}, reason=control.reason)
    assert expected in message.content
    if reason != 'user': assert 'user stopped' not in message.content
    with pytest.raises(ValueError): control.request('MODEL_FREE_TEXT')
