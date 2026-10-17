"""Execution failures become paired results inside the real batch boundary."""

from naiyue_test_agent.tools.permissions import ToolPermission

from naiyue_test_agent.tools import ToolSpec
from naiyue_test_agent.application import prepare_tool_registry

import asyncio
import logging

import pytest
from langchain.tools import tool
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import ToolException
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import interrupt

from naiyue_test_agent.agent_runtime import build_runtime_agent
from test_agent_runtime import ToolCapableFake


def graph(tools, calls, *, checkpointer=None):
    replies = [AIMessage(content='', tool_calls=calls)] if calls else []
    model = ToolCapableFake(responses=[*replies, AIMessage(content='handled')])
    _runtime_registry_1 = prepare_tool_registry(tools)
    _runtime_names_1 = _runtime_registry_1.names()
    return build_runtime_agent(
        model, tool_registry=_runtime_registry_1, resolve_active_tool_names=lambda _sid, agent_path='root', names=_runtime_names_1: names, system_prompt='', response_format=None,
        resolve_model=lambda _: ('fake', model), record_selection=lambda *_: None,
        checkpointer=checkpointer,
    )


def invoke(agent, *, async_mode=False):
    kwargs = {'context': {'session_id': 's', 'run_id': 'r'},
              'config': {'configurable': {'thread_id': 't'}}}
    inputs = {'messages': [('user', 'run')]}
    return (asyncio.run(agent.ainvoke(inputs, **kwargs)) if async_mode
            else agent.invoke(inputs, **kwargs))


@pytest.mark.parametrize('async_mode', [False, True], ids=['sync', 'async'])
@pytest.mark.parametrize('failure', [
    ToolException('Choose a UTF-8 file.'), RuntimeError('PRIVATE_BODY'), KeyError('PRIVATE_KEY'),
])
def test_execution_errors_keep_pairing_and_continue_model(failure, async_mode, caplog):
    @tool
    def probe() -> str:
        """Fail without external side effects."""
        raise failure

    identity = (probe, probe.func, probe.args_schema, probe.metadata)
    agent = graph([ToolSpec(probe, probe.args_schema, permissions=ToolPermission(default='allow'))], [{'name': 'probe', 'args': {}, 'id': 'call-1'}])
    with caplog.at_level(logging.INFO):
        result = invoke(agent, async_mode=async_mode)
    feedback = next(m for m in result['messages'] if isinstance(m, ToolMessage))
    assert (feedback.name, feedback.tool_call_id, feedback.status) == ('probe', 'call-1', 'error')
    assert result['messages'][-1].content == 'handled'
    if isinstance(failure, ToolException):
        assert feedback.content == 'Choose a UTF-8 file.'
    else:
        assert type(failure).__name__ in feedback.content
    assert 'PRIVATE_' not in str(result) + caplog.text
    assert (probe, probe.func, probe.args_schema, probe.metadata) == identity
    assert probe.handle_tool_error is False
    conversions = [r.tool_error for r in caplog.records if hasattr(r, 'tool_error')]
    assert len(conversions) == 1
    assert conversions[0]['tool_call_id'] == 'call-1'
    assert conversions[0]['error_type'] == type(failure).__name__


@pytest.mark.parametrize('async_mode', [False, True], ids=['sync', 'async'])
def test_unexpected_error_does_not_fail_sequential_plan(async_mode):
    seen = []

    @tool
    def probe(label: str) -> str:
        """Record ordered calls and fail the middle one."""
        seen.append(label)
        if label == 'B':
            raise RuntimeError('PRIVATE')
        return label

    result = invoke(graph([ToolSpec(probe, probe.args_schema, execution_mode='sequential', permissions=ToolPermission(default='allow'))], [
        {'name': 'probe', 'args': {'label': label}, 'id': label} for label in ('A', 'B', 'C')
    ]), async_mode=async_mode)
    assert seen == ['A', 'B', 'C']
    assert [(m.tool_call_id, m.status) for m in result['messages'] if isinstance(m, ToolMessage)] == [
        ('A', 'success'), ('B', 'error'), ('C', 'success'),
    ]
    assert result['messages'][-1].content == 'handled'


def test_old_internal_error_handler_is_rejected_without_mutation():
    @tool
    def probe() -> str:
        """Return a value."""
        return 'value'

    probe.handle_tool_error = True
    with pytest.raises(ValueError, match='handle_tool_error'):
        graph([ToolSpec(probe, probe.args_schema, permissions=ToolPermission(default='allow'))], [])
    assert probe.handle_tool_error is True


