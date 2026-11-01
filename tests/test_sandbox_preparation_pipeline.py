"""Parallel preparation owns both branches until failure/cancellation settles."""
import asyncio
from contextlib import ExitStack,nullcontext
from threading import Event
from uuid import uuid4

import pytest

from git_input_helpers import repository
from sandbox_session_helpers import SandboxTransport
from test_sandbox_profile_sessions import application
from test_sandbox_profiles import catalog
from naiyue_test_agent.contracts.git_inputs import GitInputError


def test_snapshot_and_container_begin_together_and_install_waits_for_both(tmp_path,monkeypatch):
    root=repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            SandboxTransport(tmp_path,monkeypatch,stack)
            async with application(tmp_path,catalog()) as app:
                project=await app.projects.add_local(root,trusted=True,operation_id='P')
                host=await app.projects.open(project.project_id)
                snapshots,containers,release=Event(),Event(),Event();installed=[]
                prepare=app.environments._inputs.prepare;create=app.environments._service.create
                install=app.environments.install_input
                def snapshot(*args,**kwargs):
                    snapshots.set();assert release.wait(15);return prepare(*args,**kwargs)
                def container(*args,**kwargs):
                    containers.set();assert release.wait(15);return create(*args,**kwargs)
                async def install_(*args,**kwargs):
                    installed.append(1);return await install(*args,**kwargs)
                monkeypatch.setattr(app.environments._inputs,'prepare',snapshot)
                monkeypatch.setattr(app.environments._service,'create',container)
                monkeypatch.setattr(app.environments,'install_input',install_)
                sid=await host.sessions.create_sandbox('fake',request_id=str(uuid4()),wait_for_ready=False)
                binding=(await host.sessions.status(sid))['execution']['binding_id']
                try:
                    assert await asyncio.to_thread(snapshots.wait,5)
                    assert await asyncio.to_thread(containers.wait,5)
                    assert installed==[]
                finally:release.set()
                await app.environments._preparations.wait(binding)
                assert installed==[1] and (await host.sessions.status(sid))['execution']['state']=='ready'
    asyncio.run(scenario())


@pytest.mark.parametrize('cleanup_fails',[False,True])
def test_snapshot_failure_drains_creation_then_rolls_back_only_its_new_uninstalled_environment(tmp_path,monkeypatch,caplog,cleanup_fails):
    root=repository(tmp_path);primary=GitInputError('git_input_sensitive')
    async def scenario():
        with ExitStack() as stack:
            transport=SandboxTransport(tmp_path,monkeypatch,stack)
            async with application(tmp_path,catalog()) as app:
                project=await app.projects.add_local(root,trusted=True,operation_id='P')
                host=await app.projects.open(project.project_id)
                other=await host.sessions.create_sandbox('fake',request_id=str(uuid4()))
                other_binding=(await host.sessions.status(other))['execution']['binding_id']
                snapshots,containers,release=Event(),Event(),Event();installed=[]
                create=app.environments._service.create;action=app.environments._service._perform_action
                def snapshot(*args,**kwargs):
                    snapshots.set();assert containers.wait(15);raise primary
                def container(*args,**kwargs):
                    containers.set();assert release.wait(15);return create(*args,**kwargs)
                def stop(binding,operation,**kwargs):
                    if operation!='destroy':return action(binding,operation,**kwargs)
                    assert binding!=other_binding and operation=='destroy'
                    if cleanup_fails:raise OSError('PRIVATE_SECONDARY')
                    return action(binding,operation,**kwargs)
                async def install(*args,**kwargs):installed.append(1)
                monkeypatch.setattr(app.environments._inputs,'prepare',snapshot)
                monkeypatch.setattr(app.environments._service,'create',container)
                monkeypatch.setattr(app.environments._service,'_perform_action',stop)
                monkeypatch.setattr(app.environments,'install_input',install)
                sid=await host.sessions.create_sandbox('fake',request_id=str(uuid4()),wait_for_ready=False)
                binding=(await host.sessions.status(sid))['execution']['binding_id']
                waiter=asyncio.create_task(app.environments._preparations.wait(binding))
                try:
                    assert await asyncio.to_thread(snapshots.wait,5)
                    assert await asyncio.to_thread(containers.wait,5)
                    assert not waiter.done()
                finally:release.set()
                with pytest.raises(GitInputError) as caught:await waiter
                assert caught.value is primary and installed==[]
                row=await app.environments.get(binding)
                assert row.input_id is None and row.input_operation_id is None and row.input_snapshot is None
                assert row.lifecycle==('running' if cleanup_fails else 'destroyed')
                assert other_binding not in transport.stopped
                assert (await host.sessions.status(other))['execution']['state']=='ready'
    asyncio.run(scenario())
    if cleanup_fails:
        assert 'sandbox_preparation_cleanup_failed' in caplog.text and 'PRIVATE_SECONDARY' not in caplog.text


def test_install_failure_is_never_treated_as_an_empty_preparation(tmp_path,monkeypatch):
    from naiyue_test_agent.contracts.sandbox import SandboxError
    root=repository(tmp_path);primary=SandboxError('env_operation_unknown')
    async def scenario():
        with ExitStack() as stack:
            transport=SandboxTransport(tmp_path,monkeypatch,stack)
            async with application(tmp_path,catalog()) as app:
                project=await app.projects.add_local(root,trusted=True,operation_id='P')
                host=await app.projects.open(project.project_id)
                async def install(*args,**kwargs):raise primary
                monkeypatch.setattr(app.environments,'install_input',install)
                sid=await host.sessions.create_sandbox('fake',request_id=str(uuid4()),wait_for_ready=False)
                binding=(await host.sessions.status(sid))['execution']['binding_id']
                with pytest.raises(SandboxError) as caught:await app.environments._preparations.wait(binding)
                assert caught.value is primary and binding not in transport.stopped
                assert (await app.environments.get(binding)).lifecycle=='running'
    asyncio.run(scenario())


