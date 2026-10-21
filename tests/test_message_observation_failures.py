"""Observability failures preserve business results, exceptions, and control."""

from naiyue_test_agent.agent_runtime.async_message_observation import ainvoke_with_message_observation

from native_application_helpers import async_test

from naiyue_test_agent.tools.permissions import ToolPermission

from naiyue_test_agent.tools import ToolSpec
from naiyue_test_agent.application import prepare_tool_registry

import json
import pytest
from tool_feedback_helpers import fail_tool_dispatch
from langchain.tools import tool
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.errors import GraphBubbleUp

from naiyue_test_agent.agent_runtime import build_runtime_agent
from naiyue_test_agent.agent_runtime.events import RunEventPublisher, RunStartedData
from test_application_message_events import messages, rows
from test_application_run_events import ToolModel, make_app


def publisher():
    events = []
    pub = RunEventPublisher('session', 'run', (events.append,))
    pub.publish('run_started', RunStartedData('message'))
    return pub, events


@async_test
async def test_old_invoke_object_keeps_exact_arguments_and_original_result():
    returned = {'original': object()}
    calls = []

    class OldAgent:
        async def ainvoke(self, input, **kwargs):
            calls.append((input, kwargs))
            return returned

    pub, events = publisher()
    result = (await ainvoke_with_message_observation(OldAgent(), 'input', context={'x': 1},
                                            config={'y': 2}, publisher=pub))
    assert result is returned and calls == [('input', {'context': {'x': 1}, 'config': {'y': 2}})]
    assert events[-1].data.reason == 'unsupported_state_interface'
    assert messages(events) == []


@async_test
async def test_baseline_read_failure_falls_back_once_without_inventing_history(monkeypatch):
    model = ToolModel(responses=[AIMessage(content='done')])
    app = await make_app(monkeypatch, model)
    graph = app._agent
    original_stream = graph.astream
    streams = []

    def stream(*args, **kwargs):
        streams.append(kwargs.get('version', 'v1'))
        return original_stream(*args, **kwargs)

    async def bad_state(*args, **kwargs):
        raise OSError('PRIVATE_BASELINE')

    monkeypatch.setattr(graph, 'astream', stream)
    monkeypatch.setattr(graph, 'aget_state', bad_state)
    events = []
    app.subscribe_events(events.append)
    assert (await app.arun(await app.acreate_session('A'), [('user', 'run')]))['messages'][-1].content == 'done'
    assert streams == ['v1'] and messages(events) == []
    unavailable = next(e for e in events if e.event_type == 'message_snapshot_unavailable')
    assert unavailable.data.reason == 'baseline_read_failed' and unavailable.data.error_type == 'OSError'
    assert 'PRIVATE_BASELINE' not in repr(unavailable)


@pytest.mark.parametrize('error', [TimeoutError('PRIVATE_ERROR'), GraphBubbleUp(), KeyboardInterrupt()])
@async_test
async def test_model_failure_or_control_keeps_original_and_only_observed_input(monkeypatch, error):
    class Failing(ToolModel):
        async def _agenerate(self, *args, **kwargs):
            raise error

    app = await make_app(monkeypatch, Failing(responses=[AIMessage(content='unused')]))
    events = []
    app.subscribe_events(events.append)
    sid = await app.acreate_session('A')
    with pytest.raises(type(error)) as caught:
        if isinstance(error, KeyboardInterrupt):
            # Inject at the stream boundary: asyncio gives KeyboardInterrupt
            # special process-level handling when raised by an internal model Task.
            original = app._agent.astream
            def fail_stream(*args, **kwargs):
                iterator = original(*args, **kwargs)
                async def parts():
                    try:
                        async for part in iterator:
                            yield part
                            if part['type'] == 'values' and part['data'].get('messages'):
                                raise error
                    finally:
                        await iterator.aclose()
                return parts()
            monkeypatch.setattr(app._agent, 'astream', fail_stream)
            pub = RunEventPublisher(sid, 'control', (events.append,))
            (await ainvoke_with_message_observation(app._agent, {'messages': [('user', 'input')]},
                context={'session_id': sid, 'run_id': 'control', '_run_event_publisher': pub},
                config={'configurable': {'thread_id': app._sessions.require(sid).get_thread_id()}}, publisher=pub))
        else:
            await app.arun(sid, [('user', 'input')])
    assert caught.value is error
    assert [row['type'] for row in rows(events)] == ['human']
    ends = [e for e in events if e.event_type == 'run_finished']
    assert len(ends) == (1 if type(error) is TimeoutError else 0)
    if ends:
        assert ends[0].data.status == 'failed'


