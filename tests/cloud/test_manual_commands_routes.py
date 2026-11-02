"""Real local HTTP entry, parent ownership and bounded command/draft views."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from test_service_protocol import service_clients
from test_async_application import StreamingToolModel
from test_multi_project_runtime import projects
from test_manual_task_preparation import Remote,Connections
from test_manual_task_commands import CommandRemote
from test_manual_task_bindings import uid
from naiyue_test_agent.application.cloud_local.tasks import ManualCloudTasks
from naiyue_test_agent.cloud_task_settings import CloudTaskSettings
from naiyue_test_agent.cloud_files.profiles import ProfileBook,CloudProfile


@asynccontextmanager
async def configured(monkeypatch,tmp_path):
    # One isolated service fixture is shared by the command and draft API branches.
    model=StreamingToolModel(replies=[])
    async with service_clients(monkeypatch,tmp_path,model) as (client,_,app,_):
        (project,host),(other,other_host)=await projects(app.state.application,tmp_path)
        sid=await host.sessions.create('A');other_sid=await other_host.sessions.create('A')
        (tmp_path/'a/test.py').write_text('fixture')
        remote=Remote(uid());profile=CloudProfile(profile_id='work',connection_file=tmp_path/'unused.json',template_ids=['test'],default_template='test')
        manager=ManualCloudTasks(CloudTaskSettings(),book=ProfileBook(profiles=[profile]),connections=Connections(remote))
        manager.connection_settings=lambda p:remote.settings;app.state.scopes.cloud_tasks=manager
        base='/api/client/v1';headers={'X-Service-Instance':app.state.owner.record.instance_id};query={'local_project_id':project.project_id,'local_session_id':sid}
        source=(await client.post(base+'/cloud-sources',params={'local_project_id':project.project_id},headers=headers,
            json=dict(local_session_id=sid,selection={'paths':['test.py']}))).json()
        created=await client.post(base+'/cloud-tasks',params={'local_project_id':project.project_id},headers=headers,json=dict(
            command_id=uid(),local_session_id=sid,profile_id='work',source_handle=source['source_handle'],template_id='test'))
        assert created.status_code==202,created.text
        await manager.drain();bid=created.json()['binding']['binding_id']
        remote.task.update(runner_phase='ready',runner_model_ids=['A'],reclaim_blockers=[],last_quiescent_at=1.0)
        binding=(await client.get(base+'/cloud-tasks/'+bid,params=query,headers=headers)).json()
        adapter=CommandRemote(remote);adapter.settings=remote.settings;manager.connections.client=adapter
        yield SimpleNamespace(client=client,app=app,model=model,manager=manager,host=host,remote=remote,adapter=adapter,
            base=base,headers=headers,query=query,bid=bid,sid=sid,other_sid=other_sid,binding=binding)


def test_message_draft_reconnect_list_and_control_are_wired_without_main_chat_write(monkeypatch,tmp_path):
    async def scenario():
        async with configured(monkeypatch,tmp_path) as t:
            path=t.base+'/cloud-tasks/'+t.bid
            saved=await t.client.put(path+'/draft',params=t.query,headers=t.headers,json=dict(expected_draft_revision=0,text='work',model_id='A'))
            assert saved.status_code==200 and saved.json()['draft_revision']==1
            req=dict(command_id=uid(),local_session_id=t.sid,expected_binding_revision=t.binding['revision'],text='work',model_id='A',draft_revision=1)
            denied=await t.client.post(path+'/messages',params=t.query,headers=t.headers|{'Authorization':''},json=req)
            assert denied.status_code==401
            sent=await t.client.post(path+'/messages',params=t.query,headers=t.headers,json=req)
            assert sent.status_code==202 and 'text' not in sent.json()
            await t.manager.drain()
            status=await t.client.get(t.base+'/cloud-operations/'+req['command_id'],params=t.query,headers=t.headers)
            assert status.json()['phase']=='accepted' and status.json()['run_id']==t.remote.task['active_run_id']
            draft=await t.client.get(path+'/draft',params=t.query,headers=t.headers)
            assert draft.json()['text']=='' and draft.json()['draft_revision']==2
            ops=await t.client.get(path+'/operations',params=t.query,headers=t.headers)
            assert req['command_id'] in [op['command_id'] for op in ops.json()]
            cross=await t.client.get(path+'/draft',params=t.query|{'local_session_id':t.other_sid},headers=t.headers)
            assert cross.status_code==404
            invalid=await t.client.post(path+'/messages',params=t.query,headers=t.headers,json=req|{'command_id':uid(),'images':[{}]})
            assert invalid.status_code==422
            fresh=(await t.client.get(path,params=t.query,headers=t.headers)).json()
            stop=await t.client.post(path+'/control',params=t.query,headers=t.headers,json=dict(command_id=uid(),local_session_id=t.sid,
                expected_binding_revision=fresh['revision'],kind='stop',run_id=t.remote.task['active_run_id']))
            assert stop.status_code==202;await t.manager.drain()
            assert t.model.seen==[] and (await t.host.sessions.status(t.sid))['run_id'] is None
    asyncio.run(scenario())


def test_long_prompt_uses_bounded_summaries_and_stale_draft_is_rejected(monkeypatch,tmp_path):
    async def scenario():
        async with configured(monkeypatch,tmp_path) as t:
            path=t.base+'/cloud-tasks/'+t.bid;text='long prompt '*20000
            req=dict(command_id=uid(),local_session_id=t.sid,expected_binding_revision=t.binding['revision'],text=text,model_id='A')
            sent=await t.client.post(path+'/messages',params=t.query,headers=t.headers,json=req)
            assert sent.status_code==202;await t.manager.drain()
            response=await t.client.get(t.base+'/cloud-operations/'+req['command_id'],params=t.query,headers=t.headers)
            assert response.status_code==200 and len(response.content)<4096 and text not in response.text
            result=await t.client.put(path+'/draft',params=t.query,headers=t.headers,json=dict(expected_draft_revision=0,text='new',model_id='A'))
            assert result.status_code==200
            stale=await t.client.put(path+'/draft',params=t.query,headers=t.headers,json=dict(expected_draft_revision=0,text='stale',model_id='A'))
            assert stale.status_code==409 and stale.json()['error']['code']=='cloud_draft_conflict'
    asyncio.run(scenario())