def test_repeated_cancellation_drains_both_workers_before_stopping_own_empty_environment(tmp_path,monkeypatch):
    root=repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            transport=SandboxTransport(tmp_path,monkeypatch,stack)
            async with application(tmp_path,catalog()) as app:
                project=await app.projects.add_local(root,trusted=True,operation_id='P')
                host=await app.projects.open(project.project_id)
                snapshots,containers,release=Event(),Event(),Event()
                prepare=app.environments._inputs.prepare;create=app.environments._service.create
                def snapshot(*args,**kwargs):
                    snapshots.set();assert release.wait(15);return prepare(*args,**kwargs)
                def container(*args,**kwargs):
                    containers.set();assert release.wait(15);return create(*args,**kwargs)
                monkeypatch.setattr(app.environments._inputs,'prepare',snapshot)
                monkeypatch.setattr(app.environments._service,'create',container)
                sid=await host.sessions.create_sandbox('fake',request_id=str(uuid4()),wait_for_ready=False)
                binding=(await host.sessions.status(sid))['execution']['binding_id']
                task=app.environments._preparations._jobs[binding][1]
                try:
                    assert await asyncio.to_thread(snapshots.wait,5)
                    assert await asyncio.to_thread(containers.wait,5)
                    task.cancel();await asyncio.sleep(0);task.cancel();await asyncio.sleep(0)
                    assert not task.done() and binding not in transport.stopped
                finally:release.set()
                with pytest.raises(asyncio.CancelledError):await task
                assert (await app.environments.get(binding)).lifecycle=='stopped'
                assert app.environments._preparations.view(binding)['state']=='cancelled'
    asyncio.run(scenario())


@pytest.mark.parametrize('marker',['input_id','input_operation_id','input_snapshot','unknown'])
def test_cleanup_never_stops_an_install_marker_or_unknown_environment(marker):
    from types import SimpleNamespace
    from test_docker_environment import record
    from naiyue_test_agent.application.sessions.execution_binding import _cleanup_empty_preparation
    row=record().changed(**({'lifecycle':'unknown'} if marker=='unknown' else {marker:'a'*32}))
    actions=[]
    api=SimpleNamespace(_service=SimpleNamespace(get=lambda _:row,activity=SimpleNamespace(manage=lambda _:nullcontext()),
        _perform_action=lambda *args:actions.append(args)),_operations=SimpleNamespace(records=lambda:SimpleNamespace(list=lambda _:[])))
    asyncio.run(_cleanup_empty_preparation(api,SimpleNamespace(binding_id=row.binding_id),
        dict(project_id=row.project_id,session_id=row.session_id),created_here=True,cancelled=False))
    assert actions==[]


def test_cleanup_rejects_a_foreign_session_even_if_the_environment_looks_empty():
    from types import SimpleNamespace
    from test_docker_environment import record
    from naiyue_test_agent.application.sessions.execution_binding import _cleanup_empty_preparation
    from naiyue_test_agent.contracts.sandbox import SandboxError
    row=record();actions=[]
    api=SimpleNamespace(_service=SimpleNamespace(get=lambda _:row,activity=SimpleNamespace(manage=lambda _:nullcontext()),
        _perform_action=lambda *args:actions.append(args)),_operations=SimpleNamespace(records=lambda:SimpleNamespace(list=lambda _:[])))
    with pytest.raises(SandboxError,match='env_binding_mismatch'):
        asyncio.run(_cleanup_empty_preparation(api,SimpleNamespace(binding_id=row.binding_id),
            dict(project_id=row.project_id,session_id='foreign'),created_here=True,cancelled=False))
    assert actions==[]


@pytest.mark.parametrize('created_here,operations',[(False,[]),(True,[{'state':'completed'}]),(True,[{'state':'unknown'}])])
def test_rollback_requires_new_receipt_and_no_user_operation_ever(created_here,operations):
    from types import SimpleNamespace
    from test_docker_environment import record
    from naiyue_test_agent.application.sessions.execution_binding import _cleanup_empty_preparation
    row=record();actions=[]
    api=SimpleNamespace(_service=SimpleNamespace(get=lambda _:row,activity=SimpleNamespace(manage=lambda _:nullcontext()),
        _perform_action=lambda *args:actions.append(args)),_operations=SimpleNamespace(records=lambda:SimpleNamespace(list=lambda _:operations)))
    asyncio.run(_cleanup_empty_preparation(api,SimpleNamespace(binding_id=row.binding_id),
        dict(project_id=row.project_id,session_id=row.session_id),created_here=created_here,cancelled=False))
    assert actions==[]


def test_creation_receipt_is_not_emitted_when_an_existing_environment_is_returned(tmp_path,monkeypatch):
    root=repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            SandboxTransport(tmp_path,monkeypatch,stack)
            async with application(tmp_path,catalog()) as app:
                project=await app.projects.add_local(root,trusted=True,operation_id='P')
                host=await app.projects.open(project.project_id)
                sid=await host.sessions.create('fake');receipts=[]
                row=await asyncio.to_thread(app.environments._service.create,project.project_id,sid,on_created=receipts.append)
                assert receipts==[row]
                existing=await asyncio.to_thread(app.environments._service.create,project.project_id,sid,
                    binding_id=row.binding_id,on_created=receipts.append)
                assert existing.binding_id==row.binding_id and receipts==[row]
    asyncio.run(scenario())
