"""A durable local ID survives a lost mutation reply without resubmission."""
from contextlib import closing
from types import SimpleNamespace
from pathlib import Path
import time
import pytest
from naiyue_test_agent.contracts.environment import EnvironmentRef
from naiyue_test_agent.contracts.sandbox import SandboxError,PROTOCOL_VERSION
from naiyue_test_agent.sandbox_settings import SandboxSettings
from naiyue_test_agent.application.environments.activity import EnvironmentActivity


def test_lost_ack_and_service_reopen_query_the_original_operation(tmp_path):
    from naiyue_test_agent.application.environments.operations import SandboxOperationService
    from naiyue_test_agent.envd.operations import EnvironmentOperations
    from naiyue_test_agent.storage.project_registry import ProjectRegistry
    from naiyue_test_agent.storage.environment_registry import EnvironmentRegistry
    from naiyue_test_agent.tools.settings import BashSettings,ProcessSettings
    code=tmp_path/'code';code.mkdir();settings=SandboxSettings()
    env=EnvironmentRef('B','sandbox','P',str(code),'linux')
    row=SimpleNamespace(binding_id='B',project_id='P',session_id='S',lifecycle='running',environment=env,settings=settings.model_dump(mode='json'))
    calls=[]
    with closing(ProjectRegistry(tmp_path/'registry.sqlite')) as registry,EnvironmentOperations(env,'S',tmp_path/'state',settings) as remote:
        class Engine:
            def request(self,_row,operation,params,**kwargs):
                calls.append(operation)
                value=remote.dispatch(dict(protocol=PROTOCOL_VERSION,request_id='Q',environment=env.public(),session_id='S',operation=operation,params=params))
                if operation=='operation_submit':raise SandboxError('env_operation_unknown')
                return value
        engine=Engine()
        service=SimpleNamespace(records=lambda:EnvironmentRegistry(registry.database),get=lambda _:row,
            validate=lambda _:row,_engine=lambda _:engine,projects=SimpleNamespace(data_dir=tmp_path))
        local=SandboxOperationService(service,activity=EnvironmentActivity(service.records))
        payload=dict(name='bash',command='printf once >> count.txt; printf output',timeout=None,
            settings=BashSettings().model_dump(mode='json'),process_settings=ProcessSettings().model_dump(mode='json'))
        metadata=dict(offset=0,saved_bytes=0,max_archive_bytes=1000,tail_limit=1000,settings=payload['settings'],background=True)
        local.submit('B','a'*32,'shell',payload,metadata)
        remote.wait('a'*32,10)
        # A new coordinator uses the same local registry, never a new ID.
        recovered=SandboxOperationService(service,activity=EnvironmentActivity(service.records))
        result=recovered.refresh('B','a'*32)
        assert result['state']=='completed' and result['metadata']['offset']==6
        assert result['metadata']['saved_bytes']==6
        assert (code/'count.txt').read_text()=='once'
        assert calls.count('operation_submit')==1
        again=recovered.submit('B','a'*32,'shell',payload,metadata)
        assert again['state']=='completed' and calls.count('operation_submit')==1
        request=engine.request
        engine.request=lambda *_a,**_k:(_ for _ in ()).throw(SandboxError('env_unreachable'))
        stale=recovered.refresh('B','a'*32)
        assert stale['stale'] and stale['metadata']['offset']==6
        assert stale['metadata']['tail_base64']
        engine.request=request
        marker=Path(result['metadata']['archive_path']).parent/'.naiyue-bash-output-v1'
        marker.write_text('not the archive owner')
        from langchain_core.tools import ToolException
        with pytest.raises(ToolException,match='archive failed'):recovered.refresh('B','a'*32)
        assert calls[-1]=='operation_stop'
        assert recovered.records().get('B','a'*32)['error_code']=='archive_error'


@pytest.mark.parametrize('value',[None,{},dict(returncode=True,termination='exited',output_complete=True,
    process_reaped=True,pipes_closed=True,consumer_settled=True,backpressure_count=0)])
def test_invalid_completed_process_result_remains_unknown(value):
    from naiyue_test_agent.application.environments.operations import SandboxOperationService
    with pytest.raises(SandboxError,match='env_operation_unknown'):
        SandboxOperationService.value(dict(state='completed',kind='shell',result={'ok':True,'value':value}))


