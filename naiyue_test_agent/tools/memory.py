"""Read current workspace/personal memory using the trusted runtime capability."""

from naiyue_test_agent.contracts.run_context import RuntimeContext
import json
from typing import Literal

from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool, ToolException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .permissions import ToolPermission, PermissionMechanismError, require_tool_permit, permission_binding
from .registry import ToolSpec
from .schemas import build_tool_schemas
from .memory_writes import MemoryProposal, MemorySource


class MemoryInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')

    action: Literal['list', 'search', 'read', 'record', 'update', 'disable', 'forget', 'refresh_overview']
    scope: Literal['both', 'workspace', 'personal'] = 'both'
    status: Literal['active', 'candidate', 'inactive', 'expired', 'all'] = 'active'
    kind: Literal['convention', 'context'] | None = None
    query: str | None = Field(default=None, min_length=1, max_length=2000)
    expansions: list[str] = Field(default_factory=list, max_length=5)
    memory_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')
    version: int | None = Field(default=None, gt=0, le=9223372036854775807)
    machine: str | None = Field(default=None, min_length=1, max_length=128)
    module: str | None = Field(default=None, min_length=1, max_length=128)
    target: str | None = Field(default=None, min_length=1, max_length=128)
    subject: str | None = Field(default=None, min_length=1, max_length=128)
    offset: int = Field(default=0, ge=0, le=9223372036854775807)
    limit: int | None = Field(default=None, gt=0, le=50)
    cursor: str | None = Field(default=None, max_length=4096)

    target_session_id: str | None = Field(default=None, min_length=1, max_length=128)
    start_sequence: int | None = Field(default=None, ge=0, le=9223372036854775807)
    end_sequence: int | None = Field(default=None, ge=0, le=9223372036854775807)
    proposal: MemoryProposal | None = None
    sources: list[MemorySource] = Field(default_factory=list, max_length=8)
    expected_version: int | None = Field(default=None, gt=0, le=9223372036854775806)
    reason: str | None = Field(default=None, min_length=1, max_length=300)
    include_history: bool = False

    @model_validator(mode='after')
    def operation(self):
        if self.action == 'refresh_overview':
            if (self.scope!='both' or self.status!='active' or self.include_history or self.expansions or self.offset!=0
                    or self.sources or any(v is not None for v in (self.kind,self.query,self.memory_id,self.version,
                        self.machine,self.module,self.target,self.subject,self.limit,self.cursor,self.proposal,self.expected_version,self.reason))
                    or self.start_sequence is not None and self.end_sequence is not None and self.start_sequence>self.end_sequence):
                raise ValueError('Arguments do not match overview request')
            return self
        if any(v is not None for v in (self.target_session_id,self.start_sequence,self.end_sequence)):
            raise ValueError('Overview arguments do not match operation')
        if self.action in ('record' ,'update','disable','forget'):
            invalid = (self.scope == 'both' or not self.sources or self.version is not None or self.include_history
                or self.status != 'active' or self.offset != 0 or self.expansions
                or any(v is not None for v in (self.kind,self.query,self.machine,self.module,self.target,self.subject,self.limit,self.cursor)))
            invalid |= (self.proposal is None) if self.action in ('record','update') else self.proposal is not None
            if self.action == 'record':
                invalid |= self.memory_id is not None or self.expected_version is not None or self.reason is not None
            else:
                invalid |= self.memory_id is None or self.expected_version is None
                invalid |= self.reason is None if self.action in ('disable','forget') else self.reason is not None
            if invalid:raise ValueError('Arguments do not match memory write operation')
            return self
        if self.proposal is not None or self.sources or self.expected_version is not None or self.reason is not None:
            raise ValueError('Write arguments do not match read operation')
        if self.include_history and self.action != 'read':raise ValueError('History requires read')
        if any(not q.strip() or len(q) > 2000 for q in self.expansions):
            raise ValueError('Invalid query expansion')
        if self.action == 'search':
            invalid = not self.query or not self.query.strip() or self.cursor is not None
        else:
            invalid = self.query is not None or bool(self.expansions)
        if self.action == 'read':
            invalid |= self.memory_id is None or self.scope == 'both' or self.cursor is not None or self.limit is not None
        else:
            invalid |= self.memory_id is not None or self.version is not None or self.offset != 0
        if invalid:
            raise ValueError('Arguments do not match memory operation')
        return self


def create_memory_tool():
    from .memory_wiki_actions import DESCRIPTION, WikiMemoryToolInput, compile_wiki_request

    contract = 'memory_actions_v2'
    public, execution = build_tool_schemas('MemoryToolInput', WikiMemoryToolInput)

    async def execute(runtime: ToolRuntime[RuntimeContext], **arguments):
        request = public.model_validate(arguments).model_dump(mode='json')
        permit = require_tool_permit(runtime)
        batch = runtime.state.get('tool_execution', {})
        call = next((c for c in batch.get('calls', []) if c['id'] == runtime.tool_call_id), None)
        if (call is None or permit != permission_binding(runtime.context.scope.identity, batch.get('batch_id'), call)
                or permit.get('tool_name') != 'memory' or permit['subject'].get('args') != request
                or permit['subject'].get('argument_contract') != contract):
            raise PermissionMechanismError('Memory query does not match its execution permit')
        request = compile_wiki_request(request)
        port = runtime.context.scope.capabilities.memory
        if not callable(port):
            raise ToolException('memory_unavailable: persistent memory is unavailable.')
        result = await port(scope=runtime.context.scope,
            request=request, argument_contract=contract,
            **({'tool_call_id':runtime.tool_call_id} if request['action'] in ('refresh_overview','organize') else {}))
        return json.dumps(result, ensure_ascii=False)

    tool = StructuredTool.from_function(coroutine=execute, name='memory', args_schema=execution,
        description=DESCRIPTION)
    def invalid_arguments(error):
        reasons = {
            'memory_scope_evidence_required': 'For a personal record, give scope_source_indexes pointing to the real user sources that establish general applicability.',
            'memory_scope_evidence_invalid': 'Use unique zero-based scope_source_indexes within sources.',
            'memory_duplicate_target': 'Use each page ID only once in page_ids.',
            'memory_cursor_offset_conflict': 'Continue with the returned cursor and omit offset.',
            'memory_query_invalid': 'Use a nonblank query and nonblank expansions of at most 2000 characters each.',
            'memory_range_invalid': 'start_sequence must be at or before end_sequence.',
            'memory_page_metadata_invalid': 'Use a nonblank page title within 480 bytes and description within 900 bytes.',
        }
        hints = []
        for issue in error.errors(include_input=False):
            path = issue['loc']
            reason = str(issue.get('ctx', {}).get('error', ''))
            if reason in reasons:
                hint = reasons[reason]
            elif path[:3] == ('request', 'record', 'destination'):
                hint = 'Supply destination with page_id and expected_page_version, or new_page with topic, title and description.'
            elif issue['type'] == 'missing' and path:
                hint = (f'Supply {path[-1]} from the latest read of the target.' if path[-1] == 'expected_version'
                        else f'Supply the required field {path[-1]}.')
            else:
                continue
            if hint not in hints:
                hints.append(hint)
            if len(hints) == 8:
                break
        if hints:
            return 'Invalid memory arguments. ' + ' '.join(hints)
        return 'Invalid memory arguments. Use request.action and its action-specific schema.'

    tool.handle_validation_error = invalid_arguments
    return ToolSpec(tool, public, execution_mode='parallel', side_effects='may_mutate',
                    permissions=ToolPermission(default='allow'))
