"""Cloud selections use the existing model catalog, durable commands and real SDK HTTP."""
import asyncio,json
from uuid import uuid4
import httpx
import pytest
from fastapi import FastAPI,Request
from starlette.responses import Response
from pydantic import ValidationError
from naiyue_test_agent.cloud_runner.kernel import open_runner
from naiyue_test_agent.cloud_runner.transport import RunnerClient
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.contracts.cloud_runner import StartRun,RunCommand
from naiyue_test_agent.contracts.cloud_local_tasks import ManualMessage,TaskDraft
from test_manual_task_commands import prepared
from model_http_helpers import cloud_task,live_app
from model_proxy_helpers import model_settings
from cloud_helpers import owner_headers
from test_runner_control import prepared as prepare_runner
from test_model_runner import until
from test_prompt_cache_call_paths import completion_response


def test_selection_is_explicit_in_all_cloud_commands_and_draft():
    values=[(TaskDraft,dict(expected_draft_revision=0,text='work',model_id='A')),
        (ManualMessage,dict(command_id=str(uuid4()),local_session_id='S',expected_binding_revision=1,text='work',model_id='A')),
        (StartRun,dict(command_id=str(uuid4()),expected_revision=1,expected_generation=1,message='work',model_id='A')),
        (RunCommand,dict(command_id=str(uuid4()),run_id=uuid4().hex,message='work',model_id='A'))]
    for cls,body in values:
        assert cls.model_validate(body).thinking_level=='model_default'
        assert cls.model_validate(body|{'thinking_level':'high'}).model_dump()['thinking_level']=='high'
        with pytest.raises(ValidationError):cls.model_validate(body|{'thinking_level':'invented'})


def test_local_draft_and_original_command_freeze_thinking(tmp_path):
    async def run():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path)
        try:
            repo=domain.repo.commands;bid=binding['binding_id']
            draft=repo.save_draft(bid,sid,dict(expected_draft_revision=0,text='work',model_id='A',thinking_level='high'))
            assert draft['thinking_level']=='high'
            req=dict(command_id=str(uuid4()),local_session_id=sid,expected_binding_revision=binding['revision'],text='work',model_id='A',thinking_level='high',draft_revision=1)
            # Check draft CAS while idle, before the first reservation adds a pending barrier.
            with pytest.raises(CloudError,match='cloud_draft_conflict'):
                repo.reserve(bid,sid,'message',req|{'command_id':str(uuid4()),'thinking_level':'low'})
            op=repo.reserve(bid,sid,'message',req)
            assert op['request']['thinking_level']=='high'
            with pytest.raises(CloudError,match='cloud_command_conflict'):
                repo.reserve(bid,sid,'message',req|{'thinking_level':'low'})
        finally:await manager.close();store.close()
    asyncio.run(run())


