"""Cooperative sync operation settlement under the unified stop contract."""

from naiyue_test_agent.application.async_application import AsyncApplication
import asyncio

from native_application_helpers import async_test, build_application

from naiyue_test_agent.tools.permissions import ToolPermission

from naiyue_test_agent.application import prepare_tool_registry

from naiyue_test_agent.tools import ToolSpec

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
            outcomes.append(('result', await app.arun(session_id, [('user', 'initial')])))
        except Exception as error:
            outcomes.append(('error', error))

    worker = asyncio.create_task(run())

    return worker, outcomes


@async_test
async def test_stop_after_current_answer_discards_pending_messages(monkeypatch, caplog):
    entered, release = Event(), Event()

    class BlockingModel(FakeMessagesListChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            entered.set()
            assert release.wait(timeout=3)
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    app = await build_app(monkeypatch, BlockingModel(responses=[AIMessage(content='answer')]))
    session_id = await app.acreate_session('A')
    with caplog.at_level(logging.INFO):
        worker, outcomes = run_in_worker(app, session_id)
        try:
            assert await asyncio.to_thread(entered.wait, timeout=3)
            app.steer(session_id, 'private-steering')
            app.follow_up(session_id, 'private-follow-up')
            app.request_stop(session_id)
            app.request_stop(session_id)
            with pytest.raises(application.SessionBusyError):
                app.steer(session_id, 'too-late')
        finally:
            release.set()
            await asyncio.wait_for(asyncio.shield(worker), 3)

    assert worker.done()
    assert outcomes[0][0] == 'result', outcomes
    assert [m.content for m in outcomes[0][1]['messages']] == ['initial', 'answer']
    assert outcomes[0][1]['run_stopped'] is True
    run_events = [r.run_event['event'] for r in caplog.records if hasattr(r, 'run_event')]
    assert run_events == ['run_started', 'run_stopped']
    stop_events = [r.stop_event['event'] for r in caplog.records if hasattr(r, 'stop_event')]
    assert stop_events == ['stop_requested']
    steering = [r.steering_event['event'] for r in caplog.records
                if hasattr(r, 'steering_event')]
    follow_up = [r.follow_up_event['event'] for r in caplog.records
                 if hasattr(r, 'follow_up_event')]
    assert steering == ['steering_accepted', 'steering_discarded']
    assert follow_up == ['follow_up_accepted', 'follow_up_discarded']
    assert 'private-steering' not in caplog.text
    assert 'private-follow-up' not in caplog.text


@async_test
async def test_stop_waits_for_current_tool_batch_and_skips_next_model(monkeypatch):
    first_started, second_started, release = Event(), Event(), Event()
    model_inputs = []

    @tool
    def first_tool() -> str:
        """Return first synthetic value."""
        first_started.set()
        assert release.wait(timeout=3)
        return 'first-result'

    @tool
    def second_tool() -> str:
        """Return second synthetic value."""
        second_started.set()
        assert release.wait(timeout=3)
        return 'second-result'

    class RecordingModel(ToolCapableFake):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            model_inputs.append(messages)
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    model = RecordingModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'first_tool', 'args': {}, 'id': 'call-one'},
        {'name': 'second_tool', 'args': {}, 'id': 'call-two'},
    ]), AIMessage(content='must-not-be-called')])
    app = await build_app(monkeypatch, model, [ToolSpec(first_tool, first_tool.args_schema, permissions=ToolPermission(default='allow')), ToolSpec(second_tool, second_tool.args_schema, permissions=ToolPermission(default='allow'))])
    session_id = await app.acreate_session('A')
    worker, outcomes = run_in_worker(app, session_id)
    try:
        assert await asyncio.to_thread(first_started.wait, timeout=3)
        assert await asyncio.to_thread(second_started.wait, timeout=3)
        app.request_stop(session_id)
    finally:
        release.set()
        await asyncio.wait_for(asyncio.shield(worker), 3)

    assert worker.done()
    assert outcomes[0][0] == 'result', outcomes
    assert len(model_inputs) == 1
    result = outcomes[0][1]
    assert result['run_stopped'] is True
    assert [m.content for m in result['messages']] == [
        'initial', '', 'first-result', 'second-result',
    ]


