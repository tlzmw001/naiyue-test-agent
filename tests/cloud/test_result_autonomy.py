"""Native model/tool execution publishes outputs without a local follow-up request."""
import asyncio,json
from pathlib import Path
import httpx
from fastapi import FastAPI,Request
from starlette.responses import Response
from uuid import uuid4
from model_http_helpers import live_app,cloud_task
from model_proxy_helpers import model_settings
from test_prompt_cache_call_paths import completion_response
from test_runner_control import prepared
from test_model_runner import until
from cloud_helpers import owner_headers
from naiyue_test_agent.cloud_runner.kernel import open_runner
from naiyue_test_agent.cloud_runner.transport import RunnerClient


def test_current_run_output_tool_and_offline_result_delivery(tmp_path,monkeypatch):
    async def scenario():
        from naiyue_test_agent.cloud_runner import results as result_module
        committed=asyncio.Event();release=asyncio.Event()
        request=result_module.task_request
        async def delayed_commit(*args,**kwargs):
            value=await request(*args,**kwargs)
            if args[2]=='POST' and args[3].endswith('/commit'):
                committed.set()
                await release.wait()
            return value
        monkeypatch.setattr(result_module,'task_request',delayed_commit)
        upstream=FastAPI();seen=[]
        @upstream.post('/v1/chat/completions')
        async def serve(request:Request):
            body=await request.json();seen.append(body);call=None
            if len(seen)==1:call=('task_output_directory',{},'output-directory')
            elif len(seen)==2:
                reply=next(m for m in reversed(body['messages']) if m['role']=='tool')
                directory=json.loads(reply['content'])['relative_directory']
                call=('write',{'path':directory+'/handoff.md','content':'fixture handoff report'},'write-report')
            elif len(seen)==3:call=('write',{'path':'generated.py','content':'assert True\n'},'write-code')
            response=completion_response(body,text='finished' if call is None else '',call=call)
            return Response(response.content,headers=dict(response.headers))
        async with live_app(upstream) as url:
            cfg,_=model_settings(tmp_path,upstream=url)
            async with cloud_task(cfg) as (r,owner,_):
                b=prepared(r);root=tmp_path/'project';root.mkdir();state=tmp_path/'state';state.mkdir(mode=0o700);stop=asyncio.Event()
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=owner),base_url=r.settings.origin,headers=owner_headers(r)) as client:
                    async with open_runner(b,root,state) as kernel:
                        job=asyncio.create_task(RunnerClient(kernel).run(stop))
                        try:
                            await until(lambda:r.registry.get_task(b.task_id).lifecycle=='ready')
                            task=r.registry.get_task(b.task_id);cid=str(uuid4())
                            started=await client.post('/api/cloud/v1/tasks/'+b.task_id+'/runs',json=dict(command_id=cid,
                                expected_revision=task.revision,expected_generation=task.generation,message='write code and report',model_id='chat'))
                            assert started.status_code==202,started.text
                            plan=started.json()['result']['command']['result_plan'];rid=plan['result_id']
                            # No UI owner command is sent while the cloud performs, freezes and publishes.
                            await until(lambda:r.results.get(b.task_id,rid).phase=='available')
                            result=r.results.get(b.task_id,rid);assert len(seen)==4
                            report=next(f.artifact for f in result.bundle.files if f.role=='artifact')
                            assert report.relative_name=='handoff.md'
                            path='/api/cloud/v1/tasks/'+b.task_id+'/results/'+rid
                            assert (await client.get(path+'/files/'+report.artifact_id+'/bytes')).content==b'fixture handoff report'
                            assert 'generated.py' in {row['path'] for row in (await client.get(path+'/changes')).json()['changes']}
                            await asyncio.wait_for(committed.wait(),8)
                            assert kernel.results.pending()!=[]
                            release.set()
                            await until(lambda:kernel.results.pending()==[])
                            assert kernel.results.pending()==[] and r.registry.get_task(b.task_id).pending_result_id is None
                            assert kernel.journal.lookup(cid)['status']=='completed'
                        finally:release.set();stop.set();await asyncio.wait_for(job,8)
    asyncio.run(scenario())
