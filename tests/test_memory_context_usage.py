"""Memory preparation participates in existing content-free context accounting."""

from runtime_context_helpers import RunIdentity, ModelCallState, typed_context

from langchain_core.messages import AIMessage, HumanMessage

from naiyue_test_agent.agent_runtime.context_usage import (
    compose_model_prompt, estimate_context, request_snapshot, finish_snapshot, read_snapshot,
)


def test_memory_and_directory_are_additive_validated_system_children():
    system = compose_model_prompt('BASE', [
        dict(id='memory', text='PRIVATE_MEMORY_INDEX', count=3),
        dict(id='session_directory', text='PRIVATE_SESSION_DIRECTORY', count=8),
    ], [])
    estimate = estimate_context(None, system, [HumanMessage(content='question')], [])
    children = next(group for group in estimate['groups'] if group['id'] == 'system')['children']
    assert [(part['id'], part.get('count')) for part in children] == [
        ('base', None), ('memory', 3), ('session_directory', 8)]
    snapshot = finish_snapshot(request_snapshot(typed_context(model_call=ModelCallState('call', 'A')), estimate))
    assert read_snapshot(snapshot) == snapshot
    assert 'PRIVATE_' not in str(snapshot)
    assert sum(part['tokens'] for part in children) == next(group for group in estimate['groups'] if group['id'] == 'system')['tokens']