@async_test
async def test_fatal_batch_pending_error_is_recovery_view_not_success(monkeypatch):
    failure = RuntimeError('PRIVATE_FATAL')
    entered = []

    @tool
    def fatal() -> str:
        """Fail a sequential batch."""
        return 'unreachable after the dispatch fault'


    @tool
    def skipped() -> str:
        """Must not execute after the earlier failure."""
        entered.append(True)
        return 'unexpected'

    fail_tool_dispatch(monkeypatch, 'fatal', failure)
    app = await make_app(monkeypatch, ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'fatal', 'args': {}, 'id': 'fatal'},
        {'name': 'skipped', 'args': {}, 'id': 'skip'},
    ])]), [ToolSpec(fatal, fatal.args_schema, execution_mode='sequential', permissions=ToolPermission(default='allow')), ToolSpec(skipped, skipped.args_schema, permissions=ToolPermission(default='allow'))])
    events = []
    app.subscribe_events(events.append)
    with pytest.raises(RuntimeError) as caught:
        await app.arun(await app.acreate_session('A'), [('user', 'run')])
    assert caught.value is failure and entered == []
    pending = [e.data for e in messages(events) if e.data.message_type == 'tool']
    assert len(pending) == 1
    assert pending[0].visibility == 'recovery_view' and pending[0].tool_call_id == 'skip'
    assert pending[0].status == 'error'
    assert json.loads(pending[0].snapshot_json)['content'] == (
        'Tool was not executed because an earlier call in this batch failed.'
    )
    assert events[-1].data.status == 'failed'


@async_test
async def test_failed_recovery_read_cannot_replace_original_error(monkeypatch, caplog):
    failure = RuntimeError('PRIVATE_PRIMARY')

    class Failing(ToolModel):
        def _generate(self, *args, **kwargs):
            raise failure

    app = await make_app(monkeypatch, Failing(responses=[AIMessage(content='unused')]))
    original = app._agent.aget_state
    reads = []

    async def get_state(*args, **kwargs):
        reads.append(True)
        if len(reads) > 1:
            raise OSError('PRIVATE_SECONDARY')
        return await original(*args, **kwargs)

    monkeypatch.setattr(app._agent, 'aget_state', get_state)
    events = []
    app.subscribe_events(events.append)
    with pytest.raises(RuntimeError) as caught:
        await app.arun(await app.acreate_session('A'), [('user', 'run')])
    assert caught.value is failure
    unavailable = next(e for e in events if e.event_type == 'message_snapshot_unavailable')
    assert unavailable.data.reason == 'recovery_read_failed' and unavailable.data.error_type == 'OSError'
    assert 'PRIVATE_' not in caplog.text


@async_test
async def test_non_json_content_without_saver_is_reported_and_result_unchanged():
    bad = HumanMessage(id='bad', content=[{'type': 'custom', 'data': object()}])
    model = ToolModel(responses=[AIMessage(content='done')])
    _runtime_registry_1 = prepare_tool_registry([])
    _runtime_names_1 = _runtime_registry_1.names()
    graph = build_runtime_agent(model, tool_registry=_runtime_registry_1, resolve_active_tool_names=lambda _sid, agent_path='root', names=_runtime_names_1: names, system_prompt='', response_format=None,
                                resolve_model=lambda _s, agent_path='root': ('A', model), )
    pub, events = publisher()
    result = (await ainvoke_with_message_observation(graph, {'messages': [bad]},
                                            context={'session_id': 'session', 'run_id': 'run'},
                                            config={}, publisher=pub))
    assert result['messages'][0] is bad
    assert result['messages'][-1].content == 'done'
    assert [row['content'] for row in rows(events)] == ['done']
    unavailable = [e for e in events if e.event_type == 'message_snapshot_unavailable']
    assert len(unavailable) == 1 and unavailable[0].data.reason == 'unsupported_projection'


@async_test
async def test_iterator_close_secondary_failure_preserves_primary_and_attempts_close(monkeypatch, caplog):
    model = ToolModel(responses=[AIMessage(content='unused')])
    _runtime_registry_2 = prepare_tool_registry([])
    _runtime_names_2 = _runtime_registry_2.names()
    graph = build_runtime_agent(model, tool_registry=_runtime_registry_2, resolve_active_tool_names=lambda _sid, agent_path='root', names=_runtime_names_2: names, system_prompt='', response_format=None,
                                resolve_model=lambda _s, agent_path='root': ('A', model), )
    primary = RuntimeError('PRIVATE_PRIMARY')
    closed = []

    class BrokenIterator:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise primary

        async def aclose(self):
            closed.append(True)
            raise OSError('PRIVATE_CLOSE')

    monkeypatch.setattr(graph, 'astream', lambda *_a, **_kw: BrokenIterator())
    pub, events = publisher()
    with pytest.raises(RuntimeError) as caught:
        (await ainvoke_with_message_observation(graph, {}, context={}, config={}, publisher=pub))
    assert caught.value is primary and closed == [True]
    assert messages(events) == [] and 'PRIVATE_' not in caplog.text


