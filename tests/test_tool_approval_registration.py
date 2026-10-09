"""A new approval tool is registered without editing the generic runtime."""

from pathlib import Path

import pytest
from langchain.tools import ToolRuntime, tool
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import ToolException

from naiyue_test_agent import application
from naiyue_test_agent.agent_runtime.tool_approval import (
    ApprovalPolicy, ApprovalReview, OneUseApprovalGrants, approval_policies_for_tools,
    attach_approval,
)
from naiyue_test_agent.model_access import ModelCatalog


class ToolCapableFake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def test_registered_approval_tool_pauses_and_resumes_without_runtime_tool_branch(
    monkeypatch, tmp_path,
):
    calls = []

    @tool
    def approval_probe(value: str, runtime: ToolRuntime[dict, dict]) -> str:
        """Read a synthetic approved value."""
        grants = runtime.context.get('approval_grants', {}).get('approval_probe')
        if not isinstance(grants, OneUseApprovalGrants) or not grants.consume(
            runtime.tool_call_id, value,
        ):
            raise ToolException('Approval is required for this value.')
        calls.append(value)
        return value

    attach_approval(approval_probe, ApprovalPolicy(
        when=lambda request: isinstance(request.tool_call.get('args', {}).get('value'), str),
        description='Review the synthetic value',
        review=lambda _root, args: ApprovalReview(
            target=args['value'], grant_value=args['value'],
        ),
        grant_factory=OneUseApprovalGrants,
    ))
    model = ToolCapableFake(responses=[
        AIMessage(content='', tool_calls=[{
            'name': 'approval_probe', 'id': 'probe-1', 'args': {'value': 'approved'},
        }]),
        AIMessage(content='finished'),
    ])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    app = application.build_local_application(
        Path('synthetic.json'), tools=[approval_probe],
        system_prompt='', response_format=None,
    )
    session = app.create_workspace_session('A', tmp_path)

    pending = app.run(session, [('user', 'use probe')])
    assert calls == []
    assert pending['approval_requests'] == [{
        'tool_call_id': 'probe-1', 'tool_name': 'approval_probe', 'target': 'approved',
    }]
    result = app.resume_tool_approval(session, ['approve'])
    assert calls == ['approved']
    assert [(message.tool_call_id, message.content) for message in result['messages']
            if isinstance(message, ToolMessage)] == [('probe-1', 'approved')]
    assert result['messages'][-1].content == 'finished'


def test_invalid_approval_metadata_is_rejected_when_registering_tool(
    monkeypatch, tmp_path,
):
    @tool
    def approval_probe() -> str:
        """Tool with malformed approval metadata."""
        return 'unexpected'

    approval_probe.metadata = {'naiyue_approval': True}
    model = ToolCapableFake(responses=[AIMessage(content='done')])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    with pytest.raises(ValueError, match='approval'):
        application.build_local_application(
            Path('synthetic.json'), tools=[approval_probe],
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
        approval_policies_for_tools([same_name, same_name])
