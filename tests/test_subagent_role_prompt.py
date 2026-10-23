"""Role indices follow the same per-request view as prompt and budget."""

from langchain_core.messages import AIMessage

from native_application_helpers import async_test
from naiyue_test_agent.tools import SubAgentSpec, create_builtin_tools
from naiyue_test_agent.settings import AppSettings
from test_subagent_execution import ChildModel, delegated_app


@async_test
async def test_role_index_is_short_dynamic_and_absent_when_tool_inactive(monkeypatch):
    from naiyue_test_agent.agent_runtime import request_preparation
    from test_dynamic_tools import DynamicModel, call
    root = DynamicModel(responses=[call(), AIMessage(content='first done'), AIMessage(content='inactive done')])
    child = ChildModel(replies=[])
    app, _ = await delegated_app(monkeypatch, child, root=root)
    budget_prompts = []
    compact = request_preparation._acompact_messages
    async def record_budget(messages, model, **kwargs):
        budget_prompts.append(kwargs['system_messages'][0].content)
        return await compact(messages, model, **kwargs)
    monkeypatch.setattr(request_preparation, '_acompact_messages', record_budget)
    sid = await app.sessions.create('A')
    await app.sessions.select_tools(sid, ['subagent', 'probe'])
    async def add_on_first_request(number):
        if number == 1:
            await app.subagents.register(SubAgentSpec('writer', 'WRITER_SHORT_DESCRIPTION', 'PRIVATE_WRITER_PROMPT', ()))
    root.on_request = add_on_first_request
    await app.runs.start(sid, [('user', 'inspect')])
    first, second = (messages[0].content for messages in root.seen)
    assert 'tester' in first and 'Verify tests' in first and 'CHILD_ROLE_PROMPT' not in first
    assert 'WRITER_SHORT_DESCRIPTION' not in first
    assert 'writer' in second and 'WRITER_SHORT_DESCRIPTION' in second and 'PRIVATE_WRITER_PROMPT' not in second
    await app.sessions.select_tools(sid, ['probe'])
    await app.runs.start(sid, [('user', 'without delegation')])
    assert 'WRITER_SHORT_DESCRIPTION' not in root.seen[-1][0].content
    assert 'Verify tests' not in root.seen[-1][0].content and child.seen == []
    assert budget_prompts == [messages[0].content for messages in root.seen]


def test_subagent_is_registered_with_public_schema_and_parallel_unknown_effects():
    spec = next(spec for spec in create_builtin_tools(AppSettings().tools) if spec.tool.name == 'subagent')
    assert set(spec.input_schema.model_fields) == {'agent', 'task', 'agent_id'}
    assert spec.execution_mode == 'parallel' and spec.side_effects == 'unknown'
    assert spec.tool.response_format == 'content_and_artifact'
