"""Queue duration is unbounded; ordering, settlement and stop guards remain."""

from dataclasses import replace
from runtime_context_helpers import RunIdentity, typed_context
from native_application_helpers import async_test

import asyncio
from threading import Event
from types import SimpleNamespace

import pytest
from langchain.tools import tool
from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent.application import prepare_tool_registry
from naiyue_test_agent.agent_runtime.run_control import RunControl
from naiyue_test_agent.agent_runtime.tool_execution import async_batch, batch
from naiyue_test_agent.tools import ToolSpec
from naiyue_test_agent.tools.permissions import ToolPermission
from event_sequence import EventSequenceChecker
from test_application_run_events import ToolModel, make_app


@tool
def queue_probe(label: str) -> str:
    """Return a synthetic value without external effects."""
    return label


def prepared(mode='sequential', *, control=None, unknown=False):
    registry = prepare_tool_registry([ToolSpec(queue_probe, queue_probe.args_schema,
        execution_mode=mode, permissions=ToolPermission(default='allow'))])
    middleware = batch.ToolBatchMiddleware(registry)
    calls = [{'name': 'unknown' if unknown else 'queue_probe', 'args': {'label': label}, 'id': label}
             for label in ('first', 'later')]
    state = {'messages': [AIMessage(content='', tool_calls=calls)],
             'tool_execution': {'tool_names': ['queue_probe']}}
    context = typed_context(identity=RunIdentity(session_id='s', run_id='r'))
    if control is not None:
        context = replace(context, scope=replace(context.scope, control=control))
    runtime = SimpleNamespace(context=context, config={'max_concurrency': 2})
    return middleware, state, runtime, calls


@async_test
async def test_blocking_tool_keeps_later_call_queued_past_old_deadline(monkeypatch):
    middleware, state, runtime, _ = prepared()
    await middleware.aafter_model(state, runtime)
    plan = runtime.context.invocation.tool_batch
    entered, release = Event(), Event()
    waiting, observed = asyncio.Event(), asyncio.Event()
    clock, trace = [0.0], []
    monkeypatch.setattr(async_batch, 'monotonic', lambda: clock[0], raising=False)

    class ObservableCondition(asyncio.Condition):
        waits = 0
        async def wait(self):
            self.waits += 1
            waiting.set()
            if self.waits == 2:
                observed.set()
            return await super().wait()
    plan.condition = ObservableCondition()

    def first_body():
        trace.append('first:start')
        entered.set()
        assert release.wait(5)
        trace.append('first:end')
        return 'first-result'
    async def first_handler():
        return await asyncio.to_thread(first_body)
    async def later_handler():
        trace.append('later')
    first = asyncio.create_task(plan.execute('first', 'queue_probe', first_handler, 2))
    later = None
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        later = asyncio.create_task(plan.execute('later', 'queue_probe', later_handler, 2))
        await asyncio.wait_for(waiting.wait(), 5)
        clock[0] = 120.0
        async with plan.condition:
            plan.condition.notify_all()
        await asyncio.wait_for(observed.wait(), 5)
        assert not later.done(), 'Queue time must not fail a still-running predecessor.'
        assert trace == ['first:start']
        release.set()
        assert await asyncio.wait_for(first, 5) == 'first-result'
        assert await asyncio.wait_for(later, 5) is None
        assert trace == ['first:start', 'first:end', 'later']
    finally:
        release.set()
        await asyncio.gather(*([first, later] if later else [first]), return_exceptions=True)


@async_test
async def test_async_later_call_has_no_execution_deadline_while_queued(monkeypatch):
    async def scenario():
        waiting, observed, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        timers, trace = [], []

        class ManualTimeout:
            def __init__(self, _seconds):
                self.active = self.expired = False
                timers.append(self)

            async def __aenter__(self):
                self.active, self.owner = True, asyncio.current_task()

            async def __aexit__(self, kind, error, traceback):
                self.active = False
                if self.expired and kind is asyncio.CancelledError:
                    self.owner.uncancel()
                    raise TimeoutError from error

            def expire(self):
                if self.active:
                    self.expired = True
                    self.owner.cancel()

        class ObservableCondition(asyncio.Condition):
            waits = 0

            async def wait(self):
                self.waits += 1
                waiting.set()
                if self.waits == 2:
                    observed.set()
                return await super().wait()

        # Replace only this module's timer constructor, not asyncio globally.
        monkeypatch.setattr(async_batch, 'asyncio', SimpleNamespace(
            Condition=ObservableCondition, Semaphore=asyncio.Semaphore, timeout=ManualTimeout))
        middleware, state, runtime, _ = prepared()
        await middleware.aafter_model(state, runtime)
        plan = runtime.context.invocation.tool_batch

        async def first_body():
            trace.append('first:start')
            await release.wait()
            trace.append('first:end')
            return 'first-result'

        async def later_body():
            trace.append('later')
            return 'later-result'

        first = asyncio.create_task(plan.execute('first', 'queue_probe', first_body, 2))
        later = asyncio.create_task(plan.execute('later', 'queue_probe', later_body, 2))
        witness = asyncio.create_task(observed.wait())
        try:
            await asyncio.wait_for(waiting.wait(), 5)
            for timer in timers:
                timer.expire()
            async with plan.condition:
                plan.condition.notify_all()
            done, _ = await asyncio.wait((later, witness), timeout=5, return_when=asyncio.FIRST_COMPLETED)
            assert done
            assert not later.done(), 'A queue must not install an execution deadline.'
            assert trace == ['first:start']
            release.set()
            assert await asyncio.wait_for(first, 5) == 'first-result'
            assert await asyncio.wait_for(later, 5) == 'later-result'
            assert trace == ['first:start', 'first:end', 'later']
        finally:
            release.set()
            witness.cancel()
            await asyncio.gather(first, later, witness, return_exceptions=True)
    await scenario()


