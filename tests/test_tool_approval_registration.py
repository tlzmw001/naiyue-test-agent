"""A new approval tool is registered without editing the generic runtime."""

from naiyue_test_agent.contracts.run_context import RuntimeContext

from native_application_helpers import async_test, build_application

from naiyue_test_agent.tools.permissions import ToolPermission

from naiyue_test_agent.tools import ToolSpec

from pathlib import Path

import pytest
from langchain.tools import ToolRuntime, tool
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import BaseModel, ConfigDict
from naiyue_test_agent.tools.schemas import build_tool_schemas
from naiyue_test_agent.application import prepare_tool_registry

from naiyue_test_agent import application
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.tools.permissions import ToolPermission, OneUseApprovalGrants, require_tool_permit



class ToolCapableFake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


class ApprovalInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    value: str


@async_test
async def test_registered_approval_tool_pauses_and_resumes_without_runtime_tool_branch(
    monkeypatch, tmp_path,
):
    calls = []
    _, execution = build_tool_schemas('ApprovalProbeInput', ApprovalInput)

    @tool(args_schema=execution)
    def approval_probe(value: str, runtime: ToolRuntime[RuntimeContext]) -> str:
        """Read a synthetic approved value."""
        assert require_tool_permit(runtime)['args']['value'] == value
        calls.append(value)
        return value

    model = ToolCapableFake(responses=[
        AIMessage(content='', tool_calls=[{
            'name': 'approval_probe', 'id': 'probe-1', 'args': {'value': 'approved'},
        }]),
        AIMessage(content='finished'),
    ])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    app = await build_application(
        Path('synthetic.json'), tools=[ToolSpec(approval_probe, ApprovalInput, permissions=ToolPermission())],
        system_prompt='', response_format=None,
    )
    session = await app.sessions.create_workspace('A', tmp_path)

    pending = await app.runs.start(session, [('user', 'use probe')])
    assert calls == []
    assert pending['approval_requests'] == [{
        'tool_call_id': 'probe-1', 'tool_name': 'approval_probe', 'target': '{"value": "approved"}',
    }]
    result = await app.runs.resume_approval(session, ['approve'])
    assert calls == ['approved']
    assert [(message.tool_call_id, message.content) for message in result['messages']
            if isinstance(message, ToolMessage)] == [('probe-1', 'approved')]
    assert result['messages'][-1].content == 'finished'


@async_test
async def test_invalid_approval_metadata_is_rejected_when_registering_tool(
    monkeypatch, tmp_path,
):
    @tool
    def approval_probe() -> str:
        """Tool with malformed approval metadata."""
        return 'unexpected'

    invalid_permission = None
    model = ToolCapableFake(responses=[AIMessage(content='done')])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    with pytest.raises(ValueError, match='permission'):
        await build_application(
            Path('synthetic.json'), tools=[ToolSpec(approval_probe, approval_probe.args_schema, permissions=invalid_permission)],
            system_prompt='', response_format=None,
        )


def test_approval_grant_with_missing_id_cannot_match_none_subject():
    grants = OneUseApprovalGrants({})
    assert grants.consume('missing', None) is False


def test_duplicate_tool_names_are_rejected_at_registration():
    @tool
    def same_name() -> str:
        """A tool registered twice by mistake."""
        return 'ok'

    with pytest.raises(ValueError, match='Duplicate tool name'):
        prepare_tool_registry([ToolSpec(same_name, same_name.args_schema, permissions=ToolPermission(default='allow')), ToolSpec(same_name, same_name.args_schema, permissions=ToolPermission(default='allow'))])
