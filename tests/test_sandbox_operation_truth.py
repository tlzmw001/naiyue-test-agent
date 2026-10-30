"""Transport observation never erases the last confirmed operation fact."""
import asyncio
from contextlib import closing
from types import SimpleNamespace

import pytest

from naiyue_test_agent.application.environments.activity import EnvironmentActivity
from naiyue_test_agent.contracts.sandbox import SandboxError
from naiyue_test_agent.execution.sandbox_channel import RequestNotSent
from naiyue_test_agent.storage.project_registry import ProjectRegistry
from naiyue_test_agent.storage.environment_registry import EnvironmentRegistry
from sandbox_operation_helpers import sandbox_operation_factory


def test_rejected_submission_is_known_failed_and_is_never_queried_or_replayed(tmp_path,sandbox_operation_factory):
    with closing(ProjectRegistry(tmp_path/'projects.sqlite')) as db:
        service=SimpleNamespace(records=lambda:EnvironmentRegistry(db.database),
            check=lambda *_,**__:SimpleNamespace(project_id='P',session_id='S'),projects=SimpleNamespace(data_dir=tmp_path))
        local=sandbox_operation_factory(service,activity=EnvironmentActivity(service.records));calls=[]
        async def rpc(binding,operation,params,**kwargs):
            calls.append(operation);raise RequestNotSent('env_connection_capacity')
        local._rpc=rpc
        row=local.submit('B','a'*32,'write',{'subject':{}}, {})
        assert row['state']=='failed' and not row['stale'] and row['error_code']=='env_connection_capacity'
        assert calls==['operation_submit']
        assert local.submit('B','a'*32,'write',{'subject':{}}, {})==row
        assert calls==['operation_submit']


def test_rejected_stop_preserves_status_and_collector_can_continue(tmp_path,sandbox_operation_factory):
    with closing(ProjectRegistry(tmp_path/'projects.sqlite')) as db:
        service=SimpleNamespace(records=lambda:EnvironmentRegistry(db.database),projects=SimpleNamespace(data_dir=tmp_path))
        local=sandbox_operation_factory(service,activity=EnvironmentActivity(service.records));calls=[]
        local.records().reserve('B','P','S','a'*32,'shell',{}, {})
        local.records().save('B','a'*32,dict(state='running'))
        async def rpc(binding,operation,params,**kwargs):
            calls.append(operation)
            if operation=='operation_stop':raise RequestNotSent('env_connection_capacity')
            row=local.records().get(binding,params['operation_id'])
            return dict(row,state='running',stale=False)
        local._rpc=rpc
        with pytest.raises(SandboxError,match='env_stop_unconfirmed'):local.stop('B','a'*32)
        row=local.records().get('B','a'*32)
        assert row['state']=='running' and not row['stale']
        assert calls==['operation_stop','operation_status']


def test_one_failed_status_observation_keeps_running_truth_and_watcher_recovers(tmp_path,sandbox_operation_factory):
    with closing(ProjectRegistry(tmp_path/'projects.sqlite')) as db:
        service=SimpleNamespace(records=lambda:EnvironmentRegistry(db.database),projects=SimpleNamespace(data_dir=tmp_path))
        local=sandbox_operation_factory(service,activity=EnvironmentActivity(service.records))
        local.records().reserve('B','P','S','a'*32,'write',{'subject':{}}, {})
        local.records().save('B','a'*32,dict(state='running'))
        attempts=[]
        async def rpc(binding,operation,params,**kwargs):
            attempts.append(operation)
            if len(attempts)==1:raise SandboxError('env_request_timeout')
            row=local.records().get(binding,params['operation_id'])
            return {'operation':dict(row,state='completed'),'output':None}
        local._rpc=rpc
        first=local.refresh('B','a'*32)
        assert first['state']=='running' and first['stale']
        async def scenario():
            local.watch('B','a'*32,20)
            await asyncio.wait_for(asyncio.gather(*local._watchers.values()),5)
            assert local.records().get('B','a'*32)['state']=='completed'
            assert not local._watchers
        asyncio.run(scenario())


