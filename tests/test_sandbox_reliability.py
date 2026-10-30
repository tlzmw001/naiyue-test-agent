"""Regression boundaries from the persistent sandbox reliability review."""
import asyncio
from contextlib import nullcontext
import sqlite3
from threading import Event, RLock, Thread, current_thread
from types import SimpleNamespace

import pytest
from langchain_core.tools import ToolException

from naiyue_test_agent.application.environments.activity import EnvironmentActivity
from naiyue_test_agent.application.environments.operations import SandboxOperationService
from naiyue_test_agent.application.environments.results import ResultService
from naiyue_test_agent.application.environments.service import EnvironmentService
from naiyue_test_agent.contracts.git_inputs import GitInputError
from naiyue_test_agent.contracts.sandbox import operation_fingerprint


@pytest.mark.parametrize('kind', ['git', 'tool'])
def test_failure_diagnostic_preserves_original_exception_and_safe_log(kind, caplog):
    ident='c'*32
    snapshot=SimpleNamespace(original_head='a'*40,input_snapshot='b'*40)
    record=SimpleNamespace(input_id='d'*32,snapshot=snapshot,settings={'timeout_seconds':5})
    environment=SimpleNamespace(input_id=record.input_id,input_snapshot=snapshot.input_snapshot,
        project_id='p',session_id='s',settings={'poll_milliseconds':100})
    payload=dict(result_id=ident,input_id=record.input_id,original_head=snapshot.original_head,
        input_snapshot=snapshot.input_snapshot,include_untracked=[])
    row=dict(binding_id='binding',operation_id=ident,kind='git_result',state='completed',stale=False,
        fingerprint=operation_fingerprint('git_result',payload),metadata={'delivery':{'state':'capturing'}})
    original=GitInputError('git_result_unconfirmed') if kind=='git' else ToolException('PRIVATE_TOOL_BODY')
    def value(_row):raise original
    service=ResultService(SimpleNamespace(get=lambda _:environment,_lock=lambda _:RLock()),
        SimpleNamespace(accepting=nullcontext,value=value),SimpleNamespace(get=lambda *_:record))
    service._row=lambda *_:row
    saves=[]
    def save(_row,**changes):
        saves.append(changes)
        raise sqlite3.OperationalError('PRIVATE_SQL_BODY')
    service._save=save
    with pytest.raises(type(original)) as caught:service.capture('binding',ident,[])
    assert caught.value is original
    assert saves==[{'error_code':'git_result_unconfirmed'}]
    assert 'git_result_failure_save_unconfirmed' in caplog.text
    assert 'binding' in caplog.text and ident in caplog.text
    assert 'PRIVATE_TOOL_BODY' not in caplog.text and 'PRIVATE_SQL_BODY' not in caplog.text


def test_successful_touch_clears_transient_uncertainty():
    failing=True
    def touch(*_):
        if failing:raise sqlite3.OperationalError('fixture')
        return True
    activity=EnvironmentActivity(lambda:SimpleNamespace(touch=touch))
    activity.touch('binding');assert activity.uncertain('binding')
    failing=False;activity.touch('binding')
    assert not activity.uncertain('binding')


def test_old_touch_completion_cannot_clear_newer_failure():
    entered,release=Event(),Event()
    def touch(*_):
        if current_thread().name=='old-touch':
            entered.set();assert release.wait(5);return True
        raise sqlite3.OperationalError('fixture')
    activity=EnvironmentActivity(lambda:SimpleNamespace(touch=touch))
    old=Thread(target=activity.touch,args=('binding',),name='old-touch');old.start()
    try:
        assert entered.wait(5)
        activity.touch('binding')
    finally:release.set();old.join(5)
    assert not old.is_alive() and activity.uncertain('binding')


def test_async_request_activity_does_not_block_its_event_loop():
    async def scenario():
        loop=asyncio.get_running_loop();marker=Event();observed=[]
        def touch(*_):
            loop.call_soon_threadsafe(marker.set)
            observed.append((current_thread().name,marker.wait(2)))
            return True
        service=EnvironmentService.__new__(EnvironmentService)
        service.activity=EnvironmentActivity(lambda:SimpleNamespace(touch=touch))
        service.operations=None;service.check=lambda *_:object()
        async def request(*_,**__):return {'ok':True}
        service.transport=SimpleNamespace(arequest=request)
        assert await service.arequest('binding','read',{})=={'ok':True}
        assert observed and observed[0][0]!='MainThread' and observed[0][1]
        assert not service.activity.busy('binding')
    asyncio.run(scenario())


def test_finished_watchers_release_their_registry_entries():
    async def scenario():
        service=SandboxOperationService(None,activity=None)
        async def refresh(*_):return dict(state='completed',observed_bytes=0,metadata={'offset':0})
        service.arefresh=refresh
        for index in range(40):service.watch('binding',f'{index:032x}',100)
        await asyncio.gather(*service._watchers.values())
        assert service._watchers=={}
    asyncio.run(scenario())


def test_no_change_merge_does_not_write_but_clears_stale():
    service=SandboxOperationService(None,activity=None);saved=[]
    def save(binding,ident,changes):saved.append(changes);return {**local,**changes}
    service.records=lambda:SimpleNamespace(save=save)
    local=dict(binding_id='binding',operation_id='a'*32,kind='write',fingerprint='f',
        state='running',observed_bytes=0,metadata={},result=None,stale=False,error_code=None)
    assert service._merge(local,local) is local and saved==[]
    stale={**local,'stale':True,'error_code':'env_unreachable'}
    assert not service._merge(stale,local)['stale']
    assert len(saved)==1


def test_run_stop_cancels_inflight_remote_search_without_waiting_for_its_reply():
    from naiyue_test_agent.agent_runtime.run_control import RunControl
    async def scenario():
        entered=asyncio.Event();cancelled=asyncio.Event();control=RunControl()
        async def request(*_,**__):
            entered.set()
            try:await asyncio.Event().wait()
            finally:cancelled.set()
        service=EnvironmentService.__new__(EnvironmentService)
        service.activity=EnvironmentActivity(lambda:SimpleNamespace(touch=lambda *_:True))
        service.operations=None;service.check=lambda *_:SimpleNamespace(settings={'poll_milliseconds':100})
        service.transport=SimpleNamespace(arequest=request)
        task=asyncio.create_task(service.arequest('binding','find',{},control=control))
        await entered.wait();control.request()
        try:
            with pytest.raises(ToolException,match='stopped'):await asyncio.wait_for(task,2)
            assert cancelled.is_set() and not service.activity.busy('binding')
        finally:
            task.cancel();await asyncio.gather(task,return_exceptions=True)
    asyncio.run(scenario())
