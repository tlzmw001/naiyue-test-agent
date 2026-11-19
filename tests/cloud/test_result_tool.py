import asyncio
import pytest
from uuid import uuid4

from naiyue_test_agent.application.cloud_local.result_tool import LocalResultTool
from test_manual_commands_routes import configured
from test_result_receive import receiving


def test_result_tool_saves_fixed_artifact_and_reports_conflict(monkeypatch, tmp_path):
    async def run():
        async with configured(monkeypatch, tmp_path) as t:
            domain = t.host.cloud_tasks
            state = (t.manager, domain, t.host.sessions._persistence.storage, t.sid, t.adapter, t.binding)
            _, _, _, _, remote, _, rid, files, data, _ = await receiving(tmp_path, prepared_state=state)
            try:
                port = LocalResultTool(t.manager, domain)
                aid = files[2]['artifact']['artifact_id']
                path = tmp_path / 'chosen-name.txt'
                path.write_text('existing')
                query = await port.query(t.sid, dict(action='query', binding_id=t.bid, result_id=rid, limit=20, offset=0))
                assert [item['artifact_id'] for item in query['items']] == [aid]
                plan = dict(binding_id=t.bid, result_id=rid, artifact_id=aid, path=str(path), force=False)
                first = str(uuid4())
                await port.download(t.sid, first, plan)
                await t.manager.drain()
                assert (await port.status(t.sid, first))['state'] == 'conflict'
                assert path.read_text() == 'existing'
                denied = await t.client.post(t.base+'/cloud-deliveries/'+first+'/execute',
                    params=t.query,headers=t.headers,json={'confirm':True,'force':True})
                assert denied.status_code==409
                assert path.read_text()=='existing'
                second = str(uuid4())
                await port.download(t.sid, second, plan | {'force': True})
                await t.manager.drain()
                assert (await port.status(t.sid, second))['state'] == 'completed'
                assert path.read_bytes() == data[aid]
                third = str(uuid4())
                await port.download(t.sid, third, plan)
                await t.manager.drain()
                assert (await port.status(t.sid, third))['outcome'] == 'already_present'
                path.write_text('changed later')
                result = await port.status(t.sid, second)
                assert result['receipt_state'] == 'completed' and 'path' not in result
            finally:
                remote.http.close()
    asyncio.run(run())


def test_default_service_runs_cloud_result_through_main_agent(monkeypatch, tmp_path):
    import json
    from langchain_core.messages import AIMessage, ToolMessage
    from naiyue_test_agent import application
    from naiyue_test_agent.model_access import ModelCatalog
    from naiyue_test_agent.service.app import create_service_app
    from test_async_application import StreamingToolModel, call
    from test_service_ownership import options

    async def run():
        model = StreamingToolModel(replies=[call('cloud_result', {'action':'query'}), AIMessage(content='checked')])
        monkeypatch.setattr(application,'load_model_catalog',lambda _:ModelCatalog('A',{'A':model}))
        assets=tmp_path/'assets';assets.mkdir();(assets/'index.html').write_text('<html>fixture</html>')
        web=create_service_app(options(tmp_path))
        async with web.router.lifespan_context(web):
            root=web.state.application;source=tmp_path/'project';source.mkdir()
            project=await root.projects.add_local(source,trusted=True,operation_id='result-project')
            host=await root.projects.open(project.project_id)
            await web.state.scopes.get(project.project_id)
            sid=await host.sessions.create('A')
            result=await host.runs.start(sid,[('user','List my cloud tasks')])
            reply=next(message for message in result['messages'] if isinstance(message,ToolMessage))
            assert reply.status=='success',reply.content
            assert json.loads(reply.content)==dict(action='query',level='tasks',items=[],next_offset=None)
    asyncio.run(run())


