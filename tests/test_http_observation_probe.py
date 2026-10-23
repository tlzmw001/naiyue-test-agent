"""Preimplementation evidence: actual SDK hooks inherit callback identity."""

from runtime_context_helpers import typed_scope
import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import uuid4

import pytest

from native_application_helpers import async_test
from http_observation_helpers import sdk_catalog, sdk_response
from naiyue_test_agent.agent_runtime.events import RunEventPublisher, AgentEventSource
from naiyue_test_agent.agent_runtime.model_usage_observation import ModelUsageCallback
from naiyue_test_agent.agent_runtime.run_scope import bind_run_scope, current_run_scope


_CURRENT = ContextVar('http_propagation_probe', default=None)


@dataclass
class Record:
    identity: str
    publisher: object
    start_task: object
    closed: bool = False


class Probe(ModelUsageCallback):
    def __init__(self):
        super().__init__()
        self.records, self.received, self.finishes = {}, [], []

    async def on_chat_model_start(self, *args, run_id, **kwargs):
        await super().on_chat_model_start(*args, run_id=run_id, **kwargs)
        record = Record(str(run_id), current_run_scope().observation.publisher, asyncio.current_task())
        self.records[str(run_id)] = record
        _CURRENT.set(record)

    def _finish(self, run_id, *args, **kwargs):
        record = self.records.pop(str(run_id), None)
        if record is not None:
            record.closed = True
            self.finishes.append((record.start_task is asyncio.current_task(), record.identity))
        super()._finish(run_id, *args, **kwargs)

    def request(self, request):
        record = _CURRENT.get()
        assert record is not None and not record.closed
        self.received.append((record.identity, record.publisher))

    async def arequest(self, request):
        self.request(request)


@pytest.mark.parametrize('kind', ['openai', 'responses', 'anthropic', 'ollama', 'sync_fallback'])
@pytest.mark.parametrize('stream', [False, True])
@async_test
async def test_actual_sdk_propagates_identity_and_shielded_end_invalidates_parent(kind, stream):
    events, probe = [], Probe()
    async with sdk_catalog(kind) as catalog:
        model = catalog.get('A')
        model.callbacks = [probe]
        for client, asynchronous in catalog._owned_http_clients:
            client.event_hooks['request'].append(probe.arequest if asynchronous else probe.request)
        publisher = RunEventPublisher('s', 'r', (events.append,))
        with bind_run_scope(typed_scope(publisher=publisher)):
            if stream:
                result = [chunk async for chunk in model.astream('task')]
                assert result
            else:
                assert await model.ainvoke('task')
            assert _CURRENT.get().closed
        start, end = [e for e in events if e.event_type.startswith('model_call_')]
        assert probe.received == [(start.data.invocation_id, publisher)]
        assert end.data.invocation_id == start.data.invocation_id
        assert probe.records == {} and probe._active == {}
        assert probe.finishes == [(False, start.data.invocation_id)]


@pytest.mark.parametrize('tag', ['naiyue_summary', 'naiyue_permission_review_fast', 'naiyue_permission_review_detailed'])
@async_test
async def test_shared_client_cross_session_child_and_tagged_call_stays_scoped(tag):
    entered, release = asyncio.Event(), asyncio.Event()
    probe, events, count, origins = Probe(), [], [], {}
    async def serve(request):
        count.append(True)
        if len(count) == 1:
            entered.set()
            await release.wait()
        return sdk_response(request, 'openai')
    async with sdk_catalog(serve=serve) as catalog:
        model = catalog.get('A')
        model.callbacks = [probe]
        for client, asynchronous in catalog._owned_http_clients:
            client.event_hooks['request'].append(probe.arequest if asynchronous else probe.request)
        source = AgentEventSource('root/tester/c', 'c', 'cr', 'r1', 'delegate')
        async def call(sid, child):
            pub = RunEventPublisher(sid, 'r1', (events.append,))
            publisher = pub.for_agent(source) if child else pub
            origins[publisher] = (sid, source if child else None)
            with bind_run_scope(typed_scope(publisher=publisher)):
                return await model.ainvoke('task', config={'tags': [tag]})
        blocked = asyncio.create_task(call('a', True))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            assert await call('b', False)
            assert not blocked.done()
            release.set()
            assert await blocked
        finally:
            release.set()
            await asyncio.gather(blocked, return_exceptions=True)
        starts = {e.data.invocation_id: (e.session_id, e.source) for e in events if e.event_type == 'model_call_started'}
        assert len(starts) == 2
        assert all(starts[identity] == origins[publisher] for identity, publisher in probe.received)
        assert probe.records == {} and probe._active == {}


@async_test
async def test_task_done_without_error_hook_invalidates_inherited_record():
    probe, entered, inherited = Probe(), asyncio.Event(), []
    async def owner():
        with bind_run_scope(typed_scope(publisher=RunEventPublisher('s', 'r', ()))):
            await probe.on_chat_model_start({}, [], run_id=uuid4())
            inherited.append(_CURRENT.get())
            entered.set()
            await asyncio.Event().wait()
    task = asyncio.create_task(owner())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)  # Yield to the already scheduled task-done callback.
    assert inherited[0].closed and probe.records == {} and probe._active == {}
