"""Real WS plus owner/task ASGI: stop survives disconnect and lost history ACK."""
import asyncio
import json
from uuid import uuid4
import httpx
import pytest
from langchain_core.messages import AIMessage
from websockets.asyncio.server import serve
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.cloud_runner.kernel import open_runner
from naiyue_test_agent.cloud_runner.transport import RunnerClient
from naiyue_test_agent.contracts.cloud_runner import Hello,RunnerFrame
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.service.cloud.app import create_cloud_apps
from cloud_helpers import owner_headers
from lifecycle_helpers import config
from test_runner_control import prepared
from test_async_application import StreamingToolModel,call
from test_runner_execution_control import FailsAfterTool


@pytest.mark.parametrize('operation',['stop','recover'])
def test_stop_control_disconnect_and_lost_ack_finish_once_then_next_run(tmp_path,operation):
    with open_cloud_runtime(config(tmp_path)) as runtime:
        b=prepared(runtime)
        async def scenario():
            owner,task=create_cloud_apps(runtime)
            class LostAck(httpx.AsyncBaseTransport):
                def __init__(self):self.real=httpx.ASGITransport(app=task);self.lost=False
                async def handle_async_request(self,request):
                    response=await self.real.handle_async_request(request)
                    if response.status_code==200 and not self.lost:
                        self.lost=True;await response.aclose();raise httpx.ReadError('fixture lost history ACK')
                    return response
            root=tmp_path/'project';root.mkdir();state=tmp_path/'state';state.mkdir(mode=0o700)
            entered,release,connected,controlled,next_finished=(asyncio.Event() for _ in range(5))
            model=(StreamingToolModel(replies=[AIMessage(content='next answer')],entered=entered,release=release)
                if operation=='stop' else FailsAfterTool(replies=[call('bash',{'command':'printf once >> effect.txt'})]))
            failed,recovery_visible=asyncio.Event(),asyncio.Event()
            connections=0;cut=False;control_id=None;second_id=None
            class Adapter:
                def __init__(self,ws):self.ws=ws
                async def send_json(self,value):await self.ws.send(json.dumps(value))
                async def close(self,*,code,reason):await self.ws.close(code=code,reason=reason)
            async def handle(ws):
                nonlocal connections,cut
                connection=None
                try:
                    connections+=1
                    value=Hello.model_validate_json(await ws.recv())
                    connection=await runtime.control.attach(Adapter(ws),b.token.get_secret_value(),value)
                    connected.set()
                    async for raw in ws:
                        frame=RunnerFrame.validate_json(raw)
                        await runtime.control.receive(connection,frame)
                        if frame.kind=='control_state' and not cut:
                            cut=True;await ws.close();break
                        if frame.kind=='finished' and frame.status=='failed':failed.set()
                        if frame.kind=='heartbeat' and frame.recovery:recovery_visible.set()
                        if frame.kind=='control_finished' and frame.command_id==control_id:controlled.set()
                        if frame.kind=='finished' and frame.command_id==second_id:next_finished.set()
                finally:
                    if connection is not None:await runtime.control.detach(connection)
            transport=LostAck();stop=asyncio.Event()
            async with serve(handle,'127.0.0.1',0) as server:
                local=b.model_copy(update={'endpoint':'http://127.0.0.1:'+str(server.sockets[0].getsockname()[1]),
                    'retry_seconds':0.01,'heartbeat_seconds':0.2})
                async with httpx.AsyncClient(transport=transport,base_url='http://'+runtime.settings.task_listen) as http:
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=owner),base_url=runtime.settings.origin,headers=owner_headers(runtime)) as client:
                        async with open_runner(local,root,state,catalog=ModelCatalog('A',{'A':model})) as kernel:
                            runner=RunnerClient(kernel,http=http);job=asyncio.create_task(runner.run(stop))
                            try:
                                await asyncio.wait_for(connected.wait(),5)
                                taskrow=runtime.registry.get_task(b.task_id)
                                cid=str(uuid4())
                                start=await client.post('/api/cloud/v1/tasks/'+b.task_id+'/runs',json=dict(command_id=cid,
                                    expected_revision=taskrow.revision,expected_generation=taskrow.generation,message='wait',model_id='A'))
                                assert start.status_code==202
                                if operation=='stop':await asyncio.wait_for(entered.wait(),5)
                                else:
                                    await asyncio.wait_for(failed.wait(),5)
                                    await asyncio.wait_for(recovery_visible.wait(),5)
                                taskrow=runtime.registry.get_task(b.task_id);control_id=str(uuid4())
                                value=dict(command_id=control_id,expected_revision=taskrow.revision,expected_generation=taskrow.generation,
                                    kind=operation,run_id=taskrow.active_run_id if operation=='stop' else taskrow.last_run_id)
                                if operation=='recover':value['checkpoint_id']=taskrow.runner_recovery['checkpoint_id']
                                response=await client.post('/api/cloud/v1/tasks/'+b.task_id+'/control',json=value)
                                assert response.status_code==202
                                await asyncio.wait_for(controlled.wait(),10)
                                assert connections>=2 and cut and transport.lost and len(model.seen)==1
                                assert runtime.registry.get_command(control_id).status=='succeeded'
                                assert runtime.registry.get_command(cid).status==('succeeded' if operation=='stop' else 'failed')
                                assert kernel.journal.lookup(cid)['status']==('stopped' if operation=='stop' else 'recovered')
                                assert kernel.journal.pending()==[]
                                events=runtime.history_page(b.task_id)['events']
                                assert sum(e['payload']['record']=='control_finished' for e in events)==1
                                if operation=='recover':
                                    assert (root/'effect.txt').read_text()=='once'
                                    model.replies.append(AIMessage(content='new input only'))
                                release.set();taskrow=runtime.registry.get_task(b.task_id);second_id=str(uuid4())
                                response=await client.post('/api/cloud/v1/tasks/'+b.task_id+'/runs',json=dict(command_id=second_id,
                                    expected_revision=taskrow.revision,expected_generation=taskrow.generation,message='new',model_id='A'))
                                assert response.status_code==202
                                await asyncio.wait_for(next_finished.wait(),8)
                                assert len(model.seen)==2 and runtime.registry.get_command(second_id).status=='succeeded'
                                if operation=='recover':assert (root/'effect.txt').read_text()=='once'
                                repeated=await client.post('/api/cloud/v1/tasks/'+b.task_id+'/control',json=value)
                                assert repeated.status_code==200 and repeated.json()['status']=='succeeded'
                            finally:
                                stop.set();release.set();await asyncio.wait_for(job,5)
        asyncio.run(scenario())