def test_owner_options_and_selected_effort_reach_real_runner_model_request(tmp_path):
    async def run():
        upstream=FastAPI();seen=[]
        @upstream.post('/v1/chat/completions')
        async def serve(request:Request):
            body=await request.json();seen.append(body)
            result=completion_response(body,text='done')
            return Response(result.content,headers=dict(result.headers))
        async with live_app(upstream) as url:
            config,data=model_settings(tmp_path,upstream=url)
            data['models']['chat']['thinking']=dict(default_level='low',supported_levels=['low','high'])
            config.model_proxy.config_path.write_text(json.dumps(data))
            async with cloud_task(config) as (r,owner,_):
                bootstrap=prepare_runner(r);root=tmp_path/'project';root.mkdir();state=tmp_path/'state';state.mkdir(mode=0o700)
                stop=asyncio.Event()
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=owner),base_url=r.settings.origin,headers=owner_headers(r)) as client:
                    async with open_runner(bootstrap,root,state) as kernel:
                        task=asyncio.create_task(RunnerClient(kernel).run(stop))
                        try:
                            await until(lambda:r.registry.get_task(bootstrap.task_id).lifecycle=='ready')
                            path='/api/cloud/v1/tasks/'+bootstrap.task_id
                            denied=await client.get(path+'/models',headers={'Authorization':''})
                            assert denied.status_code==401
                            response=await client.get(path+'/models');assert response.status_code==200,response.text
                            options=response.json()
                            assert options['thinking_options']['chat']==dict(default_level='low',supported_levels=['low','high'])
                            assert 'fixture-upstream' not in response.text and url not in response.text and 'configuration' not in options
                            original=r.registry.get_task(bootstrap.task_id)
                            r.replace_task(original.model_copy(update={'revision':original.revision+1,'runner_model_ids':['chat']}),
                                expected_revision=original.revision,expected_generation=original.generation)
                            filtered=(await client.get(path+'/models')).json()
                            assert filtered['model_ids']==['chat'] and set(filtered['thinking_options'])=={'chat'}
                            def command(level):
                                current=r.registry.get_task(bootstrap.task_id)
                                return dict(command_id=str(uuid4()),expected_revision=current.revision,expected_generation=current.generation,message='work',model_id='chat',thinking_level=level)
                            invalid=command('max');rejected=await client.post(path+'/runs',json=invalid)
                            assert rejected.status_code==422 and rejected.json()['error']['code']=='unsupported_thinking_level'
                            with pytest.raises(CloudError,match='command_not_found'):r.registry.get_command(invalid['command_id'])
                            for level,native in [('high','high'),('model_default','low')]:
                                body=command(level);sent=await client.post(path+'/runs',json=body)
                                assert sent.status_code==202,sent.text
                                assert sent.json()['result']['command']['thinking_level']==level
                                await until(lambda:r.registry.get_command(body['command_id']).status in ('succeeded','failed'))
                                assert r.registry.get_command(body['command_id']).status=='succeeded'
                                assert seen[-1]['reasoning_effort']==native
                                assert kernel.sessions.require(bootstrap.task_id).describe_session()['thinking_level']==level
                                await until(lambda:r.registry.get_task(bootstrap.task_id).pending_result_id is None)
                            assert len(seen)==2
                        finally:stop.set();await asyncio.wait_for(task,8)
    asyncio.run(run())


def test_manual_dispatch_and_receipt_include_the_original_thinking_level(tmp_path):
    from test_manual_task_commands import CommandRemote
    from naiyue_test_agent.application.cloud_local.command_dispatch import validate
    async def run():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path)
        adapter=CommandRemote(remote);adapter.settings=remote.settings;manager.connections.client=adapter
        try:
            cid=str(uuid4());request=dict(command_id=cid,local_session_id=sid,expected_binding_revision=binding['revision'],text='work',model_id='A',thinking_level='high',draft_revision=None)
            await manager.commands.submit(domain,binding['binding_id'],'message',request);await manager.drain()
            op=domain.repo.operation(cid,sid);assert op['phase']=='accepted'
            assert op['steps']['primary']['request']['thinking_level']=='high'
            assert adapter.calls[-1][1]['thinking_level']=='high'
            raw=op['steps']['primary']['receipt'];assert raw['result']['command']['thinking_level']=='high'
            altered=json.loads(json.dumps(raw));altered['result']['command']['thinking_level']='low'
            with pytest.raises(CloudError,match='cloud_receipt_mismatch'):validate(altered,op,'primary')
        finally:await manager.close();store.close()
    asyncio.run(run())


def test_runner_rejects_unconfigured_level_before_persisting_a_command(tmp_path):
    from test_runner_kernel import bootstrap,request
    from test_async_application import StreamingToolModel
    from naiyue_test_agent.model_access import ModelCatalog
    async def run():
        root=tmp_path/'project';root.mkdir();state=tmp_path/'state';state.mkdir(mode=0o700)
        model=StreamingToolModel(replies=[]);body=request(thinking_level='high')
        async with open_runner(bootstrap(),root,state,catalog=ModelCatalog('A',{'A':model})) as kernel:
            with pytest.raises(CloudError,match='unsupported_thinking_level'):await kernel.accept(body)
            assert kernel.phase=='ready' and kernel.journal.lookup(body.command_id) is None and not model.seen
    asyncio.run(run())


