"""Strict task bootstrap, owner run admission and bounded control frames."""
from typing import Annotated, Literal
from urllib.parse import urlsplit
from dataclasses import asdict
import json

from pydantic import Field, SecretStr, TypeAdapter, model_validator, field_validator

from .cloud_tasks import CloudRecord, TaskId, UUIDText, ContentId, Label
from naiyue_test_agent.tools.permission_rules import PermissionRule, PermissionSettings
from naiyue_test_agent.tools.settings import EnvironmentSettings
from naiyue_test_agent.tools.subagent import SubAgentSpec, parse_subagent_specs
from .cloud_artifacts import ArtifactLimits,CaptureArtifact
from .cloud_models import RunnerModelProfile,ThinkingLevel
from .cloud_results import ResultPlan,ResultLimits
from .cloud_inputs import InputLimits
from .cloud_history import HistoryLimits
from .cloud_observation import ObservationLimits,ObservationBatch


def task_permissions():
    return PermissionSettings(mode='NoAsk', allow=(PermissionRule(tool='*'),))


def validate_runner_configuration(subagents,environment,models=None):
    # Keep room for identity, credential, limits and JSON escaping in the
    # existing 64 KiB bootstrap reader. This is not the WS frame budget.
    value=dict(subagents=[asdict(role) for role in subagents],tool_environment=environment.model_dump())
    if models is not None:value['models']=models.model_dump()
    if len(json.dumps(value,ensure_ascii=False,allow_nan=False).encode())>49152:
        raise ValueError('runner_configuration_too_large')


class RunnerToolEnvironment(EnvironmentSettings):
    @model_validator(mode='after')
    def interpreter_identity(self):
        reserved={'PATH','PYTHONPATH','PYTHONHOME','VIRTUAL_ENV','BASH_ENV'}
        if reserved.intersection(self.values) or reserved.intersection(self.unset_env):
            raise ValueError('cloud_tool_interpreter_override')
        return self


class RunnerBootstrap(CloudRecord):
    task_id: TaskId
    project_id: UUIDText
    input_id: ContentId
    instance_id: UUIDText
    generation: int = Field(ge=1)
    boot_id: UUIDText
    state_epoch: UUIDText
    release_id: Label
    endpoint: str
    token: SecretStr
    expires_at: float = Field(gt=0)
    max_frame_bytes: int = Field(default=1048576, ge=4096, le=16777216)
    batch_events: int = Field(default=128, ge=1, le=1024)
    json_depth_limit: int = Field(default=32, ge=1, le=128)
    retry_seconds: float = Field(default=1.0, gt=0, le=60)
    heartbeat_seconds: float = Field(default=15.0, gt=0, le=60)
    input_wait_seconds: float = Field(default=300.0, gt=0, le=3600)
    permissions: PermissionSettings = Field(default_factory=task_permissions)
    tool_environment: RunnerToolEnvironment = Field(default_factory=RunnerToolEnvironment)
    subagents: tuple[SubAgentSpec,...] = ()
    models:RunnerModelProfile|None=None
    _roles=field_validator('subagents',mode='before')(parse_subagent_specs)
    artifact_limits:ArtifactLimits=Field(default_factory=ArtifactLimits)
    result_limits:ResultLimits=Field(default_factory=ResultLimits)
    input_limits:InputLimits=Field(default_factory=InputLimits)
    input_directory:str='/input'
    history_limits:HistoryLimits=Field(default_factory=HistoryLimits)
    observation_limits:ObservationLimits=Field(default_factory=ObservationLimits)

    @model_validator(mode='after')
    def transport(self):
        if self.permissions.mode!='NoAsk':raise ValueError('cloud_requires_noask')
        validate_runner_configuration(self.subagents,self.tool_environment,self.models)
        p=urlsplit(self.endpoint)
        if (p.scheme not in ('http','https') or not p.hostname or not p.port or p.username
                or p.path not in ('','/') or p.query or p.fragment or not self.token.get_secret_value()):
            raise ValueError('invalid_runner_endpoint')
        value=self.model_dump(mode='json')|{'token':self.token.get_secret_value()}
        if len(json.dumps(value,ensure_ascii=False,allow_nan=False).encode())>65536:
            raise ValueError('runner_bootstrap_too_large')
        return self


class RunCommand(CloudRecord):
    command_id: UUIDText
    run_id: TaskId
    message: str = Field(min_length=1, max_length=262144)
    model_id: Label
    thinking_level: ThinkingLevel='model_default'
    result_plan:ResultPlan|None=None


class StartRun(CloudRecord):
    command_id: UUIDText
    expected_revision: int = Field(ge=1)
    expected_generation: int = Field(ge=1)
    message: str = Field(min_length=1, max_length=262144)
    model_id: Label
    thinking_level: ThinkingLevel='model_default'


class RunnerRecovery(CloudRecord):
    kind: Label
    reason: Label
    checkpoint_id: Label|None=None
    target_command_id: UUIDText|None=None
    run_id: TaskId|None=None
    unconfirmed_tool_call_ids: list[str]=Field(default_factory=list,max_length=128)


