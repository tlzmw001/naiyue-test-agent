"""Async operation ownership and bounded combined output polling."""
import asyncio
import pytest
from threading import Event
from contextlib import closing
from types import SimpleNamespace

from naiyue_test_agent.contracts.environment import EnvironmentRef
from naiyue_test_agent.envd.operations import EnvironmentOperations
from naiyue_test_agent.sandbox_settings import SandboxSettings
from sandbox_operation_helpers import sandbox_operation_factory


def test_envd_poll_returns_status_and_same_offset_window(tmp_path):
    root=tmp_path/'workspace';root.mkdir()
    env=EnvironmentRef('B','sandbox','P',str(root),'linux')
    with EnvironmentOperations(env,'S',tmp_path/'state',SandboxSettings()) as operations:
        operations.journal.reserve('a'*32,'shell',{}, {})
        operations.journal.append('a'*32,b'output')
        value=operations.dispatch(dict(protocol=2,request_id='poll',environment=env.public(),session_id='S',
            operation='operation_poll',params=dict(operation_id='a'*32,offset=0,limit=524288)))
        assert value['operation']['observed_bytes']==6
        assert value['output']['offset']==0 and value['output']['next_offset']==6
        assert value['output']['bytes_base64']=='b3V0cHV0'


def test_operation_lock_is_binding_scoped_and_retires_after_collection(tmp_path):
    from naiyue_test_agent.application.environments.operations import SandboxOperationService
    from naiyue_test_agent.application.environments.activity import EnvironmentActivity
    from naiyue_test_agent.execution.sandbox_runtime import SandboxRuntime
    from naiyue_test_agent.storage.project_registry import ProjectRegistry
    from naiyue_test_agent.storage.environment_registry import EnvironmentRegistry
    runtime=SandboxRuntime()
    with closing(ProjectRegistry(tmp_path/'projects.sqlite')) as database:
        service=SimpleNamespace(transport=SimpleNamespace(runtime=runtime),
            records=lambda:EnvironmentRegistry(database.database),projects=SimpleNamespace(data_dir=tmp_path))
        local=SandboxOperationService(service,activity=EnvironmentActivity(service.records))
        async def scenario():
            entered=asyncio.Event();release=asyncio.Event();order=[]
            async def first():
                async with local._operation_lock('A','same-id'):
                    entered.set();await release.wait();order.append('A')
            task=asyncio.create_task(first());await entered.wait()
            async with local._operation_lock('B','same-id'):order.append('B')
            assert order==['B']
            release.set();await task
            assert order==['B','A'] and not local._locks
        try:runtime.call(scenario())
        finally:runtime.close()


def test_stop_is_sent_while_an_output_poll_is_blocked(tmp_path,sandbox_operation_factory):
    from naiyue_test_agent.application.environments.activity import EnvironmentActivity
    from naiyue_test_agent.storage.project_registry import ProjectRegistry
    from naiyue_test_agent.storage.environment_registry import EnvironmentRegistry
    with closing(ProjectRegistry(tmp_path/'projects.sqlite')) as database:
        service=SimpleNamespace(records=lambda:EnvironmentRegistry(database.database),projects=SimpleNamespace(data_dir=tmp_path))
        local=sandbox_operation_factory(service,activity=EnvironmentActivity(service.records))
        local.records().reserve('B','P','S','a'*32,'write',{'subject':{}}, {})
        async def scenario():
            entered,release,stopped=asyncio.Event(),asyncio.Event(),asyncio.Event()
            calls=[]
            async def rpc(binding,operation,params,**kwargs):
                calls.append(operation)
                if operation=='operation_stop':stopped.set()
                else:
                    assert operation=='operation_poll'
                    entered.set();await release.wait()
                row=dict(local.records().get(binding,params['operation_id']),state='stopped' if stopped.is_set() else 'running')
                return {'operation':row,'output':None} if operation=='operation_poll' else row
            local._rpc=rpc
            poll=asyncio.create_task(local._refresh('B','a'*32))
            await entered.wait()
            stop=asyncio.create_task(local._stop('B','a'*32))
            try:
                await asyncio.wait_for(stopped.wait(),5)
                assert not poll.done() and calls==['operation_poll','operation_stop']
            finally:release.set()
            await poll
            result=await stop
            assert result['state']=='stopped' and not local._locks
        local.runtime.call(scenario())


