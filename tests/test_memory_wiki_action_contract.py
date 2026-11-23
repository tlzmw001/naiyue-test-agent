"""Wiki operations remain actions of one memory tool, with strict v2 permits."""
import pytest
from pydantic import ValidationError


def test_wiki_actions_share_single_envelope():
    from naiyue_test_agent.tools.memory_wiki_actions import WikiMemoryToolInput, compile_wiki_request
    for action in ('list_topics','list_pages','read_page','organize'):
        args={'action':action,'scope':'workspace'}
        if action=='list_pages':args['topic']='collaboration'
        if action=='read_page':args['page_id']='a'*32
        if action=='organize':args['page_ids']=['a'*32]
        assert WikiMemoryToolInput(request=args).request.action==action
        assert compile_wiki_request({'request':args})['action']==action


@pytest.mark.parametrize('payload',[
    {'action':'list','kind':'convention'},
    {'action':'read_page','scope':'both','page_id':'a'*32},
    {'action':'list_topics','proposal':{}},
    {'action':'organize','scope':'workspace','page_ids':[]},
    {'action':'organize','scope':'workspace','page_ids':['a'*32,'a'*32]},
    {'action':'update','scope':'workspace','memory_id':'a'*32,'expected_version':1,
     'change':{'mode':'supplement','body':'new'},'sources':[{'current_user':True,'quote':'原文'}]},
])
def test_wiki_rejects_old_and_mixed_parameters(payload):
    from naiyue_test_agent.tools.memory_wiki_actions import WikiMemoryToolInput
    with pytest.raises(ValidationError):WikiMemoryToolInput(request=payload)


def test_wiki_personal_scope_requires_bound_source_index():
    from naiyue_test_agent.tools.memory_wiki_actions import WikiMemoryToolInput
    p=dict(title='偏好',body='先举例',subject='讲解',basis='user_declaration',intent='activate',reason='用户要求')
    args=dict(action='record',scope='personal',proposal=p,sources=[dict(current_user=True,quote='先举例')],
        destination={'new_page':dict(topic='collaboration',title='讲解方式',description='讲解偏好')})
    with pytest.raises(ValidationError):WikiMemoryToolInput(request=args)
    assert WikiMemoryToolInput(request=dict(args,scope_source_indexes=[0])).request.proposal.body=='先举例'
    for indexes in ([1],[0,0]):
        with pytest.raises(ValidationError):WikiMemoryToolInput(request=dict(args,scope_source_indexes=indexes))
    workspace = WikiMemoryToolInput(request=dict(args,scope='workspace',scope_source_indexes=[0]))
    assert workspace.request.scope_source_indexes == [] and len(workspace.request.sources) == 1


def test_permission_v2_projection_and_v1_mismatch_rejection():
    from naiyue_test_agent.tools.permission_rules import PermissionRule, match_rules
    from naiyue_test_agent.tools.permissions import PermissionMechanismError
    rule=PermissionRule(tool='memory',args={'action':'read_page','scope':'personal'})
    subject=dict(tool_name='memory',argument_contract='memory_actions_v2',
        args={'request':dict(action='read_page',scope='personal',page_id='a'*32)})
    assert match_rules([rule],subject,deny=True)==rule
    with pytest.raises(PermissionMechanismError):match_rules([rule],dict(subject,argument_contract='memory_actions_v1'),deny=True)


def test_v2_registers_one_memory_tool_and_validates_permission_rules():
    from naiyue_test_agent.tools.memory import create_memory_tool
    from naiyue_test_agent.tools.memory_actions import memory_action_contract
    from naiyue_test_agent.tools.permission_rules import PermissionRule, PermissionSettings, validate_permission_rules
    from naiyue_test_agent.tools.registry import ToolRegistry
    from langchain_core.utils.function_calling import convert_to_openai_tool
    from langchain_anthropic.chat_models import convert_to_anthropic_tool
    spec=create_memory_tool();registry=ToolRegistry();registry.register(spec)
    assert registry.get('memory') is spec
    assert spec.tool.name=='memory' and memory_action_contract(spec)=='memory_actions_v2'
    validate_permission_rules(PermissionSettings(deny=[PermissionRule(tool='memory',args={'action':'organize','scope':'personal'})]),[spec])
    with pytest.raises(ValueError):
        validate_permission_rules(PermissionSettings(deny=[PermissionRule(tool='memory',args={'kind':'convention'})]),[spec])
    for schema in (convert_to_openai_tool(spec.tool)['function']['parameters'],
                   convert_to_anthropic_tool(spec.tool)['input_schema']):
        assert set(schema['properties'])=={'request'}
        assert 'runtime' not in schema['properties']


