"""Shared facts stay independent; runtime policy and actual event registration agree."""

import subprocess
import sys
from typing import get_args, get_type_hints

import pytest


def test_shared_contracts_import_without_loading_business_layers():
    program = '''
import sys
from naiyue_test_agent.contracts.failures import FailureDescription
from naiyue_test_agent.contracts.session_events import RunFinishedData
assert FailureDescription('model', 'unknown', 'unclassified', 'ValueError').source == 'model'
assert RunFinishedData('completed').status == 'completed'
assert not any(name.startswith(('naiyue_test_agent.application', 'naiyue_test_agent.agent_runtime',
                               'naiyue_test_agent.model_access')) for name in sys.modules)
'''
    result = subprocess.run([sys.executable, '-c', program], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_response_rejection_is_owned_only_by_runtime():
    from naiyue_test_agent.contracts import failures
    from naiyue_test_agent.agent_runtime.errors import ModelResponseRejected
    assert ModelResponseRejected.__module__ == 'naiyue_test_agent.agent_runtime.errors'
    assert not hasattr(failures, 'ModelResponseRejected')
    assert not hasattr(failures, 'RejectionReason')


def test_turn_failure_type_is_resolvable_without_a_forward_import_cycle():
    from naiyue_test_agent.agent_runtime.turn_events import TurnFinishedData
    from naiyue_test_agent.contracts.failures import RunFailureData
    assert get_type_hints(TurnFinishedData)['failure'] == RunFailureData | None
    failure = RunFailureData('execute', 'runtime_error', 'tool_batch_scheduling_failed', 'RuntimeError')
    assert TurnFinishedData('turn', 'run', None, 'failed', failure=failure).failure is failure


@pytest.mark.parametrize('reason', [
    'stop_reason_error', 'invalid_tool_calls', 'missing_tool_calls', 'missing_ai_message',
    'missing_tool_call_id', 'duplicate_tool_call_ids', 'invalid_tool_call_ids',
])
def test_actual_rejection_reasons_match_the_declared_runtime_protocol(reason):
    from naiyue_test_agent.agent_runtime import errors
    assert reason in get_args(errors.RejectionReason)
    error = errors.ModelResponseRejected(reason)
    description = errors.RuntimeErrorPolicy().describe(error)
    assert description.source == 'runtime' and description.reason == reason
    assert description.category == 'model_response_rejected'
    assert error.reason == reason and error.args == (f'Model response rejected: {reason}',)


def test_application_has_one_native_async_execution_surface():
    from importlib.util import find_spec
    from naiyue_test_agent import application
    from naiyue_test_agent.application.session_service import SessionService
    from naiyue_test_agent.application.session_persistence import SessionPersistence
    from naiyue_test_agent.application import io_steps
    assert not hasattr(application, 'LocalApplication')
    assert not hasattr(application, 'build_local_application')
    assert find_spec('naiyue_test_agent.application.persistent') is None
    assert find_spec('naiyue_test_agent.application.async_session') is None
    assert not hasattr(SessionService, 'run') and not hasattr(SessionService, 'resume_tool_approval')
    assert not any(hasattr(SessionPersistence, name) for name in ('finish', 'restore', 'history', 'recover'))
    assert not hasattr(io_steps, 'run_steps')


def test_runtime_has_one_async_execution_surface_and_no_historical_discount():
    from pathlib import Path
    from naiyue_test_agent import agent_runtime
    from naiyue_test_agent.agent_runtime import resource_invocation, message_observation, context_summary_io
    from naiyue_test_agent.agent_runtime.tool_execution import batch, permissions, approval
    assert not (Path(agent_runtime.__file__).parents[1] / 'test_agent' / 'discount_case_design.py').exists()
    assert callable(agent_runtime.build_runtime_agent)
    assert not any(hasattr(agent_runtime, name) for name in ('invoke_agent_run', '_invoke_agent_run'))
    assert not any(hasattr(resource_invocation, name) for name in (
        'invoke_agent_run_with_resources', 'resume_agent_run_with_resources'))
    assert not hasattr(message_observation, 'invoke_with_message_observation')
    assert not hasattr(context_summary_io, 'run_summary_steps')
    assert not hasattr(batch, '_SequentialPlan')
    assert not hasattr(permissions.AutoPermissionReviewer, 'review')
    assert 'wrap_tool_call' not in batch.ToolBatchMiddleware.__dict__
    assert 'after_model' in approval.ObservedHumanInTheLoopMiddleware.__dict__


def test_io_step_requires_one_actual_implementation_and_settles_critical_async_io():
    import asyncio
    from naiyue_test_agent.application.io_steps import IOCall, arun_steps

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        settled = []
        async def write():
            entered.set()
            await release.wait()
            settled.append('saved')
        def steps():
            try:
                yield IOCall(async_call=write)
            finally:
                settled.append('closed')
        for kwargs in ({}, {'call': lambda: None, 'async_call': write}):
            with pytest.raises(ValueError, match='exactly one'):
                IOCall(**kwargs)
        task = asyncio.create_task(arun_steps(steps()))
        await entered.wait()
        task.cancel()
        assert not settled
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert settled == ['saved', 'closed']
        def blocking():
            result = yield IOCall(lambda: 42)
            return result
        assert await arun_steps(blocking()) == 42
    asyncio.run(scenario())


def test_tool_body_interrupt_is_not_mistaken_for_pre_tool_approval(monkeypatch):
    import asyncio
    from langchain_core.messages import AIMessage
    from langchain_core.tools import tool
    from langgraph.types import interrupt
    from naiyue_test_agent import application
    from naiyue_test_agent.application.async_application import build_async_local_application
    from naiyue_test_agent.model_access import ModelCatalog
    from naiyue_test_agent.tools import ToolSpec
    from naiyue_test_agent.tools.permissions import ToolPermission
    from test_application_run_events import ToolModel

    effects = []
    @tool
    def custom_pause() -> str:
        """Pause after a synthetic side effect, outside the approval boundary."""
        effects.append('effect')
        interrupt(1)
        return 'resumed'
    model = ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'id': 'custom', 'name': 'custom_pause', 'args': {}}])])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    async def scenario():
        app = await build_async_local_application('synthetic', tools=[ToolSpec(custom_pause,
            custom_pause.args_schema, permissions=ToolPermission(default='allow'))], system_prompt='', response_format=None)
        try:
            sid = await app.acreate_session('A')
            with pytest.raises(AttributeError):
                await app.arun(sid, [('user', 'run')])
            assert effects == ['effect']
            assert app._sessions._sessions[sid]['manual_review_required']
            assert not app._sessions._sessions[sid]['busy']
            with pytest.raises(application.SessionManualReviewRequiredError):
                await app.arun(sid, [('user', 'retry')])
            assert effects == ['effect']
        finally:
            await app.aclose()
    asyncio.run(scenario())
