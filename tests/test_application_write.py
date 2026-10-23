"""A shared Agent isolates sessions when file writes contend or become uncertain."""

from native_application_helpers import async_test, build_application

import asyncio
from pathlib import Path
from threading import Event

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from test_write_tool import make_app
from naiyue_test_agent import application
from naiyue_test_agent.tools import create_session_write_tool
from naiyue_test_agent.tools import write as write_module
from naiyue_test_agent.model_access import ModelCatalog


def call(name, args, call_id):
    return {'name': name, 'args': args, 'id': call_id}


def results(result):
    return [item for item in result['messages'] if isinstance(item, ToolMessage)]


class SessionWriteModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return 'session-write-test'

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, *args, **kwargs):
        if isinstance(messages[-1], ToolMessage):
            message = AIMessage(content='done')
        else:
            prompt = next(item.content for item in reversed(messages)
                          if isinstance(item, HumanMessage))
            message = AIMessage(content='', tool_calls=[call('write', {
                'path': 'shared.md', 'content': str(prompt),
            }, f'write-{prompt}')])
        return ChatResult(generations=[ChatGeneration(message=message)])


@async_test
async def test_two_application_sessions_contending_for_same_target_get_a_conflict(
    monkeypatch, tmp_path,
):
    model = SessionWriteModel()
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    app = await build_application(
        Path('synthetic.json'), tools=[create_session_write_tool()],
        system_prompt='', response_format=None,
    )
    first_session = await app.sessions.create_workspace('A', tmp_path)
    second_session = await app.sessions.create_workspace('A', tmp_path)
    entered = Event()
    release = Event()
    real_replace = write_module.os.replace

    def hold_first_write(source, target):
        if not entered.is_set():
            entered.set()
            assert release.wait(5)
        return real_replace(source, target)

    monkeypatch.setattr(write_module.os, 'replace', hold_first_write)
    first = asyncio.create_task(app.runs.start(first_session, [('user', 'FIRST')]))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        second = await app.runs.start(second_session, [('user', 'SECOND')])
    finally:
        release.set()
        first_result = await asyncio.wait_for(asyncio.shield(first), 5)

    assert results(first_result)[0].status == 'success'
    assert results(second)[0].status == 'error'
    assert 'busy' in results(second)[0].content.lower()
    assert (tmp_path / 'shared.md').read_text() == 'FIRST'


@async_test
async def test_unknown_write_outcome_keeps_sessions_usable_with_explicit_feedback(
    monkeypatch, tmp_path,
):
    model = SessionWriteModel()
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    app = await build_application(
        Path('synthetic.json'), tools=[create_session_write_tool()],
        system_prompt='', response_format=None,
    )
    uncertain_session = await app.sessions.create_workspace('A', tmp_path)
    other_session = await app.sessions.create_workspace('A', tmp_path)
    real_replace = write_module.os.replace

    def unknown_replace(_source, _target):
        raise OSError('PRIVATE_COMMIT_DETAIL')

    monkeypatch.setattr(write_module.os, 'replace', unknown_replace)
    uncertain = await app.runs.start(uncertain_session, [('user', 'FIRST')])
    assert results(uncertain)[0].status == 'error'
    assert 'outcome is unknown' in results(uncertain)[0].content
    assert not (tmp_path / 'shared.md').exists()
    monkeypatch.setattr(write_module.os, 'replace', real_replace)

    retried = await app.runs.start(uncertain_session, [('user', 'retry')])
    assert results(retried)[-1].status == 'success'
    other_result = await app.runs.start(other_session, [('user', 'SECOND')])
    assert results(other_result)[0].status == 'success'
    assert (tmp_path / 'shared.md').read_text() == 'SECOND'


@async_test
async def test_session_write_fails_closed_without_workspace(monkeypatch, tmp_path):
    app, model = await make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[call('write', {
            'path': str(tmp_path / 'note.md'), 'content': 'PRIVATE',
        }, 'no-workspace')]),
        AIMessage(content='denied'),
    ])

    result = await app.runs.start(await app.sessions.create('A'), [('user', 'write without workspace')])

    message = results(result)[0]
    assert message.tool_call_id == 'no-workspace'
    assert message.status == 'error'
    assert 'workspace' in message.content.lower()
    assert message in model.seen[1]
    assert not (tmp_path / 'note.md').exists()