@async_test
async def test_model_failure_wins_over_requested_stop(monkeypatch, caplog):
    entered, release = Event(), Event()
    failure = RuntimeError('private-model-failure')

    class FailingModel(FakeMessagesListChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            entered.set()
            assert release.wait(timeout=3)
            raise failure

    app = await build_app(monkeypatch, FailingModel(responses=[]))
    session_id = await app.acreate_session('A')
    with caplog.at_level(logging.INFO):
        worker, outcomes = run_in_worker(app, session_id)
        try:
            assert await asyncio.to_thread(entered.wait, timeout=3)
            app.request_stop(session_id)
        finally:
            release.set()
            await asyncio.wait_for(asyncio.shield(worker), 3)

    assert worker.done()
    assert outcomes == [('error', failure)]
    run_events = [r.run_event['event'] for r in caplog.records if hasattr(r, 'run_event')]
    assert run_events == ['run_started', 'run_failed']
    assert 'private-model-failure' not in caplog.text


@async_test
async def test_stop_state_resets_before_next_run(monkeypatch, caplog):
    entered, release = Event(), Event()
    calls = []

    class FirstCallBlocked(FakeMessagesListChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            calls.append(messages)
            if len(calls) == 1:
                entered.set()
                assert release.wait(timeout=3)
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    app = await build_app(monkeypatch, FirstCallBlocked(responses=[
        AIMessage(content='first-answer'), AIMessage(content='second-answer'),
    ]))
    session_id = await app.acreate_session('A')
    with caplog.at_level(logging.INFO):
        worker, outcomes = run_in_worker(app, session_id)
        try:
            assert await asyncio.to_thread(entered.wait, timeout=3)
            app.request_stop(session_id)
        finally:
            release.set()
            await asyncio.wait_for(asyncio.shield(worker), 3)
        second = await app.arun(session_id, [('user', 'second-task')])

    assert worker.done()
    assert outcomes[0][1]['run_stopped'] is True
    assert second['run_stopped'] is False
    assert [m.content for m in second['messages']] == [
        'initial', 'first-answer', 'second-task', 'second-answer',
    ]
    run_events = [r.run_event['event'] for r in caplog.records if hasattr(r, 'run_event')]
    assert run_events == ['run_started', 'run_stopped', 'run_started', 'run_completed']


@async_test
async def test_stop_rejects_idle_and_unknown_sessions(monkeypatch):
    app = await build_app(monkeypatch, FakeMessagesListChatModel(responses=[]))
    session_id = await app.acreate_session('A')
    with pytest.raises(application.SessionBusyError):
        app.request_stop(session_id)
    with pytest.raises(KeyError):
        app.request_stop('missing-session')


@async_test
async def test_stop_rejects_closed_window_while_run_is_still_busy():
    service = application.SessionService(ModelCatalog('A', {
        'A': FakeMessagesListChatModel(responses=[]),
    }))
    session_id = service.create_session('A')
    closed, release = Event(), Event()

    class PausedAtEndAgent:
        async def ainvoke(self, _input, *, context, config):
            assert service.finish_run_boundary(context['session_id']) == (False, [])
            closed.set()
            assert await asyncio.to_thread(release.wait, timeout=3)
            return {'messages': []}

    app = AsyncApplication(service._catalog, service, PausedAtEndAgent(), prepare_tool_registry([]), system_prompt='')
    worker, outcomes = run_in_worker(app, session_id)
    try:
        assert await asyncio.to_thread(closed.wait, timeout=3)
        with pytest.raises(application.SessionBusyError):
            app.request_stop(session_id)
    finally:
        release.set()
        try:
            await asyncio.wait_for(asyncio.shield(worker), 3)
        finally:
            await app.aclose()

    assert worker.done()
    assert outcomes == [('result', {'messages': []})]


@async_test
async def test_stop_before_first_model_does_not_inject_queued_steering(monkeypatch):
    model = FakeMessagesListChatModel(responses=[AIMessage(content='first-answer')])
    app = await build_app(monkeypatch, model)
    session_id = await app.acreate_session('A')
    agent = app._agent

    class QueueBeforeInvoke:
        async def ainvoke(self, input, *, context, config):
            app.steer(session_id, 'not-for-first-model')
            app.request_stop(session_id)
            return await agent.ainvoke(input, context=context, config=config)

    app._agent = QueueBeforeInvoke()
    result = await app.arun(session_id, [('user', 'initial')])

    assert [message.content for message in result['messages']] == ['initial']
    assert model.i == 0  # A stop before dispatch sends no model request.
    assert result['run_stopped'] is True
