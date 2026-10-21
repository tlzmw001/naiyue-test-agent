"""Reject unusable model messages before LangChain can route their tools."""

from native_application_helpers import async_test, build_application

from naiyue_test_agent.tools.permissions import ToolPermission

from naiyue_test_agent.tools import ToolSpec

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain.agents.middleware import ModelResponse
from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage

from naiyue_test_agent import application
from naiyue_test_agent.agent_runtime import make_model_selection_middleware
from naiyue_test_agent.model_access import ModelCatalog


class ToolCapableFake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


VALID_CALL = {'name': 'write_probe', 'args': {'value': 'private-argument'}, 'id': 'call-1'}
INVALID_CALL = {'name': 'write_probe', 'args': '{bad', 'id': 'call-2',
                'error': 'private-parse-error'}


@pytest.mark.parametrize(('first', 'reason'), [
    (AIMessage(content='private-filtered', tool_calls=[VALID_CALL],
               response_metadata={'finish_reason': 'content_filter'},
               usage_metadata={'input_tokens': 7, 'output_tokens': 3, 'total_tokens': 10}),
     'stop_reason_error'),
    (AIMessage(content='private-failed',
               response_metadata={'status': 'failed'}), 'stop_reason_error'),
    (AIMessage(content='', invalid_tool_calls=[INVALID_CALL],
               response_metadata={'finish_reason': 'tool_calls'}), 'invalid_tool_calls'),
    (AIMessage(content='', tool_calls=[VALID_CALL], invalid_tool_calls=[INVALID_CALL],
               response_metadata={'finish_reason': 'tool_calls'}), 'invalid_tool_calls'),
    (AIMessage(content='', tool_calls=[VALID_CALL], invalid_tool_calls=[INVALID_CALL],
               response_metadata={'finish_reason': 'content_filter'}), 'stop_reason_error'),
    (AIMessage(content='', response_metadata={'finish_reason': 'tool_calls'}),
     'missing_tool_calls'),
])
@async_test
async def test_rejected_model_message_fails_run_without_executing_any_tool(
    monkeypatch, caplog, first, reason,
):
    model = ToolCapableFake(responses=[first, AIMessage(content='unused')])
    executed = []

    @tool
    def write_probe(value: str) -> str:
        """Record a synthetic tool execution."""
        executed.append(value)
        return 'written'

    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    app = await build_application(
        Path('synthetic.json'), tools=[ToolSpec(write_probe, write_probe.args_schema, permissions=ToolPermission(default='allow'))], system_prompt='', response_format=None,
    )
    session_id = await app.acreate_session('A')

    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        with pytest.raises(RuntimeError) as caught:
            await app.arun(session_id, [('user', 'private-question')])

    assert type(caught.value).__name__ == 'ModelResponseRejected'
    assert caught.value.reason == reason
    assert executed == []
    run_events = [record.run_event for record in caplog.records
                  if hasattr(record, 'run_event')]
    assert [event['event'] for event in run_events] == ['run_started', 'run_failed']
    assert run_events[0]['run_id'] == run_events[1]['run_id']
    assert run_events[0]['session_id'] == run_events[1]['session_id'] == session_id
    model_failures = [record.model_failure for record in caplog.records
                      if hasattr(record, 'model_failure')]
    assert len(model_failures) == 1
    assert model_failures[0]['category'] == 'model_response_rejected'
    assert model_failures[0]['reason'] == reason
    assert model_failures[0]['run_id'] == run_events[0]['run_id']
    model_usages = [record.model_usage for record in caplog.records
                    if hasattr(record, 'model_usage')]
    assert [usage['event'] for usage in model_usages] == ['model_call_usage']
    if first.usage_metadata:
        assert model_usages[0]['input_tokens'] == 7
    assert not any(hasattr(record, 'tool_event') for record in caplog.records)
    for secret in ('private-question', 'private-filtered', 'private-failed',
                   'private-argument', 'private-parse-error'):
        assert secret not in caplog.text + str(caught.value)


@async_test
@pytest.mark.parametrize('result_messages', [[], [HumanMessage(content='private-unexpected')]])
async def test_model_hook_rejects_response_without_ai_message(result_messages, caplog):
    async def async_handler_0(_request):
        return ModelResponse(result=result_messages)
    model = ToolCapableFake(responses=[])
    middleware = make_model_selection_middleware(
        lambda _session, agent_path='root': ('A', model),
    )
    request = SimpleNamespace(runtime=SimpleNamespace(context={
        'session_id': 's', 'run_id': 'r',
    }))
    request.override = lambda **_kwargs: request
    await middleware.abefore_model({}, request.runtime)

    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        with pytest.raises(RuntimeError) as caught:
            (await middleware.awrap_model_call(
                request, async_handler_0,
            ))

    assert type(caught.value).__name__ == 'ModelResponseRejected'
    assert caught.value.reason == 'missing_ai_message'
    assert [record.model_failure['reason'] for record in caplog.records
            if hasattr(record, 'model_failure')] == ['missing_ai_message']
    assert 'private-unexpected' not in caplog.text + str(caught.value)


@async_test
async def test_session_can_continue_after_rejected_response(monkeypatch):
    model = ToolCapableFake(responses=[
        AIMessage(content='private-bad-response', response_metadata={'status': 'failed'}),
        AIMessage(content='recovered', response_metadata={'finish_reason': 'stop'}),
    ])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    app = await build_application(
        Path('synthetic.json'), tools=[], system_prompt='', response_format=None,
    )
    session_id = await app.acreate_session('A')

    with pytest.raises(RuntimeError):
        await app.arun(session_id, [('user', 'first')])
    result = await app.arun(session_id, [('user', 'second')])

    assert [(type(message).__name__, message.content) for message in result['messages']] == [
        ('HumanMessage', 'first'), ('HumanMessage', 'second'),
        ('AIMessage', 'recovered'),
    ]
