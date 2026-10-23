"""Run-time steering crosses the application queue and LangChain hooks."""

from naiyue_test_agent.application.async_application import AsyncApplication
import asyncio

from native_application_helpers import async_test, build_application

from naiyue_test_agent.tools.permissions import ToolPermission

from naiyue_test_agent.tools import ToolSpec
from naiyue_test_agent.application import prepare_tool_registry

import logging
from threading import Event

import pytest
from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from naiyue_test_agent import application
from naiyue_test_agent.model_access import ModelCatalog


class ToolCapableFake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


async def build_app(monkeypatch, model, tools=None):
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    return await build_application(
        'synthetic.json', tools=tools or [], system_prompt='', response_format=None,
    )


def run_in_worker(app, session_id):
    outcomes = []

    async def run():
        try:
            outcomes.append(('result', await app.arun(session_id, [('user', 'first-task')])))
        except Exception as error:
            outcomes.append(('error', error))

    worker = asyncio.create_task(run())

    return worker, outcomes


@async_test
async def test_steering_after_tool_result_is_fifo_and_stays_in_one_run(monkeypatch, caplog):
    tool_started, release_tool = Event(), Event()

    @tool
    def probe() -> str:
        """Read a synthetic probe value."""
        tool_started.set()
        assert release_tool.wait(timeout=3)
        return 'tool-result'

    model = ToolCapableFake(responses=[
        AIMessage(content='', tool_calls=[{'name': 'probe', 'args': {}, 'id': 'call-1'}]),
        AIMessage(content='answer-after-steering'),
    ])
    app = await build_app(monkeypatch, model, [ToolSpec(probe, probe.args_schema, permissions=ToolPermission(default='allow'))])
    session_id = await app.acreate_session('A')
    with caplog.at_level(logging.INFO):
        worker, outcomes = run_in_worker(app, session_id)
        try:
            assert await asyncio.to_thread(tool_started.wait, timeout=3)
            app.steer(session_id, 'private-steer-one')
            app.steer(session_id, 'private-steer-two')
        finally:
            release_tool.set()
            await asyncio.wait_for(asyncio.shield(worker), 3)

    assert worker.done()
    assert outcomes[0][0] == 'result', outcomes
    messages = outcomes[0][1]['messages']
    assert [(m.type, m.content) for m in messages] == [
        ('human', 'first-task'), ('ai', ''), ('tool', 'tool-result'),
        ('human', 'private-steer-one'), ('human', 'private-steer-two'),
        ('ai', 'answer-after-steering'),
    ]
    events = [r.run_event for r in caplog.records if hasattr(r, 'run_event')]
    assert [e['event'] for e in events] == ['run_started', 'run_completed']
    assert events[0]['run_id'] == events[1]['run_id']
    steering = [r.steering_event for r in caplog.records if hasattr(r, 'steering_event')]
    assert [e['event'] for e in steering] == [
        'steering_accepted', 'steering_accepted', 'steering_injected',
    ]
    assert steering[-1]['count'] == 2
    assert {e['run_id'] for e in steering} == {events[0]['run_id']}
    assert 'private-steer-one' not in caplog.text


@async_test
async def test_steering_during_final_model_response_continues_before_completion(monkeypatch):
    entered, release = Event(), Event()
    calls = []

    class BlockingModel(FakeMessagesListChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            calls.append([message.content for message in messages])
            if len(calls) == 1:
                entered.set()
                assert release.wait(timeout=3)
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    model = BlockingModel(responses=[AIMessage(content='first-answer'),
                                     AIMessage(content='second-answer')])
    app = await build_app(monkeypatch, model)
    session_id = await app.acreate_session('A')
    worker, outcomes = run_in_worker(app, session_id)
    try:
        assert await asyncio.to_thread(entered.wait, timeout=3)
        app.steer(session_id, 'private-steer')
    finally:
        release.set()
        await asyncio.wait_for(asyncio.shield(worker), 3)

    assert worker.done()
    assert outcomes[0][0] == 'result', outcomes
    assert [m.content for m in outcomes[0][1]['messages']] == [
        'first-task', 'first-answer', 'private-steer', 'second-answer',
    ]
    assert 'private-steer' not in calls[0]
    assert calls[1][-1] == 'private-steer'
    with pytest.raises(application.SessionBusyError):
        app.steer(session_id, 'late-message')
    with pytest.raises(ValueError):
        app.steer(session_id, '')
    with pytest.raises(KeyError):
        app.steer('unknown-session', 'message')


@async_test
async def test_failure_discards_pending_steering_and_releases_session(monkeypatch, caplog):
    entered, release = Event(), Event()
    error = RuntimeError('private-provider-failure')

    class FailingModel(FakeMessagesListChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            entered.set()
            assert release.wait(timeout=3)
            raise error

    app = await build_app(monkeypatch, FailingModel(responses=[]))
    session_id = await app.acreate_session('A')
    with caplog.at_level(logging.INFO):
        worker, outcomes = run_in_worker(app, session_id)
        try:
            assert await asyncio.to_thread(entered.wait, timeout=3)
            app.steer(session_id, 'private-pending')
        finally:
            release.set()
            await asyncio.wait_for(asyncio.shield(worker), 3)

    assert worker.done()
    assert outcomes == [('error', error)]
    steering = [r.steering_event for r in caplog.records if hasattr(r, 'steering_event')]
    assert [e['event'] for e in steering] == ['steering_accepted', 'steering_discarded']
    assert steering[-1]['count'] == 1
    assert 'private-pending' not in caplog.text
    assert 'private-provider-failure' not in caplog.text
    with pytest.raises(application.SessionBusyError):
        app.steer(session_id, 'not-running')


@async_test
async def test_natural_end_closes_steering_before_busy_gate_is_released():
    service = application.SessionService(ModelCatalog('A', {
        'A': FakeMessagesListChatModel(responses=[]),
    }))
    session_id = service.create_session('A')
    closed, release = Event(), Event()

    class PausedAtEndAgent:
        async def ainvoke(self, _input, *, context, config):
            assert service.finish_run_boundary(context.scope.identity.session_id) == (False, [])
            closed.set()
            assert await asyncio.to_thread(release.wait, timeout=3)
            return {'messages': []}

    app = AsyncApplication(service._catalog, service, PausedAtEndAgent(), prepare_tool_registry([]), system_prompt='')
    worker, outcomes = run_in_worker(app, session_id)
    try:
        assert await asyncio.to_thread(closed.wait, timeout=3)
        with pytest.raises(application.SessionBusyError):
            app.steer(session_id, 'arrived-after-close')
        with pytest.raises(application.SessionBusyError):
            await app.arun(session_id, [('user', 'second-task')])
    finally:
        release.set()
        try:
            await asyncio.wait_for(asyncio.shield(worker), 3)
        finally:
            await app.aclose()

    assert worker.done()
    assert outcomes == [('result', {'messages': []})]


@async_test
async def test_current_application_assembly_accepts_steering_callbacks():
    model = FakeMessagesListChatModel(responses=[AIMessage(content='answer')])
    catalog = ModelCatalog('A', {'A': model})
    service = application.SessionService(catalog)
    agent = service.assemble_runtime(
        None, system_prompt='', response_format=None)

    result = await service.arun(agent, service.create_session('A'), [('user', 'initial')])
    assert [message.content for message in result['messages']] == ['initial', 'answer']
