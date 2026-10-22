"""Result budgets, compilation, failure identity and cancellation boundaries."""

import json
import asyncio

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from native_application_helpers import async_test
from naiyue_test_agent.agent_runtime.errors import ModelResponseRejected
from naiyue_test_agent.agent_runtime.run_services import RunServices, bind_run_services, current_run_services
from naiyue_test_agent.tools import SubAgentSpec, create_builtin_tools
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.tools.settings import SubAgentOutputSettings
from test_async_application import call
from test_subagent_execution import ChildModel, delegated_app, record_instances, role


@pytest.mark.parametrize('text, settings, expected', [
    ('a\nb\nc\n', {'max_result_lines': 2}, 'a\nb\n'),
    ('中中\nend', {'max_result_bytes': 151}, '中'),
    ('one\nlonger\nlast', {'max_result_bytes': 155}, 'one\n'),
])
@async_test
async def test_result_budget_from_settings_preserves_prefix_and_marks_truncation(monkeypatch, text, settings, expected):
    child = ChildModel(replies=[AIMessage(content=text)])
    configured = AppSettings(subagents=(role(),), tools={'subagent': SubAgentOutputSettings(**settings)})
    app, _ = await delegated_app(monkeypatch, child, settings=configured)
    result = await app.arun(await app.acreate_session('A'), [('user', 'report')])
    feedback = next(message for message in result['messages'] if isinstance(message, ToolMessage))
    envelope = json.loads(feedback.content)
    assert envelope['report'] == expected
    assert envelope['truncated'] is True and feedback.artifact['truncated'] is True
    assert envelope['agent_id'] == feedback.artifact['agent_id']
    assert len(feedback.content.encode('utf-8')) <= configured.tools.subagent.max_result_bytes
    assert len(child.seen) == 1


@async_test
async def test_content_blocks_and_usage_are_projected_without_thinking_or_full_history(monkeypatch):
    child = ChildModel(replies=[AIMessage(content=[{'type': 'thinking', 'thinking': 'PRIVATE_THINKING'},
        {'type': 'text', 'text': 'report'}], usage_metadata={'input_tokens': 2, 'output_tokens': 3, 'total_tokens': 5})])
    app, _ = await delegated_app(monkeypatch, child)
    result = await app.arun(await app.acreate_session('A'), [('user', 'report')])
    feedback = next(message for message in result['messages'] if isinstance(message, ToolMessage))
    assert json.loads(feedback.content)['report'] == 'report' and 'PRIVATE_THINKING' not in str(feedback.artifact)
    assert feedback.artifact['usage']['total_tokens'] == 5 and 'messages' not in feedback.artifact


@async_test
async def test_child_model_request_failure_is_safe_tool_feedback_with_cause(monkeypatch, caplog):
    error = TimeoutError('PRIVATE_PROVIDER_BODY')
    child = ChildModel(replies=[error])
    app, root = await delegated_app(monkeypatch, child)
    instances = record_instances(monkeypatch, app)
    result = await app.arun(await app.acreate_session('A'), [('user', 'start')])
    feedback = next(message for message in result['messages'] if isinstance(message, ToolMessage))
    assert feedback.status == 'error' and 'timeout' in feedback.content
    assert 'PRIVATE_PROVIDER_BODY' not in feedback.content + caplog.text
    assert len(child.seen) == 1 and len(root.seen) == 2
    assert instances[0]['status'] == 'failed'


@async_test
async def test_response_gate_error_keeps_identity_instead_of_becoming_tool_feedback(monkeypatch):
    child = ChildModel(replies=[AIMessage(content='', response_metadata={'finish_reason': 'tool_calls'})])
    app, root = await delegated_app(monkeypatch, child)
    with pytest.raises(ModelResponseRejected):
        await app.arun(await app.acreate_session('A'), [('user', 'start')])
    assert len(root.seen) == len(child.seen) == 1


