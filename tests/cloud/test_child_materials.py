"""Delegated material reads retain role selection, permits and live approval."""
import asyncio
import json
from dataclasses import replace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage,HumanMessage,ToolMessage

from native_application_helpers import async_test
from naiyue_test_agent.agent_runtime.run_scope import current_run_scope
from naiyue_test_agent.contracts.cloud_receive import MaterialRef
from naiyue_test_agent.tools.cloud_materials import create_cloud_result_read_tool
from naiyue_test_agent.tools.permissions import ToolPermission
from test_async_application import call
from test_subagent_approval_resume import approval_notifications,next_approval,submit,cancel_run
from test_subagent_execution import ChildModel,delegated_app,record_instances,role


@pytest.mark.parametrize('allowed,approval',[(True,False),(False,False),(True,True)])
@async_test
async def test_child_material_read_uses_parent_capability_only_with_role_and_permit(monkeypatch,allowed,approval):
    ref=MaterialRef(material_id=str(uuid4()),receive_id=str(uuid4()),task_id='a'*32,
        result_id=str(uuid4()),input_id='b'*64,title='report.md',role='artifact',size=20,
        sha256='c'*64,source_run_id='d'*32,outcome='failed',captured_at=1.0)
    reads=[]
    class Materials:
        def validate_refs(self,sid,ids):
            assert ids==[ref.material_id]
            return [ref]
        def has_materials(self,sid):return True
        async def query(self,**kwargs):
            reads.append((kwargs,current_run_scope()))
            return dict(material=ref.model_dump(mode='json'),reported=True,text='external reported failure')
    materials=Materials()
    tool=create_cloud_result_read_tool()
    if approval:tool=replace(tool,permissions=ToolPermission(default='ask'))
    child=ChildModel(replies=[call('cloud_result_read',{'material_id':ref.material_id},'inner'),
        AIMessage(content='child report')])
    app,root=await delegated_app(monkeypatch,child,
        spec=role(tool_names=('cloud_result_read',) if allowed else ()),tools=[tool])
    app.host.sessions._sessions.cloud_materials=materials
    instances=record_instances(monkeypatch,app)
    notifications=approval_notifications(monkeypatch,app)
    sid=await app.host.sessions.create('A')
    message=await app.host.runs.prepare_input(sid,'analyse attached cloud materials',[],
        message_id='material-input',material_refs=[ref.material_id])
    task=asyncio.create_task(app.host.runs.start(sid,[message]))
    try:
        if approval:
            batch=await next_approval(app,sid,notifications)
            assert reads==[] and not task.done()
            assert [item['tool_call_id'] for item in batch['items']]==['inner']
            assert batch['agent_path']==instances[0]['agent_path']
            await submit(app,sid,batch)
        result=await asyncio.wait_for(task,5)
        instance,=instances
        graph=app.host.sessions._sessions._subagents['tester']['agent']
        saved=await graph.aget_state({'configurable':{'thread_id':instance['child_thread_id']}})
        feedback=next(m for m in saved.values['messages'] if isinstance(m,ToolMessage))
        assert feedback.tool_call_id=='inner'
        assert feedback.status==('success' if allowed else 'error')
        assert len(reads)==int(allowed)
        if allowed:
            request,scope=reads[0]
            assert request==dict(source_session_id=sid,project_id=instance['project_id'],
                request=dict(material_id=ref.material_id,offset=1,limit=None))
            assert scope.identity.agent_path==instance['agent_path']
            assert scope.identity.session_id==sid and scope.identity.project_id==instance['project_id']
            assert all(scope.capabilities.cloud_materials==materials.query for scope in child.services)
            assert json.loads(feedback.content)['reported'] is True
            assert 'external reported failure' in feedback.content
        else:
            assert 'not active' in feedback.content
        assert saved.values.get('cloud_materials') is None
        assert all(m.additional_kwargs.get('naiyue_human_input') is not True
            for m in saved.values['messages'] if isinstance(m,HumanMessage))
        assert 'external reported failure' not in str(message.content)
        assert len(root.seen)==2 and app.host.runs.pending_subagents(sid)==[]
        assert result['messages'][-1].content=='root done'
    finally:
        await cancel_run(task)