def test_status_does_not_start_interrupted_download_and_resume_keeps_plan(monkeypatch,tmp_path):
    async def run():
        async with configured(monkeypatch,tmp_path) as t:
            domain=t.host.cloud_tasks
            state=(t.manager,domain,t.host.sessions._persistence.storage,t.sid,t.adapter,t.binding)
            _,_,_,_,remote,_,rid,files,data,requests=await receiving(tmp_path,prepared_state=state)
            try:
                port=LocalResultTool(t.manager,domain)
                artifact=files[2]['artifact']
                cid=str(uuid4())
                path=tmp_path/'interrupted.txt'
                plan=dict(binding_id=t.bid,result_id=rid,artifact_id=artifact['artifact_id'],
                          path=str(path),force=False,size=artifact['size'],sha256=artifact['sha256'])
                port._reserve(t.sid,cid,plan,None)
                previous=list(requests)
                assert (await port.status(t.sid,cid))['state']=='unknown'
                assert requests==previous and not path.exists()
                await port.download(t.sid,cid,plan,resume=True)
                await t.manager.drain()
                assert (await port.status(t.sid,cid))['state']=='completed'
                assert path.read_bytes()==data[artifact['artifact_id']]
            finally:
                remote.http.close()
    asyncio.run(run())


def test_main_agent_download_uses_the_reviewed_file_target(monkeypatch,tmp_path):
    import json
    from langchain_core.messages import AIMessage, ToolMessage
    from naiyue_test_agent.tools.cloud_results import create_cloud_result_tool
    from test_async_application import call
    import test_service_protocol

    assemble=test_service_protocol.multi_app

    def app_with_cloud_tool(*args,**kwargs):
        return assemble(*args,tools=[create_cloud_result_tool()],**kwargs)

    monkeypatch.setattr(test_service_protocol,'multi_app',app_with_cloud_tool)

    async def run():
        async with configured(monkeypatch,tmp_path) as t:
            domain=t.host.cloud_tasks
            state=(t.manager,domain,t.host.sessions._persistence.storage,t.sid,t.adapter,t.binding)
            _, _, _, _, remote, _, rid, files, data, _ = await receiving(tmp_path,prepared_state=state)
            try:
                aid=files[2]['artifact']['artifact_id']
                destination=tmp_path/'a'/'artifact.txt'
                missing=destination.parent/'missing'/destination.name
                t.model.replies.extend([
                    call('cloud_result',dict(action='download',binding_id=t.bid,result_id=rid,
                         artifact_id=aid,path=str(missing)),identity='missing-folder'),
                    call('cloud_result',dict(action='download',binding_id=t.bid,result_id=rid,
                         artifact_id=aid,path=str(destination)),identity='existing-folder'),
                    AIMessage(content='Download requested'),
                ])
                await t.app.state.scopes.get(domain.project_id)
                result=await t.host.runs.start(t.sid,[('user','Save my result as artifact.txt')])
                missing_reply,feedback=[m for m in result['messages'] if isinstance(m,ToolMessage)]
                assert missing_reply.status=='success',missing_reply.content
                error=json.loads(missing_reply.content)['error']
                assert error['code']=='project_directory_missing' and error['step']=='preflight'
                assert not missing.exists()
                assert feedback.status=='success',feedback.content
                cid=json.loads(feedback.content)['operation_id']
                await t.manager.drain()
                reply=await LocalResultTool(t.manager,domain).status(t.sid,cid)
                assert reply['state']=='completed',reply
                assert destination.read_bytes()==data[aid]
            finally:
                remote.http.close()
    asyncio.run(run())


def test_remembered_cloud_save_does_not_authorize_overwrite():
    from naiyue_test_agent.tools.permissions import matches_session_allow, remembered_rule

    subject=dict(tool_name='cloud_result',operation='write',workspace='/project',
        execution_environment={'platform':'macos','kind':'host'},resolved='/project/report.txt',args={},
        download_plan=dict(path='/project/report.txt',force=False,binding_id='B',result_id='R',artifact_id='A'))
    rule=remembered_rule(subject)
    assert matches_session_allow([rule],subject)
    assert not matches_session_allow([rule],subject|{'download_plan':subject['download_plan']|{'force':True}})
    assert not matches_session_allow([rule],subject|{'download_plan':subject['download_plan']|{'path':'/project/other.txt'}})


