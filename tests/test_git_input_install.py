"""Application installation uses the real envd dispatcher through a controlled transport."""
import asyncio
from contextlib import ExitStack
from threading import Event
from types import SimpleNamespace

import pytest

from naiyue_test_agent.application.service_application import open_service_application
from naiyue_test_agent.contracts.environment import EnvironmentRef
from naiyue_test_agent.contracts.git_inputs import GitInputError
from naiyue_test_agent.contracts.sandbox import SandboxRecord,SandboxError,PROTOCOL_VERSION
from naiyue_test_agent.envd.operations import EnvironmentOperations
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.storage.environment_registry import EnvironmentRegistry
from git_input_helpers import repository


def connect(app,project,tmp_path,monkeypatch,stack,count=1):
    roots={};remotes={};rows=[];calls=[]
    monkeypatch.setattr(SandboxRecord,'environment',property(lambda self:
        EnvironmentRef(self.binding_id,'sandbox',self.project_id,str(roots[self.binding_id]),'linux')))
    for n in range(count):
        binding=f'{n+1:032x}';code=tmp_path/('code'+str(n));code.mkdir();roots[binding]=code
        row=SandboxRecord(binding,project.project_id,'S'+str(n),'running','D','container'+str(n),
            'volume'+str(n),'image',app.environments._service.settings.model_dump(mode='json'))
        app.environments._service.records().reserve(row);rows.append(row)
        remotes[binding]=stack.enter_context(EnvironmentOperations(row.environment,row.session_id,
            tmp_path/('state'+str(n)),app.environments._service.settings))
    def request(row,op,params,**kwargs):
        calls.append((row.binding_id,op,dict(params)))
        return remotes[row.binding_id].dispatch(dict(protocol=PROTOCOL_VERSION,request_id='request',
            environment=row.environment.public(),session_id=row.session_id,operation=op,params=params))
    engine=SimpleNamespace(request=request,validate=lambda *_a,**_k:None)
    monkeypatch.setattr(app.environments._service,'_engine',lambda _:engine)
    return rows,roots,remotes,calls,engine


def test_api_installs_confirmed_input_and_never_rechecks_out_later_agent_changes(tmp_path,monkeypatch):
    root=repository(tmp_path);(root/'app.txt').write_text('selected input\n')
    settings=AppSettings.model_validate({'storage':{'data_dir':str(tmp_path/'data')}})
    async def scenario():
        with ExitStack() as stack:
            async with open_service_application('missing-model',tools=(),system_prompt='',response_format=None,settings=settings) as app:
                project=await app.projects.add_local(root,trusted=True,operation_id='P')
                prepared=await app.environments.prepare_input(project.project_id,input_id='a'*32)
                rows,roots,remotes,calls,engine=connect(app,project,tmp_path,monkeypatch,stack)
                installed=await app.environments.install_input(rows[0].binding_id,'a'*32)
                assert installed.input_snapshot==prepared.snapshot.input_snapshot
                assert installed.input_id=='a'*32 and installed.input_operation_id
                code=roots[installed.binding_id];assert (code/'app.txt').read_text()=='selected input\n'
                (code/'app.txt').write_text('agent changes\n')
                assert await app.environments.install_input(installed.binding_id,'a'*32)==installed
                assert (code/'app.txt').read_text()=='agent changes\n'
                assert sum(op=='operation_submit' for _,op,_ in calls)==1
    asyncio.run(scenario())


def test_lost_install_ack_only_queries_original_operation_and_confirms_required_binding_save(tmp_path,monkeypatch):
    root=repository(tmp_path)
    settings=AppSettings.model_validate({'storage':{'data_dir':str(tmp_path/'data')}})
    async def scenario():
        with ExitStack() as stack:
            async with open_service_application('missing-model',tools=(),system_prompt='',response_format=None,settings=settings) as app:
                project=await app.projects.add_local(root,trusted=True,operation_id='P')
                await app.environments.prepare_input(project.project_id,input_id='a'*32)
                rows,roots,remotes,calls,engine=connect(app,project,tmp_path,monkeypatch,stack)
                normal=engine.request;lost=False
                def unreliable(row,op,params,**kwargs):
                    nonlocal lost
                    if lost:raise SandboxError('env_unreachable')
                    result=normal(row,op,params,**kwargs)
                    if op=='operation_submit':lost=True;raise SandboxError('env_operation_unknown')
                    return result
                engine.request=unreliable
                with pytest.raises(GitInputError,match='git_input_install_unconfirmed'):
                    await app.environments.install_input(rows[0].binding_id,'a'*32)
                pending=await app.environments.get(rows[0].binding_id)
                assert pending.input_id=='a'*32 and pending.input_snapshot is None
                await asyncio.to_thread(remotes[rows[0].binding_id].wait,pending.input_operation_id,10)
                lost=False
                status=await app.environments.operation_status(rows[0].binding_id,pending.input_operation_id)
                assert status['state']=='completed'
                assert (await app.environments.get(rows[0].binding_id)).input_snapshot is not None
                assert sum(op=='operation_submit' for _,op,_ in calls)==1
    asyncio.run(scenario())


