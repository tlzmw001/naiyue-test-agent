"""Offline research: compare returned model calls, graph values, and saved state."""

from naiyue_test_agent.tools.permissions import ToolPermission

from naiyue_test_agent.tools import ToolSpec

import asyncio
from contextlib import AsyncExitStack
import hashlib
import json
import logging
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from langchain.agents.structured_output import ToolStrategy
from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage, message_to_dict
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel

from naiyue_test_agent import application
from naiyue_test_agent.application.async_application import build_async_local_application
from naiyue_test_agent.tools.permissions import PermissionMechanismError
from naiyue_test_agent.tools import create_session_read_tool
from naiyue_test_agent.model_access import ModelCatalog


class Model(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


class StateProbe:
    """Research-only proxy; reproduces the native async result from observed graph parts."""

    def __init__(self, graph):
        self.graph = graph
        self.values = []
        self.task_starts = []
        self.update_nodes = []
        self.returned_ai_counts = []
        self.timeline = []
        self.runs = []
        self.aliases = {}

    def __getattr__(self, name):
        return getattr(self.graph, name)

    def record_event(self, event):
        self.timeline.append(event.event_type)
        if event.event_type == 'model_call_finished' and event.data.status == 'returned':
            latest = self.values[-1] if self.values else []
            self.returned_ai_counts.append(sum(row['role'] == 'ai' for row in latest))

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

    async def ainvoke(self, input, *, context, config):
        latest, interrupts = None, []
        run_rows = []
        async for part in self.graph.astream(
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


async def make_app(model, *, resources, tools=None, workspace=None, response_format=None):
    with patch.object(application, 'load_model_catalog', return_value=ModelCatalog('A', {'A': model})):
        app = await build_async_local_application(
            'synthetic.json', tools=tools or [], system_prompt='', response_format=response_format,
        )
    resources.push_async_callback(app.aclose)
    probe = StateProbe(app._agent)
    app._agent = probe
    app.subscribe_events(probe.record_event)
    session = await app.acreate_workspace_session('A', workspace) if workspace else await app.acreate_session('A')
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
        'returned_ai_counts': probe.returned_ai_counts, **extra,
    }, ensure_ascii=False))


async def normal_and_followup(resources):
    @tool
    def probe_value() -> str:
        """Read one synthetic value."""
        return 'value'

    model = Model(responses=[AIMessage(content='', tool_calls=[
        {'name': 'probe_value', 'args': {}, 'id': 'read-1'},
    ], response_metadata={'finish_reason': 'tool_calls'}),
        AIMessage(content='answer', response_metadata={'finish_reason': 'stop'})])
    app, session, probe = await make_app(model, tools=[ToolSpec(probe_value, probe_value.args_schema, permissions=ToolPermission(default='allow'))], resources=resources)
    result = await app.arun(session, [('user', 'question')])
    assert [row['role'] for row in probe.values[-1]] == ['human', 'ai', 'tool', 'ai']
    assert probe.returned_ai_counts == [0, 1]
    assert len({row['id'] for row in probe.values[-1]}) == 4
    assert len(result['messages']) == 4
    report('normal_tool', probe, model_returned_precedes_new_ai_value=True)

    holder = {}
    queued = False

    class QueueingModel(Model):
        def _generate(self, *args, **kwargs):
            nonlocal queued
            if not queued:
                queued = True
                holder['app'].follow_up(holder['session'], 'follow question')
            return super()._generate(*args, **kwargs)

    app, session, probe = await make_app(QueueingModel(responses=[AIMessage(content='first'), AIMessage(content='second')]), resources=resources)
    holder.update(app=app, session=session)
    result = await app.arun(session, [('user', 'initial')])
    assert [m.type for m in result['messages']] == ['human', 'ai', 'human', 'ai']
    assert probe.task_starts.count('model') == 2
    assert any(name.endswith('inject_at_natural_end.after_agent') for name in probe.update_nodes)
    report('followup', probe, one_app_run_two_model_turns=True)


async def approval_and_failure(root, resources):
    workspace = root / 'workspace'
    workspace.mkdir()
    outside = root / 'outside.md'
    outside.write_text('value', encoding='utf-8')
    app, session, probe = await make_app(Model(responses=[AIMessage(content='', tool_calls=[
        {'name': 'read', 'args': {'path': str(outside)}, 'id': 'approval-1'},
    ]), AIMessage(content='handled')]), tools=[create_session_read_tool()], workspace=workspace, resources=resources)
    paused = await app.arun(session, [('user', 'read outside')])
    assert paused['approval_requests'] and '__interrupt__' in paused
    pending_ai_id = probe.values[-1][-1]['id']
    result = await app.aresume_tool_approval(session, ['reject'])
    assert probe.runs[1][0][-1]['id'] == pending_ai_id
    assert [m.type for m in result['messages']] == ['human', 'ai', 'tool', 'ai']
    assert any(row['tool_result'] == 'approval-1' for rows in probe.runs[1] for row in rows)
    report('approval_reject', probe, restored_ai_id_same=True, pause_keeps_turn_unresolved=True)

    failure = RuntimeError('synthetic failure')

    class Failing(Model):
        def _generate(self, *args, **kwargs):
            raise failure

    app, session, probe = await make_app(Failing(responses=[AIMessage(content='unused')]), resources=resources)
    try:
        await app.arun(session, [('user', 'question')])
    except RuntimeError as error:
        assert error is failure
    else:
        raise AssertionError('Expected model failure')
    assert [row['role'] for row in probe.values[-1]] == ['human']
    assert 'run_finished' == probe.timeline[-1]
    report('model_failure', probe, input_visible_without_ai=True)


