"""Observe the framework-side model request without recording its contents."""

import hashlib
import logging
from types import SimpleNamespace

import pytest
from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from naiyue_test_agent import application
from naiyue_test_agent.agent_runtime import build_runtime_agent, invoke_agent_run
from naiyue_test_agent.agent_runtime.context_observation import observe_model_request
from naiyue_test_agent.model_access import ModelCatalog


class ToolCapableFake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def _context_events(caplog):
    return [record.model_request_context for record in caplog.records
            if hasattr(record, 'model_request_context')]


def test_each_model_call_observes_final_prompt_messages_tools_and_usage(
    monkeypatch, tmp_path, caplog,
):
    (tmp_path / 'AGENTS.md').write_text('SECRET_PROJECT', encoding='utf-8')

    @tool
    def read_probe(value: str) -> str:
        """Read a private probe value."""
        assert value == 'SECRET_ARGUMENT'
        return 'SECRET_RESULT'

    model = ToolCapableFake(responses=[
        AIMessage(content='', tool_calls=[{
            'name': 'read_probe', 'args': {'value': 'SECRET_ARGUMENT'}, 'id': 'call-1',
        }], usage_metadata={'input_tokens': 31, 'output_tokens': 4, 'total_tokens': 35}),
        AIMessage(content='done'),
    ])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    app = application.build_local_application(
        'synthetic.json', tools=[read_probe], system_prompt='BASE', response_format=None,
    )
    session_id = app.create_workspace_session('A', tmp_path)

    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        app.run(session_id, [('user', 'SECRET_REQUEST')])

    first, second = _context_events(caplog)
    assert first['event'] == second['event'] == 'model_request_context'
    assert first['session_id'] == second['session_id'] == session_id
    assert first['run_id'] == second['run_id']
    assert first['message_types'] == {'human': 1}
    assert second['message_types'] == {'human': 1, 'ai': 1, 'tool': 1}
    assert first['message_count'] == 1
    assert second['message_count'] == 3
    assert first['system_text_chars'] == second['system_text_chars'] == len(
        'BASE\n\n<project_instructions source="AGENTS.md">\n'
        'SECRET_PROJECT\n</project_instructions>'
    )
    assert first['message_text_chars'] == len('SECRET_REQUEST')
    assert second['message_text_chars'] == len('SECRET_REQUESTSECRET_RESULT')
    assert first['tool_call_count'] == 0
    assert second['tool_call_count'] == 1
    assert first['tool_count'] == second['tool_count'] == 1
    assert first['schema_status'] == second['schema_status'] == 'available'
    assert first['tool_schema_bytes'] == second['tool_schema_bytes'] > 0
    assert first['tool_schema_sha256'] == second['tool_schema_sha256']
    assert len(first['tool_schema_sha256']) == 64
    assert first['input_tokens'] == 31
    assert second['input_tokens'] is None
    assert first['outcome'] == second['outcome'] == 'returned'
    serialized = repr(_context_events(caplog)) + caplog.text
    for private in ('SECRET_PROJECT', 'SECRET_REQUEST', 'SECRET_ARGUMENT',
                    'SECRET_RESULT', 'Read a private probe value.'):
        assert private not in serialized


def test_failed_model_call_still_records_safe_request_and_propagates(caplog):
    failure = RuntimeError('SECRET_PROVIDER_FAILURE')

    class FailingModel(ToolCapableFake):
        def _generate(self, *_args, **_kwargs):
            raise failure

    model = FailingModel(responses=[AIMessage(content='unused')])
    agent = build_runtime_agent(
        model, tools=[], system_prompt='BASE', response_format=None,
        resolve_model=lambda _session_id: ('A', model),
        record_selection=lambda _session_id, _model_id: None,
    )

    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        with pytest.raises(RuntimeError) as caught:
            invoke_agent_run(agent, [('user', 'SECRET_REQUEST')],
                             session_id='session-1', thread_id='PRIVATE_THREAD', run_id='run-1')

    assert caught.value is failure
    assert _context_events(caplog) == [{
        'event': 'model_request_context', 'session_id': 'session-1', 'run_id': 'run-1',
        'message_count': 1, 'message_types': {'human': 1},
        'system_text_chars': 4, 'message_text_chars': len('SECRET_REQUEST'),
        'tool_call_count': 0, 'tool_count': 0,
        'schema_status': 'available', 'tool_schema_bytes': 2,
        'tool_schema_sha256': hashlib.sha256(b'[]').hexdigest(),
        'input_tokens': None, 'outcome': 'failed',
    }]
    assert 'SECRET_REQUEST' not in caplog.text
    assert 'SECRET_PROVIDER_FAILURE' not in caplog.text
    assert 'PRIVATE_THREAD' not in caplog.text


def test_unserializable_schema_warns_without_blocking_model_call(caplog):
    request = SimpleNamespace(
        runtime=SimpleNamespace(context={'session_id': 's1', 'run_id': 'r1'}),
        messages=[HumanMessage(content='SECRET_REQUEST')],
        system_message=SystemMessage(content='BASE'),
        tools=[{'private': object()}],
    )
    response = SimpleNamespace(result=[AIMessage(content='done')])
    seen = []

    def handler(received):
        seen.append(received)
        return response

    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        assert observe_model_request.wrap_model_call(request, handler) is response

    assert seen == [request]
    assert _context_events(caplog)[0]['schema_status'] == 'unavailable'
    assert _context_events(caplog)[0]['tool_schema_bytes'] is None
    assert 'model_request_tool_schema_unavailable error_type=TypeError' in caplog.text
    assert 'SECRET_REQUEST' not in caplog.text
