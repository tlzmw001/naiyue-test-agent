"""Public action envelopes retain canonical business and permission semantics."""
import pytest
from pydantic import ValidationError
from naiyue_test_agent.tools.memory import create_memory_tool


def test_public_schema_uses_required_nested_action_branches():
    spec=create_memory_tool()
    assert set(spec.input_schema.model_fields)=={'request'}
    schema=spec.input_schema.model_json_schema()
    assert schema['type']=='object' and schema['required']==['request']
    assert len(schema['properties']['request']['oneOf'])==11
    assert 'runtime' not in str(spec.input_schema.model_fields)
    assert spec.validate_input({'request':{'action':'list'}}).request.action=='list'
    with pytest.raises(ValidationError):spec.validate_input({'action':'list'})


@pytest.mark.parametrize('payload',[
    {'action':'list'}, {'action':'search','query':'pytest'},
    {'action':'read','scope':'workspace','memory_id':'a'*32,'status':'all'},
    {'action':'refresh_overview','start_sequence':1,'end_sequence':2},
])
def test_compile_preserves_canonical_defaults(payload):
    from naiyue_test_agent.tools.memory_wiki_actions import compile_wiki_request, WikiMemoryToolInput
    assert compile_wiki_request({'request':payload})==WikiMemoryToolInput(request=payload).request.model_dump(mode='json')


@pytest.mark.parametrize('payload',[
    {'action':'list','proposal':None}, {'action':'list','reason':None},
    {'action':'read','memory_id':'a'*32}, {'action':'search','query':' '},
    {'action':'refresh_overview','scope':'both'},
    {'action':'refresh_overview','start_sequence':2,'end_sequence':1},
    {'action':'list','limit':True}, {'action':'record','scope':'both','proposal':{},'sources':[]},
])
def test_action_rejects_irrelevant_or_invalid_arguments(payload):
    from naiyue_test_agent.tools.memory_wiki_actions import WikiMemoryToolInput as MemoryToolInput
    with pytest.raises(ValidationError):MemoryToolInput(request=payload)


def test_permission_projection_keeps_subject_envelope_and_internal_flat():
    from naiyue_test_agent.tools.permission_rules import PermissionRule,match_rules,validate_permission_rules,PermissionSettings
    spec=create_memory_tool();rule=PermissionRule(tool='memory',args={'action':'list','scope':'personal'})
    validate_permission_rules(PermissionSettings(deny=[rule]),[spec])
    subject=dict(tool_name='memory',args={'request':{'action':'list','scope':'personal'}},argument_contract='memory_actions_v2')
    assert match_rules([rule],subject,deny=True)==rule
    assert subject['args']=={'request':{'action':'list','scope':'personal'}}
    assert match_rules([rule],dict(tool_name='memory',args={'action':'list','scope':'personal'}),deny=True)==rule


def test_malformed_permission_contract_fails_closed():
    from naiyue_test_agent.tools.permission_rules import PermissionRule,match_rules
    from naiyue_test_agent.tools.permissions import PermissionMechanismError
    for subject in [dict(tool_name='other',args={'request':{'action':'list'}},argument_contract='memory_actions_v2'),
        dict(tool_name='memory',args={'action':'list'},argument_contract='memory_actions_v2')]:
        with pytest.raises(PermissionMechanismError):match_rules([PermissionRule(tool='*')],subject,deny=True)


def test_same_name_custom_tool_keeps_its_public_schema_and_rule_view():
    from pydantic import BaseModel
    from naiyue_test_agent.tools.schemas import build_tool_schemas
    from naiyue_test_agent.tools.registry import ToolSpec
    from langchain_core.tools import StructuredTool
    from naiyue_test_agent.tools.permission_rules import PermissionRule,match_rules,validate_permission_rules,PermissionSettings
    from naiyue_test_agent.tools.memory_actions import is_memory_action_spec
    class Custom(BaseModel):
        request: str
    public,execution=build_tool_schemas('CustomMemory',Custom)
    async def execute(**kwargs):return ''
    custom=ToolSpec(StructuredTool.from_function(coroutine=execute,name='memory',description='custom',args_schema=execution),public)
    assert not is_memory_action_spec(custom)
    rule=PermissionRule(tool='memory',args={'request':'custom'})
    validate_permission_rules(PermissionSettings(deny=[rule]),[custom])
    assert match_rules([rule],dict(tool_name='memory',args={'request':'custom'}),deny=True)==rule


@pytest.mark.parametrize('action',['record','update','disable','forget'])
def test_write_branch_compiles_and_exposes_only_action_fields(action):
    from naiyue_test_agent.tools.memory_wiki_actions import WikiMemoryToolInput,compile_wiki_request
    from test_memory_write_runtime import record_args
    request=record_args()
    if action!='record':
        request.pop('destination')
        proposal=request.pop('proposal')
        request.update(action=action,memory_id='a'*32,expected_version=1)
        if action=='update':request['change']={'mode':'replace','proposal':proposal}
        else:request['reason']='用户要求'
    assert compile_wiki_request({'request':request})==WikiMemoryToolInput(request=request).request.model_dump(mode='json')
    for extra in ('kind','query','machine','status'):
        with pytest.raises(ValidationError):WikiMemoryToolInput(request=request|{extra:None})
    assert WikiMemoryToolInput(request=request).request.action==action


