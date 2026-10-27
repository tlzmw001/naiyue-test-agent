"""One application, real isolated stores, shared models and run capacity."""
import asyncio
from contextlib import asynccontextmanager

from langchain_core.messages import AIMessage
import pytest

from naiyue_test_agent import application
from naiyue_test_agent.application.service_application import open_service_application
from naiyue_test_agent.contracts.projects import ProjectError
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.storage.errors import SessionPersistenceError
from test_async_application import StreamingToolModel


@asynccontextmanager
async def multi_app(monkeypatch, tmp_path, model, *, tools=(), capacity=2, subagents=(), permissions=None):
    calls = []
    def catalog(_):
        calls.append(True)
        return ModelCatalog('A', {'A': model})
    monkeypatch.setattr(application, 'load_model_catalog', catalog)
    values = {'storage': {'data_dir': str(tmp_path / 'data')}, 'subagents': subagents,
        'max_active_runs': capacity, 'memory': {'auto_enabled': False}}
    if permissions is not None:
        values['permissions'] = permissions
    settings = AppSettings.model_validate(values)
    async with open_service_application('unused',
            tools=list(tools), system_prompt='', response_format=None, settings=settings) as app:
        yield app, calls


async def projects(app, tmp_path):
    result = []
    for name in ('a', 'b'):
        root = tmp_path / name
        root.mkdir(exist_ok=True)
        item = await app.projects.add_local(root, trusted=True, operation_id=name)
        result.append((item, await app.projects.open(item.project_id)))
    return result