def test_v2_runtime_preparation_binds_contract_and_applies_deny():
    import asyncio
    from naiyue_test_agent.tools.memory import create_memory_tool
    from naiyue_test_agent.tools.permission_rules import PermissionRule,PermissionSettings
    from naiyue_test_agent.agent_runtime.tool_execution.permissions import prepare_permission_call
    from runtime_context_helpers import typed_context,RunIdentity
    async def scenario():
        settings=PermissionSettings(deny=[PermissionRule(tool='memory',args={'action':'list_topics'})])
        return await prepare_permission_call(dict(id='call',name='memory',args={'request':{'action':'list_topics'}}),
            create_memory_tool(),typed_context(identity=RunIdentity(session_id='s')),settings,[])
    result=asyncio.run(scenario())
    assert result['subject']['argument_contract']=='memory_actions_v2'
    assert result['decision']=='deny'


@pytest.mark.parametrize('old_contract',[False,True])
def test_registered_v2_execution_uses_bound_permit_before_api(monkeypatch,tmp_path,old_contract):
    import asyncio
    from copy import deepcopy
    from dataclasses import replace
    from langchain_core.messages import AIMessage
    from test_async_application import StreamingToolModel,call
    from test_memory_tool import opened,feedback
    from naiyue_test_agent.application.memory.api import MemoryAPI
    from naiyue_test_agent.tools.memory import create_memory_tool
    from naiyue_test_agent.tools.permissions import PermissionMechanismError
    seen=[];rejected=[]
    async def observe(self,*,scope,request,**kwargs):
        seen.append(request)
        return {'status':'ok','items':[]}
    monkeypatch.setattr(MemoryAPI,'query',observe)
    spec=create_memory_tool();original=spec.tool.coroutine
    async def execute(runtime,**arguments):
        if old_contract:
            permit=deepcopy(runtime.context.authorization.permit)
            permit['subject']['argument_contract']='memory_actions_v1'
            context=replace(runtime.context,authorization=replace(runtime.context.authorization,permit=permit))
            state=deepcopy(runtime.state)
            for prepared in state['tool_execution']['calls']:
                if prepared['id']==runtime.tool_call_id:prepared['subject']=deepcopy(permit['subject'])
            with pytest.raises(PermissionMechanismError):
                await original(runtime=replace(runtime,context=context,state=state),**arguments)
            rejected.append(True)
            return 'rejected'
        return await original(runtime=runtime,**arguments)
    spec=replace(spec,tool=spec.tool.model_copy(update={'coroutine':execute}))
    async def scenario():
        model=StreamingToolModel(replies=[call('memory',{'request':{'action':'list_topics'}}),AIMessage(content='done')])
        async with opened(monkeypatch,tmp_path,model,tools=[spec]) as app:
            sid=await app.host.sessions.create_workspace('A',tmp_path)
            result=await app.host.runs.start(sid,[('user','查看主题')])
            assert feedback(result)[0].status=='success'
    asyncio.run(scenario())
    assert rejected==([True] if old_contract else [])
    assert seen==([] if old_contract else [dict(action='list_topics',scope='both')])


def test_default_memory_tool_explains_partial_confirmation_and_project_default():
    from naiyue_test_agent.tools.memory import create_memory_tool

    description = create_memory_tool().tool.description
    assert 'A project instruction does not become personal just because it says from now on' in description
    assert 'Confirmation of only part of an existing rule uses supplement' in description
