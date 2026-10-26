"""Run identity, invocation state and tool authorization have distinct lifetimes."""

from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace

import pytest


def types():
    from naiyue_test_agent.contracts.run_context import (
        RunIdentity, RunScope, RunCapabilities, RunObservationPorts, RuntimeContext,
        InvocationState, ModelCallState, ToolAuthorization,
    )
    return SimpleNamespace(**locals())


def test_run_scope_and_capability_references_are_readonly_without_a_publisher():
    t = types()
    memory = object()
    scope = t.RunScope(t.RunIdentity('S', 'R'), capabilities=t.RunCapabilities(memory=memory))
    with pytest.raises(FrozenInstanceError):
        scope.identity = t.RunIdentity('foreign', 'R')
    with pytest.raises(FrozenInstanceError):
        scope.capabilities.memory = None
    assert scope.capabilities.memory is memory and scope.observation.publisher is None


def test_each_invocation_has_fresh_state_but_tool_copies_share_its_batch():
    t = types()
    scope = t.RunScope(t.RunIdentity('S', 'R'))
    first, second = t.RuntimeContext(scope), t.RuntimeContext(scope)
    first.invocation.model_call = t.ModelCallState('model-call')
    first.invocation.tool_batch = object()
    permit_a, permit_b = {'call_id': 'A'}, {'call_id': 'B'}
    a = replace(first, authorization=t.ToolAuthorization(permit=permit_a))
    b = replace(first, authorization=t.ToolAuthorization(permit=permit_b))
    assert a.scope is b.scope is scope
    assert a.invocation is b.invocation is first.invocation
    assert a.invocation.tool_batch is first.invocation.tool_batch
    assert a.authorization.permit is permit_a and b.authorization.permit is permit_b
    assert first.authorization is None and second.authorization is None
    assert first.invocation is not second.invocation
    assert second.invocation.model_call is None and second.invocation.tool_batch is None


def test_context_binding_resets_on_nested_exception_and_rejects_foreign_identity(tmp_path):
    t = types()
    from naiyue_test_agent.agent_runtime.run_scope import bind_run_scope, current_run_scope
    from naiyue_test_agent.agent_runtime.invocation_context import make_invocation_context
    outer = t.RunScope(t.RunIdentity('S', 'R'))
    child = t.RunScope(t.RunIdentity('S', 'child', agent_path='root/worker'))
    with bind_run_scope(outer):
        assert make_invocation_context('S', 'R', None, None, ()).scope is outer
        with pytest.raises(ValueError, match='identity'):
            make_invocation_context('other', 'R', None, None, ())
        with pytest.raises(RuntimeError, match='original'):
            with bind_run_scope(child):
                assert current_run_scope() is child
                raise RuntimeError('original')
        assert current_run_scope() is outer
    assert current_run_scope() is None


def test_observers_cannot_borrow_a_different_publisher_object():
    t = types()
    publisher, other = object(), object()
    with pytest.raises(ValueError, match='publisher'):
        t.RunObservationPorts(publisher=publisher, turns=SimpleNamespace(publisher=other))


def test_approval_resume_can_start_without_a_model_call():
    t = types()
    from naiyue_test_agent.agent_runtime.invocation_context import make_invocation_context
    first = make_invocation_context('S', 'R', None, None, ())
    resumed = make_invocation_context('S', 'resume', None, None, ())
    first.invocation.model_call = t.ModelCallState('old')
    first.invocation.tool_batch = object()
    assert resumed.invocation.model_call is None and resumed.invocation.tool_batch is None
    assert resumed.scope.identity.run_id == 'resume'


def test_hidden_runtime_schema_preserves_references_without_serializing_dependencies():
    import warnings
    from langchain.tools import ToolRuntime
    from naiyue_test_agent.tools import create_session_read_tool
    t = types()
    scope = t.RunScope(t.RunIdentity('S', 'R'))
    context = t.RuntimeContext(scope)
    runtime = ToolRuntime(state={}, context=context, config={}, stream_writer=lambda _: None,
        tool_call_id='C', store=None)
    spec = create_session_read_tool()
    with warnings.catch_warnings(record=True) as recorded:
        execution = spec.tool.args_schema.model_validate({'path': 'a.txt', 'runtime': runtime})
        dumped = execution.model_dump()
    assert execution.runtime is runtime and execution.runtime.context is context
    assert dumped['runtime'] is None, 'Internal schema dumping must not traverse live services'
    assert recorded == []
    assert set(spec.tool.tool_call_schema.model_fields) == set(spec.input_schema.model_fields)
    assert 'runtime' not in spec.input_schema.model_fields


def test_framework_generated_tool_schema_accepts_only_the_typed_context_instance():
    from langchain.tools import ToolRuntime, tool
    from naiyue_test_agent.contracts.run_context import RuntimeContext, RunScope, RunIdentity
    context = RuntimeContext(RunScope(RunIdentity('S', 'R')))
    runtime = ToolRuntime(state={}, context=context, config={}, stream_writer=lambda _: None,
        tool_call_id='C', store=None)
    seen = []
    @tool
    def probe(runtime: ToolRuntime[RuntimeContext]) -> str:
        """Check framework injection without exposing its private fields."""
        seen.append(runtime.context)
        return runtime.context.scope.identity.session_id
    assert probe.invoke({'runtime': runtime}) == 'S'
    assert seen == [context] and seen[0] is context
    assert not probe.tool_call_schema.model_fields


def test_frozen_human_review_input_is_materialized_before_budget_cropping():
    from naiyue_test_agent.contracts.run_context import InvocationInputs
    from naiyue_test_agent.agent_runtime.tool_execution.permissions import prepare_review_input
    from test_auto_permission_review import prepared, reviewer, model
    human = ({'sequence': 0, 'id': 'old', 'content': 'OLD ' * 10000},
             {'sequence': 1, 'id': 'latest', 'content': 'run the tests'})
    inputs = InvocationInputs(human_review_messages=human)
    data = prepare_review_input([], prepared(), 'PROJECT_RULE', human_messages=inputs.human_review_messages)
    assert isinstance(data['human_messages'], list)
    fitted = reviewer(model([], window=2048), model([], window=2048))._fit_input(data)
    assert fitted['human_messages'] == [human[1]] and fitted['history_truncated']
    assert len(inputs.human_review_messages) == 2 and len(data['human_messages']) == 2


def test_runtime_consumes_the_declared_error_policy_port():
    from naiyue_test_agent.contracts.run_context import RunScope, RunIdentity, RuntimeContext
    from naiyue_test_agent.contracts.failures import FailureDescription
    from naiyue_test_agent.agent_runtime.errors import describe_failure_in_context
    seen = []

    class Policy:
        def describe(self, error, *, source, model_id=None):
            seen.append((error, source, model_id))
            return FailureDescription(source, 'transient', 'timeout', type(error).__name__)

    context = RuntimeContext(RunScope(RunIdentity('S', 'R'), errors=Policy()))
    failure = ValueError('synthetic')
    description = describe_failure_in_context(failure, context, source='model', model_id='A')
    assert seen == [(failure, 'model', 'A')]
    assert (description.category, description.reason) == ('transient', 'timeout')
