"""Host opening and closing preserve identity and resource ownership on failure."""
import asyncio
import shutil

import pytest
from langchain_core.messages import AIMessage

from naiyue_test_agent.contracts.projects import ProjectError
from naiyue_test_agent.storage.project_store import ProjectStore
from test_async_application import StreamingToolModel
from test_multi_project_runtime import multi_app, projects


def test_chat_profile_refuses_project_database_before_migration(monkeypatch, tmp_path):
    async def scenario():
        async with multi_app(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (app, _):
            (pa, _), _ = await projects(app, tmp_path)
            await app.projects.close(pa.project_id)
            target = tmp_path / 'data/chats/sessions.sqlite'
            target.parent.mkdir()
            shutil.copyfile(pa.database_path, target)
            before = target.read_bytes()
            with pytest.raises(ProjectError, match='chat_database_mismatch'):
                await app.chats.open()
            assert target.read_bytes() == before
            with ProjectStore(target):
                pass
    asyncio.run(scenario())


def test_project_graph_failure_releases_store_and_keeps_shared_model(monkeypatch, tmp_path):
    from naiyue_test_agent.application.sessions.session_service import SessionService
    async def scenario():
        async with multi_app(monkeypatch, tmp_path, StreamingToolModel(replies=[AIMessage(content='ok')])) as (app, _):
            (pa, a), (pb, _) = await projects(app, tmp_path)
            await app.projects.close(pb.project_id)
            original = SessionService.assemble_runtime
            expected = RuntimeError('assembly failed')
            def fail(self, *args, **kwargs):
                if self._project.project_id == pb.project_id:
                    raise expected
                return original(self, *args, **kwargs)
            monkeypatch.setattr(SessionService, 'assemble_runtime', fail)
            with pytest.raises(RuntimeError) as caught:
                await app.projects.open(pb.project_id)
            assert caught.value is expected
            with ProjectStore(pb.database_path):
                pass
            sid = await a.sessions.create('A')
            await a.runs.start(sid, [('user', 'still running')])
            monkeypatch.setattr(SessionService, 'assemble_runtime', original)
            assert (await app.projects.open(pb.project_id)).project_id == pb.project_id
    asyncio.run(scenario())


def test_project_close_does_not_close_application_models(monkeypatch, tmp_path):
    async def scenario():
        async with multi_app(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (app, _):
            (project, host), _ = await projects(app, tmp_path)
            calls = []
            original = app.models._catalog.aclose
            async def close():
                calls.append(True)
                await original()
            monkeypatch.setattr(app.models._catalog, 'aclose', close)
            assert await app.projects.open(project.project_id) is host
            await app.projects.close(project.project_id)
            assert calls == []
            chat = await app.chats.open()
            assert await chat.sessions.create('A')
        assert calls == [True]
    asyncio.run(scenario())


def test_unloaded_history_subagent_and_regeneration_reject_other_host(monkeypatch, tmp_path):
    from naiyue_test_agent.storage.errors import SessionPersistenceError
    async def scenario():
        async with multi_app(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (app, _):
            (_, a), (_, b) = await projects(app, tmp_path)
            sid = await a.sessions.create('A')
            for operation in (b.history.chat_subagents(sid), b.runs.regeneration_action(sid, 'request')):
                with pytest.raises(SessionPersistenceError, match='session_not_found'):
                    await operation
    asyncio.run(scenario())
