"""A user submits material identities; only a real tool read carries report bytes."""
import asyncio
from contextlib import nullcontext
from types import SimpleNamespace
from uuid import uuid4
import pytest
from naiyue_test_agent.contracts.cloud_receive import MaterialRef
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.application.sessions.run_api import RunAPI
from naiyue_test_agent.web.schemas import MessageInput


def material():
    return MaterialRef(material_id=str(uuid4()),receive_id=str(uuid4()),task_id='a'*32,result_id=str(uuid4()),input_id='b'*64,
        title='handoff.md',role='artifact',size=20,sha256='c'*64,source_run_id='d'*32,outcome='failed',captured_at=1.0)


def test_prepare_material_input_keeps_report_out_of_human_text_and_checks_original_identity():
    ref=material();seen=[]
    class Materials:
        def validate_refs(self,sid,ids):seen.append((sid,ids));return [ref]
    sessions=SimpleNamespace(cloud_materials=Materials())
    api=RunAPI(SimpleNamespace(operation=nullcontext),sessions,None,None,None,None,None)
    async def scenario():
        value=await api.prepare_input('S','analyse this cloud report',[],message_id='message',material_refs=[ref.material_id])
        assert value.content=='analyse this cloud report'
        assert value.additional_kwargs['naiyue_cloud_material_refs']==[ref.model_dump(mode='json')]
        assert 'test result body' not in value.model_dump_json()
        assert seen==[('S',[ref.material_id])]
    asyncio.run(scenario())


def test_live_message_snapshot_does_not_hide_saved_material_reference():
    import json
    from langchain_core.messages import HumanMessage
    from naiyue_test_agent.agent_runtime.message_observation import _snapshot
    ref=material();message=HumanMessage(id='material',content='analyse',additional_kwargs={'naiyue_cloud_material_refs':[ref.model_dump(mode='json')]})
    row=json.loads(_snapshot(message,'graph_values').snapshot_json)
    assert row['material_refs']==[dict(material_id=ref.material_id,title=ref.title,role=ref.role,size=ref.size)]


def test_real_web_live_and_archive_both_keep_material_ids_after_confirmed_send(monkeypatch,tmp_path):
    from langchain_core.messages import AIMessage
    from test_manual_commands_routes import configured
    from test_result_receive import receiving
    async def scenario():
        async with configured(monkeypatch,tmp_path) as t:
            domain=t.host.cloud_tasks;store=t.host.sessions._persistence.storage
            _,_,_,_,remote,_,rid,files,_,_=await receiving(tmp_path,prepared_state=(t.manager,domain,store,t.sid,t.adapter,t.binding))
            try:
                record=await t.manager.receives.submit(domain,t.bid,rid,dict(command_id=str(uuid4()),local_session_id=t.sid,artifact_ids=[files[2]['artifact']['artifact_id']]))
                await t.manager.drain();draft=await t.manager.receives.analysis(domain,record.receive_id,t.sid)
                ids=[ref.material_id for ref in draft.material_refs];t.model.replies.append(AIMessage(content='analysed'))
                base='/api/v1/scopes/'+domain.project_id
                request_id=str(uuid4())
                sent=await t.client.post(base+'/sessions/'+t.sid+'/runs',headers=t.headers|{'X-Request-ID':request_id},
                    json=dict(request_id=request_id,text='analyse material',material_refs=ids))
                assert sent.status_code==202,sent.text
                service=await t.app.state.scopes.get(domain.project_id);await service.delivery.wait(sent.json()['run_id'])
                live=(await t.client.get(base+'/sessions/'+t.sid,headers=t.headers)).json()
                archived=(await t.client.get(base+'/sessions/'+t.sid+'/messages',headers=t.headers)).json()
                for view in (live,archived):
                    row=next(m for m in view['messages'] if m['type']=='human')
                    assert [r['material_id'] for r in row['material_refs']]==ids
                    assert row['content']=='analyse material' and '第二行' not in row['content']
            finally:remote.http.close()
    asyncio.run(scenario())


def test_material_input_without_owner_capability_is_rejected():
    api=RunAPI(SimpleNamespace(operation=nullcontext),SimpleNamespace(cloud_materials=None),None,None,None,None,None)
    async def scenario():
        with pytest.raises(CloudError,match='cloud_materials_unavailable'):
            await api.prepare_input('S','analyse',[],message_id='message',material_refs=[str(uuid4())])
    asyncio.run(scenario())


def test_wire_accepts_only_unique_material_ids_not_client_provenance():
    from pydantic import ValidationError
    rid=str(uuid4());message=dict(request_id=str(uuid4()),text='analyse',material_refs=[rid])
    assert MessageInput.model_validate(message).material_refs==[rid]
    with pytest.raises(ValidationError):MessageInput.model_validate(message|{'material_refs':[dict(material_id=rid,reported=False)]})
    with pytest.raises(ValidationError):MessageInput.model_validate(message|{'material_refs':[rid,rid]})


