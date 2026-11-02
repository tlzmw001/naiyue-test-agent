"""The real local service exposes only authenticated project-bound manual APIs."""
import asyncio
import json
from uuid import uuid4
from test_service_protocol import service_clients,command
from test_async_application import StreamingToolModel
from test_multi_project_runtime import projects


def test_manual_source_route_is_project_bound_bounded_and_does_not_invoke_model(monkeypatch,tmp_path):
    async def scenario():
        model=StreamingToolModel(replies=[])
        async with service_clients(monkeypatch,tmp_path,model) as (client,_,app,_):
            prefix='/api/client/v1'
            assert (await client.get(prefix+'/cloud-profiles')).status_code==409
            pinned={'X-Service-Instance':app.state.owner.record.instance_id}
            assert (await client.get(prefix+'/cloud-profiles',headers=pinned)).json()==[]
            assert (await client.get(prefix+'/cloud-profiles',headers=pinned|{'Authorization':''})).status_code==401
            (project,host),(other,_)=await projects(app.state.application,tmp_path)
            sid=await host.sessions.create('A');(tmp_path/'a/api.proto').write_text('proto fixture')
            url=prefix+'/cloud-sources?local_project_id='+project.project_id
            body=dict(local_session_id=sid,selection={'schema':1,'paths':['api.proto']})
            first=await client.post(url,json=body,headers=pinned)
            assert first.status_code==201,first.text
            value=first.json();assert len(value['input_id'])==64 and 'directory' not in value and 'root' not in value
            second=await client.post(url,json=body,headers=pinned)
            assert second.json()['input_id']==value['input_id'] and second.json()['source_handle']!=value['source_handle']
            unsafe=await client.post(url,json=body|{'root':'/tmp'},headers=pinned)
            assert unsafe.status_code==422
            cross=await client.post(prefix+'/cloud-sources?local_project_id='+other.project_id,json=body,headers=pinned)
            assert cross.status_code in (404,409)
            oversized=await client.post(url,content=b'x'*(app.state.web_settings.max_frame_bytes+1),headers=pinned)
            assert oversized.status_code==413
            listed=await client.get(prefix+'/cloud-tasks',params={'local_project_id':project.project_id,'local_session_id':sid},headers=pinned)
            assert listed.json()==[]
            assert model.seen==[]
    asyncio.run(scenario())


def test_manual_create_is_wired_and_parent_delete_is_blocked(monkeypatch,tmp_path):
    from types import SimpleNamespace
    from test_manual_task_preparation import Remote,Connections
    from naiyue_test_agent.application.cloud_local.tasks import ManualCloudTasks
    from naiyue_test_agent.cloud_task_settings import CloudTaskSettings
    from naiyue_test_agent.cloud_files.profiles import ProfileBook,CloudProfile
    async def scenario():
        model=StreamingToolModel(replies=[])
        async with service_clients(monkeypatch,tmp_path,model) as (client,_,app,_):
            (project,host),(_,other)=await projects(app.state.application,tmp_path)
            sid=await host.sessions.create('A');other_sid=await other.sessions.create('A')
            (tmp_path/'a/test.py').write_text('print(1)')
            remote=Remote(str(uuid4()));profile=CloudProfile(profile_id='work',connection_file=tmp_path/'unused.json',template_ids=['test'],default_template='test')
            manager=ManualCloudTasks(CloudTaskSettings(),book=ProfileBook(profiles=[profile]),connections=Connections(remote))
            manager.connection_settings=lambda p:SimpleNamespace(instance_id=remote.settings.instance_id)
            app.state.scopes.cloud_tasks=manager
            headers={'X-Service-Instance':app.state.owner.record.instance_id}
            base='/api/client/v1';query={'local_project_id':project.project_id,'local_session_id':sid}
            src=(await client.post(base+'/cloud-sources',params={'local_project_id':project.project_id},headers=headers,
                json=dict(local_session_id=sid,selection={'paths':['test.py']}))).json()
            body=dict(command_id=str(uuid4()),local_session_id=sid,profile_id='work',source_handle=src['source_handle'],template_id='test')
            response=await client.post(base+'/cloud-tasks',params={'local_project_id':project.project_id},headers=headers,json=body)
            assert response.status_code==202,response.text
            bid=response.json()['binding']['binding_id'];await manager.drain()
            result=await client.get(base+'/cloud-tasks/'+bid,params=query,headers=headers)
            assert result.status_code==200 and result.json()['task_id']==remote.task['task_id']
            listed=await client.get(base+'/cloud-tasks',params=query,headers=headers)
            assert [b['binding_id'] for b in listed.json()]==[bid]
            op=await client.get(base+'/cloud-operations/'+body['command_id'],params=query,headers=headers)
            assert op.json()['phase']=='prepared'
            cross=await client.get(base+'/cloud-tasks/'+bid,params=query|{'local_session_id':other_sid},headers=headers)
            assert cross.status_code==404
            deleted=await client.delete(f'/api/v1/scopes/{project.project_id}/sessions/{sid}',headers=command())
            assert deleted.status_code==409 and deleted.json()['error']['code']=='session_cloud_tasks_retained'
            assert model.seen==[] and not any('/runs' in p for p,b in remote.posts)
    asyncio.run(scenario())