@pytest.mark.parametrize('mode,unknown', [('parallel', False), ('sequential', False), ('parallel', True)])
@async_test
async def test_dispatch_does_not_enter_downstream_after_stop(mode, unknown):
    control = RunControl()
    middleware, state, runtime, calls = prepared(mode, control=control, unknown=unknown)
    await middleware.aafter_model(state, runtime)
    control.request()
    executed = []
    request = SimpleNamespace(tool_call=calls[0], state=state, runtime=runtime)
    async def downstream(_request):
        executed.append('entered')
    result = await middleware.awrap_tool_call(request, downstream)
    assert executed == []
    assert isinstance(result, ToolMessage) and result.status == 'error'
    assert result.tool_call_id == 'first'
    assert result.additional_kwargs['naiyue_not_executed'] == 'user_stop'


@pytest.mark.parametrize('concurrency', [1, 2])
@async_test
async def test_sync_app_settles_first_tool_then_skips_later_without_another_model(monkeypatch, caplog, concurrency):
    entered, release, later_dispatched = Event(), Event(), Event()
    executed, events = [], []

    @tool
    def first() -> str:
        """An already-started action must settle before releasing this run."""
        executed.append('first')
        entered.set()
        assert release.wait(5)
        return 'committed'

    @tool
    def later() -> str:
        """A queued action must not start after a user stop."""
        executed.append('later')
        return 'unexpected'

    model = ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'first', 'args': {}, 'id': 'first-call'},
        {'name': 'later', 'args': {}, 'id': 'later-call'},
    ]), AIMessage(content='must not request')])
    app = await make_app(monkeypatch, model, [
        ToolSpec(first, first.args_schema, execution_mode='sequential', permissions=ToolPermission(default='allow')),
        ToolSpec(later, later.args_schema, permissions=ToolPermission(default='allow')),
    ])
    app.host.runs._agent = app.host.runs._agent.with_config({'max_concurrency': concurrency})
    session = await app.host.sessions.create('A')

    def observe(event):
        events.append(event)
        if event.event_type == 'tool_call_started' and event.data.tool_call_id == 'later-call':
            later_dispatched.set()

    app.host.observability.subscribe(observe)
    try:
        task = asyncio.create_task(app.host.runs.start(session, [('user', 'start')], max_concurrency=concurrency))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            if concurrency == 2:
                assert await asyncio.to_thread(later_dispatched.wait, 5)
            app.host.runs.stop(session)
            assert app.host.sessions._sessions.require(session).describe_session()['busy'] and not task.done()
        finally:
            release.set()
        result = await asyncio.wait_for(asyncio.shield(task), 5)
        assert executed == ['first']
        results = {message.tool_call_id: message for message in result['messages'] if isinstance(message, ToolMessage)}
        assert results['first-call'].content == 'committed' and results['first-call'].status == 'success'
        assert results['later-call'].additional_kwargs['naiyue_not_executed'] == 'user_stop'
        assert results['later-call'].status == 'error' and result['run_stopped']
        assert sum(event.event_type == 'model_call_started' for event in events) == 1
        finished = {event.data.tool_call_id: event.data for event in events if event.event_type == 'tool_call_finished'}
        assert finished['first-call'].outcome == 'success'
        assert finished['later-call'].outcome == 'not_executed' and finished['later-call'].reason == 'user_stop'
        assert events[-1].data.status == 'stopped' and not events[-1].data.manual_review_required
        snapshot = await app.host.runs._agent.aget_state({'configurable': {'thread_id': app.host.sessions._sessions.require(session).get_thread_id()}})
        assert not snapshot.next and not any(task.error or task.interrupts for task in snapshot.tasks)
        assert not app.host.sessions._sessions.require(session).describe_session()['busy']
        EventSequenceChecker.assert_healthy(events, caplog.records)
    finally:
        release.set()
        await app.host.sessions._sessions._catalog.aclose()


@async_test
async def test_async_cancelled_waiter_wakes_remaining_calls_without_running_them():
    async def scenario():
        middleware, state, runtime, calls = prepared()
        calls.append({'name': 'queue_probe', 'args': {'label': 'tail'}, 'id': 'tail'})
        state['messages'] = [AIMessage(content='', tool_calls=calls)]
        await middleware.aafter_model(state, runtime)
        plan = runtime.context.invocation.tool_batch
        waiting, release = asyncio.Event(), asyncio.Event()
        trace = []

        class ObservableCondition(asyncio.Condition):
            waits = 0

            async def wait(self):
                self.waits += 1
                if self.waits == 2:
                    waiting.set()
                return await super().wait()

        plan.condition = ObservableCondition()

        async def body(label):
            trace.append(label)
            if label == 'first':
                await release.wait()
            return label

        tasks = [asyncio.create_task(plan.execute(label, 'queue_probe', lambda label=label: body(label), 3))
                 for label in ('first', 'later', 'tail')]
        try:
            await asyncio.wait_for(waiting.wait(), 5)
            tasks[1].cancel('fixture cancellation')
            with pytest.raises(asyncio.CancelledError, match='fixture cancellation'):
                await tasks[1]
            skipped = await asyncio.wait_for(tasks[2], 5)
            assert skipped.tool_call_id == 'tail' and skipped.status == 'error'
            assert trace == ['first']
            release.set()
            assert await asyncio.wait_for(tasks[0], 5) == 'first'
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
    await scenario()
