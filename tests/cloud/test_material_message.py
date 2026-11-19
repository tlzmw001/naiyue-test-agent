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


def test_live_message_snapshot_does_not_hide_saved_material_reference():
    import json
    from langchain_core.messages import HumanMessage
    from naiyue_test_agent.agent_runtime.message_observation import _snapshot
    ref=material();message=HumanMessage(id='material',content='analyse',additional_kwargs={'naiyue_cloud_material_refs':[ref.model_dump(mode='json')]})
    row=json.loads(_snapshot(message,'graph_values').snapshot_json)
    assert row['material_refs']==[dict(material_id=ref.material_id,title=ref.title,role=ref.role,size=ref.size)]


def test_real_web_rejects_retired_material_submission(monkeypatch,tmp_path):
    from test_manual_commands_routes import configured
    async def scenario():
        async with configured(monkeypatch,tmp_path) as t:
            base='/api/v1/scopes/'+t.host.cloud_tasks.project_id
            request_id=str(uuid4())
            sent=await t.client.post(base+'/sessions/'+t.sid+'/runs',headers=t.headers|{'X-Request-ID':request_id},
                json=dict(request_id=request_id,text='analyse material',material_refs=[str(uuid4())]))
            assert sent.status_code==410,sent.text
            assert sent.json()['error']['code']=='cloud_result_workflow_retired'
            assert t.model.seen==[]
    asyncio.run(scenario())


def test_legacy_material_input_is_rejected_by_run_api():
    api=RunAPI(SimpleNamespace(operation=nullcontext),SimpleNamespace(cloud_materials=None),None,None,None,None,None)
    async def scenario():
        with pytest.raises(CloudError,match='cloud_result_workflow_retired'):
            await api.prepare_input('S','analyse',[],message_id='message',material_refs=[str(uuid4())])
    asyncio.run(scenario())


def test_wire_accepts_only_unique_material_ids_not_client_provenance():
    from pydantic import ValidationError
    rid=str(uuid4());message=dict(request_id=str(uuid4()),text='analyse',material_refs=[rid])
    assert MessageInput.model_validate(message).material_refs==[rid]
    with pytest.raises(ValidationError):MessageInput.model_validate(message|{'material_refs':[dict(material_id=rid,reported=False)]})
    with pytest.raises(ValidationError):MessageInput.model_validate(message|{'material_refs':[rid,rid]})


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