def test_unconfirmed_receiver_base_falls_back_to_full_bundle(tmp_path,monkeypatch):
    root=repository(tmp_path);(root/'app.txt').write_text('dirty\n')
    settings=AppSettings.model_validate({'storage':{'data_dir':str(tmp_path/'data')}})
    async def scenario():
        with ExitStack() as stack:
            async with open_service_application('missing-model',tools=(),system_prompt='',response_format=None,settings=settings) as app:
                project=await app.projects.add_local(root,trusted=True,operation_id='P')
                await app.environments.prepare_input(project.project_id,input_id='a'*32)
                rows,roots,remotes,calls,engine=connect(app,project,tmp_path,monkeypatch,stack)
                normal=engine.request
                def stale_probe(row,op,params,**kwargs):
                    if op=='input_probe':return dict(original_head=params['original_head'],has_base=True)
                    return normal(row,op,params,**kwargs)
                engine.request=stale_probe
                await app.environments.install_input(rows[0].binding_id,'a'*32)
                begins=[p for _,op,p in calls if op=='input_begin']
                assert len(begins)==2 and begins[0]['base'] is not None and begins[1]['base'] is None
                assert (roots[rows[0].binding_id]/'app.txt').read_text()=='dirty\n'
    asyncio.run(scenario())


def test_slow_transfer_in_one_binding_does_not_block_another_binding(tmp_path,monkeypatch):
    root=repository(tmp_path)
    settings=AppSettings.model_validate({'storage':{'data_dir':str(tmp_path/'data')}})
    entered,release=Event(),Event()
    async def scenario():
        with ExitStack() as stack:
            async with open_service_application('missing-model',tools=(),system_prompt='',response_format=None,settings=settings) as app:
                project=await app.projects.add_local(root,trusted=True,operation_id='P')
                await app.environments.prepare_input(project.project_id,input_id='a'*32)
                rows,roots,remotes,calls,engine=connect(app,project,tmp_path,monkeypatch,stack,2)
                normal=engine.request
                def delayed(row,op,params,**kwargs):
                    if row.binding_id==rows[0].binding_id and op=='input_begin':
                        entered.set();assert release.wait(10)
                    return normal(row,op,params,**kwargs)
                engine.request=delayed
                first=asyncio.create_task(app.environments.install_input(rows[0].binding_id,'a'*32))
                try:
                    assert await asyncio.to_thread(entered.wait,5)
                    second=await asyncio.wait_for(app.environments.install_input(rows[1].binding_id,'a'*32),10)
                    assert second.input_id=='a'*32
                finally:release.set()
                assert (await first).input_id=='a'*32
    asyncio.run(scenario())


def test_failed_confirmation_save_blocks_tools_until_original_operation_is_confirmed(tmp_path,monkeypatch):
    root=repository(tmp_path)
    settings=AppSettings.model_validate({'storage':{'data_dir':str(tmp_path/'data')}})
    async def scenario():
        with ExitStack() as stack:
            async with open_service_application('missing-model',tools=(),system_prompt='',response_format=None,settings=settings) as app:
                project=await app.projects.add_local(root,trusted=True,operation_id='P')
                await app.environments.prepare_input(project.project_id,input_id='a'*32)
                rows,roots,remotes,calls,engine=connect(app,project,tmp_path,monkeypatch,stack)
                binding=rows[0].binding_id
                original=EnvironmentRegistry.save
                def fail_confirm(self,row):
                    if row.input_snapshot is not None:raise OSError('confirmation write failed')
                    return original(self,row)
                monkeypatch.setattr(EnvironmentRegistry,'save',fail_confirm)
                with pytest.raises(GitInputError,match='git_input_io_failed'):
                    await app.environments.install_input(binding,'a'*32)
                pending=await app.environments.get(binding)
                assert pending.input_snapshot is None and pending.input_operation_id
                with pytest.raises(SandboxError,match='git_input_install_unconfirmed'):
                    await app.environments.files(binding,project_id=project.project_id,session_id=rows[0].session_id)
                with pytest.raises(SandboxError,match='git_input_install_unconfirmed'):
                    await app.environments.processes(binding,project_id=project.project_id,session_id=rows[0].session_id)
                monkeypatch.setattr(EnvironmentRegistry,'save',original)
                status=await app.environments.operation_status(binding,pending.input_operation_id)
                assert status['state']=='completed'
                assert (await app.environments.get(binding)).input_snapshot is not None
                assert await app.environments.files(binding,project_id=project.project_id,session_id=rows[0].session_id)
    asyncio.run(scenario())


def test_close_drains_the_whole_transfer_and_prevents_late_install_submission(tmp_path,monkeypatch):
    root=repository(tmp_path)
    settings=AppSettings.model_validate({'storage':{'data_dir':str(tmp_path/'data')}})
    entered,release,closing=Event(),Event(),Event()
    async def scenario():
        with ExitStack() as stack:
            async with open_service_application('missing-model',tools=(),system_prompt='',response_format=None,settings=settings) as app:
                project=await app.projects.add_local(root,trusted=True,operation_id='P')
                await app.environments.prepare_input(project.project_id,input_id='a'*32)
                rows,roots,remotes,calls,engine=connect(app,project,tmp_path,monkeypatch,stack)
                normal=engine.request
                def delayed(row,op,params,**kwargs):
                    if op=='input_begin':entered.set();assert release.wait(10)
                    return normal(row,op,params,**kwargs)
                engine.request=delayed
                condition=app.environments._operations._condition;wait_for=condition.wait_for
                def waiting(predicate,*args,**kwargs):
                    closing.set()
                    return wait_for(predicate,*args,**kwargs)
                monkeypatch.setattr(condition,'wait_for',waiting)
                install=asyncio.create_task(app.environments.install_input(rows[0].binding_id,'a'*32))
                assert await asyncio.to_thread(entered.wait,5)
                close=asyncio.create_task(asyncio.to_thread(app.environments._operations.close))
                try:
                    assert await asyncio.to_thread(closing.wait,5)
                    assert not close.done()
                finally:release.set()
                with pytest.raises(SandboxError,match='env_stopped'):await install
                await asyncio.wait_for(close,10)
                assert not any(op=='operation_submit' for _,op,_ in calls)
                assert (await app.environments.get(rows[0].binding_id)).input_snapshot is None
    asyncio.run(scenario())
