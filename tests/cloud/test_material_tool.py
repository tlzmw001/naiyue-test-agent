"""Material reads use normal tool permits and a narrow runtime capability."""
import asyncio,json
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4
import pytest
from runtime_context_helpers import typed_context,RunIdentity,RunCapabilities,ToolAuthorization
from naiyue_test_agent.tools.permissions import permission_binding,execution_environment,PermissionMechanismError
from naiyue_test_agent.tools.cloud_materials import create_cloud_result_read_tool,CloudMaterialReadInput


def runtime(port,request):
    context=typed_context(identity=RunIdentity('S',project_id=str(uuid4())),capabilities=RunCapabilities(cloud_materials=port))
    call=dict(id='call',name='cloud_result_read',args=request,subject=dict(args=request,execution_environment=execution_environment()))
    permit=permission_binding(context.scope.identity,'batch',call)
    return SimpleNamespace(context=replace(context,authorization=ToolAuthorization(permit=permit)),tool_call_id='call',state={'tool_execution':{'batch_id':'batch','calls':[call]}})


def test_tool_reads_original_material_only_and_returns_reported_data():
    seen=[];request=CloudMaterialReadInput(material_id=str(uuid4()),offset=2,limit=3).model_dump()
    async def port(**kwargs):seen.append(kwargs);return dict(reported=True,text='cloud report',offset=2,next_offset=3)
    r=runtime(port,request)
    result=asyncio.run(create_cloud_result_read_tool().tool.coroutine(runtime=r,**request))
    assert json.loads(result)['reported'] is True and seen==[dict(source_session_id='S',project_id=r.context.scope.identity.project_id,request=request)]
    async def forged():
        with pytest.raises(PermissionMechanismError):await create_cloud_result_read_tool().tool.coroutine(runtime=r,**(request|{'material_id':str(uuid4())}))
    asyncio.run(forged());assert len(seen)==1


def test_material_tool_is_hidden_without_runtime_capability():
    from naiyue_test_agent.tools.registry import ToolRegistry
    registry=ToolRegistry();registry.register(create_cloud_result_read_tool())
    context=typed_context(identity=RunIdentity('S'))
    assert registry.select_available(['cloud_result_read'],context=context)==()


def test_omitted_line_limit_is_resolved_by_configured_material_service():
    assert CloudMaterialReadInput(material_id=str(uuid4())).limit is None


def test_generic_builtin_collection_has_no_service_only_material_tool():
    from naiyue_test_agent.tools.builtin import create_builtin_tools
    from naiyue_test_agent.tools.settings import BuiltinToolSettings
    assert 'cloud_result_read' not in {s.tool.name for s in create_builtin_tools(BuiltinToolSettings())}
