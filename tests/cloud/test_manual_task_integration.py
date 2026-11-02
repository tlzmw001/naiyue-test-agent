"""Real owner API/capture/upload/install integration with a controlled backend."""
import asyncio
import json
from pathlib import Path
import httpx
from test_manual_task_bindings import populated,uid
from lifecycle_helpers import config,Backend
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.application.cloud.backend_runtime import open_cloud_backend
from naiyue_test_agent.service.cloud.app import create_cloud_apps
from naiyue_test_agent.cloud_files.client import CloudClient,CloudConnection
from naiyue_test_agent.cloud_files.profiles import ConnectedProfile
from naiyue_test_agent.cloud_task_settings import CloudTaskSettings
from naiyue_test_agent.application.cloud_local.tasks import ManualCloudTasks,LocalTaskDomain
from naiyue_test_agent.application.lifetime import ApplicationLifetime
from contextlib import AsyncExitStack


def test_manual_two_tasks_share_one_import_and_keep_independent_workspaces(tmp_path,monkeypatch):
    remote=tmp_path/'remote';remote.mkdir();local=tmp_path/'local';local.mkdir()
    with open_cloud_runtime(config(remote)) as runtime:
        async def scenario():
            backend=Backend();store,project,sid=populated(local)
            owner=ApplicationLifetime(None,None,AsyncExitStack());domain=LocalTaskDomain(owner,store,project)
            async with open_cloud_backend(runtime,backend=backend):
                app,_=create_cloud_apps(runtime);loop=asyncio.get_running_loop();seen=[]
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=runtime.settings.origin) as upstream:
                    async def forward(request):
                        seen.append((request.method,request.url.path))
                        response=await upstream.request(request.method,str(request.url),headers=request.headers,content=request.content)
                        return httpx.Response(response.status_code,headers=response.headers,content=response.content,request=request)
                    def transport(request):return asyncio.run_coroutine_threadsafe(forward(request),loop).result(timeout=10)
                    connection=CloudConnection(endpoint=runtime.settings.origin,owner_authority=runtime.settings.owner_listen,
                        owner_token=runtime.settings.owner_token,instance_id=runtime.instance_id)
                    path=local/'connection.json';path.write_text(connection.model_dump_json());path.chmod(0o600)
                    profiles=local/'profiles.json';profiles.write_text(json.dumps({'profiles':[dict(profile_id='work',
                        connection_file=str(path),template_ids=['python-tests'],default_template='python-tests')]}));profiles.chmod(0o600)
                    def open_one(profile):
                        value=ConnectedProfile(profile,CloudClient(connection,transport=httpx.MockTransport(transport)))
                        value.check();return value
                    monkeypatch.setattr(ConnectedProfile,'open',open_one)
                    manager=ManualCloudTasks(CloudTaskSettings(profiles_file=profiles))
                    try:
                        source=local/'source';(source/'test.py').write_bytes(b'print("fixture")\r\n')
                        handle=await domain.capture(sid,source,{'paths':['test.py']},manager.settings.input_limits)
                        bindings=[]
                        for _ in range(2):
                            request=dict(command_id=uid(),local_session_id=sid,profile_id='work',source_handle=handle['source_handle'],template_id='python-tests')
                            result=await manager.submit(domain,'local project',request);await manager.drain()
                            binding=domain.repo.binding(result['binding']['binding_id'],sid)
                            assert binding['preparation']=='prepared',domain.repo.operation(request['command_id'],sid)
                            assert binding['cloud']['input_ready'] and binding['cloud']['active_run_id'] is None
                            bindings.append(binding)
                        assert bindings[0]['task_id']!=bindings[1]['task_id']
                        assert bindings[0]['cloud_project_id']==bindings[1]['cloud_project_id']
                        assert bindings[0]['input_id']==bindings[1]['input_id']
                        workspaces=[runtime.settings.data_dir/'tasks'/b['task_id']/'workspace/project' for b in bindings]
                        assert all((p/'test.py').read_bytes()==b'print("fixture")\r\n' for p in workspaces)
                        (workspaces[0]/'test.py').write_text('independent change')
                        assert (workspaces[1]/'test.py').read_bytes()==b'print("fixture")\r\n'
                        assert sum(m=='POST' and p.endswith('/transfers') for m,p in seen)==1
                        assert not any('/runs' in p or '/control' in p for _,p in seen)
                        assert len([c for c in backend.calls if c[0]=='create'])==2
                        await manager.close()
                        assert len(backend.sandboxes)==2 and not any(c[0]=='kill' for c in backend.calls)
                    finally:
                        await manager.close();store.close()
        asyncio.run(scenario())
