"""Real HTTP/WS/SSE with the existing Agent, tools and required history outbox."""
import asyncio
import json
import shlex
import sys
from uuid import uuid4

import httpx
import pytest
from langchain_core.messages import AIMessage

from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.cloud_runner.kernel import open_runner
from naiyue_test_agent.cloud_runner.transport import RunnerClient
from naiyue_test_agent.contracts.cloud_runner import StartRun
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.service.cloud.app import create_cloud_apps
from cloud_helpers import owner_headers
from lifecycle_helpers import config
from model_http_helpers import live_app
from test_runner_control import prepared
from test_async_application import StreamingToolModel,call


@pytest.mark.parametrize('disconnect_ui',[False,True])
def test_real_runner_progress_then_confirmed_history_and_usage_over_sse(tmp_path,disconnect_ui):
    async def run(r):
        b=prepared(r);owner,task=create_cloud_apps(r)
        root=tmp_path/'workspace';root.mkdir();state=tmp_path/'runner-state';state.mkdir(mode=0o700)
        script="import pathlib,time; gate=pathlib.Path('release'); print('working',flush=True)\nwhile not gate.exists(): time.sleep(0.1)"
        command=shlex.quote(sys.executable)+' -c '+shlex.quote(script)
        model=StreamingToolModel(replies=[AIMessage(content='running tests',tool_calls=[dict(id='shell',name='bash',args={'command':command})]),AIMessage(content='tests complete')])
        stop=asyncio.Event();connected=asyncio.Event();progress=asyncio.Event();finished=asyncio.Event();captured=asyncio.Event();events=[]
        original_receive=r.observations.receive
        async def receive(connection,frame):
            await original_receive(connection,frame)
            if any(v.event_type=='run_finished' and v.source is None for v in frame.batch.records):captured.set()
        r.observations.receive=receive
        async with live_app(task) as task_url,live_app(owner) as owner_url:
            r.settings=r.settings.model_copy(update={'task_listen':task_url.removeprefix('http://')})
            b=b.model_copy(update={'endpoint':task_url,'retry_seconds':0.02,'heartbeat_seconds':0.2})
            async with open_runner(b,root,state,catalog=ModelCatalog('A',{'A':model})) as kernel:
                client=RunnerClient(kernel);original=client.connected
                async def connection():connected.set();await original()
                client.connected=connection
                worker=asyncio.create_task(client.run(stop))
                headers=owner_headers(r)|{'Host':r.settings.authority}
                async with httpx.AsyncClient(base_url=owner_url,headers=headers,timeout=10,trust_env=False) as http:
                    async def observe():
                        async with http.stream('GET','/api/cloud/v1/tasks/'+b.task_id+'/stream') as response:
                            assert response.status_code==200
                            async for line in response.aiter_lines():
                                if not line.startswith('data: '):continue
                                value=json.loads(line[6:]);events.append(value)
                                live=value.get('live',value)
                                if any(t.get('tool_name')=='bash' for t in live.get('tools',[])):progress.set()
                                if value.get('task',{}).get('last_run_id') and value['task']['active_run_id'] is None:
                                    finished.set();return
                    observer=asyncio.create_task(observe())
                    try:
                        await asyncio.wait_for(connected.wait(),5)
                        # Owner task read waits on an actual ready heartbeat, never a fixed sleep.
                        async def ready():
                            while True:
                                row=r.registry.get_task(b.task_id)
                                if row.runner_connection_id:return row
                                await asyncio.sleep(0)
                        row=await asyncio.wait_for(ready(),5)
                        receipt=await http.post('/api/cloud/v1/tasks/'+b.task_id+'/runs',json=StartRun(command_id=str(uuid4()),
                            expected_generation=row.generation,expected_revision=row.revision,message='run tests',model_id='A').model_dump())
                        assert receipt.status_code==202,receipt.text
                        await asyncio.wait_for(progress.wait(),8)
                        assert kernel.active_task is not None and not kernel.active_task.done()
                        if disconnect_ui:
                            observer.cancel();await asyncio.gather(observer,return_exceptions=True)
                            async def disconnected():
                                while r.observations.leases:await asyncio.sleep(0)
                            await asyncio.wait_for(disconnected(),5)
                            assert kernel.active_task is not None and not kernel.active_task.done()
                            observer=asyncio.create_task(observe())
                        (root/'release').write_text('continue')
                        await asyncio.wait_for(finished.wait(),10);await observer
                        await asyncio.wait_for(captured.wait(),5)
                        page=await http.get('/api/cloud/v1/tasks/'+b.task_id+'/history')
                        assert page.status_code==200 and page.json()['messages'][-1]['content']=='tests complete'
                        usage=await http.get('/api/cloud/v1/tasks/'+b.task_id+'/usage')
                        assert usage.status_code==200 and usage.json()['recorded_calls']==2
                        assert not r.observations.leases
                        assert len(model.seen)==2 and any(v.get('archive',{}).get('messages') for v in events)
                    finally:
                        (root/'release').touch();observer.cancel();await asyncio.gather(observer,return_exceptions=True)
                        stop.set();await asyncio.wait_for(worker,8)
            await r.observations.aclose();await r.control.aclose()
    with open_cloud_runtime(config(tmp_path)) as r:asyncio.run(run(r))