@pytest.mark.parametrize('async_mode', [False, True], ids=['sync', 'async'])
def test_validation_stays_outside_execution_feedback(async_mode, caplog):
    called = []

    @tool
    def probe(value: str) -> str:
        """Require a value before execution."""
        called.append(value)
        return value

    probe.handle_validation_error = 'Invalid arguments.'
    result = invoke(graph([ToolSpec(probe, probe.args_schema, permissions=ToolPermission(default='allow'))], [{'name': 'probe', 'args': {}, 'id': 'invalid'}]),
                    async_mode=async_mode)
    feedback = next(m for m in result['messages'] if isinstance(m, ToolMessage))
    assert feedback.status == 'error' and feedback.tool_call_id == 'invalid'
    assert feedback.content == 'Invalid arguments.'
    assert called == []
    assert not any(hasattr(r, 'tool_error') for r in caplog.records)


@pytest.mark.parametrize('async_mode', [False, True], ids=['sync', 'async'])
def test_real_interrupt_is_not_converted(async_mode, caplog):
    @tool
    def probe() -> str:
        """Pause through the actual graph interrupt protocol."""
        return interrupt('approval')

    result = invoke(graph([ToolSpec(probe, probe.args_schema, permissions=ToolPermission(default='allow'))], [{'name': 'probe', 'args': {}, 'id': 'pause'}],
                          checkpointer=InMemorySaver()), async_mode=async_mode)
    assert result['__interrupt__'][0].value == 'approval'
    assert not any(isinstance(m, ToolMessage) for m in result['messages'])
    assert not any(hasattr(r, 'tool_error') for r in caplog.records)


@pytest.mark.parametrize('async_mode', [False, True], ids=['sync', 'async'])
def test_process_cleanup_failure_propagates_original(async_mode):
    from naiyue_test_agent.tools.processes import ProcessCleanupError
    failure = ProcessCleanupError('PRIVATE_PROCESS_FAULT')

    @tool('bash')
    def bash() -> str:
        """Represent unconfirmed necessary process cleanup."""
        raise failure

    with pytest.raises(ProcessCleanupError) as caught:
        invoke(graph([ToolSpec(bash, bash.args_schema, permissions=ToolPermission(default='allow'))], [{'name': 'bash', 'args': {}, 'id': 'shell'}]),
               async_mode=async_mode)
    assert caught.value is failure


@pytest.mark.parametrize('async_mode', [False, True], ids=['sync', 'async'])
def test_bash_unexpected_error_uses_same_public_feedback_boundary(async_mode, caplog):
    @tool('bash')
    def bash() -> str:
        """Fail after the shared process runner has confirmed settlement."""
        raise RuntimeError('PRIVATE_PROCESS_FAULT')
    result = invoke(graph([ToolSpec(bash, bash.args_schema, permissions=ToolPermission(default='allow'))],
        [{'name': 'bash', 'args': {}, 'id': 'shell'}]), async_mode=async_mode)
    message = next(m for m in result['messages'] if isinstance(m, ToolMessage))
    assert message.tool_call_id == 'shell' and message.name == 'bash' and message.status == 'error'
    assert 'RuntimeError' in message.content and result['messages'][-1].content == 'handled'
    assert 'PRIVATE' not in str(result) + caplog.text


@pytest.mark.parametrize('async_mode', [False, True], ids=['sync', 'async'])
def test_empty_tools_and_unknown_name_never_execute(async_mode):
    empty = invoke(graph([], []), async_mode=async_mode)
    assert empty['messages'][-1].content == 'handled'
    result = invoke(graph([], [{'name': 'missing', 'args': {}, 'id': 'unknown'}]),
                    async_mode=async_mode)
    feedback = next(m for m in result['messages'] if isinstance(m, ToolMessage))
    assert feedback.status == 'error' and feedback.tool_call_id == 'unknown'
    assert result['messages'][-1].content == 'handled'


