"""Preserve necessary process settlement failures across the envd boundary."""
import asyncio
from contextlib import closing
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage

from naiyue_test_agent.agent_runtime import build_runtime_agent
from naiyue_test_agent.agent_runtime.events import RunEventPublisher
from naiyue_test_agent.application import prepare_tool_registry
from naiyue_test_agent.application.environments.operations import SandboxOperationService
from naiyue_test_agent.application.environments.activity import EnvironmentActivity
from naiyue_test_agent.contracts.run_context import RuntimeContext,RunScope,RunIdentity,RunCapabilities,RunObservationPorts
from naiyue_test_agent.contracts.sandbox import PROTOCOL_VERSION,SandboxRecord,SandboxError
from naiyue_test_agent.envd.operations import EnvironmentOperations
from naiyue_test_agent.execution.docker_processes import DockerProcesses
from naiyue_test_agent.sandbox_settings import SandboxSettings
from naiyue_test_agent.shell_task_settings import ShellTaskSettings
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.storage.environment_registry import EnvironmentRegistry
from naiyue_test_agent.storage.project_registry import ProjectRegistry
from naiyue_test_agent.tools import create_builtin_tools
from naiyue_test_agent.tools.processes import ProcessCleanupError
from test_agent_runtime import ToolCapableFake


@pytest.mark.parametrize('name,phase,background',[
    ('bash','submit',False),('bash','poll',False),('bash','submit',True),
    ('aitest_kit','submit',False),('aitest_kit','poll',False),
    ('bash','promote',False),
])
def test_remote_cleanup_failure_aborts_formal_graph_before_next_model_call(tmp_path,caplog,name,phase,background):
    async def scenario():
        policy=SandboxSettings(poll_milliseconds=20)
        record=SandboxRecord('B','P','S','running','D','c','v','i',policy.model_dump(mode='json'))
        release=Event();calls=[];events=[];models=[]
        if phase=='submit':release.set()
        with closing(ProjectRegistry(tmp_path/'projects.sqlite')) as registry,EnvironmentOperations(
                record.environment,'S',tmp_path/'state',policy) as remote:
            class Engine:
                def request(self,_row,operation,params,**_kwargs):
                    calls.append(operation)
                    if (operation=='operation_status' and phase=='poll' and calls.count(operation)==2
                            or operation=='operation_background' and phase=='promote'):
                        release.set();assert remote.wait(params['operation_id'],5)
                    value=remote.dispatch(dict(protocol=PROTOCOL_VERSION,request_id='Q',
                        environment=record.environment.public(),session_id='S',operation=operation,params=params))
                    if operation=='operation_submit' and phase=='submit':
                        assert remote.wait(params['operation_id'],5)
                        value=remote.journal.get(params['operation_id'])
                    return value
            service=SimpleNamespace(records=lambda:EnvironmentRegistry(registry.database),get=lambda _:record,
                validate=lambda _:record,_engine=lambda _:Engine(),projects=SimpleNamespace(data_dir=tmp_path))
            coordinator=SandboxOperationService(service,activity=EnvironmentActivity(service.records));port=DockerProcesses(coordinator,record)
            spec=next(spec for spec in create_builtin_tools(AppSettings().tools,
                shell_task_settings=ShellTaskSettings(foreground_wait_seconds=1)) if spec.tool.name==name)
            args={'args':['--help']} if name=='aitest_kit' else {'command':'never started','background':background}
            model=ToolCapableFake(responses=[AIMessage(content='',tool_calls=[dict(name=name,args=args,id='C')]),
                AIMessage(content='must not continue')])
            tools=prepare_tool_registry([spec])
            def select(*_args,**_kwargs):models.append('model');return 'fake',model
            agent=build_runtime_agent(model,tool_registry=tools,resolve_active_tool_names=lambda *_a,**_k:tools.names(),
                system_prompt='',response_format=None,resolve_model=select,
                resolve_permission_context=lambda *_a,**_k:{'mode':'BypassApprovals','session_allow':[]})
            publisher=RunEventPublisher('S','R',(events.append,))
            scope=RunScope(RunIdentity('S','R',project_id='P'),RunCapabilities(processes=port),
                observation=RunObservationPorts(publisher=publisher),environment=record.environment)
            def fail(*_args,**_kwargs):
                assert release.wait(5)
                raise ProcessCleanupError('PRIVATE_CLEANUP_BODY',facts={'PRIVATE_FACT':'secret'})
            try:
                with patch('naiyue_test_agent.envd.operations.execute_process',fail):
                    with pytest.raises(ProcessCleanupError) as caught:
                        await agent.ainvoke({'messages':[('user','probe settlement')]},context=RuntimeContext(scope))
            finally:release.set()
            rows=coordinator.records().list();assert len(rows)==1
            row=rows[0]
            assert row['state']=='unknown' and row['result']['error']['code']=='env_process_cleanup_unconfirmed'
            assert row['operation_id'] in str(caught.value) and caught.value.consumer_task is None
            assert models==['model'] and calls.count('operation_submit')==1
            assert not coordinator._watchers
            starts=[event for event in events if event.event_type=='tool_call_started']
            finishes=[event for event in events if event.event_type=='tool_call_finished']
            assert len(starts)==len(finishes)==1
            assert finishes[0].data.outcome=='outcome_unknown'
            assert finishes[0].data.failure.category=='tool_outcome_unknown'
            assert 'PRIVATE_' not in str(row)+str(caught.value)+caplog.text
    asyncio.run(scenario())


