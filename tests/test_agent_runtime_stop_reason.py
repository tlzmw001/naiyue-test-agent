"""A truncated model response cannot execute its parsed tool calls."""

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import Field

from naiyue_test_agent import application
from naiyue_test_agent.agent_runtime import _record_tool_call
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.model_access.stop_reason import MODEL_STOP_REASON_KEY


class RecordingFake(FakeMessagesListChatModel):
    seen_tool_results: list[list[tuple[str, str, str]]] = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen_tool_results.append([
            (message.tool_call_id, message.status, str(message.content))
            for message in messages if isinstance(message, ToolMessage)
        ])
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def test_truncated_call_gets_error_result_then_model_can_reissue(monkeypatch, caplog):
    model = RecordingFake(responses=[
        AIMessage(
            content='',
            tool_calls=[{'name': 'write_probe', 'args': {'value': 'private-cut'}, 'id': 'cut'}],
            response_metadata={'finish_reason': 'length'},
        ),
        AIMessage(
            content='',
            tool_calls=[{'name': 'write_probe', 'args': {'value': 'complete'}, 'id': 'good'}],
            response_metadata={'finish_reason': 'tool_calls'},
        ),
        AIMessage(content='finished', response_metadata={'finish_reason': 'stop'}),
    ])
    executed = []

    @tool
    def write_probe(value: str) -> str:
        """Record the value given to an actual tool execution."""
        executed.append(value)
        return 'written'

    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    app = application.build_local_application(
        Path('synthetic.json'), tools=[write_probe],
        system_prompt='', response_format=None,
    )
    session_id = app.create_session('A')

    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        result = app.run(session_id, [('user', 'write a probe value')])

    messages = result['messages']
    tool_results = [message for message in messages if isinstance(message, ToolMessage)]
    assert executed == ['complete']
    assert [(item.tool_call_id, item.status) for item in tool_results] == [
        ('cut', 'error'), ('good', 'success'),
    ]
    assert 'not executed' in str(tool_results[0].content)
    assert model.seen_tool_results[1] == [
        ('cut', 'error', str(tool_results[0].content)),
    ]
    assert messages[1].response_metadata['finish_reason'] == 'length'
    assert messages[1].response_metadata[MODEL_STOP_REASON_KEY] == 'length'
    assert messages[-1].content == 'finished'
    tool_events = [record.tool_event['event'] for record in caplog.records
                   if hasattr(record, 'tool_event')]
    assert tool_events == ['tool_started', 'tool_failed', 'tool_started', 'tool_completed']
    assert 'private-cut' not in caplog.text


def test_every_call_in_one_truncated_response_is_rejected(monkeypatch):
    model = RecordingFake(responses=[
        AIMessage(
            content='',
            tool_calls=[
                {'name': 'write_probe', 'args': {'value': 'first'}, 'id': 'cut-1'},
                {'name': 'write_probe', 'args': {'value': 'second'}, 'id': 'cut-2'},
            ],
            response_metadata={'stop_reason': 'max_tokens'},
        ),
        AIMessage(content='retry later', response_metadata={'stop_reason': 'end_turn'}),
    ])
    executed = []

    @tool
    def write_probe(value: str) -> str:
        """Record the value given to an actual tool execution."""
        executed.append(value)
        return 'written'

    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    app = application.build_local_application(
        Path('synthetic.json'), tools=[write_probe],
        system_prompt='', response_format=None,
    )
    result = app.run(app.create_session('A'), [('user', 'probe')])

    tool_results = [item for item in result['messages'] if isinstance(item, ToolMessage)]
    assert executed == []
    assert {(item.tool_call_id, item.status) for item in tool_results} == {
        ('cut-1', 'error'), ('cut-2', 'error'),
    }
    assert {item[0] for item in model.seen_tool_results[1]} == {'cut-1', 'cut-2'}


def test_claude_context_window_truncation_rejects_tool_through_application(monkeypatch):
    model = RecordingFake(responses=[
        AIMessage(
            content='',
            tool_calls=[{'name': 'write_probe', 'args': {'value': 'partial'}, 'id': 'cut'}],
            response_metadata={'stop_reason': 'model_context_window_exceeded'},
        ),
        AIMessage(content='retry later', response_metadata={'stop_reason': 'end_turn'}),
    ])
    executed = []

    @tool
    def write_probe(value: str) -> str:
        """Record a synthetic tool invocation."""
        executed.append(value)
        return 'written'

    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    app = application.build_local_application(
        Path('synthetic.json'), tools=[write_probe],
        system_prompt='', response_format=None,
    )
    result = app.run(app.create_session('A'), [('user', 'probe')])

    tool_results = [item for item in result['messages'] if isinstance(item, ToolMessage)]
    assert executed == []
    assert [(item.tool_call_id, item.status) for item in tool_results] == [('cut', 'error')]
    assert model.seen_tool_results[1] == [
        ('cut', 'error', str(tool_results[0].content)),
    ]
    assert result['messages'][1].response_metadata['stop_reason'] == 'model_context_window_exceeded'
    assert result['messages'][1].response_metadata[MODEL_STOP_REASON_KEY] == 'length'


@pytest.mark.parametrize('messages', [
    [AIMessage(content='', tool_calls=[{'name': 'write_probe', 'args': {}, 'id': 'original'}],
               response_metadata={MODEL_STOP_REASON_KEY: 'length'})],
    [AIMessage(content='', tool_calls=[{'name': 'write_probe', 'args': {}, 'id': 'original'}],
               response_metadata={MODEL_STOP_REASON_KEY: 'tool_use'})],
    [],
])
def test_tool_request_without_matching_latest_model_call_fails_closed(messages, caplog):
    request = SimpleNamespace(
        tool_call={'name': 'write_probe', 'args': {}, 'id': 'other'},
        state={'messages': messages},
        runtime=SimpleNamespace(context={'run_id': 'run-1'}),
    )
    executed = []

    def handler(_request):
        executed.append(True)
        return ToolMessage(content='written', tool_call_id='other')

    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        result = _record_tool_call.wrap_tool_call(request, handler)

    assert executed == []
    assert isinstance(result, ToolMessage)
    assert result.tool_call_id == 'other'
    assert result.status == 'error'
    assert 'not executed' in str(result.content)
    assert [record.tool_event['event'] for record in caplog.records
            if hasattr(record, 'tool_event')] == ['tool_started', 'tool_failed']