@async_test
async def test_child_checkpoint_failure_is_not_mistaken_for_model_timeout(monkeypatch):
    from langchain_core.tools import ToolException
    child = ChildModel(replies=[AIMessage(content='done')])
    app, root = await delegated_app(monkeypatch, child)
    saver = app._agent.checkpointer
    save = saver.aput
    error = TimeoutError('PRIVATE_SAVE_BODY')
    async def fail_child(config, *args, **kwargs):
        if config['configurable']['thread_id'].startswith('subagent:'):
            raise error
        return await save(config, *args, **kwargs)
    monkeypatch.setattr(saver, 'aput', fail_child)
    result = await app.arun(await app.acreate_session('A'), [('user', 'start')])
    feedback = next(m for m in result['messages'] if isinstance(m, ToolMessage))
    assert feedback.status == 'error' and 'checkpoint' in feedback.content
    assert 'model request failed' not in feedback.content and len(root.seen) == 2 and child.seen == []
    child_saver = app._sessions._subagents['tester']['agent'].checkpointer
    with pytest.raises(ToolException) as caught:
        await child_saver.aput({'configurable': {'thread_id': 'subagent:source-check'}}, {}, {}, {})
    assert caught.value.__cause__ is error


@async_test
async def test_compilation_failure_does_not_publish_role_or_replace_existing_graph(monkeypatch):
    from naiyue_test_agent.agent_runtime import subagents as module
    child = ChildModel(replies=[])
    app, _ = await delegated_app(monkeypatch, child)
    original = app._sessions._subagents['tester']
    error = RuntimeError('compile failed')
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(module, 'build_subagent_runtime', fail)
    with pytest.raises(RuntimeError) as caught:
        await app.aregister_subagent(SubAgentSpec('other', 'Other', 'Prompt', ()))
    assert caught.value is error and app._sessions._subagents == {'tester': original}
    assert child.seen == []


@async_test
async def test_external_cancel_settles_child_and_restores_parent_services(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    child = ChildModel(replies=[AIMessage(content='unused')], entered=entered, release=release)
    app, _ = await delegated_app(monkeypatch, child)
    sid = await app.acreate_session('A')
    previous = RunServices()
    with bind_run_services(previous):
        task = asyncio.create_task(app.arun(sid, [('user', 'start')]))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            instance, = app._sessions._sessions[sid]._run.subagent_instances.values()
            driver = instance['driver']
            task.cancel()
            done, _ = await asyncio.wait((task,), timeout=5)
            assert task in done, 'Parent cancellation must settle its owned child without releasing the model'
            with pytest.raises(asyncio.CancelledError):
                await task
            assert instance['status'] == 'stopped' and driver.done() and instance['settled']
            assert instance['control'].reason == 'failure'
            assert all(not (owner._run.subagent_instances if owner._run else {}) for owner in app._sessions._sessions.values()) and 'driver' not in instance
            assert not app._sessions.require(sid).describe_session()['busy']
            assert current_run_services() is previous
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)


def test_subagent_builtin_has_a_real_coroutine_and_hides_runtime():
    spec = next(spec for spec in create_builtin_tools(AppSettings().tools) if spec.tool.name == 'subagent')
    assert spec.tool.coroutine is not None and 'runtime' not in spec.input_schema.model_fields


@async_test
async def test_finished_roots_release_instances_and_drivers_but_keep_checkpoints(monkeypatch):
    child = ChildModel(replies=[AIMessage(content='report' + 'x' * 20000) for _ in range(2)])
    from test_async_application import StreamingToolModel
    root = StreamingToolModel(replies=[item for index in range(2) for item in (
        call('subagent', {'agent': 'tester', 'task': f'task {index}'}, f'outer-{index}'), AIMessage(content='done'))])
    app, _ = await delegated_app(monkeypatch, child, root=root)
    instances = record_instances(monkeypatch, app)
    sid = await app.acreate_session('A')
    for index in range(2):
        await app.arun(sid, [('user', f'run {index}')])
        assert all(not (owner._run.subagent_instances if owner._run else {}) for owner in app._sessions._sessions.values())
        assert all('driver' not in instance and instance['status'] == 'completed' for instance in instances)
        snapshot = await app._sessions._subagents['tester']['agent'].aget_state(
            {'configurable': {'thread_id': instances[-1]['child_thread_id']}})
        assert snapshot.values['messages'][-1].content == 'report' + 'x' * 20000
        assert not app._sessions.require(sid).describe_session()['busy']
    assert len(instances) == 2