@async_test
async def test_custom_invoke_on_compiled_graph_is_not_bypassed(monkeypatch):
    app = await make_app(monkeypatch)
    original_result = {'messages': [], 'custom': object()}
    invoked = []

    async def custom(input, **kwargs):
        invoked.append(input)
        return original_result

    monkeypatch.setattr(app._agent, 'ainvoke', custom)
    events = []
    app.subscribe_events(events.append)
    result = await app.arun(await app.acreate_session('A'), [('user', 'run')])
    assert result is original_result and len(invoked) == 1
    assert messages(events) == []
    assert next(e.data.reason for e in events if e.event_type == 'message_snapshot_unavailable') == (
        'unsupported_state_interface'
    )


@async_test
async def test_message_consumer_failure_is_isolated_from_other_consumers_and_result(monkeypatch, caplog):
    app = await make_app(monkeypatch)
    events, calls = [], []

    def broken(event):
        calls.append(event.event_type)
        if event.event_type == 'message_completed':
            raise ValueError('PRIVATE_BODY_REJECTED')

    app.subscribe_events(broken)
    app.subscribe_events(events.append)
    result = await app.arun(await app.acreate_session('A'), [('user', 'run')])
    assert result['messages'][-1].content == 'PRIVATE_REPLY'
    assert calls == ['run_started', 'message_completed']
    assert [row['type'] for row in rows(events)] == ['human', 'ai']
    assert events[-1].data.status == 'completed'
    assert 'event_observer_failed' in caplog.text and 'PRIVATE_' not in caplog.text


@async_test
async def test_state_adapter_result_equals_native_invoke_with_all_output_fields():
    model = ToolModel(responses=[AIMessage(id='answer', content='done')])
    _runtime_registry_3 = prepare_tool_registry([])
    _runtime_names_3 = _runtime_registry_3.names()
    graph = build_runtime_agent(model, tool_registry=_runtime_registry_3, resolve_active_tool_names=lambda _sid, agent_path='root', names=_runtime_names_3: names, system_prompt='', response_format=None,
                                resolve_model=lambda _s, agent_path='root': ('A', model), )
    input = {'messages': [HumanMessage(id='input', content='run')]}
    context = {'session_id': 'session', 'run_id': 'run'}
    original = (await graph.ainvoke(input, context=context, config={}))
    pub, events = publisher()
    observed = (await ainvoke_with_message_observation(graph, input, context=context, config={}, publisher=pub))
    assert observed == original
    assert [e.data.message_id for e in messages(events)] == ['input', 'answer']


@pytest.mark.parametrize('with_control', [False, True])
def test_real_probe_guard_counts_model_requests_not_preflight_resolution(monkeypatch, capsys, caplog, with_control):
    from probe_budget_helpers import guard_fake_catalog
    monkeypatch.setattr('scripts.provider_acceptance_budget.guard_catalog', guard_fake_catalog)
    import logging
    from types import SimpleNamespace
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_core.messages import ToolMessage
    from scripts.probe_message_events import _worker
    from naiyue_test_agent import application
    from naiyue_test_agent.model_access import ModelCatalog

    model_calls = []

    class ProbeModel(ToolModel):
        def _generate(self, messages, *args, **kwargs):
            model_calls.append(True)
            results = [m for m in messages if isinstance(m, ToolMessage)]
            response = (AIMessage(content=results[-1].content) if results else AIMessage(
                content='', tool_calls=[{'name': 'read_probe_value', 'args': {}, 'id': 'probe'}],
            ))
            return ChatResult(generations=[ChatGeneration(message=response)])

    model = ProbeModel(responses=[AIMessage(content='unused')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _p: ModelCatalog('A', {'A': model}))
    monkeypatch.setenv('LANGSMITH_TRACING', 'false')
    monkeypatch.setenv('LANGCHAIN_TRACING_V2', 'false')
    with caplog.at_level(logging.CRITICAL):
        _worker(SimpleNamespace(config='synthetic.json', model_id='A', with_control=with_control))
    assert len(model_calls) == 2
    expected = ('real_message_events=PASS events=27 messages=5 model_calls=2 '
                'tool_calls=1 turns=2 controls=1 pairing=verified terminal=completed\n'
                if with_control else 'real_message_events=PASS events=23 messages=4 model_calls=2 '
                'tool_calls=1 turns=2 pairing=verified terminal=completed\n')
    assert capsys.readouterr().out == expected


@async_test
async def test_iterator_without_optional_close_preserves_native_stream_return(monkeypatch):
    app = await make_app(monkeypatch)
    expected = {'messages': [HumanMessage(id='input', content='input')], 'custom': object()}
    class BareIterator:
        def __aiter__(self):
            return self
        async def __anext__(self):
            if getattr(self, 'done', False):
                raise StopAsyncIteration
            self.done = True
            return {'type': 'values', 'ns': (), 'data': expected}
    monkeypatch.setattr(app._agent, 'astream', lambda *_a, **_kw: BareIterator())
    pub, events = publisher()
    result = (await ainvoke_with_message_observation(app._agent, {}, context={},
                                            config={'configurable': {'thread_id': 'thread'}}, publisher=pub))
    assert result is expected
    assert [e.data.message_id for e in messages(events)] == ['input']
