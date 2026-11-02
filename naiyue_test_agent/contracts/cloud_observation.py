"""Explicit public observation wire data; no SDK objects or authorization claims."""
from typing import Annotated,Literal
from pydantic import Field,model_validator
from .cloud_tasks import CloudRecord,TaskId,UUIDText,Label,ContentId
from .cloud_history import Identity
from .model_events import ModelInvocationStartedData,ModelInvocationFinishedData

Counter=Annotated[int,Field(ge=0,le=9223372036854775807)]


class ObservationLimits(CloudRecord):
    queue_records:int=Field(default=256,ge=1,le=4096)
    queue_bytes:int=Field(default=524288,ge=4096,le=16777216)
    record_bytes:int=Field(default=32768,ge=1024,le=262144)
    batch_records:int=Field(default=64,ge=1,le=128)
    flush_seconds:float=Field(default=0.05,gt=0,le=5)
    text_chars:int=Field(default=2048,ge=1,le=16384)
    live_messages:int=Field(default=64,ge=1,le=512)
    live_threads:int=Field(default=32,ge=1,le=128)
    live_tools:int=Field(default=64,ge=1,le=512)
    state_bytes:int=Field(default=524288,ge=4096,le=8388608)
    cached_tasks:int=Field(default=64,ge=1,le=1024)
    replay_events:int=Field(default=128,ge=1,le=4096)
    replay_bytes:int=Field(default=1048576,ge=4096,le=16777216)
    subscribers:int=Field(default=64,ge=1,le=1024)
    task_subscribers:int=Field(default=8,ge=1,le=128)
    heartbeat_seconds:float=Field(default=10.0,gt=0,le=60)
    send_timeout_seconds:float=Field(default=15.0,gt=0,le=300)
    stream_seconds:float=Field(default=1800.0,gt=0,le=86400)
    snapshot_attempts:int=Field(default=3,ge=1,le=10)
    snapshot_messages:int=Field(default=10,ge=1,le=100)
    cursor_seconds:int=Field(default=3600,ge=1,le=86400)


class ObservationSource(CloudRecord):
    agent_path:Identity
    agent_id:Identity
    agent_run_id:Identity
    parent_run_id:TaskId
    parent_tool_call_id:Identity

    @model_validator(mode='after')
    def path(self):
        if not self.agent_path.startswith('root/') or self.agent_path.rsplit('/',1)[-1]!=self.agent_id:
            raise ValueError('invalid_observation_source')
        return self


class ObservedCall(CloudRecord):
    id:Identity
    name:Identity
    args:dict=Field(default_factory=dict)

    @model_validator(mode='after')
    def no_arguments(self):
        if self.args:raise ValueError('live_arguments_not_public')
        return self


class ObservedMessage(CloudRecord):
    id:Identity
    type:Literal['human','ai','tool']
    content:str=Field(default='',max_length=16384)
    content_id:ContentId|None=None
    reasoning:str|None=Field(default=None,max_length=16384)
    reply_id:Identity|None=None
    is_context_summary:bool=False
    tool_calls:list[ObservedCall]=Field(default_factory=list,max_length=128)
    tool_call_id:Identity|None=None
    name:Identity|None=None
    status:Literal['success','error']|None=None
    content_truncated:bool=False
    reasoning_truncated:bool=False
    tool_calls_truncated:bool=False
    arguments_truncated:bool=False

    @model_validator(mode='after')
    def tool(self):
        if self.type=='tool' and (not self.tool_call_id or not self.status):raise ValueError('missing_tool_identity')
        if self.type!='tool' and any((self.tool_call_id,self.name,self.status)):raise ValueError('unexpected_tool_identity')
        return self


class DeltaData(CloudRecord):
    message_id:Identity
    text:str=Field(min_length=1,max_length=16384)
    index:int=Field(ge=1,le=9223372036854775807)
    end_index:int=Field(ge=1,le=9223372036854775807)
    reply_id:Identity|None=None

    @model_validator(mode='after')
    def range(self):
        if self.end_index<self.index:raise ValueError('invalid_delta_range')
        return self


class UsageStart(CloudRecord):
    invocation_id:Identity
    model_id:Label|None
    purpose:Literal['agent','summary','auto_fast','auto_detailed','memory']|None

    @model_validator(mode='after')
    def semantic(self):
        ModelInvocationStartedData(**self.model_dump());return self


class UsageFinish(CloudRecord):
    invocation_id:Identity
    status:Literal['returned','failed','cancelled']
    usage_status:Literal['final','partial','unavailable']='unavailable'
    input_tokens:Counter|None=None
    output_tokens:Counter|None=None
    cache_read_tokens:Counter|None=None
    cache_write_tokens:Counter|None=None
    duration_ms:Counter|None=None
    error_type:str|None=Field(default=None,max_length=100)

    @model_validator(mode='after')
    def semantic(self):
        ModelInvocationFinishedData(**self.model_dump());return self


class ToolData(CloudRecord):
    tool_call_id:Identity
    tool_name:Identity|None
    boundary:Literal['dispatch','approval_resolution']='dispatch'
    outcome:Literal['success','error_result','not_executed','exception','outcome_unknown']|None=None
    reason:Label|None=None
    error_type:Label|None=None
    duration_ms:Counter|None=None


class ProgressData(CloudRecord):
    tool_call_id:Identity
    tool_name:Identity
    activity:Literal['queued','preparing','running','stopping','settling']
    phase:str=Field(min_length=1,max_length=64)
    text:str=Field(default='',max_length=16384)


class RunData(CloudRecord):
    status:Literal['running','completed','stopped','interrupted','failed']
    error_type:Label|None=None


class BaseObservation(CloudRecord):
    run_id:TaskId
    thread_id:Identity
    source:ObservationSource|None=None

    @model_validator(mode='after')
    def scope(self):
        if self.source and (self.source.parent_run_id!=self.run_id or self.thread_id!='subagent:'+self.source.agent_id):
            raise ValueError('observation_scope_mismatch')
        return self


class DeltaObservation(BaseObservation):
    event_type:Literal['message_delta','message_reasoning_delta']
    data:DeltaData


class MessageObservation(BaseObservation):
    event_type:Literal['message_completed','message_revised']
    data:ObservedMessage


class StartObservation(BaseObservation):
    event_type:Literal['model_call_started']
    data:UsageStart


class FinishObservation(BaseObservation):
    event_type:Literal['model_call_finished']
    data:UsageFinish


class ToolObservation(BaseObservation):
    event_type:Literal['tool_call_started','tool_call_finished']
    data:ToolData


class ProgressObservation(BaseObservation):
    event_type:Literal['tool_progress']
    data:ProgressData


class RunObservation(BaseObservation):
    event_type:Literal['run_started','run_finished']
    data:RunData


Observation=Annotated[DeltaObservation|MessageObservation|StartObservation|FinishObservation|ToolObservation|ProgressObservation|RunObservation,
    Field(discriminator='event_type')]


class ObservationBatch(CloudRecord):
    task_id:TaskId
    epoch:UUIDText
    first_seq:int=Field(ge=1,le=9223372036854775807)
    dropped:Counter=0
    records:list[Observation]=Field(max_length=128)

    @model_validator(mode='after')
    def bound(self):
        if not self.records and not self.dropped or self.first_seq+len(self.records)>9223372036854775807:
            raise ValueError('invalid_observation_batch')
        return self
