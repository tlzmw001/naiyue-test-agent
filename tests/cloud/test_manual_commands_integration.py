"""Local dispatch through real Coordinator HTTP and required-history control fences."""
import asyncio
from contextlib import AsyncExitStack
from types import SimpleNamespace
import httpx
from test_manual_task_bindings import populated,uid
from lifecycle_helpers import config,Backend
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.application.cloud.backend_runtime import open_cloud_backend
from naiyue_test_agent.service.cloud.app import create_cloud_apps
from naiyue_test_agent.cloud_files.client import CloudClient,CloudConnection
from naiyue_test_agent.cloud_files.profiles import ConnectedProfile,ProfileBook,CloudProfile
from naiyue_test_agent.cloud_task_settings import CloudTaskSettings
from naiyue_test_agent.application.cloud_local.tasks import ManualCloudTasks,LocalTaskDomain
from naiyue_test_agent.application.lifetime import ApplicationLifetime
from naiyue_test_agent.contracts.cloud_runner import Hello,RunnerFrame
from observation_helpers import Socket
from test_runner_control import hello


def test_real_owner_message_stop_required_save_release_and_restart_original_workspace(tmp_path,monkeypatch):
    remote=tmp_path/'remote';remote.mkdir();local=tmp_path/'local';local.mkdir()
    with open_cloud_runtime(config(remote)) as runtime:
        async def scenario():
            store,project,sid=populated(local);backend=Backend()
            domain=LocalTaskDomain(ApplicationLifetime(None,None,AsyncExitStack()),store,project)
            async with open_cloud_backend(runtime,backend=backend):
                owner,_=create_cloud_apps(runtime);loop=asyncio.get_running_loop();seen=[]
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=owner),base_url=runtime.settings.origin) as upstream:
                    async def forward(request):
                        seen.append((request.method,request.url.path))
                        response=await upstream.request(request.method,str(request.url),headers=request.headers,content=request.content)
                        return httpx.Response(response.status_code,headers=response.headers,content=response.content,request=request)
                    def transport(request):return asyncio.run_coroutine_threadsafe(forward(request),loop).result(timeout=10)
                    connection=CloudConnection(endpoint=runtime.settings.origin,owner_authority=runtime.settings.owner_listen,
                        owner_token=runtime.settings.owner_token,instance_id=runtime.instance_id)
                    profile=CloudProfile(profile_id='work',connection_file=local/'unused.json',template_ids=['python-tests'],default_template='python-tests')
                    def open_one(p):
                        value=ConnectedProfile(p,CloudClient(connection,transport=httpx.MockTransport(transport)));value.check();return value
                    monkeypatch.setattr(ConnectedProfile,'open',open_one)
                    manager=ManualCloudTasks(CloudTaskSettings(),book=ProfileBook(profiles=[profile]))
                    manager.connection_settings=lambda p:connection
                    try:
                        source=local/'source';(source/'test.py').write_text('original')
                        captured=await domain.capture(sid,source,{'paths':['test.py']},manager.settings.input_limits)
                        created=await manager.submit(domain,'project',dict(command_id=uid(),local_session_id=sid,profile_id='work',
                            source_handle=captured['source_handle'],template_id='python-tests'));await manager.drain()
                        bid=created['binding']['binding_id'];binding=domain.repo.binding(bid,sid);tid=binding['task_id']
                        async def attach():
                            bootstrap=runtime.control.prepare(tid);socket=Socket()
                            values=hello(bootstrap)|{'capabilities':['control:stop','control:recover']}
                            control=await runtime.control.attach(socket,bootstrap.token.get_secret_value(),Hello.model_validate(values))
                            return bootstrap,socket,control
                        b,socket,control=await attach();binding=await manager.read(domain,bid,sid)
                        def request(binding,**values):return dict(command_id=uid(),local_session_id=sid,
                            expected_binding_revision=binding['revision'],**values)
                        message=await manager.commands.submit(domain,bid,'message',request(binding,text='write tests',model_id='A',draft_revision=None))
                        await manager.drain();run=runtime.registry.get_command(message['command_id']).result['command']
                        assert domain.repo.operation(message['command_id'],sid)['phase']=='accepted'
                        binding=await manager.read(domain,bid,sid)
                        stop=await manager.commands.submit(domain,bid,'stop',request(binding,kind='stop',run_id=run['run_id'],checkpoint_id=None))
                        await manager.drain();assert runtime.registry.get_command(stop['command_id']).status=='running'
                        binding=await manager.read(domain,bid,sid)
                        release=await manager.commands.submit(domain,bid,'release',request(binding,action='release',acknowledge_unknown=False,expected_generation=None))
                        await manager.drain();assert domain.repo.operation(release['command_id'],sid)['error_code']=='cloud_task_not_quiescent'
                        assert not any(c[0]=='kill' for c in backend.calls)
                        events=[dict(seq=1,source_generation=b.generation,run_id=run['run_id'],kind='history_record',
                            payload=dict(record='run_finished',command_id=run['command_id'],status='stopped',session_state='ready'))]
                        runtime.append_events(b.token.get_secret_value(),dict(task_id=tid,generation=b.generation,epoch=b.state_epoch,events=events))
                        await runtime.control.receive(control,RunnerFrame.validate_python(dict(kind='finished',command_id=run['command_id'],run_id=run['run_id'],
                            status='stopped',terminal_seq=1,quiescent=True,**control.fence)))
                        end=dict(command_id=stop['command_id'],status='completed',phase='ready',quiescent=True,error_code=None,recovery=None)
                        runtime.append_events(b.token.get_secret_value(),dict(task_id=tid,generation=b.generation,epoch=b.state_epoch,
                            events=[dict(seq=2,source_generation=b.generation,run_id=run['run_id'],kind='history_record',payload=dict(record='control_finished',**end))]))
                        await runtime.control.receive(control,RunnerFrame.validate_python(dict(kind='control_finished',terminal_seq=2,**end,**control.fence)))
                        assert (await manager.commands.reconcile(domain,stop['command_id'],sid))['phase']=='succeeded'
                        pending=runtime.registry.get_task(tid).pending_result_id
                        assert pending and runtime.results.get(tid,pending).phase=='pending'
                        runtime.results.action(tid,pending,'abandon',dict(command_id=uid(),expected_revision=runtime.results.get(tid,pending).revision))
                        root=runtime.settings.data_dir/'tasks'/tid/'workspace/project';(root/'test.py').write_text('retained changes')
                        binding=await manager.read(domain,bid,sid)
                        release=await manager.commands.submit(domain,bid,'release',request(binding,action='release',acknowledge_unknown=False,expected_generation=None))
                        await manager.drain();assert domain.repo.operation(release['command_id'],sid)['phase']=='succeeded'
                        await runtime.control.detach(control)
                        binding=await manager.read(domain,bid,sid);previous=runtime.registry.get_task(tid).generation
                        async def reconnect():
                            async with asyncio.timeout(8):
                                while True:
                                    current=runtime.registry.get_task(tid)
                                    if current.generation>previous and current.environment_phase=='backend_ready':return await attach()
                                    await asyncio.sleep(0.01)
                        reconnecting=asyncio.create_task(reconnect())
                        follow=await manager.commands.submit(domain,bid,'message',request(binding,text='continue',model_id='A',draft_revision=None))
                        await manager.drain();await reconnecting
                        assert domain.repo.operation(follow['command_id'],sid)['phase']=='accepted'
                        assert (root/'test.py').read_text()=='retained changes'
                        assert len([c for c in backend.calls if c[0]=='create'])==2
                        assert sum(m=='POST' and p.endswith('/transfers') for m,p in seen)==1
                        assert store.sessions.get(sid)['run_id'] is None
                        await manager.close();assert runtime.registry.get_task(tid).active_run_id is not None
                    finally:await manager.close();store.close()
        asyncio.run(scenario())
