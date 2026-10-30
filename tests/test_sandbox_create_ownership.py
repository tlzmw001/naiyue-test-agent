"""Low-level environment creation and deletion share the actual session owner."""
import asyncio
from contextlib import ExitStack
from threading import Event

import pytest

from git_input_helpers import repository
from sandbox_session_helpers import SandboxTransport
from test_sandbox_profile_sessions import application
from test_sandbox_profiles import catalog
from naiyue_test_agent.storage.errors import SessionPersistenceError
from naiyue_test_agent.contracts.sandbox import SandboxError


def test_low_level_create_reserves_owner_until_creation_finishes(tmp_path, monkeypatch):
    root = repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            SandboxTransport(tmp_path, monkeypatch, stack)
            async with application(tmp_path, catalog()) as app:
                project = await app.projects.add_local(root, trusted=True, operation_id='P')
                host = await app.projects.open(project.project_id)
                sid = await host.sessions.create('fake')
                entered, release = Event(), Event()
                original = app.environments._service.create
                def create(*args, **kwargs):
                    entered.set()
                    assert release.wait(10)
                    return original(*args, **kwargs)
                monkeypatch.setattr(app.environments._service, 'create', create)
                task = asyncio.create_task(app.environments.create(project.project_id, sid))
                try:
                    assert await asyncio.to_thread(entered.wait, 10)
                    with pytest.raises(SessionPersistenceError, match='session_recovery_blocked'):
                        await host.sessions.delete(sid)
                finally:
                    release.set()
                    environment = await task
                with pytest.raises(SessionPersistenceError, match='session_environment_in_use'):
                    await host.sessions.delete(sid)
                await app.environments.destroy(environment.binding_id,confirm_destroy=True)
                await host.sessions.delete(sid)
                assert await host.sessions.list() == []
    asyncio.run(scenario())


def test_deleted_owner_is_rechecked_before_creating_environment(tmp_path, monkeypatch):
    root = repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            transport = SandboxTransport(tmp_path, monkeypatch, stack)
            async with application(tmp_path, catalog()) as app:
                project = await app.projects.add_local(root, trusted=True, operation_id='P')
                host = await app.projects.open(project.project_id)
                sid = await host.sessions.create('fake')
                entered, release = asyncio.Event(), asyncio.Event()
                original = app.environments._hosts.open
                async def opening(*args, **kwargs):
                    entered.set()
                    await release.wait()
                    return await original(*args, **kwargs)
                monkeypatch.setattr(app.environments._hosts, 'open', opening)
                task = asyncio.create_task(app.environments.create(project.project_id, sid))
                try:
                    await asyncio.wait_for(entered.wait(), 10)
                    await host.sessions.delete(sid)
                finally:
                    release.set()
                with pytest.raises(SandboxError, match='env_session_mismatch'):
                    await task
                assert not transport.created and await app.environments.list(project.project_id) == ()
    asyncio.run(scenario())