def test_project_hosts_and_chats_run_and_restore(monkeypatch, tmp_path):
    async def scenario():
        async with multi_app(monkeypatch, tmp_path, StreamingToolModel(replies=[
                AIMessage(content='A answer'), AIMessage(content='B answer'), AIMessage(content='Chat answer')])) as (app, calls):
            (pa, a), (pb, b) = await projects(app, tmp_path)
            assert await app.projects.open(pa.project_id) is a
            chat = await app.chats.open()
            assert await app.chats.open() is chat
            assert calls == [True]
            sa, sb, sc = await a.sessions.create('A'), await b.sessions.create('A'), await chat.sessions.create('A')
            for host, sid, text in ((a, sa, 'A question'), (b, sb, 'B question'), (chat, sc, 'Chat question')):
                await host.runs.start(sid, [('user', text)])
            assert (await a.sessions.status(sa))['project_id'] == pa.project_id
            assert (await b.sessions.status(sb))['workspace_root'] == str(tmp_path / 'b')
            status = await chat.sessions.status(sc)
            assert status['project_id'] is None and status['workspace_root'] is None
            assert not (tmp_path / 'a/.naiyue/workspace.json').exists()
            assert not (tmp_path / 'b/.naiyue/workspace.json').exists()
            assert (tmp_path / 'data/chats/sessions.sqlite').exists()
            assert a.runs._agent is not b.runs._agent
            assert a.sessions._sessions._catalog is b.sessions._sessions._catalog
        async with multi_app(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (app, _):
            for pid, sid, expected in ((pa.project_id, sa, ['A question', 'A answer']),
                    (pb.project_id, sb, ['B question', 'B answer']), (None, sc, ['Chat question', 'Chat answer'])):
                host = await app.projects.open(pid) if pid else await app.chats.open()
                await host.sessions.restore(sid)
                assert [m.content for m in await host.history.messages(sid)] == expected
    asyncio.run(scenario())


def test_global_capacity_and_busy_close(monkeypatch, tmp_path):
    async def scenario():
        model = StreamingToolModel(replies=[AIMessage(content='one'), AIMessage(content='two')],
            entered=asyncio.Event(), release=asyncio.Event())
        async with multi_app(monkeypatch, tmp_path, model, capacity=1) as (app, _):
            (pa, a), (_, b) = await projects(app, tmp_path)
            sa, sb = await a.sessions.create('A'), await b.sessions.create('A')
            running = asyncio.create_task(a.runs.start(sa, [('user', 'one')]))
            try:
                await asyncio.wait_for(model.entered.wait(), 5)
                from naiyue_test_agent.application.errors import RunCapacityExceededError
                with pytest.raises(RunCapacityExceededError):
                    await b.runs.start(sb, [('user', 'two')])
                with pytest.raises(ProjectError, match='project_in_use'):
                    await app.projects.close(pa.project_id)
            finally:
                model.release.set()
                await running
            await b.runs.start(sb, [('user', 'two')])
            await app.projects.close(pa.project_id)
            with pytest.raises(RuntimeError, match='owning event loop'):
                await a.sessions.list()
            reopened = await app.projects.open(pa.project_id)
            assert reopened is not a
            await reopened.sessions.restore(sa)
    asyncio.run(scenario())


def test_wrong_host_ids_are_rejected(monkeypatch, tmp_path):
    async def scenario():
        async with multi_app(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (app, _):
            (_, a), (_, b) = await projects(app, tmp_path)
            sa = await a.sessions.create('A')
            for operation in (b.sessions.status(sa), b.history.page(sa), b.tasks.list(sa),
                    b.observability.usage(sa), b.observability.runs(sa)):
                with pytest.raises(SessionPersistenceError, match='session_not_found'):
                    await operation
            with pytest.raises(KeyError):
                await b.runs.start(sa, [('user', 'wrong')])
    asyncio.run(scenario())


def test_locked_project_open_does_not_break_other_host(monkeypatch, tmp_path):
    from naiyue_test_agent.storage.project_store import ProjectStore
    async def scenario():
        async with multi_app(monkeypatch, tmp_path, StreamingToolModel(replies=[AIMessage(content='ok')])) as (app, _):
            root = tmp_path / 'b'
            root.mkdir()
            item = await app.projects.add_local(root, trusted=True, operation_id='b')
            with ProjectStore(item.database_path):
                with pytest.raises(SessionPersistenceError, match='storage_in_use'):
                    await app.projects.open(item.project_id)
            b = await app.projects.open(item.project_id)
            sid = await b.sessions.create('A')
            await b.runs.start(sid, [('user', 'hi')])
    asyncio.run(scenario())


def test_registration_relocation_keeps_binding_and_follows_confirmed_position(monkeypatch, tmp_path):
    from naiyue_test_agent.execution_roots import ExecutionRootUnavailable
    async def scenario():
        async with multi_app(monkeypatch, tmp_path, StreamingToolModel(replies=[AIMessage(content='continued')])) as (app, _):
            (pa, a), _ = await projects(app, tmp_path)
            sid = await a.sessions.create('A')
            await app.projects.close(pa.project_id)
            destination = tmp_path / 'moved'
            (tmp_path / 'a').rename(destination)
            await app.projects.relocate(pa.project_id, expected_root=tmp_path / 'a', root=destination,
                trusted=True, operation_id='move')
            a = await app.projects.open(pa.project_id)
            await a.sessions.restore(sid)
            status = await a.sessions.status(sid)
            assert status['workspace_root'] == str(destination)
            assert status['last_execution_path'] == str(tmp_path / 'a')
            assert status['execution_root_error'] is None and status['can_run']
            assert (await a.runs.start(sid, [('user', 'continue')]))['messages'][-1].content == 'continued'
            fresh = await a.sessions.create('A')
            assert (await a.sessions.status(fresh))['workspace_root'] == str(destination)
            assert {row['session_id'] for row in await a.sessions.list_workspace(destination)} == {sid, fresh}
            await a.sessions.reorder(destination, [sid, fresh])
            assert [row['session_id'] for row in await a.sessions.list_workspace(destination)] == [sid, fresh]
            newest = await a.sessions.create('A')
            assert [row['session_id'] for row in await a.sessions.list_workspace(destination)] == [newest, sid, fresh]
            with pytest.raises(ProjectError, match='project_execution_scope_mismatch'):
                await a.sessions.create_workspace('A', tmp_path / 'b')
    asyncio.run(scenario())


def test_two_project_graphs_run_concurrently_without_mixing_histories(monkeypatch, tmp_path):
    from test_cross_session_concurrency import SessionModel, gated, drain
    async def scenario():
        model = SessionModel(replies=[])
        async with multi_app(monkeypatch, tmp_path, model, capacity=2) as (app, _):
            (_, a), (_, b) = await projects(app, tmp_path)
            sa, sb = await a.sessions.create('A'), await b.sessions.create('A')
            ea, ra = gated(model, sa)
            eb, rb = gated(model, sb)
            runs = [asyncio.create_task(host.runs.start(sid, [('user', sid)])) for host, sid in ((a, sa), (b, sb))]
            try:
                await asyncio.wait_for(asyncio.gather(ea.wait(), eb.wait()), 5)
                assert (await a.sessions.status(sa))['busy'] and (await b.sessions.status(sb))['busy']
                ra.set()
                result_a = await runs[0]
                assert not runs[1].done()
                rb.set()
                result_b = await runs[1]
                assert [m.content for m in result_a['messages']] == [sa, 'done:' + sa]
                assert [m.content for m in result_b['messages']] == [sb, 'done:' + sb]
            finally:
                await drain(runs, model)
    asyncio.run(scenario())
