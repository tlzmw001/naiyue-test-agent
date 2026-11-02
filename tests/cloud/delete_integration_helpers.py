"""One real owner API paired with the existing local journal/transport producer."""
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from types import SimpleNamespace
import httpx
from test_manual_task_bindings import populated, uid
from lifecycle_helpers import config, Backend
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.application.cloud.backend_runtime import open_cloud_backend
from naiyue_test_agent.service.cloud.app import create_cloud_apps
from naiyue_test_agent.cloud_files.client import CloudClient, CloudConnection
from naiyue_test_agent.cloud_files.profiles import ConnectedProfile, ProfileBook, CloudProfile
from naiyue_test_agent.cloud_task_settings import CloudTaskSettings
from naiyue_test_agent.application.cloud_local.tasks import ManualCloudTasks, LocalTaskDomain
from naiyue_test_agent.application.lifetime import ApplicationLifetime


@asynccontextmanager
async def pair(tmp_path, monkeypatch):
    remote=tmp_path/'remote';remote.mkdir();local=tmp_path/'local';local.mkdir()
    with open_cloud_runtime(config(remote)) as runtime:
        store,project,sid=populated(local);backend=Backend()
        domain=LocalTaskDomain(ApplicationLifetime(None,None,AsyncExitStack()),store,project)
        async with open_cloud_backend(runtime,backend=backend):
            owner,_=create_cloud_apps(runtime);loop=asyncio.get_running_loop()
            state=SimpleNamespace(drop=False,offline=False,posts=[])
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=owner),base_url=runtime.settings.origin) as upstream:
                async def forward(req):
                    if state.offline:raise httpx.ConnectError('controlled offline',request=req)
                    if req.method=='POST':state.posts.append(req.url.path)
                    response=await upstream.request(req.method,str(req.url),headers=req.headers,content=req.content)
                    if state.drop and req.method=='POST' and req.url.path.endswith('/delete'):
                        state.drop=False;raise httpx.ReadError('controlled lost delete ACK',request=req)
                    return httpx.Response(response.status_code,headers=response.headers,content=response.content,request=req)
                def transport(req):return asyncio.run_coroutine_threadsafe(forward(req),loop).result(timeout=10)
                connection=CloudConnection(endpoint=runtime.settings.origin,owner_authority=runtime.settings.owner_listen,
                    owner_token=runtime.settings.owner_token,instance_id=runtime.instance_id)
                profile=CloudProfile(profile_id='work',connection_file=local/'unused.json',template_ids=['python-tests'],default_template='python-tests')
                def open_one(p):
                    result=ConnectedProfile(p,CloudClient(connection,transport=httpx.MockTransport(transport)));result.check();return result
                monkeypatch.setattr(ConnectedProfile,'open',open_one)
                manager=ManualCloudTasks(CloudTaskSettings(),book=ProfileBook(profiles=[profile]))
                manager.connection_settings=lambda p:connection
                try:
                    source=local/'source';(source/'test.py').write_text('baseline')
                    captured=await domain.capture(sid,source,{'paths':['test.py']},manager.settings.input_limits)
                    created=await manager.submit(domain,'project',dict(command_id=uid(),local_session_id=sid,profile_id='work',
                        source_handle=captured['source_handle'],template_id='python-tests'));await manager.drain()
                    bid=created['binding']['binding_id'];binding=domain.repo.binding(bid,sid);tid=binding['task_id']
                    task=runtime.registry.get_task(tid)
                    runtime.replace_task(task.model_copy(update={'revision':task.revision+1,'last_quiescent_at':1.0}),
                        expected_revision=task.revision,expected_generation=task.generation)
                    binding=await manager.read(domain,bid,sid)
                    yield SimpleNamespace(runtime=runtime,backend=backend,manager=manager,domain=domain,store=store,
                        sid=sid,bid=bid,binding=binding,state=state)
                finally:await manager.close();store.close()