@pytest.mark.parametrize('retired', [False, True], ids=['rotated', 'retired'])
def test_conflict_can_be_resolved_from_local_receive_after_source_unavailable(monkeypatch, tmp_path, retired):
    from naiyue_test_agent.contracts.cloud_tasks import CloudError

    async def run():
        async with configured(monkeypatch, tmp_path) as t:
            domain = t.host.cloud_tasks
            context = (t.manager, domain, t.host.sessions._persistence.storage, t.sid, t.adapter, t.binding)
            _, _, _, _, remote, _, rid, files, data, _ = await receiving(tmp_path, prepared_state=context)
            try:
                aid = files[2]['artifact']['artifact_id']
                port = LocalResultTool(t.manager, domain)
                path = tmp_path / 'report.txt'
                path.write_text('local')
                plan = dict(binding_id=t.bid, result_id=rid, artifact_id=aid, path=str(path), force=False)
                first = str(uuid4())
                await port.download(t.sid, first, plan)
                await t.manager.drain()
                assert (await port.status(t.sid, first))['state'] == 'conflict'

                if retired:
                    binding=domain.repo.binding(t.bid,t.sid)
                    await t.manager.forget(domain,t.bid,dict(command_id=str(uuid4()),local_session_id=t.sid,
                        expected_binding_revision=binding['revision'],acknowledge_unmanaged=True))

                def removed(*args, **kwargs):
                    raise CloudError('result_not_available', 404)

                monkeypatch.setattr(t.adapter, 'request', removed)
                renamed = tmp_path / 'report-copy.txt'
                for changes, target in ((dict(path=str(renamed)), renamed), (dict(force=True), path)):
                    operation = str(uuid4())
                    await port.download(t.sid, operation, plan | changes)
                    await t.manager.drain()
                    assert (await port.status(t.sid, operation))['state'] == 'completed'
                    assert target.read_bytes() == data[aid]
                another_session=await t.host.sessions.create('A')
                with pytest.raises(CloudError,match='cloud_binding_not_found'):
                    await port.download(another_session,str(uuid4()),plan)
            finally:
                remote.http.close()

    asyncio.run(run())


def test_temporary_writer_conflict_can_resume_and_identical_content_keeps_mode(monkeypatch, tmp_path):
    import os
    import stat

    async def run():
        async with configured(monkeypatch, tmp_path) as t:
            domain = t.host.cloud_tasks
            context = (t.manager, domain, t.host.sessions._persistence.storage, t.sid, t.adapter, t.binding)
            _, _, _, _, remote, _, rid, files, data, _ = await receiving(tmp_path, prepared_state=context)
            try:
                aid = files[2]['artifact']['artifact_id']
                port = LocalResultTool(t.manager, domain)
                path = tmp_path / 'report.txt'
                plan = dict(binding_id=t.bid, result_id=rid, artifact_id=aid, path=str(path), force=False)
                cid = str(uuid4())
                t.manager.deliveries.writers.add(domain.project_id)
                await port.download(t.sid, cid, plan)
                await t.manager.drain()
                failure = await port.status(t.sid, cid)
                assert failure['error']['code'] == 'cloud_delivery_project_busy'
                assert failure['error']['retryable']
                assert failure['progress']['received_bytes'] == len(data[aid])
                assert failure['progress']['saved_bytes'] == 0
                t.manager.deliveries.writers.remove(domain.project_id)
                await port.download(t.sid, cid, plan, resume=True)
                await t.manager.drain()
                assert path.read_bytes() == data[aid]
                assert (await port.status(t.sid, cid))['state'] == 'completed'

                if os.name != 'nt':
                    path.chmod(0o755)
                    repeat = str(uuid4())
                    await port.download(t.sid, repeat, plan)
                    await t.manager.drain()
                    assert (await port.status(t.sid, repeat))['outcome'] == 'already_present'
                    assert stat.S_IMODE(path.stat().st_mode) == 0o755
            finally:
                remote.http.close()

    asyncio.run(run())
