"""One local application owns its catalog, session service, agent, and checkpoint."""

from native_application_helpers import async_test, build_application

from naiyue_test_agent.tools.permissions import ToolPermission

from naiyue_test_agent.tools import ToolSpec

import logging
from pathlib import Path

import pytest
from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent import application
from naiyue_test_agent.model_access import ModelCatalog, ModelConfigError


class ToolCapableFake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


@async_test
async def test_local_application_wires_one_catalog_one_checkpoint_and_session_events(monkeypatch, caplog):
    models = {
        'A': ToolCapableFake(responses=[
            AIMessage(content='', tool_calls=[
                {'name': 'read_probe', 'args': {}, 'id': 'call-1'},
            ]),
            AIMessage(content='A-one'),
            AIMessage(content='A-other'),
        ]),
        'B': ToolCapableFake(responses=[AIMessage(content='B-two')]),
    }
    catalog = ModelCatalog('A', models)
    loaded_paths = []

    def load_catalog(path):
        loaded_paths.append(path)
        return catalog

    monkeypatch.setattr(application, 'load_model_catalog', load_catalog, raising=False)

    @tool
    def read_probe() -> str:
        """Read a synthetic probe value."""
        return 'private-tool-result'

    config_path = Path('synthetic-models.json')
    app = await build_application(
        config_path, tools=[ToolSpec(read_probe, read_probe.args_schema, permissions=ToolPermission(default='allow'))], system_prompt='', response_format=None,
    )
    assert loaded_paths == [config_path]
    assert app.root.models.default_id == 'A'
    assert not hasattr(app, 'thread_id')

    first_session = await app.host.sessions.create('A')
    other_session = await app.host.sessions.create('A')
    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        first = await app.host.runs.start(first_session, [('user', 'private-first')])
        await app.host.sessions.select_model(first_session, 'B')
        second = await app.host.runs.start(first_session, [('user', 'private-second')])
        other = await app.host.runs.start(other_session, [('user', 'private-other')])

    assert [m.type for m in first['messages']] == ['human', 'ai', 'tool', 'ai']
    assert [m.content for m in second['messages']] == [
        'private-first', '', 'private-tool-result', 'A-one',
        'private-second', 'B-two',
    ]
    assert [m.content for m in other['messages']] == ['private-other', 'A-other']
    assert first['messages'][1].tool_calls[0]['id'] == first['messages'][2].tool_call_id
    assert isinstance(first['messages'][2], ToolMessage)
    assert loaded_paths == [config_path]

    run_events = [r.run_event for r in caplog.records if hasattr(r, 'run_event')]
    model_events = [r.model_selection for r in caplog.records if hasattr(r, 'model_selection')]
    tool_events = [r.tool_event for r in caplog.records if hasattr(r, 'tool_event')]
    assert [event['session_id'] for event in run_events] == [
        first_session, first_session, first_session, first_session,
        other_session, other_session,
    ]
    assert [event['event'] for event in tool_events] == ['tool_started', 'tool_completed']
    assert [event['model_id'] for event in model_events] == ['A', 'A', 'B', 'A']
    assert [event['run_id'] for event in tool_events] == [run_events[0]['run_id']] * 2
    assert 'private-first' not in caplog.text
    assert 'private-tool-result' not in caplog.text


@async_test
async def test_local_application_stops_at_configuration_failure(monkeypatch):
    loaded_paths = []

    def fail_loading(path):
        loaded_paths.append(path)
        raise ModelConfigError('synthetic invalid configuration')

    monkeypatch.setattr(application, 'load_model_catalog', fail_loading, raising=False)
    with pytest.raises(ModelConfigError, match='synthetic invalid configuration'):
        await build_application(
            Path('broken.json'), tools=[], system_prompt='', response_format=None,
        )
    assert loaded_paths == [Path('broken.json')]
