"""Configured applications exercise routing and history through the formal entry."""

from native_application_helpers import async_test
from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from naiyue_test_agent.application.async_application import build_async_local_application
from naiyue_test_agent.tools import ToolSpec
from naiyue_test_agent.tools.permissions import ToolPermission
from naiyue_test_agent.tools.settings import ToolSettings
from test_model_access import ToolCapableFake, write_config


@async_test
async def test_configured_agent_uses_file_and_switches_after_tool_result(tmp_path, monkeypatch):
    holder = {}
    received_settings = {}
    models = {
        'model-first': ToolCapableFake(responses=[AIMessage(
            content='', tool_calls=[{'name': 'switch_to_second', 'args': {}, 'id': 'call-1'}]
        )]),
        'model-second': ToolCapableFake(responses=[AIMessage(content='answer-from-second')]),
    }
    def fake_chat_openai(*, model, **kwargs):
        received_settings[model] = kwargs
        return models[model]

    monkeypatch.setattr('naiyue_test_agent.model_access.ChatOpenAI', fake_chat_openai)
    config = {
        'schema_version': 1,
        'default_model_id': 'first',
        'providers': {'p': {'kind': 'openai', 'api_key': 'test-key'}},
        'models': {
            'first': {'provider_id': 'p', 'model': 'model-first', 'api': 'chat_completions',
                      'thinking': {'supported_levels': ['off', 'low', 'high'],
                                   'default_level': 'high'}},
            'second': {'provider_id': 'p', 'model': 'model-second', 'api': 'chat_completions'},
        },
    }
    selections = []

    @tool
    async def switch_to_second() -> str:
        """Select the second configured model for this test session."""
        await holder['app'].aselect_model(holder['session'], 'second')
        return 'selected second'

    app = await build_async_local_application(
        write_config(tmp_path, config),
        tools=[ToolSpec(switch_to_second, switch_to_second.args_schema,
                        permissions=ToolPermission(default='allow'))],
        system_prompt='Test model selection.', response_format=None, settings=ToolSettings())
    try:
        session = await app.acreate_session('first')
        holder.update(app=app, session=session)
        app.subscribe_events(lambda event: selections.append((event.session_id, event.data.model_id))
                             if event.event_type == 'model_call_started' else None)
        result = await app.arun(session, [('user', 'switch, then answer')])
    finally:
        await app.aclose()

    assert selections == [(session, 'first'), (session, 'second')]
    assert [message.type for message in result['messages']] == ['human', 'ai', 'tool', 'ai']
    assert result['messages'][2].tool_call_id == result['messages'][1].tool_calls[0]['id']
    assert result['messages'][-1].content == 'answer-from-second'
    assert received_settings['model-first']['reasoning_effort'] == 'high'
    assert received_settings['model-first']['timeout'] == 60
    assert received_settings['model-first']['max_retries'] == 2
    assert 'reasoning_effort' not in received_settings['model-second']
    assert received_settings['model-second']['timeout'] == 60
    assert received_settings['model-second']['max_retries'] == 2


@async_test
async def test_configured_agent_forwards_checkpointer_for_continuous_messages(tmp_path, monkeypatch):
    model = FakeMessagesListChatModel(responses=[
        AIMessage(content='first answer'), AIMessage(content='second answer'),
    ])
    monkeypatch.setattr('naiyue_test_agent.model_access.ChatOpenAI', lambda **_kwargs: model)
    config_path = write_config(tmp_path, {
        'schema_version': 1,
        'default_model_id': 'first',
        'providers': {'p': {'kind': 'openai', 'api_key': 'test-key'}},
        'models': {'first': {'provider_id': 'p', 'model': 'model-first', 'api': 'chat_completions'}},
    })
    app = await build_async_local_application(config_path, tools=[], system_prompt='',
                                              response_format=None, settings=ToolSettings())
    try:
        session = await app.acreate_session('first')
        thread = app._sessions.get_thread_id(session)
        await app.arun(session, [('user', 'first question')])
        result = await app.arun(session, [('user', 'second question')])
        assert app._sessions.get_thread_id(session) == thread
    finally:
        await app.aclose()

    assert [message.content for message in result['messages']] == [
        'first question', 'first answer', 'second question', 'second answer',
    ]