def test_disconnect_during_durable_control_admission_keeps_the_owned_producer(tmp_path,monkeypatch):
    from threading import Event
    from contextlib import suppress
    from test_runner_kernel import bootstrap,request
    from test_runner_execution_control import action
    async def scenario():
        root=tmp_path/'project';root.mkdir();state=tmp_path/'state';state.mkdir(mode=0o700)
        entered,release=asyncio.Event(),asyncio.Event()
        model=StreamingToolModel(replies=[AIMessage(content='unused')],entered=entered,release=release)
        b=bootstrap()
        async with open_runner(b,root,state,catalog=ModelCatalog('A',{'A':model})) as kernel:
            run=request();await kernel.accept(run);await asyncio.wait_for(entered.wait(),5)
            cmd=action('stop',run=run);persisted,gate=Event(),Event()
            original=kernel.journal.admit_control
            def admit(command):
                result=original(command);persisted.set()
                assert gate.wait(5),'control admission release timed out'
                return result
            monkeypatch.setattr(kernel.journal,'admit_control',admit)
            fence=dict(connection_id=str(uuid4()),generation=b.generation,boot_id=b.boot_id,state_epoch=b.state_epoch)
            class Socket:
                async def recv(self):return json.dumps(dict(kind='control_command',command=cmd.model_dump(),**fence))
                async def send(self,value):pass
            client=RunnerClient(kernel);client.ws=Socket();client.fence=fence
            receiver=asyncio.create_task(client.receiver())
            try:
                assert await asyncio.to_thread(persisted.wait,5)
                receiver.cancel();gate.set()
                with pytest.raises(asyncio.CancelledError):await receiver
                assert kernel.actions.task is not None
                result=await asyncio.wait_for(asyncio.shield(kernel.actions.task),5)
                assert result['status']=='completed' and kernel.journal.lookup(run.command_id)['status']=='stopped'
            finally:
                gate.set();release.set()
                receiver.cancel()
                with suppress(asyncio.CancelledError):await receiver
                if kernel.active_task is not None:
                    with suppress(Exception,asyncio.CancelledError):await asyncio.wait_for(kernel.active_task,5)
    asyncio.run(scenario())
