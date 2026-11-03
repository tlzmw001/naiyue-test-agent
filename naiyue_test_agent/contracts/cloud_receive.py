"""Local result receipt and material identities; no caller-selected host path."""
import time
from typing import Literal
from pydantic import Field,model_validator
from .cloud_tasks import CloudRecord,UUIDText,TaskId,ContentId,Label
from .cloud_local_tasks import SessionId
from .cloud_artifacts import ArtifactRecord
from .cloud_results import ResultRecord
from .cloud_inputs import InputPackage


class ReceiveLimits(CloudRecord):
    max_results:int=Field(default=128,gt=0,le=4096)
    max_active:int=Field(default=4,gt=0,le=32)
    max_cache_bytes:int=Field(default=512*1024*1024,gt=0)
    max_file_bytes:int=Field(default=64*1024*1024,gt=0)
    max_files:int=Field(default=32,ge=2,le=128)
    segment_bytes:int=Field(default=4*1024*1024,gt=0,le=64*1024*1024)
    transfer_timeout_seconds:float=Field(default=120,gt=0,le=3600)
    max_read_bytes:int=Field(default=65536,gt=0,le=1024*1024)
    max_read_lines:int=Field(default=2000,gt=0,le=10000)


class ReceiveRequest(CloudRecord):
    command_id:UUIDText
    local_session_id:SessionId
    artifact_ids:list[UUIDText]=Field(default_factory=list,max_length=126)

    @model_validator(mode='after')
    def unique(self):
        if len(set(self.artifact_ids))!=len(self.artifact_ids):raise ValueError('duplicate_artifact_id')
        return self


class ReceiveFile(CloudRecord):
    material_id:UUIDText
    role:Literal['manifest','patch','artifact']
    artifact:ArtifactRecord
    offset:int=Field(default=0,ge=0)
    verified:bool=False


class ReceiveRecord(CloudRecord):
    receive_id:UUIDText
    command_id:UUIDText
    local_project_id:UUIDText
    local_session_id:SessionId
    binding_id:UUIDText
    source_handle:UUIDText
    instance_id:UUIDText
    task_id:TaskId
    result_id:UUIDText
    input_id:ContentId
    phase:Literal['reserved','downloading','verifying','available','failed','unknown']
    files:list[ReceiveFile]=Field(default_factory=list)
    result:ResultRecord|None=None
    input_package:InputPackage|None=None
    error_code:Label|None=None
    active:bool=False
    created_at:float=Field(default_factory=time.time)
    updated_at:float=Field(default_factory=time.time)


class MaterialRef(CloudRecord):
    material_id:UUIDText
    receive_id:UUIDText
    task_id:TaskId
    result_id:UUIDText
    input_id:ContentId
    title:str=Field(min_length=1,max_length=1024)
    role:Literal['manifest','patch','artifact']
    size:int=Field(ge=0)
    sha256:ContentId
    source_run_id:TaskId
    outcome:Literal['completed','stopped','failed','interrupted','unconfirmed']
    captured_at:float=Field(ge=0)
    reported:Literal[True]=True


class AnalysisDraft(CloudRecord):
    local_project_id:UUIDText
    local_session_id:SessionId
    receive_id:UUIDText
    text:str=Field(min_length=1,max_length=4096)
    material_refs:list[MaterialRef]=Field(min_length=1,max_length=128)
