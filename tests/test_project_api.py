"""The real application exposes projects and owns its lazy registry resource."""
import asyncio
from contextlib import asynccontextmanager

import pytest

from naiyue_test_agent import application
from naiyue_test_agent.application.service_application import open_service_application
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.settings import AppSettings
from test_async_application import StreamingToolModel


@asynccontextmanager
async def app_context(monkeypatch, tmp_path):
    monkeypatch.setattr(application, 'load_model_catalog',
        lambda _: ModelCatalog('A', {'A': StreamingToolModel(replies=[])}))
    options = dict(tools=[], system_prompt='', response_format=None,
        settings=AppSettings.model_validate({'storage': {'data_dir': str(tmp_path / 'data')}}))
    async with open_service_application('unused', **options) as app:
        yield app


def test_application_wires_projects_and_closes_registry(monkeypatch, tmp_path):
    async def scenario():
        async with app_context(monkeypatch, tmp_path) as app:
            assert not (tmp_path / 'data/projects.sqlite').exists()
            project = await app.projects.add_local(tmp_path, trusted=True, operation_id='add')
            assert (await app.projects.list())[0] == project
            assert (await app.projects.rename(project.project_id, 'Renamed', operation_id='rename')).name == 'Renamed'
        with pytest.raises(RuntimeError, match='owning event loop'):
            await app.projects.list()
        from naiyue_test_agent.storage.project_registry import ProjectRegistry
        registry = ProjectRegistry(tmp_path / 'data/projects.sqlite')
        registry.close()
    asyncio.run(scenario())


def test_temporary_host_has_no_project_api_or_persistent_effect(monkeypatch, tmp_path):
    async def scenario():
        async with app_context(monkeypatch, tmp_path) as app:
            host = await app.temporary.open('temporary-component')
            assert not hasattr(host, 'projects')
            sid = await host.sessions.create_workspace('A', tmp_path)
            assert (await host.sessions.status(sid))['project_id'] is None
            await app.temporary.close('temporary-component')
        assert not (tmp_path / 'data').exists()
        assert not (tmp_path / '.naiyue').exists()
    asyncio.run(scenario())


def test_import_current_owned_database_and_refuse_its_deletion(monkeypatch, tmp_path):
    async def scenario():
        from naiyue_test_agent.contracts.projects import ProjectError
        from uuid import uuid4
        identity = str(uuid4())
        binding = {'workspace_id': identity, 'root_path': str(tmp_path)}
        import json
        from naiyue_test_agent.storage.project_store import ProjectStore
        (tmp_path / '.naiyue').mkdir()
        (tmp_path / '.naiyue/workspace.json').write_text(json.dumps({'version': 1, 'workspace_id': identity}))
        with ProjectStore(tmp_path / 'current.sqlite') as store:
            store.sessions.bind_workspace(identity, tmp_path)
        async with app_context(monkeypatch, tmp_path) as app:
            result = await app.projects.import_legacy([tmp_path / 'current.sqlite'])
            assert result[0]['project'].project_id == identity
            await app.projects.add_local(tmp_path, trusted=True, operation_id='trust')
            await app.projects.open(identity)
            with pytest.raises(ProjectError, match='project_in_use'):
                await app.projects.remove(identity, operation_id='remove')
            assert (tmp_path / 'current.sqlite').exists()
        assert tmp_path.is_dir()
    asyncio.run(scenario())


def test_close_waits_for_cancelled_project_operation_to_settle(monkeypatch, tmp_path):
    from threading import Event
    from naiyue_test_agent.storage.project_registry import ProjectRegistry
    entered, release = Event(), Event()
    original = ProjectRegistry.finish
    def finish(self, *args, **kwargs):
        entered.set()
        assert release.wait(10), 'project operation was not released'
        return original(self, *args, **kwargs)
    monkeypatch.setattr(ProjectRegistry, 'finish', finish)
    async def scenario():
        async with app_context(monkeypatch, tmp_path) as app:
            task = asyncio.create_task(app.projects.add_local(tmp_path, trusted=True, operation_id='add'))
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                task.cancel()
                closing = asyncio.create_task(app.aclose())
                await asyncio.sleep(0)
                assert not closing.done()
            finally:
                release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            await closing
        from test_project_registry import service
        with service(tmp_path) as projects:
            assert projects.operations()[0].stage == 'done'
            assert len(projects.list()) == 1
    asyncio.run(scenario())
