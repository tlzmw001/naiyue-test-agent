"""Admission and shutdown remain safe through cancellation and partial acquisition."""
import asyncio
from threading import Event

from langchain_core.messages import AIMessage
import pytest

from naiyue_test_agent import application
from naiyue_test_agent.application.service_application import open_service_application
from naiyue_test_agent.application.errors import RunCapacityExceededError
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.storage.project_store import ProjectStore
from naiyue_test_agent.contracts.projects import ProjectError
from test_async_application import StreamingToolModel
from test_multi_project_runtime import multi_app, projects


def test_global_capacity_includes_chats_and_retained_busy_actor(monkeypatch, tmp_path):
    async def scenario():
        async with multi_app(monkeypatch, tmp_path, StreamingToolModel(replies=[AIMessage(content='chat')]),
                capacity=1) as (app, _):
            (_, a), _ = await projects(app, tmp_path)
            chat = await app.chats.open()
            sa, sc = await a.sessions.create('A'), await chat.sessions.create('A')
            actor, run = a.sessions._sessions._admit(sa, 'held', 'message')
            # A retained run still owns work, even if its original Python caller
            # has returned. It must keep consuming global capacity.
            try:
                with pytest.raises(RunCapacityExceededError):
                    await chat.runs.start(sc, [('user', 'blocked')])
            finally:
                actor.controls.close_control_window(run)
                actor.lifecycle.release_run(run)
            await chat.runs.start(sc, [('user', 'allowed')])
    asyncio.run(scenario())


def test_cancelled_host_acquisition_releases_store(monkeypatch, tmp_path):
    from naiyue_test_agent.storage.sessions import SessionRepo
    async def scenario():
        async with multi_app(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (app, _):
            (pa, _), _ = await projects(app, tmp_path)
            await app.projects.close(pa.project_id)
            entered, release = Event(), Event()
            original = SessionRepo.get_workspace_binding
            def gate(self):
                if self.database.path == pa.database_path:
                    entered.set()
                    assert release.wait(10)
                return original(self)
            monkeypatch.setattr(SessionRepo, 'get_workspace_binding', gate)
            opening = asyncio.create_task(app.projects.open(pa.project_id))
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                opening.cancel()
            finally:
                release.set()
            with pytest.raises(asyncio.CancelledError):
                await opening
            monkeypatch.setattr(SessionRepo, 'get_workspace_binding', original)
            with ProjectStore(pa.database_path):
                pass
            assert (await app.projects.open(pa.project_id)).project_id == pa.project_id
    asyncio.run(scenario())


def test_application_close_during_host_open_drains_partial_host(monkeypatch, tmp_path):
    from naiyue_test_agent.storage.sessions import SessionRepo
    async def scenario():
        async with multi_app(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (app, _):
            (pa, _), _ = await projects(app, tmp_path)
            await app.projects.close(pa.project_id)
            entered, release = Event(), Event()
            original = SessionRepo.get_workspace_binding
            def gate(self):
                if self.database.path == pa.database_path:
                    entered.set()
                    assert release.wait(10)
                return original(self)
            monkeypatch.setattr(SessionRepo, 'get_workspace_binding', gate)
            opening = asyncio.create_task(app.projects.open(pa.project_id))
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                closing = asyncio.create_task(app.aclose())
                await asyncio.sleep(0)
                assert not closing.done()
            finally:
                release.set()
            with pytest.raises(RuntimeError, match='owning event loop'):
                await opening
            await closing
            monkeypatch.setattr(SessionRepo, 'get_workspace_binding', original)
            with ProjectStore(pa.database_path):
                pass
    asyncio.run(scenario())


def test_chats_are_explicit_and_root_has_no_session_alias(monkeypatch, tmp_path):
    async def scenario():
        async with multi_app(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (app, _):
            chat = await app.chats.open()
            assert not hasattr(app, 'sessions') and chat.project_id is None
            with pytest.raises(ProjectError, match='project_execution_scope_mismatch'):
                await chat.sessions.create_workspace('A', tmp_path)
    asyncio.run(scenario())


def test_chat_database_cannot_be_imported_as_project(monkeypatch, tmp_path):
    async def scenario():
        async with multi_app(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (app, _):
            chat = await app.chats.open()
            await chat.sessions.create('A')
            database = chat.sessions._persistence.storage.path
            await app.chats.close()
            result = await app.projects.import_legacy([database])
            assert result[0]['project'] is None and result[0]['error'] is not None
            assert await app.projects.list() == ()
            assert database.exists()
    asyncio.run(scenario())


def test_chat_path_normalizes_configured_data_directory(monkeypatch, tmp_path):
    async def scenario():
        monkeypatch.setattr(application, 'load_model_catalog',
            lambda _: ModelCatalog('A', {'A': StreamingToolModel(replies=[])}))
        settings = AppSettings.model_validate({'storage': {'data_dir': str(tmp_path / 'data/../data')}})
        async with open_service_application('unused', settings=settings, tools=[], system_prompt='', response_format=None) as app:
            host = await app.chats.open()
            assert host.sessions._persistence.storage.path == tmp_path / 'data/chats/sessions.sqlite'
            assert await app.chats.open() is host
    asyncio.run(scenario())


def test_registered_host_is_reused_without_default_adoption(monkeypatch, tmp_path):
    async def scenario():
        async with multi_app(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (app, _):
            (pa, _), _ = await projects(app, tmp_path)
        settings = AppSettings.model_validate({'storage': {'data_dir': str(tmp_path / 'data/../data')}})
        async with open_service_application('unused', settings=settings,
                tools=[], system_prompt='', response_format=None) as app:
            host = await app.projects.open(pa.project_id)
            assert await app.projects.open(pa.project_id) is host and not hasattr(app, 'sessions')
            sid = await host.sessions.create('A')
            assert (await host.sessions.status(sid))['project_id'] == pa.project_id
            await app.projects.close(pa.project_id)
            imported = await app.projects.import_legacy([pa.database_path])
            assert imported[0]['error'] is None and imported[0]['project'].project_id == pa.project_id
    asyncio.run(scenario())
