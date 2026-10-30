"""Read-only cancellation has remote custody, including cancellation before dispatch."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from langchain_core.tools import ToolException

from naiyue_test_agent.contracts.environment import EnvironmentRef
from naiyue_test_agent.envd.operations import EnvironmentOperations
from naiyue_test_agent.sandbox_settings import SandboxSettings
from naiyue_test_agent.tools.permission_rules import PermissionSettings


def request(env,operation,params=None,ident='search'):
    return dict(protocol=2,request_id=ident,environment=env.public(),session_id='s',operation=operation,params=params or {})


def test_cancel_registered_search_reaches_runtime_and_releases_registration(tmp_path):
    env=EnvironmentRef('a','sandbox','p',str(tmp_path),'linux')
    with EnvironmentOperations(env,'s',tmp_path/'state',SandboxSettings()) as ops:
        entered=Event();captured=[]
        async def find(subject,pattern,limit,runtime,**options):
            control=runtime.context.scope.control
            captured.append((control,options['timeout']))
            entered.set()
            assert control is not None and control.event.wait(5)
            return 'cancelled'
        ops.files.files.find=find
        ops.dispatch(request(env,'request_register',{'request_id':'search'}))
        params=dict(subject={},pattern='*',limit=10,settings={},timeout=None,process_settings=None,registered=True,
            access=dict(permit=dict(session_id='s',agent_path='root',call_id='c',
                subject=dict(execution_environment=env.public())),permission_rules=PermissionSettings().model_dump(mode='json'),
                session_allow=[],permission_mode='Default'))
        with ThreadPoolExecutor(1) as pool:
            result=pool.submit(ops.dispatch,request(env,'find',params))
            assert entered.wait(5)
            assert ops.dispatch(request(env,'request_cancel',{'request_id':'search'},'cancel'))=={'cancelled':True}
            assert result.result(5)=='cancelled'
        assert captured[0][0].requested and captured[0][1] is None
        assert not ops._searches and ops._readers==0


def test_cancel_before_search_execution_is_not_lost_and_release_prevents_late_start(tmp_path):
    env=EnvironmentRef('a','sandbox','p',str(tmp_path),'linux')
    with EnvironmentOperations(env,'s',tmp_path/'state',SandboxSettings()) as ops:
        ops.dispatch(request(env,'request_register',{'request_id':'search'}))
        ops.dispatch(request(env,'request_cancel',{'request_id':'search'}))
        seen=[]
        ops.files.dispatch=lambda req,control=None:seen.append(control.requested) or 'stopped'
        assert ops.dispatch(request(env,'find',{'registered':True}))=='stopped'
        assert seen==[True] and not ops._searches
        ops.dispatch(request(env,'request_register',{'request_id':'late'}))
        ops.dispatch(request(env,'request_release',{'request_id':'late'}))
        with pytest.raises(ToolException,match='cancelled'):
            ops.dispatch(request(env,'find',{'registered':True},'late'))
        assert seen==[True] and not ops._searches


def test_search_registrations_are_bounded_and_duplicate_registration_is_rejected(tmp_path):
    from naiyue_test_agent.contracts.sandbox import SandboxError
    env=EnvironmentRef('a','sandbox','p',str(tmp_path),'linux')
    with EnvironmentOperations(env,'s',tmp_path/'state',SandboxSettings(transport={
            'io_requests':1,'control_requests':1,'pending_requests':2})) as ops:
        for ident in ('a','b'):ops.dispatch(request(env,'request_register',{'request_id':ident}))
        with pytest.raises(SandboxError,match='env_operation_conflict'):
            ops.dispatch(request(env,'request_register',{'request_id':'a'}))
        with pytest.raises(SandboxError,match='env_connection_capacity'):
            ops.dispatch(request(env,'request_register',{'request_id':'c'}))
        ops.dispatch(request(env,'request_release',{'request_id':'a'}))
        ops.dispatch(request(env,'request_register',{'request_id':'c'}))
        assert set(ops._searches)=={'b','c'}
