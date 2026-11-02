"""Formal catalog: real owner commands/WS, SDK HTTP, tools, token renewal and history."""
import asyncio
import json
from uuid import uuid4

import httpx
from fastapi import FastAPI,Request
from starlette.responses import Response,StreamingResponse

from naiyue_test_agent.cloud_runner.kernel import open_runner
from naiyue_test_agent.cloud_runner.transport import RunnerClient
from cloud_helpers import owner_headers
from model_http_helpers import live_app,cloud_task
from model_proxy_helpers import model_settings
from test_model_proxy import endpoint,headers
from test_prompt_cache_call_paths import completion_response
from test_runner_control import prepared


async def until(predicate):
    async with asyncio.timeout(8):
        while not predicate():await asyncio.sleep(0.01)


def test_formal_catalog_runs_tools_twice_with_history_and_renewal_without_rebuilding_models(tmp_path):
    async def scenario():
        upstream=FastAPI();seen=[]
        @upstream.post('/v1/chat/completions')
        async def serve(request:Request):
            body=await request.json();seen.append(body)
            step=len(seen)
            call=('write',{'path':'result.txt','content':'fixture result'},'write-id') if step==1 else (
                ('bash',{'command':'cat result.txt > verified.txt'},'shell-id') if step==2 else None)
            response=completion_response(body,text='done' if call is None else '',call=call)
            return Response(response.content,headers=dict(response.headers))
        async with live_app(upstream) as url:
            config,_=model_settings(tmp_path,upstream=url)
            async with cloud_task(config) as (r,owner,task_url):
                b=prepared(r);root=tmp_path/'project';root.mkdir();state=tmp_path/'state';state.mkdir(mode=0o700)
                stop=asyncio.Event()
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=owner),base_url=r.settings.origin,
                        headers=owner_headers(r)) as client:
                    async with open_runner(b,root,state) as kernel:
                        native=kernel.catalog.get('chat');runner=RunnerClient(kernel);job=asyncio.create_task(runner.run(stop))
                        try:
                            await until(lambda:r.registry.get_task(b.task_id).lifecycle=='ready')
                            async def run(message):
                                taskrow=r.registry.get_task(b.task_id);cid=str(uuid4())
                                response=await client.post('/api/cloud/v1/tasks/'+b.task_id+'/runs',json=dict(command_id=cid,
                                    expected_revision=taskrow.revision,expected_generation=taskrow.generation,message=message,model_id='chat'))
                                assert response.status_code==202,response.text
                                await until(lambda:r.registry.get_command(cid).status in ('succeeded','failed'))
                                assert r.registry.get_command(cid).status=='succeeded'
                                return cid
                            first=await run('write and verify')
                            assert (root/'result.txt').read_text()==(root/'verified.txt').read_text()=='fixture result'
                            assert len(seen)==3 and any(m['role']=='tool' for m in seen[-1]['messages'])
                            connection=r.control.connections[b.task_id]
                            renewed=r.control.bootstrap.renew(connection)
                            await connection.send(dict(kind='renewed',token=renewed.token.get_secret_value(),expires_at=renewed.expires_at))
                            await until(lambda:kernel.remote_models.bootstrap.token==renewed.token)
                            async with httpx.AsyncClient(base_url=task_url,trust_env=False) as raw:
                                denied=await raw.post(endpoint(b,'chat','chat/completions'),json={'model':'fixture-chat'},headers=headers(r,b))
                                assert denied.status_code==401
                            second=await run('continue')
                            assert len(seen)==4 and kernel.catalog.get('chat') is native
                            records=r.history_page(b.task_id)['events']
                            assert {e['payload'].get('command_id') for e in records if e['payload']['record']=='run_finished'}=={first,second}
                            assert kernel.journal.pending()==[] and not r.models.relay.leases
                        finally:stop.set();await asyncio.wait_for(job,8)
    asyncio.run(scenario())


def test_owner_stop_cancels_native_model_stream_and_upstream_then_next_input_runs(tmp_path):
    async def scenario():
        upstream=FastAPI();entered,closed,release=asyncio.Event(),asyncio.Event(),asyncio.Event();calls=0
        @upstream.post('/v1/chat/completions')
        async def serve(request:Request):
            nonlocal calls
            body=await request.json();calls+=1
            if calls>1:
                response=completion_response(body,text='next input')
                return Response(response.content,headers=dict(response.headers))
            async def chunks():
                try:
                    yield b'data: {"id":"fixture","object":"chat.completion.chunk","created":0,"model":"fixture-chat","choices":[{"index":0,"delta":{"role":"assistant","content":"partial"},"finish_reason":null}]}\n\n'
                    entered.set();await release.wait()
                finally:closed.set()
            return StreamingResponse(chunks(),media_type='text/event-stream')
        async with live_app(upstream) as url:
            config,_=model_settings(tmp_path,upstream=url)
            async with cloud_task(config) as (r,owner,_):
                b=prepared(r);root=tmp_path/'project';root.mkdir();state=tmp_path/'state';state.mkdir(mode=0o700)
                stop=asyncio.Event()
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=owner),base_url=r.settings.origin,
                        headers=owner_headers(r)) as client:
                    async with open_runner(b,root,state) as kernel:
                        runner=RunnerClient(kernel);job=asyncio.create_task(runner.run(stop))
                        try:
                            await until(lambda:r.registry.get_task(b.task_id).lifecycle=='ready')
                            row=r.registry.get_task(b.task_id);run_id=str(uuid4())
                            response=await client.post('/api/cloud/v1/tasks/'+b.task_id+'/runs',json=dict(command_id=run_id,
                                expected_revision=row.revision,expected_generation=row.generation,message='wait',model_id='chat'))
                            assert response.status_code==202
                            await asyncio.wait_for(entered.wait(),5)
                            row=r.registry.get_task(b.task_id);control_id=str(uuid4())
                            response=await client.post('/api/cloud/v1/tasks/'+b.task_id+'/control',json=dict(command_id=control_id,
                                expected_revision=row.revision,expected_generation=row.generation,kind='stop',run_id=row.active_run_id))
                            assert response.status_code==202
                            await until(lambda:r.registry.get_command(control_id).status in ('succeeded','failed'))
                            assert r.registry.get_command(control_id).status=='succeeded'
                            await asyncio.wait_for(closed.wait(),5)
                            assert kernel.journal.lookup(run_id)['status']=='stopped' and not r.models.relay.leases
                            row=r.registry.get_task(b.task_id);next_id=str(uuid4())
                            response=await client.post('/api/cloud/v1/tasks/'+b.task_id+'/runs',json=dict(command_id=next_id,
                                expected_revision=row.revision,expected_generation=row.generation,message='continue',model_id='chat'))
                            assert response.status_code==202
                            await until(lambda:r.registry.get_command(next_id).status in ('succeeded','failed'))
                            assert r.registry.get_command(next_id).status=='succeeded' and calls==2
                        finally:stop.set();release.set();await asyncio.wait_for(job,8)
    asyncio.run(scenario())
