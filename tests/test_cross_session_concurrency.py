"""Root capacity and concurrent histories through the real async graph."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import Field, ValidationError

from native_application_helpers import async_test
from naiyue_test_agent import application
from naiyue_test_agent.application.async_application import build_async_local_application
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.application.errors import SessionBusyError
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.settings import AppSettings, load_settings
from test_async_application import StreamingToolModel


class SessionModel(StreamingToolModel):
    """Each session owns its replies and gates, independent of task ordering."""
    gates: dict = Field(default_factory=dict)
    responses: dict = Field(default_factory=dict)
    requests: dict = Field(default_factory=dict)

    async def _next(self, messages):
        sid = next(m.content for m in messages if isinstance(m, HumanMessage))
        self.requests.setdefault(sid, []).append(messages)
        if sid in self.gates:
            entered, release = self.gates[sid]
            entered.set()
            await release.wait()
        response = self.responses[sid].pop(0) if self.responses.get(sid) else AIMessage(content='done:' + sid)
        if isinstance(response, BaseException):
            raise response
        return response


@asynccontextmanager
async def concurrent_app(monkeypatch, path=None, *, settings=None, model=None, tools=(), child=None):
    model = model if model is not None else SessionModel(replies=[])
    models = {'A': model, **({'B': child} if child is not None else {})}
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', models))
    kwargs = dict(tools=list(tools), system_prompt='', response_format=None, settings=settings or AppSettings())
    if path is not None:
        async with open_async_persistent_application('unused', database_path=path, **kwargs) as app:
            yield app, model
    else:
        app = await build_async_local_application('unused', **kwargs)
        try:
            yield app, model
        finally:
            await app.aclose()


def gated(model, sid):
    model.gates[sid] = asyncio.Event(), asyncio.Event()
    return model.gates[sid]


async def drain(tasks, model):
    for _, release in model.gates.values():
        release.set()
    await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize('raw', ['0', '-1', 'true', '1.5', '"PRIVATE"', '[1]'])
def test_invalid_capacity_falls_back_without_losing_permissions(tmp_path, caplog, raw):
    path = tmp_path / 'settings.toml'
    path.write_text(f'max_active_runs={raw}\n[permissions]\ndeny=[{{tool="read",path="/private/**"}}]\n')
    settings = load_settings(path)
    assert settings.max_active_runs == 10 and len(settings.permissions.deny) == 1
    assert sum(r.message == 'max_active_runs_fallback reason=invalid' for r in caplog.records) == 1
    assert 'PRIVATE' not in caplog.text


def test_capacity_configuration_defaults_validation_and_permission_failure(tmp_path):
    assert AppSettings().max_active_runs == load_settings(tmp_path / 'missing').max_active_runs == 10
    path = tmp_path / 'settings.toml'
    path.write_text('max_active_runs=2\n')
    assert load_settings(path).max_active_runs == 2
    for invalid in (0, -1, True, 1.5, '2'):
        with pytest.raises(ValidationError):
            AppSettings(max_active_runs=invalid)
        with pytest.raises(ValueError):
            application.SessionService(ModelCatalog('A', {'A': SessionModel(replies=[])}), max_active_runs=invalid)
    path.write_text('max_active_runs=0\n[permissions]\nmode="invalid"\n')
    with pytest.raises(ValueError):
        load_settings(path)


@pytest.mark.parametrize('persistent', [False, True])
@pytest.mark.parametrize('limit', [None, 2])
@async_test
async def test_capacity_is_atomic_and_rejection_has_no_run_effects(monkeypatch, tmp_path, persistent, limit):
    from naiyue_test_agent.application import RunCapacityExceededError
    settings = AppSettings() if limit is None else AppSettings(max_active_runs=limit)
    count = limit or 10
    async with concurrent_app(monkeypatch, tmp_path / 'state.sqlite' if persistent else None,
                              settings=settings) as (app, model):
        ids = [await app.sessions.create('A') for _ in range(count + 1)]
        events, tasks = [], []
        app.observability.subscribe(events.append)
        try:
            for sid in ids[:-1]:
                entered, _ = gated(model, sid)
                task = asyncio.create_task(app.runs.start(sid, [('user', sid)]))
                tasks.append(task)
                await asyncio.wait_for(entered.wait(), 5)
            assert all(app.sessions._sessions.require(sid).describe_session()['busy'] for sid in ids[:-1])
            if persistent:
                assert set(app.sessions._persistence._active) == set(ids[:-1])
            with pytest.raises(SessionBusyError):
                await app.runs.start(ids[0], [('user', 'duplicate')])
            with pytest.raises(RunCapacityExceededError):
                await app.runs.start(ids[-1], [('user', ids[-1])])
            with pytest.raises(RunCapacityExceededError):
                await app.runs.compact(ids[-1])
            assert ids[-1] not in model.requests and not any(e.session_id == ids[-1] for e in events)
            assert not app.sessions._sessions.require(ids[-1]).describe_session()['busy']
            snapshot = await app.runs._agent.aget_state({'configurable': {'thread_id': app.sessions._sessions.require(ids[-1]).get_thread_id()}})
            assert not snapshot.values and not snapshot.config['configurable'].get('checkpoint_id')
            if persistent:
                assert app.sessions._persistence.storage.sessions.get(ids[-1])['run_id'] is None
            model.gates[ids[0]][1].set()
            await tasks[0]
            result = await app.runs.start(ids[-1], [('user', ids[-1])])
            assert result['messages'][-1].content == 'done:' + ids[-1]
        finally:
            await drain(tasks, model)
        for sid in ids:
            messages = await app.history.messages(sid)
            assert [m.content for m in messages] == [sid, 'done:' + sid]
            own_events = [e for e in events if e.session_id == sid]
            assert len({e.run_id for e in own_events}) == 1
            assert own_events[0].event_type == 'run_started'
            assert own_events[-1].event_type == 'run_finished' and own_events[-1].data.status == 'completed'
            if persistent:
                assert app.sessions._persistence.storage.sessions.get(sid)['state'] == 'ready'
                page = await app.history.page(sid, limit=50, body_bytes=128000, preview_chars=100)
                assert {r['message_id'] for r in page['records']} == {m.id for m in messages}
        if persistent:
            assert app.sessions._persistence._active == {}
        assert app._lifetime._operations == {}


@async_test
async def test_active_compaction_consumes_capacity_until_its_finish(monkeypatch, tmp_path):
    from naiyue_test_agent.application import RunCapacityExceededError
    from test_manual_compaction import compact_model
    model = compact_model()
    entered, release = asyncio.Event(), asyncio.Event()
    original = type(model)._next
    async def hold_summary(self, messages):
        if self is model and len(self.seen) == 2:
            entered.set()
            await release.wait()
        return await original(self, messages)
    async with concurrent_app(monkeypatch, tmp_path / 'state.sqlite', model=model,
                              settings=AppSettings(max_active_runs=1)) as (app, _):
        a, b = [await app.sessions.create('A') for _ in range(2)]
        await app.runs.start(a, [('user', 'old task')])
        await app.runs.start(a, [('user', 'recent task')])
        monkeypatch.setattr(type(model), '_next', hold_summary)
        task = asyncio.create_task(app.runs.compact(a))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            assert set(app.sessions._persistence._active) == {a}
            with pytest.raises(RunCapacityExceededError):
                await app.runs.start(b, [('user', 'not started')])
            release.set()
            assert (await task)['compaction_status'] == 'applied'
            assert app.sessions._persistence._active == {} and not app.sessions._sessions.require(a).describe_session()['busy']
            model.replies.append(AIMessage(content='new task'))
            assert (await app.runs.start(b, [('user', 'start after compaction')]))['messages'][-1].content == 'new task'
            assert len(model.seen) == 4
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