def test_shutdown_seals_ingress_and_waits_for_accepted_submission_before_stop(tmp_path):
    from threading import Thread,Event
    from naiyue_test_agent.application.environments.operations import SandboxOperationService
    from naiyue_test_agent.storage.project_registry import ProjectRegistry
    from naiyue_test_agent.storage.environment_registry import EnvironmentRegistry
    env=EnvironmentRef('B','sandbox','P',str(tmp_path),'linux')
    row=SimpleNamespace(binding_id='B',project_id='P',session_id='S',lifecycle='running',environment=env,settings={})
    entered,release,sealed=Event(),Event(),Event();actions=[];errors=[]
    with closing(ProjectRegistry(tmp_path/'projects.sqlite')) as registry:
        service=SimpleNamespace(records=lambda:EnvironmentRegistry(registry.database),get=lambda _:row,
            validate=lambda _:row,projects=SimpleNamespace(data_dir=tmp_path))
        local=SandboxOperationService(service,activity=EnvironmentActivity(service.records))
        def rpc(binding,operation,params,**kwargs):
            if operation=='operation_submit':
                entered.set();assert release.wait(5);actions.append('submitted')
            record=local.records().get(binding,params['operation_id'])
            if operation=='operation_stop':
                actions.append('stopped');record.update(state='stopped',result={'ok':True,'value':['done',{}]})
            elif record['state']=='submitting':record['state']='running'
            return record
        local._rpc=rpc
        def submit():
            try:local.submit('B','a'*32,'write',{'subject':{}},{})
            except Exception as error:errors.append(error)
        submitter=Thread(target=submit);submitter.start();assert entered.wait(5)
        # Observe the actual condition wait used after close has sealed ingress.
        wait_for=local._condition.wait_for
        def observed(predicate,*args):sealed.set();return wait_for(predicate,*args)
        local._condition.wait_for=observed
        def close():
            try:local.close()
            except Exception as error:errors.append(error)
        closer=Thread(target=close);closer.start();assert sealed.wait(5)
        with pytest.raises(SandboxError,match='env_stopped'):
            local.submit('B','b'*32,'write',{'subject':{}},{})
        release.set();submitter.join(5);closer.join(5)
        assert not errors and not submitter.is_alive() and not closer.is_alive()
        assert actions==['submitted','stopped']
        assert local.records().get('B','a'*32)['state']=='stopped'


def test_malformed_output_frame_is_stale_protocol_failure_without_archive_stop(tmp_path):
    from naiyue_test_agent.application.environments.operations import SandboxOperationService
    from naiyue_test_agent.storage.project_registry import ProjectRegistry
    from naiyue_test_agent.storage.environment_registry import EnvironmentRegistry
    with closing(ProjectRegistry(tmp_path/'projects.sqlite')) as registry:
        service=SimpleNamespace(records=lambda:EnvironmentRegistry(registry.database),projects=SimpleNamespace(data_dir=tmp_path))
        local=SandboxOperationService(service,activity=EnvironmentActivity(service.records))
        meta=dict(offset=0,saved_bytes=0,max_archive_bytes=1000,tail_limit=100)
        local.records().reserve('B','P','S','a'*32,'shell',{},meta)
        calls=[]
        def rpc(binding,operation,params,**_kwargs):
            calls.append(operation)
            if operation=='operation_status':
                return dict(local.records().get(binding,params['operation_id']),state='running',observed_bytes=5)
            return {'bytes_base64':'%%%invalid%%%'}
        local._rpc=rpc
        row=local.refresh('B','a'*32)
        assert row['stale'] and row['error_code']=='env_protocol_mismatch'
        assert row['metadata']['offset']==0 and calls==['operation_status','operation_output']


def test_background_collection_can_resume_after_a_stale_connection():
    import asyncio
    from naiyue_test_agent.application.environments.operations import SandboxOperationService
    async def scenario():
        local=SandboxOperationService(SimpleNamespace(),activity=EnvironmentActivity(lambda:None));calls=[]
        def refresh(*_a):calls.append('query');return {'state':'unknown','stale':True}
        local.refresh=refresh
        local.watch('B','a'*32,20);await local._watchers[('B','a'*32)]
        local.watch('B','a'*32,20);await local._watchers[('B','a'*32)]
        assert calls==['query','query']
    asyncio.run(scenario())


@pytest.mark.parametrize('regression_source',['status','output'])
def test_confirmed_output_total_cannot_regress_or_hide_a_missing_tail(tmp_path,regression_source):
    from naiyue_test_agent.application.environments.operations import SandboxOperationService
    from naiyue_test_agent.storage.project_registry import ProjectRegistry
    from naiyue_test_agent.storage.environment_registry import EnvironmentRegistry
    with closing(ProjectRegistry(tmp_path/'projects.sqlite')) as registry:
        service=SimpleNamespace(records=lambda:EnvironmentRegistry(registry.database),projects=SimpleNamespace(data_dir=tmp_path))
        local=SandboxOperationService(service,activity=EnvironmentActivity(service.records))
        meta=dict(offset=0,saved_bytes=0,max_archive_bytes=1000,tail_limit=100)
        local.records().reserve('B','P','S','a'*32,'shell',{},meta)
        local.records().save('B','a'*32,dict(state='completed',observed_bytes=10))
        def rpc(binding,operation,params,**_kwargs):
            if operation=='operation_status':
                return dict(local.records().get(binding,params['operation_id']),state='completed',
                    observed_bytes=5 if regression_source=='status' else 10)
            assert operation=='operation_output'
            return dict(offset=0,next_offset=5,observed_bytes=5,first_offset=0,gap=False,bytes_base64='aGVsbG8=')
        local._rpc=rpc
        row=local.refresh('B','a'*32)
        assert row['stale'] and row['error_code']=='env_protocol_mismatch'
        assert row['observed_bytes']==10 and row['metadata']['offset']==0
        assert not local.finished(row)
