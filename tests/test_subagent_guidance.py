"""The parent receives actionable delegation guidance through the real tool view."""
from langchain_core.messages import AIMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

from native_application_helpers import async_test
from naiyue_test_agent.tools.subagent import create_subagent_tool
from test_subagent_execution import ChildModel, delegated_app


def test_public_delegation_contract_explains_roles_tasks_and_continuation():
    function = convert_to_openai_tool(create_subagent_tool().tool)['function']
    assert 'user or a loaded Skill explicitly requires delegation' in function['description']
    assert 'Independent tasks may be delegated in parallel' in function['description']
    parameters = function['parameters']
    assert set(parameters['properties']) == {'agent', 'task', 'agent_id'}
    assert parameters['required'] == ['agent', 'task']
    descriptions = {key: value['description'] for key, value in parameters['properties'].items()}
    assert 'registered role name' in descriptions['agent']
    assert all(word in descriptions['task'] for word in ('context', 'input locations', 'scope', 'deliverables'))
    assert 'previously returned' in descriptions['agent_id'] and 'new instance' in descriptions['agent_id']


@async_test
async def test_real_parent_request_gets_guidance_without_misleading_permission_or_history_text(monkeypatch):
    child = ChildModel(replies=[AIMessage(content='verified report')])
    app, root = await delegated_app(monkeypatch, child)
    sid = await app.host.sessions.create('A')
    await app.host.runs.start(sid, [('user', 'Follow the Skill and delegate the verification.')])
    prompt = root.seen[0][0].content
    assert 'tester: Verify tests' in prompt
    assert 'user or a loaded Skill explicitly requires delegation' in prompt
    assert 'A new instance has independent conversation history' in prompt
    assert 'does not automatically see the full parent conversation' in prompt
    assert 'pass its agent_id to continue an existing instance' in prompt
    assert 'The parent continues after receiving the report' in prompt
    assert 'Each delegation starts a new foreground task' not in prompt
    assert 'delegation does not grant permission to internal actions' not in prompt
    assert 'CHILD_ROLE_PROMPT' not in prompt
    assert 'CHILD_ROLE_PROMPT' in child.seen[0][0].content
