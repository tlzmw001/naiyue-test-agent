"""Continuing a cancelled preparation starts only the original empty sandbox."""
import asyncio
from contextlib import ExitStack
from threading import Event
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest

from git_input_helpers import repository
from sandbox_session_helpers import SandboxTransport
from test_sandbox_profile_sessions import application
from test_sandbox_profiles import catalog
from test_docker_environment import record
from naiyue_test_agent.contracts.sandbox import SandboxError


@pytest.mark.parametrize('initial_cancel',['task','cooperative'])
@pytest.mark.parametrize('outcome',['ready','cancel','start_failure','capacity'])
def test_cancelled_empty_environment_continues_without_recreation(tmp_path,monkeypatch,outcome,initial_cancel):
    root=repository(tmp_path);primary=SandboxError('env_start_failed')
    async def scenario():
        with ExitStack() as stack:
            transport=SandboxTransport(tmp_path,monkeypatch,stack)
            async with application(tmp_path,catalog()) as app:
                project=await app.projects.add_local(root,trusted=True,operation_id='P')
                host=await app.projects.open(project.project_id)
                other=await host.sessions.create_sandbox('fake',request_id=str(uuid4()))
                snapshot_started,created,release=Event(),Event(),Event()
                prepare=app.environments._inputs.prepare;create=app.environments._service.create
                def snapshot(*args,**kwargs):
                    snapshot_started.set();assert release.wait(15);return prepare(*args,**kwargs)
                def container(*args,**kwargs):
                    row=create(*args,**kwargs);created.set();return row
                monkeypatch.setattr(app.environments._inputs,'prepare',snapshot)
                monkeypatch.setattr(app.environments._service,'create',container)
                sid=await host.sessions.create_sandbox('fake',request_id=str(uuid4()),wait_for_ready=False)
                binding=(await host.sessions.status(sid))['execution']['binding_id']
                task=app.environments._preparations._jobs[binding][1]
                try:
                    assert await asyncio.to_thread(snapshot_started.wait,5)
                    assert await asyncio.to_thread(created.wait,5)
                    if initial_cancel=='task':task.cancel();await asyncio.sleep(0)
                    else:
                        stopping=asyncio.create_task(app.environments._preparations.stop(binding))
                        await asyncio.sleep(0)
                        assert app.environments._preparations._jobs[binding][0].cancelled.is_set()
                finally:release.set()
                if initial_cancel=='task':
                    with pytest.raises(asyncio.CancelledError):await task
                else:
                    await stopping
                    with pytest.raises(SandboxError,match='env_preparation_cancelled'):await task
                assert (await app.environments.get(binding)).lifecycle=='stopped'
                if outcome=='capacity':
                    service=app.environments._service
                    service.configuration=service.configuration.model_copy(update={'lifecycle':
                        service.configuration.lifecycle.model_copy(update={'max_running':1})})
                starts,finish=Event(),Event();engine=app.environments._service._engine
                def resumed_engine(row):
                    target=engine(row);action=target.action
                    def changed(current,operation):
                        if operation=='start' and current.binding_id==binding:
                            starts.set()
                            if outcome=='cancel':assert finish.wait(15)
                            if outcome=='start_failure':raise primary
                        return action(current,operation)
                    target.action=changed;return target
                monkeypatch.setattr(app.environments._service,'_engine',resumed_engine)
                continuing=asyncio.create_task(host.sessions.prepare_environment(sid))
                if outcome=='cancel':
                    try:
                        assert await asyncio.to_thread(starts.wait,5)
                        job=app.environments._preparations._jobs[binding][1]
                        job.cancel();await asyncio.sleep(0);job.cancel();await asyncio.sleep(0)
                        assert not continuing.done()
                    finally:finish.set()
                    with pytest.raises(asyncio.CancelledError):await continuing
                    assert (await app.environments.get(binding)).lifecycle=='stopped'
                    assert transport.actions[-1]==(binding,'stop')
                elif outcome=='start_failure':
                    with pytest.raises(SandboxError) as caught:await continuing
                    assert caught.value is primary
                    assert (await app.environments.get(binding)).lifecycle=='unknown'
                elif outcome=='capacity':
                    with pytest.raises(SandboxError,match='env_running_capacity'):await continuing
                    assert not starts.is_set()
                    assert (await app.environments.get(binding)).lifecycle=='stopped'
                else:
                    ready=await continuing
                    assert ready['state']=='ready'
                    row=await app.environments.get(binding)
                    assert row.lifecycle=='running' and row.input_snapshot is not None
                    assert transport.actions.count((binding,'start'))==1
                assert transport.created.count(binding)==1 and binding not in transport.expired
                assert (await host.sessions.status(other))['execution']['state']=='ready'
    asyncio.run(scenario())


@pytest.mark.parametrize('reason',['unknown','destroyed','input_id','input_operation_id','input_snapshot','operation'])
def test_continuation_never_starts_unsafe_or_nonempty_records(reason):
    from naiyue_test_agent.application.sessions.execution_binding import _resume_empty_preparation
    from naiyue_test_agent.application.environments.preparation import PreparationProgress
    from naiyue_test_agent.application.environments.activity import EnvironmentActivity
    row=record().changed(lifecycle='stopped')
    if reason in ('unknown','destroyed'):row=row.changed(lifecycle=reason)
    elif reason!='operation':row=row.changed(**{reason:'a'*32})
    actions=[];service=SimpleNamespace(get=lambda _:row,activity=EnvironmentActivity(lambda:None),
        _perform_action=lambda *args:actions.append(args))
    api=SimpleNamespace(get=AsyncMock(return_value=row),_service=service,
        _operations=SimpleNamespace(records=lambda:SimpleNamespace(list=lambda _:[{'state':'completed'}] if reason=='operation' else [])))
    asyncio.run(_resume_empty_preparation(api,SimpleNamespace(binding_id=row.binding_id,selection=None),
        dict(project_id=row.project_id,session_id=row.session_id),PreparationProgress(row.binding_id,row.project_id,row.session_id)))
    assert actions==[]


@pytest.mark.parametrize('foreign',['project_id','session_id','binding_id','selection'])
def test_continuation_rejects_foreign_identity_before_start(foreign):
    from naiyue_test_agent.application.sessions.execution_binding import _resume_empty_preparation
    from naiyue_test_agent.application.environments.preparation import PreparationProgress
    from naiyue_test_agent.application.environments.activity import EnvironmentActivity
    row=record().changed(lifecycle='stopped');actions=[]
    setup=SimpleNamespace(binding_id=row.binding_id,selection=object() if foreign=='selection' else None)
    session=dict(project_id=row.project_id,session_id=row.session_id)
    if foreign in session:session[foreign]='foreign'
    elif foreign=='binding_id':setup.binding_id='b'*32
    api=SimpleNamespace(get=AsyncMock(return_value=row),_service=SimpleNamespace(get=lambda _:row,
        activity=EnvironmentActivity(lambda:None),_perform_action=lambda *args:actions.append(args)))
    with pytest.raises(SandboxError,match='env_binding_mismatch'):
        asyncio.run(_resume_empty_preparation(api,setup,session,PreparationProgress(row.binding_id,row.project_id,row.session_id)))
    assert actions==[]
