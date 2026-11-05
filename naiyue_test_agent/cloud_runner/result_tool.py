"""Read-only current-root output location; files are created by ordinary task tools."""
import json
from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool,ToolException
from pydantic import BaseModel,ConfigDict,TypeAdapter
from naiyue_test_agent.contracts.run_context import RuntimeContext
from naiyue_test_agent.contracts.cloud_tasks import TaskId
from naiyue_test_agent.tools.schemas import build_tool_schemas
from naiyue_test_agent.tools.registry import ToolSpec
from naiyue_test_agent.tools.permissions import ToolPermission,require_tool_permit


class _Empty(BaseModel):
    model_config=ConfigDict(strict=True,extra='forbid')


def create_result_directory_tool(limits):
    public,execution=build_tool_schemas('TaskOutputDirectoryInput',_Empty)
    async def execute(runtime:ToolRuntime[RuntimeContext],**arguments):
        public.model_validate(arguments);permit=require_tool_permit(runtime)
        if permit.get('tool_name')!='task_output_directory':raise ToolException('task_output_directory_unavailable')
        identity=runtime.context.scope.identity
        run=identity.root_run_id or identity.run_id
        TypeAdapter(TaskId).validate_python(run)
        return json.dumps({'relative_directory':limits.output_directory+'/'+run,
            'purpose':'Files and archives explicitly selected for download from this root run; create it with task tools if needed.'})
    item=StructuredTool.from_function(coroutine=execute,name='task_output_directory',args_schema=execution,
        description='Get this root run\'s output directory for files and archives the user wants to download. '
        'Write output files there with ordinary task tools; they will be collected automatically after the run is quiet. '
        'Copy any code or other files intended for delivery here, or create an archive here. This tool only returns a path and creates no files.')
    return ToolSpec(item,public,side_effects='read_only',permissions=ToolPermission(default='allow'))