def test_result_value_restores_only_the_fixed_process_failure_code():
    row=dict(operation_id='a'*32,kind='shell',state='unknown',result={'ok':False,
        'error':{'code':'env_process_cleanup_unconfirmed','message':'PRIVATE_CLEANUP_BODY'}})
    with pytest.raises(ProcessCleanupError) as caught:SandboxOperationService.value(row)
    assert 'PRIVATE_' not in str(caught.value)
    for kind,code in [('write','env_process_cleanup_unconfirmed'),('shell','env_operation_unknown'),
            ('argv','ProcessCleanupError'),('shell','arbitrary.module.Class')]:
        row['kind']=kind;row['result']['error']['code']=code
        with pytest.raises(SandboxError,match='env_operation_unknown'):SandboxOperationService.value(row)


def test_cleanup_failure_while_stopping_queries_original_id_and_preserves_mechanism_error(tmp_path):
    async def scenario():
        from naiyue_test_agent.tools.settings import BashSettings
        policy=SandboxSettings();record=SandboxRecord('B','P','S','running','D','c','v','i',policy.model_dump(mode='json'))
        calls=[]
        with closing(ProjectRegistry(tmp_path/'projects.sqlite')) as registry,EnvironmentOperations(
                record.environment,'S',tmp_path/'state',policy) as remote:
            class Engine:
                def request(self,_row,operation,params,**_kwargs):
                    calls.append(operation)
                    return remote.dispatch(dict(protocol=PROTOCOL_VERSION,request_id='Q',
                        environment=record.environment.public(),session_id='S',operation=operation,params=params))
            service=SimpleNamespace(records=lambda:EnvironmentRegistry(registry.database),get=lambda _:record,
                validate=lambda _:record,_engine=lambda _:Engine(),projects=SimpleNamespace(data_dir=tmp_path))
            coordinator=SandboxOperationService(service,activity=EnvironmentActivity(service.records));port=DockerProcesses(coordinator,record)
            class Control:
                checks=0
                @property
                def requested(self):self.checks+=1;return self.checks>=3
            scope=RunScope(RunIdentity('S','R',project_id='P'),control=Control(),environment=record.environment)
            access=SimpleNamespace(context=RuntimeContext(scope),tool_call_id='C')
            def fail(_payload,_kind,_root,_consume,control,_started):
                assert control.event.wait(5)
                raise ProcessCleanupError('PRIVATE_STOP_FAILURE')
            settings=BashSettings()
            with patch('naiyue_test_agent.envd.operations.execute_process',fail):
                with pytest.raises(ProcessCleanupError):
                    await port._execute('shell',dict(command='never started',timeout=None,
                        settings=settings.model_dump(mode='json')),access,settings,False,None,1000)
            row=coordinator.records().list()[0]
            assert row['result']['error']['code']=='env_process_cleanup_unconfirmed'
            assert calls.count('operation_submit')==1 and 'operation_stop' in calls
            assert calls.index('operation_stop')<len(calls)-1 and calls[-1]=='operation_status'
    asyncio.run(scenario())
