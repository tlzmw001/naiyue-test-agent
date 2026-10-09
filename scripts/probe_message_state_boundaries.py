"""Offline research: compare accepted calls, graph values, and saved state."""

import hashlib
import json
import logging
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from unittest.mock import patch

from langchain.agents.structured_output import ToolStrategy
from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage, message_to_dict
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel

from naiyue_test_agent import application
from naiyue_test_agent.agent_runtime.tools import create_session_read_tool
from naiyue_test_agent.model_access import ModelCatalog


class Model(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


class StateProbe:
    """Research-only proxy; reproduces the current invoke result from sync parts."""

    def __init__(self, graph):
        self.graph = graph
        self.values = []
        self.task_starts = []
        self.update_nodes = []
        self.accepted_ai_counts = []
        self.timeline = []
        self.runs = []
        self.aliases = {}

    def __getattr__(self, name):
        return getattr(self.graph, name)

    def record_event(self, event):
        self.timeline.append(event.event_type)
        if event.event_type == 'model_call_finished' and event.data.status == 'accepted':
            latest = self.values[-1] if self.values else []
            self.accepted_ai_counts.append(sum(row['role'] == 'ai' for row in latest))

    def rows(self, messages):
        rows = []
        for message in messages:
            assert message.id is not None
            alias = self.aliases.setdefault(message.id, f'm{len(self.aliases) + 1}')
            encoded = json.dumps(message_to_dict(message), ensure_ascii=False, sort_keys=True)
            rows.append({
                'id': alias, 'role': message.type,
                'fingerprint': hashlib.sha256(encoded.encode()).hexdigest()[:12],
                'tool_calls': tuple(c['id'] for c in message.tool_calls) if isinstance(message, AIMessage) else (),
                'tool_result': message.tool_call_id if isinstance(message, ToolMessage) else None,
                'summary': message.additional_kwargs.get('naiyue_compaction_summary') is True,
            })
        return rows

    def invoke(self, input, *, context, config):
        latest, interrupts = None, []
        run_rows = []
        for part in self.graph.stream(
            input, context=context, config=config,
            stream_mode=['updates', 'values', 'tasks'], version='v2',
            output_keys=self.graph.output_channels,
        ):
            assert part['ns'] == ()
            mode, payload = part['type'], part['data']
            if mode == 'values':
                latest = payload
                rows = self.rows(payload.get('messages', []))
                self.values.append(rows)
                run_rows.append(rows)
                self.timeline.append('values:' + ','.join(row['role'] for row in rows))
            elif mode == 'updates':
                if '__interrupt__' in payload:
                    interrupts.extend(payload['__interrupt__'])
                self.update_nodes.extend(k for k in payload if k != '__interrupt__')
            elif mode == 'tasks' and 'input' in payload:
                self.task_starts.append(payload['name'])
        self.runs.append(run_rows)
        if interrupts:
            return {**(latest or {}), '__interrupt__': interrupts}
        return latest


def make_app(model, *, tools=None, workspace=None, response_format=None):
    with patch.object(application, 'load_model_catalog', return_value=ModelCatalog('A', {'A': model})):
        app = application.build_local_application(
            'synthetic.json', tools=tools or [], system_prompt='', response_format=response_format,
        )
    probe = StateProbe(app._agent)
    app._agent = probe
    app.subscribe_events(probe.record_event)
    session = app.create_workspace_session('A', workspace) if workspace else app.create_session('A')
    return app, session, probe


def report(case, probe, **extra):
    shapes = []
    for rows in probe.values:
        shape = [row['role'] + ('[summary]' if row['summary'] else '') for row in rows]
        if not shapes or shape != shapes[-1]:
            shapes.append(shape)
    print(json.dumps({
        'case': case, 'value_shapes': shapes,
        'model_task_starts': probe.task_starts.count('model'),
        'accepted_ai_counts': probe.accepted_ai_counts, **extra,
    }, ensure_ascii=False))


def normal_and_followup():
    @tool
    def probe_value() -> str:
        """Read one synthetic value."""
        return 'value'

    model = Model(responses=[AIMessage(content='', tool_calls=[
        {'name': 'probe_value', 'args': {}, 'id': 'read-1'},
    ], response_metadata={'finish_reason': 'tool_calls'}),
        AIMessage(content='answer', response_metadata={'finish_reason': 'stop'})])
    app, session, probe = make_app(model, tools=[probe_value])
    result = app.run(session, [('user', 'question')])
    assert [row['role'] for row in probe.values[-1]] == ['human', 'ai', 'tool', 'ai']
    assert probe.accepted_ai_counts == [0, 1]
    assert len({row['id'] for row in probe.values[-1]}) == 4
    assert len(result['messages']) == 4
    report('normal_tool', probe, model_accepted_precedes_new_ai_value=True)

    holder = {}
    queued = False

    class QueueingModel(Model):
        def _generate(self, *args, **kwargs):
            nonlocal queued
            if not queued:
                queued = True
                holder['app'].follow_up(holder['session'], 'follow question')
            return super()._generate(*args, **kwargs)

    app, session, probe = make_app(QueueingModel(responses=[AIMessage(content='first'), AIMessage(content='second')]))
    holder.update(app=app, session=session)
    result = app.run(session, [('user', 'initial')])
    assert [m.type for m in result['messages']] == ['human', 'ai', 'human', 'ai']
    assert probe.task_starts.count('model') == 2
    assert any(name.endswith('inject_at_natural_end.after_agent') for name in probe.update_nodes)
    report('followup', probe, one_app_run_two_model_turns=True)


def approval_and_failure(root):
    workspace = root / 'workspace'
    workspace.mkdir()
    outside = root / 'outside.md'
    outside.write_text('value', encoding='utf-8')
    app, session, probe = make_app(Model(responses=[AIMessage(content='', tool_calls=[
        {'name': 'read', 'args': {'path': str(outside)}, 'id': 'approval-1'},
    ]), AIMessage(content='handled')]), tools=[create_session_read_tool()], workspace=workspace)
    paused = app.run(session, [('user', 'read outside')])
    assert paused['approval_requests'] and '__interrupt__' in paused
    pending_ai_id = probe.values[-1][-1]['id']
    result = app.resume_tool_approval(session, ['reject'])
    assert probe.runs[1][0][-1]['id'] == pending_ai_id
    assert [m.type for m in result['messages']] == ['human', 'ai', 'tool', 'ai']
    assert any(row['tool_result'] == 'approval-1' for rows in probe.runs[1] for row in rows)
    report('approval_reject', probe, restored_ai_id_same=True, pause_keeps_turn_unresolved=True)

    failure = RuntimeError('synthetic failure')

    class Failing(Model):
        def _generate(self, *args, **kwargs):
            raise failure

    app, session, probe = make_app(Failing(responses=[AIMessage(content='unused')]))
    try:
        app.run(session, [('user', 'question')])
    except RuntimeError as error:
        assert error is failure
    else:
        raise AssertionError('Expected model failure')
    assert [row['role'] for row in probe.values[-1]] == ['human']
    assert 'run_finished' == probe.timeline[-1]
    report('model_failure', probe, input_visible_without_ai=True)


def steering_stop_and_partial_failure():
    for action in ('steer', 'stop'):
        holder = {}

        @tool
        def controlled_tool() -> str:
            """Submit one controlled run signal during tool execution."""
            if action == 'steer':
                holder['app'].steer(holder['session'], 'adjust question')
            else:
                holder['app'].request_stop(holder['session'])
            return 'value'

        model = Model(responses=[AIMessage(content='', tool_calls=[
            {'name': 'controlled_tool', 'args': {}, 'id': 'control-1'},
        ]), AIMessage(content='answer')])
        app, session, probe = make_app(model, tools=[controlled_tool])
        holder.update(app=app, session=session)
        result = app.run(session, [('user', 'question')])
        expected = ['human', 'ai', 'tool', 'human', 'ai'] if action == 'steer' else ['human', 'ai', 'tool']
        assert [m.type for m in result['messages']] == expected
        assert probe.task_starts.count('model') == (2 if action == 'steer' else 1)
        if action == 'stop':
            assert result['run_stopped'] is True
        report(action, probe, signal_effect_visible=True)

    failure = RuntimeError('synthetic fatal tool')

    @tool
    def fatal() -> str:
        """Fail a controlled sequential batch."""
        raise failure

    fatal.metadata = {'naiyue_execution_mode': 'sequential'}

    @tool
    def skipped() -> str:
        """This call must be skipped."""
        raise AssertionError('Unexpected execution')

    model = Model(responses=[AIMessage(content='', tool_calls=[
        {'name': 'fatal', 'args': {}, 'id': 'fatal-1'},
        {'name': 'skipped', 'args': {}, 'id': 'skip-1'},
    ])])
    app, session, probe = make_app(model, tools=[fatal, skipped])
    try:
        app.run(session, [('user', 'run')])
    except RuntimeError as error:
        assert error is failure
    else:
        raise AssertionError('Expected tool failure')
    config = {'configurable': {'thread_id': app._sessions._sessions[session]['thread_id']}}
    saved = probe.graph.checkpointer.get_tuple(config)
    pending_tools = []
    for _task, channel, value in saved.pending_writes:
        if channel == 'messages':
            pending_tools.extend(m for m in (value if isinstance(value, list) else [value])
                                 if isinstance(m, ToolMessage))
    first, second = probe.graph.get_state(config), probe.graph.get_state(config)
    tools_first = [m.id for m in first.values['messages'] if isinstance(m, ToolMessage)]
    tools_second = [m.id for m in second.values['messages'] if isinstance(m, ToolMessage)]
    exact = probe.graph.get_state(first.config)
    report('partial_tool_failure', probe,
           latest_read_roles=[m.type for m in first.values['messages']],
           exact_checkpoint_roles=[m.type for m in exact.values['messages']],
           pending_ids_assigned=all(m.id is not None for m in pending_tools) if pending_tools else None,
           pending_tool_ids_stable=(tools_first == tools_second) if tools_first else None)


def recovery_and_structured():
    # Research reuses the existing deterministic recovery fixture, not product code.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
    from test_context_overflow_recovery import _configured_model, _history, _status

    model = _configured_model([_status(), AIMessage(content='recovered')])
    app, session, probe = make_app(model)
    result = app.run(session, _history())
    initial_ids = {row['id'] for row in probe.values[0]}
    final = probe.values[-1]
    assert final[0]['summary'] and initial_ids - {row['id'] for row in final}
    assert result['messages'][-1].content == 'recovered'
    assert model.normal_inputs and len(model.normal_inputs) == 2
    report('overflow_replacement', probe, old_prefix_removed=True, summary_is_context_human=True)

    class Answer(BaseModel):
        value: str

    model = Model(responses=[AIMessage(content='', tool_calls=[
        {'name': 'Answer', 'args': {'value': 'structured'}, 'id': 'format-1'},
    ])])
    app, session, probe = make_app(model, response_format=ToolStrategy(Answer))
    result = app.run(session, [('user', 'answer')])
    assert result['structured_response'].value == 'structured'
    assert [m.type for m in result['messages']] == ['human', 'ai', 'tool']
    assert 'tools' not in probe.task_starts
    report('structured_response', probe, tool_message_without_tool_node=True)


def persistence_lag():
    saver_entered, allow_save, ai_observed = Event(), Event(), Event()
    put_shapes = []

    class LaggingSaver(InMemorySaver):
        def put(self, config, checkpoint, metadata, new_versions):
            put_shapes.append([type(m).__name__ for m in checkpoint['channel_values'].get('messages', [])])
            if any(isinstance(m, AIMessage) for m in checkpoint['channel_values'].get('messages', [])):
                saver_entered.set()
                assert allow_save.wait(timeout=5)
            return super().put(config, checkpoint, metadata, new_versions)

    saver = LaggingSaver()
    app, session, probe = make_app(Model(responses=[AIMessage(content='answer')]))
    graph = probe.graph
    graph.checkpointer = saver
    config = {'configurable': {'thread_id': 'lag-probe'}}
    errors = []

    def run():
        try:
            for part in graph.stream({'messages': [('user', 'question')]}, config=config,
                                     context={'session_id': session, 'run_id': 'lag-probe'},
                                     stream_mode='values', version='v2'):
                if any(isinstance(m, AIMessage) for m in part['data']['messages']):
                    ai_observed.set()
        except BaseException as error:
            errors.append(error)

    worker = Thread(target=run)
    worker.start()
    try:
        observed = ai_observed.wait(timeout=5)
        entered = saver_entered.wait(timeout=5)
        assert observed and entered, {
            'ai_observed': observed, 'saver_entered': entered,
            'put_shapes': put_shapes, 'worker_errors': [type(e).__name__ for e in errors],
        }
        saved = saver.get_tuple(config)
        assert saved is not None
        assert not any(isinstance(m, AIMessage) for m in saved.checkpoint['channel_values']['messages'])
    finally:
        allow_save.set()
        worker.join(timeout=5)
    assert not worker.is_alive() and not errors, errors
    assert any(isinstance(m, AIMessage) for m in saver.get_tuple(config).checkpoint['channel_values']['messages'])
    print(json.dumps({'case': 'persistence_lag', 'ai_value_before_save': True, 'save_after_release': True}))


def main():
    logging.getLogger().setLevel(logging.CRITICAL)
    normal_and_followup()
    with TemporaryDirectory(prefix='message-state-') as directory:
        approval_and_failure(Path(directory))
    recovery_and_structured()
    steering_stop_and_partial_failure()
    persistence_lag()
    print('message_state_boundaries=PASS cases=10 network=none product_changes=none')


if __name__ == '__main__':
    main()
