"""Execution reads must not serialize behind another project's slow mutation."""
import asyncio
from contextlib import asynccontextmanager
from threading import Event, Thread
from types import SimpleNamespace

from langchain_core.messages import AIMessage
import pytest

from naiyue_test_agent import application
from naiyue_test_agent.application.projects import operations
from naiyue_test_agent.application.service_application import open_service_application
from naiyue_test_agent.application.sessions.session_service import SessionService
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.settings import AppSettings
from test_async_application import StreamingToolModel


@asynccontextmanager
async def opened(monkeypatch, tmp_path, model, *, tools=(), subagents=()):
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    settings = AppSettings.model_validate({'storage': {'data_dir': str(tmp_path / 'data')},
                                          'memory': {'auto_enabled': False}, 'subagents': subagents})
    async with open_service_application('unused', tools=tools, system_prompt='',
            response_format=None, settings=settings) as app:
        root = tmp_path / 'a'
        root.mkdir(exist_ok=True)
        project = await app.projects.add_local(root, trusted=True, operation_id='a')
        host = await app.projects.open(project.project_id)
        sid = await host.sessions.create('A')
        yield app, project, host, sid


def test_execution_facts_use_only_database_short_lock(monkeypatch, tmp_path):
    async def scenario():
        async with opened(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (app, project, host, sid):
            service = app.projects._service
            registry = service.registry
            entered, release = Event(), Event()
            def mutation():
                with service._lock:
                    entered.set()
                    assert release.wait(10)
            worker = Thread(target=mutation)
            worker.start()
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                facts = await asyncio.wait_for(asyncio.to_thread(registry.execution_facts, project.project_id), 5)
                assert facts.revision == project.revision
                assert facts.path_key == str(tmp_path / 'a')
                assert facts.lifecycle == 'active' and facts.trusted and facts.source_kind == 'local'
            finally:
                release.set()
                await asyncio.to_thread(worker.join, 5)
    asyncio.run(scenario())


def test_other_project_run_and_event_loop_respond_during_git_init(monkeypatch, tmp_path):
    from naiyue_test_agent.tools import create_session_read_tool
    async def scenario():
        model = StreamingToolModel(replies=[AIMessage(content='', tool_calls=[
            {'name': 'read', 'args': {'path': 'file.txt'}, 'id': 'read-a'}]), AIMessage(content='ok')])
        async with opened(monkeypatch, tmp_path, model, tools=[create_session_read_tool()]) as (app, _, host, sid):
            (tmp_path / 'a/file.txt').write_text('READ_WHILE_B_WAITING')
            entered, release, heartbeat, start, completed = Event(), Event(), Event(), Event(), Event()
            original = operations.subprocess.run
            observed = []
            loop = asyncio.get_running_loop()
            def git(args, **kwargs):
                if args[:2] != ['git', 'init']:
                    return original(args, **kwargs)
                entered.set()
                assert release.wait(10)
                return SimpleNamespace(returncode=0)
            def watchdog():
                try:
                    assert start.wait(5)
                    loop.call_soon_threadsafe(heartbeat.set)
                    observed.append(heartbeat.wait(1))
                    if observed[-1]:
                        observed.append(completed.wait(5))
                finally:
                    release.set()
            monkeypatch.setattr(operations.subprocess, 'run', git)
            creating = asyncio.create_task(app.projects.create_local(parent=tmp_path, folder_name='b',
                trusted=True, operation_id='b', git_init=True))
            assert await asyncio.to_thread(entered.wait, 5)
            worker = Thread(target=watchdog)
            worker.start()
            async def run():
                start.set()
                return await host.runs.start(sid, [('user', 'run')])
            try:
                result = await run()
                completed.set()
                assert result['messages'][-1].content == 'ok'
                assert any('READ_WHILE_B_WAITING' in str(m.content) for m in result['messages'] if m.type == 'tool')
                assert not creating.done(), 'A must complete while B remains blocked'
            finally:
                release.set()
                await creating
                await asyncio.to_thread(worker.join, 5)
            assert observed == [True, True], 'owner loop and project A must run independently of project B mutation'
    asyncio.run(scenario())


def test_closing_after_execution_validation_refuses_new_run(monkeypatch, tmp_path):
    async def scenario():
        model = StreamingToolModel(replies=[AIMessage(content='must not run')])
        async with opened(monkeypatch, tmp_path, model) as (app, _, host, sid):
            original = SessionService.prepare_admission
            closing = []
            async def validated(self, *args, **kwargs):
                result = await original(self, *args, **kwargs)
                closing.append(asyncio.create_task(app.aclose()))
                await asyncio.sleep(0)
                assert app._lifetime._closing
                return result
            monkeypatch.setattr(SessionService, 'prepare_admission', validated)
            with pytest.raises(RuntimeError, match='closing|owning event loop'):
                await host.runs.start(sid, [('user', 'refuse')])
            await asyncio.gather(*closing)
            assert model.seen == []
            assert not host.sessions._sessions.require(sid).is_busy()
    asyncio.run(scenario())


def test_registration_revision_changes_for_facts_but_not_idempotent_retry(monkeypatch, tmp_path):
    async def scenario():
        async with opened(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (app, project, _, _):
            before = project.revision
            changed = await app.projects.rename(project.project_id, 'renamed', operation_id='rename')
            assert changed.revision == before + 1
            retry = await app.projects.rename(project.project_id, 'renamed', operation_id='rename')
            assert retry.revision == changed.revision
    asyncio.run(scenario())


def test_revision_change_after_validation_refuses_admission(monkeypatch, tmp_path):
    from naiyue_test_agent.execution_roots import ExecutionRootUnavailable
    async def scenario():
        model = StreamingToolModel(replies=[AIMessage(content='must not run')])
        async with opened(monkeypatch, tmp_path, model) as (app, project, host, sid):
            original = SessionService.prepare_admission
            async def validated(self, *args, **kwargs):
                prepared = await original(self, *args, **kwargs)
                await app.projects.rename(project.project_id, 'changed', operation_id='change-during-admission')
                return prepared
            monkeypatch.setattr(SessionService, 'prepare_admission', validated)
            with pytest.raises(ExecutionRootUnavailable, match='not_registered'):
                await host.runs.start(sid, [('user', 'refuse')])
            assert model.seen == []
            assert not host.sessions._sessions.require(sid).is_busy()
    asyncio.run(scenario())


def test_http_and_sse_respond_while_other_project_creation_is_blocked(monkeypatch, tmp_path):
    from test_service_protocol import service_clients, command
    from uuid import uuid4
    async def scenario():
        model = StreamingToolModel(replies=[AIMessage(content='HTTP answer')])
        async with service_clients(monkeypatch, tmp_path, model) as (client, _, server, config):
            root = tmp_path / 'a'
            root.mkdir()
            app = server.state.application
            project = await app.projects.add_local(root, trusted=True, operation_id='a')
            scope = '/api/v1/scopes/' + project.project_id
            sid = (await client.post(scope + '/sessions', json={'model_id': 'A'}, headers=command())).json()['session_id']
            entered, release = Event(), Event()
            original = operations.subprocess.run
            def git(args, **kwargs):
                if args[:2] != ['git', 'init']:
                    return original(args, **kwargs)
                entered.set()
                assert release.wait(10)
                return SimpleNamespace(returncode=0)
            monkeypatch.setattr(operations.subprocess, 'run', git)
            creating = asyncio.create_task(client.post('/api/v1/projects/create', headers=command(), json={
                'parent': str(tmp_path), 'folder_name': 'b', 'trusted': True, 'git_init': True}))
            assert await asyncio.to_thread(entered.wait, 5)
            finished, responsive = Event(), Event()
            loop = asyncio.get_running_loop()
            observations = []
            def watchdog():
                try:
                    loop.call_soon_threadsafe(responsive.set)
                    observations.append(responsive.wait(1))
                    if observations[-1]:
                        observations.append(finished.wait(5))
                finally:
                    release.set()
            worker = Thread(target=watchdog)
            worker.start()
            try:
                handshake = await client.post('/api/v1/handshake', json=config.identity.model_dump(mode='json'))
                assert handshake.status_code == 200
                identity = str(uuid4())
                started = await client.post(scope + '/sessions/' + sid + '/runs', headers={'X-Request-ID': identity},
                                            json={'request_id': identity, 'text': 'run'})
                assert started.status_code == 202, started.text
                events = await client.get(scope + '/runs/' + started.json()['run_id'] + '/events')
                assert events.status_code == 200 and 'run_finished' in events.text and 'HTTP answer' in events.text
                finished.set()
                assert not creating.done()
            finally:
                finished.set()
                release.set()
                assert (await creating).status_code == 201
                await asyncio.to_thread(worker.join, 5)
            assert observations == [True, True]
    asyncio.run(scenario())