@pytest.mark.parametrize('async_mode', [False, True], ids=['sync', 'async'])
@pytest.mark.parametrize('after_replace', [False, True], ids=['before-commit', 'after-commit'])
@pytest.mark.parametrize('tool_name', ['write', 'edit'])
def test_uncertain_commit_is_not_replayed_or_quarantined(
    monkeypatch, tmp_path, caplog, async_mode, after_replace, tool_name,
):
    from naiyue_test_agent import application
    from naiyue_test_agent.tools import create_session_write_tool, create_session_edit_tool
    from naiyue_test_agent.tools import write as write_module
    from naiyue_test_agent.model_access import ModelCatalog
    from test_async_application import make_app, StreamingToolModel

    target = tmp_path / 'case.md'
    target.write_text('old', encoding='utf-8')
    args = ({'path': 'case.md', 'content': 'new'} if tool_name == 'write' else
            {'path': 'case.md', 'edits': [{'oldText': 'old', 'newText': 'new'}]})
    replies = [AIMessage(content='', tool_calls=[{'name': tool_name, 'args': args, 'id': 'commit'}]),
               AIMessage(content='inspect result'), AIMessage(content='continued safely')]
    tools = [create_session_write_tool() if tool_name == 'write' else create_session_edit_tool()]
    actual_replace = write_module.os.replace
    attempts = []

    def uncertain(source, destination):
        attempts.append(True)
        if after_replace:
            actual_replace(source, destination)
        raise OSError('PRIVATE_COMMIT_DETAIL')

    monkeypatch.setattr(write_module.os, 'replace', uncertain)

    def assert_result(result, continued, app, session):
        feedback = next(m for m in result['messages'] if isinstance(m, ToolMessage))
        assert (feedback.name, feedback.tool_call_id, feedback.status) == (tool_name, 'commit', 'error')
        assert 'outcome is unknown' in feedback.content
        assert result['messages'][-1].content == 'inspect result'
        assert continued['messages'][-1].content == 'continued safely'
        assert target.read_text(encoding='utf-8') == ('new' if after_replace else 'old')
        assert [p.name for p in tmp_path.iterdir()] == ['case.md']
        assert attempts == [True]
        assert not app._sessions._sessions[session]['manual_review_required']
        conversions = [r.tool_error for r in caplog.records if hasattr(r, 'tool_error')]
        assert len(conversions) == 1 and conversions[0]['cause_type'] == 'OSError'
        assert 'PRIVATE_' not in caplog.text + feedback.content

    if async_mode:
        async def scenario():
            app = await make_app(monkeypatch, StreamingToolModel(replies=replies), tools)
            try:
                session = await app.acreate_workspace_session('A', tmp_path)
                result = await app.arun(session, [('user', 'modify')])
                continued = await app.arun(session, [('user', 'continue')])
                assert_result(result, continued, app, session)
            finally:
                await app.aclose()
        asyncio.run(scenario())
    else:
        model = ToolCapableFake(responses=replies)
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
        app = application.build_local_application('synthetic', tools=tools, system_prompt='', response_format=None)
        session = app.create_workspace_session('A', tmp_path)
        result = app.run(session, [('user', 'modify')])
        continued = app.run(session, [('user', 'continue')])
        assert_result(result, continued, app, session)


@pytest.mark.parametrize('failure', [ToolException('Choose another input.'), RuntimeError('PRIVATE')])
def test_conversion_diagnostic_fault_does_not_replace_feedback(monkeypatch, caplog, failure):
    from naiyue_test_agent.agent_runtime.tool_execution import feedback as module

    @tool
    def probe() -> str:
        """Produce feedback even when optional diagnostic delivery fails."""
        raise failure

    def broken_diagnostic(*_args, **_kwargs):
        raise ValueError('PRIVATE_DIAGNOSTIC')

    monkeypatch.setattr(module.logger, 'warning', broken_diagnostic)
    result = invoke(graph([ToolSpec(probe, probe.args_schema, permissions=ToolPermission(default='allow'))], [{'name': 'probe', 'args': {}, 'id': 'diagnostic'}]))
    feedback = next(m for m in result['messages'] if isinstance(m, ToolMessage))
    assert feedback.status == 'error' and feedback.tool_call_id == 'diagnostic'
    assert feedback.content == (str(failure) if isinstance(failure, ToolException) else
        'Tool execution failed (RuntimeError). No normal result is available. '
        'Use the available context to decide the next step.')
    assert result['messages'][-1].content == 'handled'
    assert 'tool_error_diagnostics_failed' in caplog.text
    assert 'PRIVATE' not in feedback.content + caplog.text