async def steering_stop_and_partial_failure(resources):
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
        app, session, probe = await make_app(model, tools=[ToolSpec(controlled_tool, controlled_tool.args_schema, permissions=ToolPermission(default='allow'))], resources=resources)
        holder.update(app=app, session=session)
        result = await app.arun(session, [('user', 'question')])
        expected = ['human', 'ai', 'tool', 'human', 'ai'] if action == 'steer' else ['human', 'ai', 'tool']
        assert [m.type for m in result['messages']] == expected
        assert probe.task_starts.count('model') == (2 if action == 'steer' else 1)
        if action == 'stop':
            assert result['run_stopped'] is True
        report(action, probe, signal_effect_visible=True)

    failure = PermissionMechanismError('synthetic mechanism failure')

    @tool
    def fatal() -> str:
        """Fail a controlled sequential batch."""
        raise failure


    @tool
    def skipped() -> str:
        """This call must be skipped."""
        raise AssertionError('Unexpected execution')

    model = Model(responses=[AIMessage(content='', tool_calls=[
        {'name': 'fatal', 'args': {}, 'id': 'fatal-1'},
        {'name': 'skipped', 'args': {}, 'id': 'skip-1'},
    ])])
    app, session, probe = await make_app(model, tools=[ToolSpec(fatal, fatal.args_schema, execution_mode='sequential', permissions=ToolPermission(default='allow')), ToolSpec(skipped, skipped.args_schema, permissions=ToolPermission(default='allow'))], resources=resources)
    try:
        await app.arun(session, [('user', 'run')])
    except RuntimeError as error:
        assert error is failure
    else:
        raise AssertionError('Expected tool failure')
    config = {'configurable': {'thread_id': app._sessions.require(session).get_thread_id()}}
    saved = await probe.graph.checkpointer.aget_tuple(config)
    pending_tools = []
    for _task, channel, value in saved.pending_writes:
        if channel == 'messages':
            pending_tools.extend(m for m in (value if isinstance(value, list) else [value])
                                 if isinstance(m, ToolMessage))
    first, second = await probe.graph.aget_state(config), await probe.graph.aget_state(config)
    tools_first = [m.id for m in first.values['messages'] if isinstance(m, ToolMessage)]
    tools_second = [m.id for m in second.values['messages'] if isinstance(m, ToolMessage)]
    exact = await probe.graph.aget_state(first.config)
    report('partial_tool_failure', probe,
           latest_read_roles=[m.type for m in first.values['messages']],
           exact_checkpoint_roles=[m.type for m in exact.values['messages']],
           pending_ids_assigned=all(m.id is not None for m in pending_tools) if pending_tools else None,
           pending_tool_ids_stable=(tools_first == tools_second) if tools_first else None)


async def recovery_and_structured(resources):
    # Research reuses the existing deterministic recovery fixture, not product code.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
    from test_context_overflow_recovery import _configured_model, _history, _status

    model = _configured_model([_status(), AIMessage(content='recovered')])
    app, session, probe = await make_app(model, resources=resources)
    result = await app.arun(session, _history())
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
    app, session, probe = await make_app(model, response_format=ToolStrategy(Answer), resources=resources)
    result = await app.arun(session, [('user', 'answer')])
    assert result['structured_response'].value == 'structured'
    assert [m.type for m in result['messages']] == ['human', 'ai', 'tool']
    assert 'tools' not in probe.task_starts
    report('structured_response', probe, tool_message_without_tool_node=True)


async def persistence_lag(resources):
    saver_entered, allow_save, ai_observed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    put_shapes = []

    class LaggingSaver(InMemorySaver):
        async def aput(self, config, checkpoint, metadata, new_versions):
            put_shapes.append([type(m).__name__ for m in checkpoint['channel_values'].get('messages', [])])
            if any(isinstance(m, AIMessage) for m in checkpoint['channel_values'].get('messages', [])):
                saver_entered.set()
                await allow_save.wait()
            return await super().aput(config, checkpoint, metadata, new_versions)

    saver = LaggingSaver()
    app, session, probe = await make_app(Model(responses=[AIMessage(content='answer')]), resources=resources)
    graph = probe.graph
    graph.checkpointer = saver
    config = {'configurable': {'thread_id': 'lag-probe'}}

    async def run():
        async for part in graph.astream({'messages': [('user', 'question')]}, config=config,
                context={'session_id': session, 'run_id': 'lag-probe'}, stream_mode='values', version='v2'):
            if any(isinstance(m, AIMessage) for m in part['data']['messages']):
                ai_observed.set()

    worker = asyncio.create_task(run())
    try:
        await asyncio.wait_for(ai_observed.wait(), 5)
        await asyncio.wait_for(saver_entered.wait(), 5)
        saved = await saver.aget_tuple(config)
        assert saved is not None
        assert not any(isinstance(m, AIMessage) for m in saved.checkpoint['channel_values']['messages'])
    finally:
        allow_save.set()
        await asyncio.wait_for(asyncio.shield(worker), 5)
    assert worker.done()
    saved = await saver.aget_tuple(config)
    assert any(isinstance(m, AIMessage) for m in saved.checkpoint['channel_values']['messages'])
    print(json.dumps({'case': 'persistence_lag', 'ai_value_before_save': True, 'save_after_release': True}))


async def main():
    logging.getLogger().setLevel(logging.CRITICAL)
    async with AsyncExitStack() as resources:
        await normal_and_followup(resources)
        with TemporaryDirectory(prefix='message-state-') as directory:
            await approval_and_failure(Path(directory), resources)
        await recovery_and_structured(resources)
        await steering_stop_and_partial_failure(resources)
        await persistence_lag(resources)
    print('message_state_boundaries=PASS cases=10 network=none product_changes=none')


if __name__ == '__main__':
    asyncio.run(main())
