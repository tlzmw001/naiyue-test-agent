"""Local manual-task input and binding validation; no runtime dependencies."""
from typing import Literal,Annotated
from pydantic import Field,ValidationError,model_validator,StringConstraints
from .cloud_tasks import CloudRecord,CloudError,CloudTask,UUIDText,Label
from .cloud_inputs import Selection,distinct_paths
from .cloud_models import ThinkingLevel


SessionId=Annotated[str,StringConstraints(pattern=r'^[a-zA-Z0-9_-]{1,128}$')]


class ManualCreate(CloudRecord):
    command_id:UUIDText
    local_session_id:SessionId
    profile_id:str=Field(pattern=r'^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$')
    source_handle:UUIDText|None=Field(default=None,exclude_if=lambda v:v is None)
    template_id:Label


class LocalSourceRequest(CloudRecord):
    local_session_id:SessionId
    mode:Literal['selection','git']='selection'
    selection:dict|None=None
    paths:list[str]|None=Field(default=None,max_length=4096)
    include:list[str]=Field(default_factory=list,max_length=4096)
    exclude:list[str]=Field(default_factory=list,max_length=4096)

    @model_validator(mode='after')
    def selected(self):
        if self.paths is not None:
            if self.selection is not None:raise ValueError('selection_scope_conflict')
            if self.mode=='selection' and (not self.paths or self.include):raise ValueError('selection_required')
            distinct_paths(self.include);distinct_paths(self.exclude)
            return self
        if self.mode=='selection':
            if self.selection is None or self.include or self.exclude:raise ValueError('selection_required')
            value=Selection.model_validate(self.selection)
            if value.policy is not None:raise ValueError('server_source_plan_required')
        elif self.selection is not None:raise ValueError('git_plan_selection_conflict')
        distinct_paths(self.include);distinct_paths(self.exclude)
        return self


def checked_task(raw,binding):
    try:task=CloudTask.model_validate(raw)
    except ValidationError:raise CloudError('cloud_task_invalid',502) from None
    if (task.network_id!=binding.get('network_id') or task.project_id!=binding['cloud_project_id'] or task.input_id!=binding['input_id']
            or task.template_id!=binding['template_id'] or binding['task_id'] is not None and task.task_id!=binding['task_id']):
        raise CloudError('cloud_task_identity_mismatch',502)
    return task


class ManualMessage(CloudRecord):
    attachment_ids:list[UUIDText]=Field(default_factory=list,max_length=128,exclude_if=lambda v:not v)
    command_id:UUIDText
    local_session_id:SessionId
    expected_binding_revision:int=Field(ge=1)
    text:str=Field(min_length=1,max_length=262144)
    model_id:Label
    thinking_level:ThinkingLevel='model_default'
    draft_revision:int|None=Field(default=None,ge=0)

    @model_validator(mode='after')
    def nonblank(self):
        if not self.text.strip():raise ValueError('blank_message')
        return self


class ManualControl(CloudRecord):
    command_id:UUIDText
    local_session_id:SessionId
    expected_binding_revision:int=Field(ge=1)
    kind:Literal['stop','recover']
    run_id:Annotated[str,StringConstraints(pattern=r'^[a-f0-9]{32}$')]|None=None
    checkpoint_id:Label|None=None

    @model_validator(mode='after')
    def target(self):
        if self.kind=='recover' and self.run_id is None:raise ValueError('recovery_run_required')
        if self.kind=='stop' and self.checkpoint_id is not None:raise ValueError('invalid_control_target')
        return self


class ManualEnvironment(CloudRecord):
    command_id:UUIDText
    local_session_id:SessionId
    expected_binding_revision:int=Field(ge=1)
    action:Literal['start','release']
    acknowledge_unknown:bool=False
    expected_generation:int|None=Field(default=None,ge=1)

    @model_validator(mode='after')
    def acknowledgment(self):
        if self.acknowledge_unknown and (self.action!='release' or self.expected_generation is None):
            raise ValueError('explicit_generation_required')
        return self


class TaskDraft(CloudRecord):
    attachment_ids:list[UUIDText]=Field(default_factory=list,max_length=128)
    expected_draft_revision:int=Field(ge=0)
    text:str=Field(max_length=262144)
    model_id:Label|None=None
    thinking_level:ThinkingLevel='model_default'


class ManualDelete(CloudRecord):
    command_id:UUIDText
    local_session_id:SessionId
    expected_binding_revision:int=Field(ge=1)
    expected_revision:int=Field(ge=1)
    expected_generation:int=Field(ge=1)
    preview_digest:Annotated[str,StringConstraints(pattern=r'^[a-f0-9]{64}$')]
    acknowledge_unknown:bool=False


def command_summary(op):
    value={k:op.get(k) for k in ('command_id','binding_id','local_session_id','kind','phase','request_digest',
        'run_id','error_code','created_at','updated_at')}
    value['model_id']=op['request'].get('model_id')
    value['thinking_level']=op['request'].get('thinking_level','model_default')
    value['text_chars']=len(op['request'].get('text',''))
    value['steps']={}
    for name,step in op['steps'].items():
        item={k:step[k] for k in ('command_id','operation','sent','rejected') if k in step}
        if 'receipt' in step:item['receipt']={k:step['receipt'].get(k) for k in ('status','http_status','error_code','updated_at')}
        value['steps'][name]=item
    return value


class ManualResultAction(CloudRecord):
    command_id:UUIDText
    local_session_id:SessionId
    expected_binding_revision:int=Field(ge=1)
    result_id:UUIDText
    expected_revision:int=Field(ge=1)
    action:Literal['retry','abandon']
