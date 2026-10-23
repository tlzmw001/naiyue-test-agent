"""Follow-up waits for a natural end and continues the same run."""

from naiyue_test_agent.application.lifetime import ApplicationLifetime
from naiyue_test_agent.application.sessions.run_api import RunAPI
from contextlib import AsyncExitStack
from types import SimpleNamespace
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
from langgraph.errors import GraphRecursionError

from naiyue_test_agent import application
from naiyue_test_agent.model_access import ModelCatalog


class ToolCapableFake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


async def build_app(monkeypatch, models, tools=None):
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', models))
    return await build_application(
        'synthetic.json', tools=tools or [], system_prompt='', response_format=None,
    )


def run_in_worker(app, session_id):
    outcomes = []

    async def run():
        try:
            outcomes.append(('result', await app.runs.start(session_id, [('user', 'initial')])))
        except Exception as error:
            outcomes.append(('error', error))

    worker = asyncio.create_task(run())

    return worker, outcomes


@async_test
async def test_two_follow_ups_are_fifo_separate_turns_in_one_run(monkeypatch, caplog):
    entered, release = Event(), Event()
    inputs = []

    class BlockingModel(FakeMessagesListChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            inputs.append([message.content for message in messages])
            if len(inputs) == 1:
                entered.set()
                assert release.wait(timeout=3)
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    model = BlockingModel(responses=[AIMessage(content='answer-one'),
                                     AIMessage(content='answer-two'),
                                     AIMessage(content='answer-three')])
    app = await build_app(monkeypatch, {'A': model})
    session_id = await app.sessions.create('A')
    with caplog.at_level(logging.INFO):
        worker, outcomes = run_in_worker(app, session_id)
        try:
            assert await asyncio.to_thread(entered.wait, timeout=3)
            app.runs.follow_up(session_id, 'private-follow-one')
            app.runs.follow_up(session_id, 'private-follow-two')
        finally:
            release.set()
            await asyncio.wait_for(asyncio.shield(worker), 3)

    assert worker.done()
    assert outcomes[0][0] == 'result', outcomes
    assert [m.content for m in outcomes[0][1]['messages']] == [
        'initial', 'answer-one', 'private-follow-one', 'answer-two',
        'private-follow-two', 'answer-three',
    ]
    assert inputs[0] == ['', 'initial']
    assert inputs[1][-1] == 'private-follow-one'
    assert inputs[2][-1] == 'private-follow-two'
    run_events = [r.run_event for r in caplog.records if hasattr(r, 'run_event')]
    assert [e['event'] for e in run_events] == ['run_started', 'run_completed']
    assert run_events[0]['run_id'] == run_events[1]['run_id']
    follow_events = [r.follow_up_event for r in caplog.records
                     if hasattr(r, 'follow_up_event')]
    assert [e['event'] for e in follow_events] == [
        'follow_up_accepted', 'follow_up_accepted',
        'follow_up_injected', 'follow_up_injected',
    ]
    assert {e['run_id'] for e in follow_events} == {run_events[0]['run_id']}
    assert 'private-follow-one' not in caplog.text


@async_test
async def test_follow_up_does_not_enter_model_immediately_after_tool(monkeypatch):
    tool_started, release_tool = Event(), Event()
    inputs = []

    @tool
    def probe() -> str:
        """Read a synthetic probe value."""
        tool_started.set()
        assert release_tool.wait(timeout=3)
        return 'tool-result'

    class RecordingModel(ToolCapableFake):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            inputs.append([message.content for message in messages])
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    model = RecordingModel(responses=[
        AIMessage(content='', tool_calls=[{'name': 'probe', 'args': {}, 'id': 'call-1'}]),
        AIMessage(content='tool-answer'), AIMessage(content='follow-answer'),
    ])
    app = await build_app(monkeypatch, {'A': model}, [ToolSpec(probe, probe.args_schema, permissions=ToolPermission(default='allow'))])
    session_id = await app.sessions.create('A')
    worker, outcomes = run_in_worker(app, session_id)
    try:
        assert await asyncio.to_thread(tool_started.wait, timeout=3)
        app.runs.follow_up(session_id, 'private-follow')
    finally:
        release_tool.set()
        await asyncio.wait_for(asyncio.shield(worker), 3)

    assert worker.done()
    assert outcomes[0][0] == 'result', outcomes
    assert 'private-follow' not in inputs[1]
    assert inputs[1][-1] == 'tool-result'
    assert inputs[2][-1] == 'private-follow'
    assert [m.type for m in outcomes[0][1]['messages']] == [
        'human', 'ai', 'tool', 'ai', 'human', 'ai',
    ]


@async_test
async def test_steering_has_priority_over_follow_up_at_natural_end(monkeypatch):
    entered, release = Event(), Event()
    calls = []

    class BlockingModel(FakeMessagesListChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            calls.append(messages)
            if len(calls) == 1:
                entered.set()
                assert release.wait(timeout=3)
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    model = BlockingModel(responses=[AIMessage(content='first'),
                                     AIMessage(content='after-steering'),
                                     AIMessage(content='after-follow-up')])
    app = await build_app(monkeypatch, {'A': model})
    session_id = await app.sessions.create('A')
    worker, outcomes = run_in_worker(app, session_id)
    try:
        assert await asyncio.to_thread(entered.wait, timeout=3)
        app.runs.follow_up(session_id, 'private-follow')
        app.runs.steer(session_id, 'private-steer')
    finally:
        release.set()
        await asyncio.wait_for(asyncio.shield(worker), 3)

    assert worker.done()
    assert outcomes[0][0] == 'result', outcomes
    assert [m.content for m in outcomes[0][1]['messages']] == [
        'initial', 'first', 'private-steer', 'after-steering',
        'private-follow', 'after-follow-up',
    ]


@async_test
async def test_failure_discards_pending_follow_up_and_rejects_idle_input(monkeypatch, caplog):
    entered, release = Event(), Event()
    failure = RuntimeError('private-model-failure')

    class FailingModel(FakeMessagesListChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            entered.set()
            assert release.wait(timeout=3)
            raise failure

    app = await build_app(monkeypatch, {'A': FailingModel(responses=[])})
    session_id = await app.sessions.create('A')
    with caplog.at_level(logging.INFO):
        worker, outcomes = run_in_worker(app, session_id)
        try:
            assert await asyncio.to_thread(entered.wait, timeout=3)
            app.runs.follow_up(session_id, 'private-pending-follow')
        finally:
            release.set()
            await asyncio.wait_for(asyncio.shield(worker), 3)

    assert worker.done()
    assert outcomes == [('error', failure)]
    run_events = [r.run_event['event'] for r in caplog.records if hasattr(r, 'run_event')]
    assert run_events == ['run_started', 'run_failed']
    follow_events = [r.follow_up_event for r in caplog.records
                     if hasattr(r, 'follow_up_event')]
    assert [e['event'] for e in follow_events] == [
        'follow_up_accepted', 'follow_up_discarded',
    ]
    assert follow_events[-1]['count'] == 1
    assert 'private-pending-follow' not in caplog.text
    with pytest.raises(application.SessionBusyError):
        app.runs.follow_up(session_id, 'after-run')
    with pytest.raises(ValueError):
        app.runs.follow_up(session_id, '')
    with pytest.raises(KeyError):
        app.runs.follow_up('unknown-session', 'message')


@async_test
async def test_follow_up_is_isolated_between_active_sessions(monkeypatch):
    entered_a, entered_b, release = Event(), Event(), Event()

    def blocking_model(entered):
        calls = []

        class Model(FakeMessagesListChatModel):
            def _generate(self, messages, stop=None, run_manager=None, **kwargs):
                calls.append(messages)
                if len(calls) == 1:
                    entered.set()
                    assert release.wait(timeout=3)
                return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

        return Model(responses=[AIMessage(content='first'),
                                AIMessage(content='second')])

    app = await build_app(monkeypatch, {
        'A': blocking_model(entered_a), 'B': blocking_model(entered_b),
    })
    session_a = await app.sessions.create('A')
    session_b = await app.sessions.create('B')
    worker_a, outcomes_a = run_in_worker(app, session_a)
    worker_b, outcomes_b = run_in_worker(app, session_b)
    try:
        assert await asyncio.to_thread(entered_a.wait, timeout=3)
        assert await asyncio.to_thread(entered_b.wait, timeout=3)
        app.runs.follow_up(session_a, 'private-A-only')
    finally:
        release.set()
        await asyncio.wait_for(asyncio.shield(worker_a), 3)
        await asyncio.wait_for(asyncio.shield(worker_b), 3)

    assert worker_a.done() and worker_b.done()
    assert outcomes_a[0][0] == outcomes_b[0][0] == 'result'
    assert [m.content for m in outcomes_a[0][1]['messages']] == [
        'initial', 'first', 'private-A-only', 'second',
    ]
    assert [m.content for m in outcomes_b[0][1]['messages']] == ['initial', 'first']


@async_test
async def test_natural_end_closes_follow_up_before_busy_gate_is_released():
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

    lifetime = ApplicationLifetime(service._catalog, service, AsyncExitStack())
    app = SimpleNamespace(runs=RunAPI(lifetime, service, PausedAtEndAgent(), None, None, None, None),
                          aclose=lifetime.aclose)
    worker, outcomes = run_in_worker(app, session_id)
    try:
        assert await asyncio.to_thread(closed.wait, timeout=3)
        with pytest.raises(application.SessionBusyError):
            app.runs.follow_up(session_id, 'arrived-after-close')
    finally:
        release.set()
        try:
            await asyncio.wait_for(asyncio.shield(worker), 3)
        finally:
            await app.aclose()

    assert worker.done()
    assert outcomes == [('result', {'messages': []})]


@async_test
async def test_recursion_limit_is_a_failed_run_not_a_completed_answer(monkeypatch, caplog):
    app = None
    session_id = None

    class RequeueingModel(FakeMessagesListChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            app.runs.follow_up(session_id, 'continue')
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    app = await build_app(monkeypatch, {
        'A': RequeueingModel(responses=[AIMessage(content='partial')]),
    })
    session_id = await app.sessions.create('A')
    agent = app.runs._agent

    class LimitedAgent:
        def __getattr__(self, name):
            return getattr(agent, name)
        async def ainvoke(self, input, *, context, config, **options):
            assert options == {'durability': 'sync'}
            return await agent.ainvoke(input, context=context,
                                config={**config, 'recursion_limit': 5}, **options)

    app.runs._agent = LimitedAgent()
    with caplog.at_level(logging.INFO), pytest.raises(GraphRecursionError):
        await app.runs.start(session_id, [('user', 'initial')])

    run_events = [r.run_event['event'] for r in caplog.records if hasattr(r, 'run_event')]
    assert run_events == ['run_started', 'run_failed']
    with pytest.raises(application.SessionBusyError):
        app.runs.follow_up(session_id, 'after-failure')
