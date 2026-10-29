import asyncio
from contextlib import ExitStack
from threading import Event
from uuid import uuid4

from git_input_helpers import repository
from sandbox_session_helpers import SandboxTransport
from test_sandbox_profile_sessions import application
from test_sandbox_profiles import catalog


def test_background_prepare_returns_identity_and_stop_drains_create_without_install(tmp_path,monkeypatch):
    root=repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            transport=SandboxTransport(tmp_path,monkeypatch,stack)
            from naiyue_test_agent.application.environments.service import DockerEnvironment
            original=DockerEnvironment.create;entered,release=Event(),Event()
            def blocked(self,row,save):
                entered.set();assert release.wait(10);return original(self,row,save)
            monkeypatch.setattr(DockerEnvironment,'create',blocked)
            async with application(tmp_path,catalog()) as app:
                project=await app.projects.add_local(root,trusted=True,operation_id='P')
                host=await app.projects.open(project.project_id)
                sid=await host.sessions.create_sandbox('fake',request_id=str(uuid4()),wait_for_ready=False)
                assert await asyncio.to_thread(entered.wait,10)
                status=await host.sessions.status(sid)
                binding=status['execution']['binding_id']
                assert status['execution']['state']=='preparing'
                assert status['execution']['preparation']['phase']=='container'
                stopping=asyncio.create_task(app.environments.manage(binding,'stop',interrupt=True))
                await asyncio.sleep(0)
                assert not stopping.done()
                release.set()
                assert (await stopping)['lifecycle']=='stopped'
                record=await app.environments.get(binding)
                assert record.input_operation_id is None and record.input_snapshot is None
                assert transport.created==[binding] and binding in transport.stopped
                assert (await host.sessions.status(sid))['execution']['state']!='ready'
    asyncio.run(scenario())


def test_explicit_interrupt_can_stop_busy_binding_without_touching_another(tmp_path,monkeypatch):
    root=repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            transport=SandboxTransport(tmp_path,monkeypatch,stack)
            async with application(tmp_path,catalog()) as app:
                project=await app.projects.add_local(root,trusted=True,operation_id='P')
                host=await app.projects.open(project.project_id)
                first=await host.sessions.create_sandbox('fake',request_id=str(uuid4()))
                second=await host.sessions.create_sandbox('fake',request_id=str(uuid4()))
                a=(await host.sessions.status(first))['execution']['binding_id']
                b=(await host.sessions.status(second))['execution']['binding_id']
                with app.environments._service.activity.use(a,used=False):
                    assert (await app.environments.manage(a,'stop',interrupt=True))['lifecycle']=='stopped'
                assert b not in transport.stopped
                assert (await host.sessions.status(second))['execution']['state']=='ready'
    asyncio.run(scenario())


def test_session_cannot_be_deleted_while_snapshot_precedes_environment_registration(tmp_path,monkeypatch):
    import pytest
    from naiyue_test_agent.storage.errors import SessionPersistenceError
    root=repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            transport=SandboxTransport(tmp_path,monkeypatch,stack)
            async with application(tmp_path,catalog()) as app:
                project=await app.projects.add_local(root,trusted=True,operation_id='P')
                host=await app.projects.open(project.project_id)
                entered,release=Event(),Event();original=app.environments._inputs.prepare
                def prepare(*args,**kwargs):entered.set();assert release.wait(10);return original(*args,**kwargs)
                monkeypatch.setattr(app.environments._inputs,'prepare',prepare)
                sid=await host.sessions.create_sandbox('fake',request_id=str(uuid4()),wait_for_ready=False)
                assert await asyncio.to_thread(entered.wait,10)
                try:
                    with pytest.raises(SessionPersistenceError,match='session_environment_in_use'):
                        await host.sessions.delete(sid)
                finally:release.set()
                row=await host.sessions._persistence.astatus(sid)
                binding=row['execution']['binding_id']
                await app.environments._preparations.stop(binding)
                assert (await host.sessions.list())[0]['session_id']==sid
    asyncio.run(scenario())