@pytest.mark.parametrize('code',['unsupported_thinking_level','model_profile_changed'])
def test_definitive_owner_rejection_does_not_turn_into_unknown_pending(tmp_path,code):
    from test_manual_task_commands import CommandRemote
    async def run():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path)
        adapter=CommandRemote(remote);adapter.settings=remote.settings;manager.connections.client=adapter;original=adapter.request
        def reject(method,path,**kw):
            if method=='POST' and path.endswith('/runs'):raise CloudError(code,422 if code=='unsupported_thinking_level' else 409)
            return original(method,path,**kw)
        adapter.request=reject
        try:
            request=dict(command_id=str(uuid4()),local_session_id=sid,expected_binding_revision=binding['revision'],text='work',model_id='A',thinking_level='high',draft_revision=None)
            await manager.commands.submit(domain,binding['binding_id'],'message',request);await manager.drain()
            op=domain.repo.operation(request['command_id'],sid)
            assert op['phase']=='rejected' and op['steps']['primary']['rejected']==code
            adapter.request=original
            current=await manager.read(domain,binding['binding_id'],sid);cid=str(uuid4())
            await manager.commands.submit(domain,binding['binding_id'],'message',request|{'command_id':cid,'thinking_level':'model_default','expected_binding_revision':current['revision']})
            await manager.drain();assert domain.repo.operation(cid,sid)['phase']=='accepted'
        finally:await manager.close();store.close()
    asyncio.run(run())


def test_model_default_cannot_bypass_changed_profile_before_dispatch(tmp_path):
    from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
    from test_runner_control import client,connect,hello
    config,_=model_settings(tmp_path)
    with open_cloud_runtime(config) as r:
        bootstrap=prepare_runner(r)
        with client(r) as c,connect(c,r,bootstrap) as ws:
            frame=hello(bootstrap);frame['model_ids']=['chat'];ws.send_json(frame);ws.receive_json()
            row=r.registry.get_task(bootstrap.task_id)
            r.models.profile=r.models.profile.model_copy(update={'profile_id':'f'*64})
            cid=str(uuid4());response=c.post('/api/cloud/v1/tasks/'+bootstrap.task_id+'/runs',headers=owner_headers(r),json=dict(
                command_id=cid,expected_revision=row.revision,expected_generation=row.generation,message='work',model_id='chat',thinking_level='model_default'))
            assert response.status_code==409 and response.json()['error']['code']=='model_profile_changed'
            assert r.registry.get_task(bootstrap.task_id).active_run_id is None
            with pytest.raises(CloudError,match='command_not_found'):r.registry.get_command(cid)


def test_model_default_does_not_trust_runner_advertised_model_outside_profile(tmp_path):
    from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
    from test_runner_control import client,connect,hello
    config,_=model_settings(tmp_path)
    with open_cloud_runtime(config) as r:
        bootstrap=prepare_runner(r)
        with client(r) as c,connect(c,r,bootstrap) as ws:
            frame=hello(bootstrap);frame['model_ids']=['outside-profile'];ws.send_json(frame);ws.receive_json()
            row=r.registry.get_task(bootstrap.task_id);cid=str(uuid4())
            options=c.get('/api/cloud/v1/tasks/'+bootstrap.task_id+'/models',headers=owner_headers(r))
            assert options.status_code==200 and options.json()['model_ids']==[]
            response=c.post('/api/cloud/v1/tasks/'+bootstrap.task_id+'/runs',headers=owner_headers(r),json=dict(
                command_id=cid,expected_revision=row.revision,expected_generation=row.generation,message='work',model_id='outside-profile',thinking_level='model_default'))
            assert response.status_code==409 and response.json()['error']['code']=='model_unavailable'
            assert r.registry.get_task(bootstrap.task_id).active_run_id is None
            with pytest.raises(CloudError,match='command_not_found'):r.registry.get_command(cid)
