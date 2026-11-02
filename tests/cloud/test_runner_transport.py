"""Lost HTTP ACK and WS reconnection deliver once without replaying tools."""
import asyncio
import json

import httpx
from langchain_core.messages import AIMessage
from websockets.asyncio.server import serve

from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.cloud_runner.kernel import open_runner
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.service.cloud.app import create_cloud_apps
from cloud_helpers import settings
from test_runner_kernel import bootstrap,request
from test_async_application import StreamingToolModel


def test_active_execution_survives_control_loss_and_outbox_ack_loss(tmp_path):
    from naiyue_test_agent.cloud_runner.transport import RunnerClient
    async def scenario(r):
        from naiyue_test_agent.contracts.cloud_tasks import CreateProject
        from cloud_helpers import task_record
        from uuid import uuid4
        p=r.commands.create_project(CreateProject(command_id=str(uuid4()),name='transport'))
        record=task_record(p.resource_id);r.registry.add_task(record)
        issued=r.credentials.issue(record.task_id,scopes=('events:append',))
        b=bootstrap().model_copy(update={'task_id':record.task_id,'project_id':p.resource_id,'token':issued.token})
        _,app=create_cloud_apps(r)
        class LostAck(httpx.AsyncBaseTransport):
            def __init__(self):self.real=httpx.ASGITransport(app=app);self.lost=False
            async def handle_async_request(self,request):
                response=await self.real.handle_async_request(request)
                if response.status_code==200 and not self.lost:
                    self.lost=True;await response.aclose();raise httpx.ReadError('test lost ACK')
                return response
        root=tmp_path/'project';root.mkdir();state=tmp_path/'state';state.mkdir(mode=0o700)
        entered,release,completed=asyncio.Event(),asyncio.Event(),asyncio.Event()
        model=StreamingToolModel(replies=[AIMessage(content='once')],entered=entered,release=release)
        command=request();connections=0
        async def handle(ws):
            nonlocal connections
            connections+=1
            hello=json.loads(await ws.recv());assert hello['kind']=='hello'
            fence=dict(connection_id=str(uuid4()),generation=b.generation,boot_id=b.boot_id,state_epoch=b.state_epoch)
            await ws.send(json.dumps(dict(kind='welcome',**fence)))
            if connections==1:
                await ws.send(json.dumps(dict(kind='command',command=command.model_dump(),**fence)))
                value=json.loads(await ws.recv());assert value['kind']=='accepted'
                await asyncio.wait_for(entered.wait(),5)
                await ws.close();release.set()
            else:
                await ws.send(json.dumps(dict(kind='query',command_id=command.command_id,**fence)))
                async for raw in ws:
                    value=json.loads(raw)
                    if value['kind']=='finished':completed.set();break
                    if value['kind']=='heartbeat':await ws.send(json.dumps(dict(kind='welcome',**fence)))
        stop=asyncio.Event();transport=LostAck()
        async with serve(handle,'127.0.0.1',0) as server:
            port=server.sockets[0].getsockname()[1]
            b=b.model_copy(update={'endpoint':'http://127.0.0.1:'+str(port),'retry_seconds':0.01})
            async with httpx.AsyncClient(transport=transport,base_url='http://'+r.settings.task_listen) as http:
                async with open_runner(b,root,state,catalog=ModelCatalog('A',{'A':model})) as kernel:
                    client=RunnerClient(kernel,http=http)
                    job=asyncio.create_task(client.run(stop))
                    try:
                        await asyncio.wait_for(completed.wait(),10)
                        assert len(model.seen)==1 and connections>=2 and transport.lost
                        assert kernel.journal.pending()==[]
                        history=r.history_page(b.task_id)
                        assert history['last_seq']==3 and len(history['events'])==3
                    finally:
                        stop.set();await asyncio.wait_for(job,5)
    with open_cloud_runtime(settings(tmp_path)) as runtime:asyncio.run(scenario(runtime))
