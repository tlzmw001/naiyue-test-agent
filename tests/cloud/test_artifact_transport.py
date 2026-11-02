"""Real control sockets plus task HTTP: lost ACKs resend frozen bytes, not capture."""
import asyncio
import json

import httpx
import pytest
from websockets.asyncio.server import serve

from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.cloud_runner.kernel import open_runner
from naiyue_test_agent.cloud_runner.transport import RunnerClient
from naiyue_test_agent.contracts.cloud_runner import RunnerFrame
from naiyue_test_agent.service.cloud.app import create_cloud_apps
from cloud_helpers import owner_headers
from lifecycle_helpers import config
from test_runner_control import prepared


@pytest.mark.parametrize('phase',['prepare','bytes','commit'])
def test_capture_survives_upload_ack_loss_without_rescanning(tmp_path,phase):
    async def scenario(r):
        b=prepared(r);root=tmp_path/'project';root.mkdir();state=tmp_path/'state';state.mkdir(mode=0o700)
        source=root/'report.bin';data=bytes(range(256))*257;source.write_bytes(data)
        owner,task=create_cloud_apps(r);connected,completed=asyncio.Event(),asyncio.Event();errors=[]
        class LostAck(httpx.AsyncBaseTransport):
            def __init__(self):self.real=httpx.ASGITransport(app=task);self.lost=False;self.puts=0
            async def handle_async_request(self,request):
                if request.method=='PUT':self.puts+=1
                response=await self.real.handle_async_request(request)
                if request.url.path.endswith('/'+phase) and response.status_code==200 and not self.lost:
                    self.lost=True;source.write_bytes(b'workspace changed after capture')
                    await response.aclose();raise httpx.ReadError('synthetic lost artifact ACK')
                return response
        async def handler(ws):
            class Socket:
                async def send_json(self,value):await ws.send(json.dumps(value))
                async def close(self,**kwargs):await ws.close()
            connection=None
            try:
                hello=RunnerFrame.validate_json(await ws.recv())
                connection=await r.control.attach(Socket(),b.token.get_secret_value(),hello);connected.set()
                async for raw in ws:
                    frame=RunnerFrame.validate_json(raw)
                    await r.control.receive(connection,frame)
                    if frame.kind=='artifact_finished':completed.set()
            except BaseException as error:
                errors.append(error);completed.set()
            finally:
                if connection is not None:await r.control.detach(connection)
        transport=LostAck();stop=asyncio.Event()
        async with serve(handler,'127.0.0.1',0) as server:
            b=b.model_copy(update={'endpoint':'http://127.0.0.1:'+str(server.sockets[0].getsockname()[1]),'retry_seconds':0.01})
            async with httpx.AsyncClient(transport=transport,base_url='http://'+r.settings.task_listen) as http:
                async with open_runner(b,root,state) as kernel:
                    client=RunnerClient(kernel,http=http);job=asyncio.create_task(client.run(stop))
                    try:
                        await asyncio.wait_for(connected.wait(),5)
                        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=owner),base_url=r.settings.origin,
                                headers=owner_headers(r)) as user:
                            from uuid import uuid4
                            t=r.registry.get_task(b.task_id);request=dict(command_id=str(uuid4()),expected_revision=t.revision,
                                expected_generation=t.generation,relative_name='report.bin')
                            response=await user.post('/api/cloud/v1/tasks/'+b.task_id+'/artifacts',json=request)
                            assert response.status_code==202
                            await asyncio.wait_for(completed.wait(),10)
                            assert errors==[] and transport.lost and transport.puts==1
                            receipt=r.registry.get_command(request['command_id'])
                            assert receipt.status=='succeeded' and kernel.phase=='waiting_model'
                            ident=receipt.result['artifact']['artifact_id']
                            download=await user.get('/api/cloud/v1/tasks/'+b.task_id+'/artifacts/'+ident+'/bytes')
                            assert download.content==data
                    finally:
                        stop.set();await asyncio.wait_for(job,5)
    with open_cloud_runtime(config(tmp_path)) as r:asyncio.run(scenario(r))