def test_formal_model_loop_reads_material_through_real_tool_and_persists_separate_sources(tmp_path,monkeypatch):
    from langchain_core.messages import AIMessage,HumanMessage,ToolMessage
    from test_async_application import make_app,StreamingToolModel,call
    from naiyue_test_agent.tools.cloud_materials import create_cloud_result_read_tool
    async def scenario():
        ref=material();calls=[]
        class Materials:
            def validate_refs(self,sid,ids):assert ids==[ref.material_id];return [ref]
            def has_materials(self,sid):return True
            async def query(self,**kwargs):
                calls.append(kwargs);return dict(material=ref.model_dump(mode='json'),reported=True,text='external reported failure',next_offset=None,binary=False)
        model=StreamingToolModel(replies=[call('cloud_result_read',{'material_id':ref.material_id}),AIMessage(content='report analysis')])
        app=await make_app(monkeypatch,model,[create_cloud_result_read_tool()])
        app.host.sessions._sessions.cloud_materials=Materials()
        try:
            sid=await app.host.sessions.create('A')
            message=await app.host.runs.prepare_input(sid,'analyse selected report',[],message_id='material-input',material_refs=[ref.material_id])
            assert calls==[] and model.seen==[]
            result=await app.host.runs.start(sid,[message])
            assert len(calls)==1 and len(model.seen)==2
            human=next(m for m in result['messages'] if isinstance(m,HumanMessage))
            tool=next(m for m in result['messages'] if isinstance(m,ToolMessage))
            assert human.content=='analyse selected report' and 'external reported failure' not in human.content
            assert human.additional_kwargs['naiyue_cloud_material_refs'][0]['material_id']==ref.material_id
            assert tool.status=='success' and 'external reported failure' in tool.content
            stored=await app.host.history.messages(sid)
            assert any(isinstance(m,ToolMessage) and m.tool_call_id==tool.tool_call_id for m in stored)
            assert 'cloud_result_read' in str(model.seen[0]) and 'external reported failure' not in str(model.seen[0])
        finally:await app.aclose()
    asyncio.run(scenario())


def test_host_material_read_budget_uses_application_configuration(monkeypatch):
    from naiyue_test_agent import application
    from naiyue_test_agent.settings import AppSettings
    from naiyue_test_agent.cloud_task_settings import CloudTaskSettings
    from naiyue_test_agent.contracts.cloud_receive import ReceiveLimits
    from naiyue_test_agent.model_access import ModelCatalog
    from test_async_application import StreamingToolModel
    from scripts.component_fixtures import build_component_application
    async def scenario():
        monkeypatch.setattr(application,'load_model_catalog',lambda _:ModelCatalog('A',{'A':StreamingToolModel(replies=[])}))
        limits=ReceiveLimits(max_read_bytes=17,max_read_lines=3)
        app=await build_component_application('unused',tools=[],system_prompt='',response_format=None,
            settings=AppSettings(cloud_tasks=CloudTaskSettings(receive_limits=limits)))
        try:assert app.host.cloud_tasks.materials.limits==limits
        finally:await app.aclose()
    asyncio.run(scenario())


def test_material_only_submission_is_a_meaningful_user_input():
    value=MessageInput.model_validate(dict(request_id=str(uuid4()),material_refs=[str(uuid4())]))
    assert value.text=='' and len(value.material_refs)==1


def test_material_reference_is_visible_in_live_and_bounded_saved_history(monkeypatch,tmp_path):
    from langchain_core.messages import AIMessage,HumanMessage
    from naiyue_test_agent.agent_runtime.message_presentation import message_view
    from naiyue_test_agent.web.chat_history import record_view
    from test_persistent_sessions import build
    from test_application_run_events import ToolModel
    ref=material()
    expected=[dict(material_id=ref.material_id,title=ref.title,role=ref.role,size=ref.size)]
    message=HumanMessage(id='reference',content='',additional_kwargs={'naiyue_cloud_material_refs':[ref.model_dump(mode='json')]})
    assert message_view(message)['material_refs']==expected
    async def scenario():
        async with build(monkeypatch,tmp_path/'state.sqlite',ToolModel(responses=[AIMessage(content='ok')])) as app:
            sid=await app.host.sessions.create('A')
            await app.host.runs.start(sid,[message])
            store=app.host.sessions._persistence.storage
            page=store.conversation.read_chat_page(sid,store.sessions.get(sid)['thread_id'],limit=10,body_bytes=1,preview_chars=1)
            record=next(row for row in page['records'] if row['message_id']=='reference')
            assert record['body_json'] is None
            assert record_view(record)['material_refs']==expected
    asyncio.run(scenario())