def test_terminal_submit_receipt_does_not_require_an_extra_poll(tmp_path,sandbox_operation_factory):
    with closing(ProjectRegistry(tmp_path/'projects.sqlite')) as db:
        service=SimpleNamespace(records=lambda:EnvironmentRegistry(db.database),
            check=lambda *_,**__:SimpleNamespace(project_id='P',session_id='S'),projects=SimpleNamespace(data_dir=tmp_path))
        local=sandbox_operation_factory(service,activity=EnvironmentActivity(service.records));calls=[]
        async def rpc(binding,operation,params,**kwargs):
            calls.append(operation)
            row=local.records().get(binding,params['operation_id'])
            return dict(row,state='completed',result={'ok':True,'value':['done',{}]})
        local._rpc=rpc
        assert local.submit('B','a'*32,'write',{'subject':{}}, {})['state']=='completed'
        assert calls==['operation_submit']


def test_confirmed_stop_is_saved_even_if_following_output_observation_fails(tmp_path,sandbox_operation_factory):
    with closing(ProjectRegistry(tmp_path/'projects.sqlite')) as db:
        service=SimpleNamespace(records=lambda:EnvironmentRegistry(db.database),projects=SimpleNamespace(data_dir=tmp_path))
        local=sandbox_operation_factory(service,activity=EnvironmentActivity(service.records))
        local.records().reserve('B','P','S','a'*32,'shell',{}, {})
        local.records().save('B','a'*32,dict(state='running'))
        async def rpc(binding,operation,params,**kwargs):
            if operation=='operation_poll':raise SandboxError('env_request_timeout')
            return dict(local.records().get(binding,params['operation_id']),state='stopped',observed_bytes=10)
        local._rpc=rpc
        row=local.stop('B','a'*32)
        assert row['state']=='stopped' and row['stale'] and row['observed_bytes']==10
        assert not local.finished(row)


def test_cancelled_stop_persists_its_confirmed_receipt_before_propagating_cancel(tmp_path,sandbox_operation_factory):
    from naiyue_test_agent.execution.sandbox_channel import SubmittedRequestCancelled
    with closing(ProjectRegistry(tmp_path/'projects.sqlite')) as db:
        service=SimpleNamespace(records=lambda:EnvironmentRegistry(db.database),projects=SimpleNamespace(data_dir=tmp_path))
        local=sandbox_operation_factory(service,activity=EnvironmentActivity(service.records))
        local.records().reserve('B','P','S','a'*32,'shell',{}, {})
        local.records().save('B','a'*32,dict(state='running'))
        async def rpc(binding,operation,params,**kwargs):
            row=dict(local.records().get(binding,params['operation_id']),state='stopped')
            raise SubmittedRequestCancelled(result=row)
        local._rpc=rpc
        async def scenario():
            with pytest.raises(asyncio.CancelledError):await local.astop('B','a'*32)
        asyncio.run(scenario())
        row=local.records().get('B','a'*32)
        assert row['state']=='stopped' and not row['stale']


def test_cancelled_submission_with_known_rejection_is_saved_as_failed(tmp_path,sandbox_operation_factory):
    from naiyue_test_agent.execution.sandbox_channel import SubmittedRequestCancelled
    with closing(ProjectRegistry(tmp_path/'projects.sqlite')) as db:
        service=SimpleNamespace(records=lambda:EnvironmentRegistry(db.database),
            check=lambda *_,**__:SimpleNamespace(project_id='P',session_id='S'),projects=SimpleNamespace(data_dir=tmp_path))
        local=sandbox_operation_factory(service,activity=EnvironmentActivity(service.records))
        async def rpc(*_,**__):raise SubmittedRequestCancelled(error=RequestNotSent('env_operation_capacity'))
        local._rpc=rpc
        async def scenario():
            with pytest.raises(asyncio.CancelledError):await local.asubmit('B','a'*32,'write',{'subject':{}},{})
        asyncio.run(scenario())
        row=local.records().get('B','a'*32)
        assert row['state']=='failed' and not row['stale'] and row['error_code']=='env_operation_capacity'