class RunnerActionCommand(CloudRecord):
    command_id: UUIDText
    kind: Literal['stop','recover']
    run_id: TaskId|None=None
    target_command_id: UUIDText|None=None
    checkpoint_id: Label|None=None

    @model_validator(mode='after')
    def target(self):
        if (self.run_id is None)!=(self.target_command_id is None):raise ValueError('invalid_control_target')
        if self.kind=='recover' and not self.run_id:raise ValueError('invalid_recovery_target')
        if self.kind=='stop' and self.checkpoint_id:raise ValueError('invalid_control_target')
        return self


class TaskControl(CloudRecord):
    command_id: UUIDText
    expected_revision: int=Field(ge=1)
    expected_generation: int=Field(ge=1)
    kind: Literal['stop','recover']
    run_id: TaskId|None=None
    checkpoint_id: Label|None=None


class Hello(CloudRecord):
    kind: Literal['hello']
    task_id: TaskId
    project_id: UUIDText
    input_id: ContentId
    instance_id: UUIDText
    generation: int = Field(ge=1)
    boot_id: UUIDText
    state_epoch: UUIDText
    release_id: Label
    phase: Literal['ready','waiting_model','blocked']
    model_ids: list[Label] = Field(max_length=128)
    capabilities:list[Label]=Field(default_factory=list,max_length=128)
    recovery:RunnerRecovery|None=None


class Fenced(CloudRecord):
    connection_id: UUIDText
    generation: int = Field(ge=1)
    boot_id: UUIDText
    state_epoch: UUIDText


class Welcome(Fenced):
    kind: Literal['welcome']


class Dispatch(Fenced):
    kind: Literal['command']
    command: RunCommand


class Query(Fenced):
    kind: Literal['query']
    command_id: UUIDText


class State(Fenced):
    kind: Literal['state','accepted']
    command_id: UUIDText
    run_id: TaskId | None = None
    status: Literal['not_found','running','completed','stopped','failed','interrupted','unknown','recovered']
    terminal_seq: int | None = Field(default=None,ge=1)


class Finished(State):
    kind: Literal['finished']
    run_id: TaskId
    terminal_seq: int = Field(ge=1)
    quiescent: bool


class Heartbeat(Fenced):
    kind: Literal['heartbeat','renew']
    quiescent:bool|None=None
    phase:Literal['ready','waiting_model','blocked']|None=None
    run_id:TaskId|None=None
    recovery:RunnerRecovery|None=None


class ControlDispatch(Fenced):
    kind:Literal['control_command']
    command:RunnerActionCommand


class ControlQuery(Fenced):
    kind:Literal['control_query']
    command_id:UUIDText


class ControlState(Fenced):
    kind:Literal['control_state']
    command_id:UUIDText
    status:Literal['not_found','running','completed','failed','unknown']


class ControlFinished(Fenced):
    kind:Literal['control_finished']
    command_id:UUIDText
    status:Literal['completed','failed']
    terminal_seq:int=Field(ge=1)
    phase:Literal['ready','waiting_model','blocked']
    quiescent:bool
    error_code:Label|None=None
    recovery:RunnerRecovery|None=None


class Renewed(Fenced):
    kind: Literal['renewed']
    token: SecretStr
    expires_at: float = Field(gt=0)


class ArtifactDispatch(Fenced):
    kind:Literal['artifact_capture']
    command:CaptureArtifact


class ArtifactQuery(Fenced):
    kind:Literal['artifact_query']
    command_id:UUIDText


class ArtifactState(Fenced):
    kind:Literal['artifact_state']
    command_id:UUIDText
    artifact_id:UUIDText|None=None
    status:Literal['not_found','capturing','captured','available','failed','unknown']


class ArtifactFinished(Fenced):
    kind:Literal['artifact_finished']
    command_id:UUIDText
    artifact_id:UUIDText
    status:Literal['available','failed']
    quiescent:bool
    phase:Literal['ready','waiting_model','blocked']
    error_code:Label|None=None


class AdmissionRejected(Fenced):
    kind:Literal['admission_rejected']
    command_id:UUIDText
    operation:Literal['run:start','artifact:capture']
    request_digest:ContentId
    error_code:Literal['runner_busy','model_unavailable','unsupported_thinking_level','result_capacity','artifact_busy']
    phase:Literal['ready','waiting_model','blocked']
    quiescent:bool


class Observations(Fenced):
    kind:Literal['observations']
    batch:ObservationBatch


RunnerFrame=TypeAdapter(Annotated[Hello|State|Finished|Heartbeat|ArtifactState|ArtifactFinished|ControlState|ControlFinished|AdmissionRejected|Observations,Field(discriminator='kind')])
CoordinatorFrame=TypeAdapter(Annotated[Welcome|Dispatch|Query|Renewed|ArtifactDispatch|ArtifactQuery|ControlDispatch|ControlQuery,Field(discriminator='kind')])
