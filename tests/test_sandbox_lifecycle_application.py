"""Idle and shutdown stop the original container while retaining its data."""
import asyncio
from contextlib import ExitStack
from uuid import uuid4

import pytest

from git_input_helpers import repository
from sandbox_session_helpers import SandboxTransport
from test_sandbox_profile_sessions import application
from test_sandbox_profiles import catalog
from naiyue_test_agent.contracts.sandbox import SandboxError


def test_capacity_failure_keeps_original_preparation_and_retries_after_a_stop(tmp_path, monkeypatch):
    root = repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            transport = SandboxTransport(tmp_path, monkeypatch, stack)
            async with application(tmp_path, dict(catalog(), lifecycle=dict(max_running=1, max_retained=2))) as app:
                project = await app.projects.add_local(root, trusted=True, operation_id='P')
                host = await app.projects.open(project.project_id)
                first = await host.sessions.create_sandbox('fake', request_id=str(uuid4()))
                request = str(uuid4()); second = await host.sessions.create_sandbox('fake', request_id=request)
                status = await host.sessions.status(second)
                assert status['execution']['error']['code'] == 'env_running_capacity'
                assert len(transport.created) == 1 and not status['can_run']
                binding = (await host.sessions.status(first))['execution']['binding_id']
                await app.environments.stop(binding)
                assert await host.sessions.create_sandbox('fake', request_id=request) == second
                assert (await host.sessions.status(second))['execution']['state'] == 'ready'
                assert len(transport.created) == 2
    asyncio.run(scenario())


@pytest.mark.parametrize('protected', ['none', 'using', 'background', 'approval', 'legacy'])
def test_idle_sweep_checks_real_activity_before_stopping(tmp_path, monkeypatch, protected):
    root = repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            SandboxTransport(tmp_path, monkeypatch, stack)
            async with application(tmp_path, dict(catalog(), lifecycle=dict(idle_stop_seconds=30))) as app:
                project = await app.projects.add_local(root, trusted=True, operation_id='P')
                host = await app.projects.open(project.project_id)
                sid = await host.sessions.create_sandbox('fake', request_id=str(uuid4()))
                env = (await app.environments.list(project.project_id))[0]
                records = app.environments._service.records()
                records.save(env.changed(last_used_at=None if protected == 'legacy' else 100.0))
                if protected == 'approval':
                    with host.sessions._persistence.storage.sessions.database.transaction(write=True) as connection:
                        connection.execute('UPDATE naiyue_sessions SET state=? WHERE session_id=?',('interrupted',sid))
                if protected == 'background':
                    app.environments._operations.records().reserve(env.binding_id, project.project_id, sid,
                        'a'*32, 'shell', {'command': 'pending'}, {})
                with ExitStack() as guards:
                    if protected == 'using': guards.enter_context(app.environments._service.activity.use(env.binding_id, used=False))
                    await app.environments.sweep_idle(now=200.0)
                assert (await app.environments.get(env.binding_id)).lifecycle == ('stopped' if protected == 'none' else 'running')
                if protected == 'background':
                    # This injected controller-only row has no remote process to stop.
                    with records.database.transaction(write=True) as connection:
                        connection.execute('DELETE FROM sandbox_operations WHERE binding_id=? AND operation_id=?',(env.binding_id,'a'*32))
    asyncio.run(scenario())


def test_application_shutdown_stops_containers_without_destroying_and_reopen_uses_original(tmp_path, monkeypatch):
    root = repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            transport = SandboxTransport(tmp_path, monkeypatch, stack)
            async with application(tmp_path, catalog()) as app:
                project = await app.projects.add_local(root, trusted=True, operation_id='P')
                host = await app.projects.open(project.project_id)
                sid = await host.sessions.create_sandbox('fake', request_id=str(uuid4()))
                env = (await app.environments.list(project.project_id))[0]
                await app.environments.stop(env.binding_id)
                transport.stopped.discard(env.binding_id)  # An external restart must still be stopped on service exit.
            async with application(tmp_path, {}) as app:
                assert (await app.environments.get(env.binding_id)).lifecycle == 'stopped'
                assert env.binding_id in transport.stopped
                assert env.binding_id not in transport.expired
                await app.environments.start(env.binding_id)
                host = await app.projects.open(project.project_id)
                assert (await host.sessions.prepare_environment(sid))['state'] == 'ready'
                assert len(transport.created) == 1
    asyncio.run(scenario())