def test_cancel_during_archive_creation_saves_identity_before_releasing_custody(tmp_path,sandbox_operation_factory,monkeypatch):
    from naiyue_test_agent.application.environments.activity import EnvironmentActivity
    from naiyue_test_agent.execution.sandbox_output import SandboxOutput
    from naiyue_test_agent.storage.project_registry import ProjectRegistry
    from naiyue_test_agent.storage.environment_registry import EnvironmentRegistry
    with closing(ProjectRegistry(tmp_path/'projects.sqlite')) as database:
        service=SimpleNamespace(records=lambda:EnvironmentRegistry(database.database),projects=SimpleNamespace(data_dir=tmp_path))
        local=sandbox_operation_factory(service,activity=EnvironmentActivity(service.records))
        local.records().reserve('B','P','S','a'*32,'shell',{},dict(offset=0,saved_bytes=0,max_archive_bytes=1000,tail_limit=100))
        entered,release=Event(),Event();calls=[]
        original=SandboxOutput.prepare
        def prepare(self,metadata):
            result=original(self,metadata)
            if not metadata.get('archive_identity'):entered.set();assert release.wait(5)
            return result
        monkeypatch.setattr(SandboxOutput,'prepare',prepare)
        async def rpc(binding,operation,params,**kwargs):
            calls.append(operation)
            return {'operation':dict(local.records().get(binding,params['operation_id']),state='running'),'output':None}
        local._rpc=rpc
        async def scenario():
            task=asyncio.create_task(local._refresh('B','a'*32))
            assert await asyncio.to_thread(entered.wait,5)
            task.cancel();release.set()
            with pytest.raises(asyncio.CancelledError):await task
            assert local.records().get('B','a'*32)['metadata']['archive_identity']
            assert not (await local._refresh('B','a'*32))['stale']
            assert calls==['operation_poll','operation_poll']
        local.runtime.call(scenario())


def test_cancelled_submission_retains_order_before_stop(tmp_path,sandbox_operation_factory):
    from naiyue_test_agent.execution.sandbox_channel import SubmittedRequestCancelled
    from naiyue_test_agent.application.environments.activity import EnvironmentActivity
    from naiyue_test_agent.storage.project_registry import ProjectRegistry
    from naiyue_test_agent.storage.environment_registry import EnvironmentRegistry
    with closing(ProjectRegistry(tmp_path/'projects.sqlite')) as database:
        env=SimpleNamespace(project_id='P',session_id='S')
        service=SimpleNamespace(records=lambda:EnvironmentRegistry(database.database),
            check=lambda *_,**__:env,projects=SimpleNamespace(data_dir=tmp_path))
        local=sandbox_operation_factory(service,activity=EnvironmentActivity(service.records))
        async def scenario():
            entered,release=asyncio.Event(),asyncio.Event();calls=[];remote_state='running'
            async def rpc(binding,operation,params,**kwargs):
                nonlocal remote_state
                calls.append(operation)
                if operation=='operation_submit':
                    entered.set()
                    waiting=asyncio.create_task(release.wait())
                    try:await asyncio.shield(waiting)
                    except asyncio.CancelledError:
                        await waiting
                        row=dict(local.records().get(binding,params['operation_id']),state=remote_state)
                        raise SubmittedRequestCancelled(result=row)
                if operation=='operation_stop':remote_state='stopped'
                row=dict(local.records().get(binding,params['operation_id']),state=remote_state)
                return {'operation':row,'output':None} if operation=='operation_poll' else row
            local._rpc=rpc
            submit=asyncio.create_task(local._submit('B','a'*32,'write',{'subject':{}},{}))
            await entered.wait();submit.cancel()
            stopping=asyncio.create_task(local._stop('B','a'*32))
            await asyncio.sleep(0)
            assert not stopping.done() and calls==['operation_submit']
            release.set()
            with pytest.raises(asyncio.CancelledError):await submit
            assert (await stopping)['state']=='stopped'
            assert calls==['operation_submit','operation_stop','operation_poll']
            assert not local._locks and not local._submissions
        local.runtime.call(scenario())