def test_old_flat_model_call_is_rejected_without_business_io(monkeypatch,tmp_path):
    import asyncio
    from langchain_core.messages import AIMessage
    from test_async_application import StreamingToolModel,call
    from test_memory_tool import opened,feedback
    from naiyue_test_agent.application.memory.api import MemoryAPI
    seen=[]
    async def forbidden(*args,**kwargs):seen.append(True);raise AssertionError('no IO')
    monkeypatch.setattr(MemoryAPI,'query',forbidden)
    async def scenario():
        model=StreamingToolModel(replies=[call('memory',{'action':'list'}),AIMessage(content='rejected')])
        async with opened(monkeypatch,tmp_path,model) as app:
            sid=await app.host.sessions.create_workspace('A',tmp_path)
            result=await app.host.runs.start(sid,[('user','读取')])
            assert feedback(result)[0].status=='error'
            assert seen==[]
    asyncio.run(scenario())


@pytest.mark.parametrize('fault',['missing_marker','changed_marker','changed_body'])
def test_execution_rejects_tampered_permit_before_api(monkeypatch,tmp_path,fault):
    import asyncio
    from copy import deepcopy
    from dataclasses import replace
    from langchain_core.messages import AIMessage
    from test_async_application import StreamingToolModel,call
    from test_memory_tool import opened,feedback
    from naiyue_test_agent.application.memory.api import MemoryAPI
    from naiyue_test_agent.tools.permissions import PermissionMechanismError
    seen=[];rejected=[]
    async def forbidden(*args,**kwargs):seen.append(True);raise AssertionError('no IO')
    monkeypatch.setattr(MemoryAPI,'query',forbidden)
    spec=create_memory_tool();original=spec.tool.coroutine
    async def corrupt(runtime,**arguments):
        permit=deepcopy(runtime.context.authorization.permit)
        if fault=='missing_marker':permit['subject'].pop('argument_contract')
        elif fault=='changed_marker':permit['subject']['argument_contract']='other'
        else:permit['subject']['args']['request']['scope']='personal'
        context=replace(runtime.context,authorization=replace(runtime.context.authorization,permit=permit))
        state=deepcopy(runtime.state)
        if fault in ('missing_marker','changed_marker'):
            for prepared in state['tool_execution']['calls']:
                if prepared['id']==runtime.tool_call_id:prepared['subject']=deepcopy(permit['subject'])
        runtime=replace(runtime,context=context,state=state)
        with pytest.raises(PermissionMechanismError):await original(runtime=runtime,**arguments)
        rejected.append(True)
        return 'rejected'
    tool=spec.tool.model_copy(update={'coroutine':corrupt})
    async def scenario():
        model=StreamingToolModel(replies=[call('memory',{'request':{'action':'list'}}),AIMessage(content='done')])
        async with opened(monkeypatch,tmp_path,model,tools=[replace(spec,tool=tool)]) as app:
            sid=await app.host.sessions.create_workspace('A',tmp_path)
            await app.host.runs.start(sid,[('user','读取')])
    asyncio.run(scenario())
    assert rejected==[True] and seen==[]


def test_registry_and_provider_exports_share_wiki_action_contract():
    from naiyue_test_agent.tools.registry import ToolRegistry
    from langchain_core.utils.function_calling import convert_to_openai_tool
    from langchain_anthropic.chat_models import convert_to_anthropic_tool
    spec=create_memory_tool();registry=ToolRegistry();registry.register(spec)
    assert registry.get('memory') is spec
    for schema in (convert_to_openai_tool(spec.tool)['function']['parameters'],
                   convert_to_anthropic_tool(spec.tool)['input_schema']):
        assert schema['type']=='object' and set(schema['properties'])=={'request'}
        branches=schema['properties']['request']['anyOf']
        assert len(branches)==11
        actions=set()
        for branch in branches:
            action=branch['properties']['action']
            actions.update(action['enum'] if 'enum' in action else [action['const']])
        assert actions=={'list_topics','list_pages','read_page','list','search','read',
            'record','update','disable','forget','refresh_overview','organize'}
        assert 'runtime' not in schema['properties']


def test_write_subject_does_not_become_a_read_filter_permission():
    from naiyue_test_agent.tools.permission_rules import PermissionRule,match_rules
    from test_memory_write_runtime import record_args
    subject=dict(tool_name='memory',args={'request':record_args()},argument_contract='memory_actions_v2')
    assert match_rules([PermissionRule(tool='memory',args={'subject':'pytest环境'})],subject,deny=True) is None
    assert match_rules([PermissionRule(tool='memory',args={'subject':None})],subject,deny=True) is None
    assert match_rules([PermissionRule(tool='memory',args={'action':'record','scope':'workspace'})],subject,deny=True) is not None


@pytest.mark.parametrize('mode',['Default','NoAsk','BypassApprovals'])
def test_existing_deny_rules_block_nested_writes_in_all_modes(monkeypatch,tmp_path,mode):
    import asyncio
    from langchain_core.messages import AIMessage
    from test_async_application import StreamingToolModel,call
    from test_memory_tool import opened,feedback
    from test_memory_write_runtime import record_args
    from naiyue_test_agent.settings import AppSettings
    async def scenario():
        settings=AppSettings.model_validate(dict(storage=dict(data_dir=str(tmp_path/'data')),
            permissions=dict(mode=mode,deny=[dict(tool='memory',args=dict(action='record',scope='workspace'))])))
        model=StreamingToolModel(replies=[call('memory',{'request':record_args()}),AIMessage(content='denied')])
        async with opened(monkeypatch,tmp_path,model,settings=settings) as app:
            sid=await app.host.sessions.create_workspace('A',tmp_path)
            result=await app.host.runs.start(sid,[('user','以后测试使用.venv')])
            assert feedback(result)[0].status=='error'
            assert app.host.memory._access.storage.database.connection.execute('SELECT count(*) FROM naiyue_memories').fetchone()[0]==0
    asyncio.run(scenario())
