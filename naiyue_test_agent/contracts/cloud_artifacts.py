"""Single-file immutable artifacts; raw bytes never travel in JSON/control frames."""
from typing import Literal
from pydantic import Field,field_validator
from .cloud_tasks import CloudRecord,UUIDText,TaskId,ContentId,Label
from .cloud_inputs import file_path


class ArtifactLimits(CloudRecord):
    max_file_bytes:int=Field(default=20*1024**3,gt=0)
    max_cache_bytes:int=Field(default=64*1024**3,gt=0)
    max_artifacts:int=Field(default=4096,gt=0)
    segment_bytes:int=Field(default=8*1024*1024,gt=0,le=64*1024*1024)
    io_bytes:int=Field(default=256*1024,gt=0,le=1024*1024)
    transfer_timeout_seconds:float=Field(default=120.0,gt=0,le=3600)


class CaptureArtifact(CloudRecord):
    command_id:UUIDText
    artifact_id:UUIDText
    relative_name:str

    @field_validator('relative_name')
    @classmethod
    def safe_name(cls,value):return file_path(value)


class CaptureRequest(CloudRecord):
    command_id:UUIDText
    expected_revision:int=Field(ge=1)
    expected_generation:int=Field(ge=1)
    relative_name:str

    @field_validator('relative_name')
    @classmethod
    def safe_name(cls,value):return file_path(value)


class ArtifactRecord(CaptureArtifact):
    task_id:TaskId
    project_id:UUIDText
    input_id:ContentId|None
    generation:int=Field(ge=1)
    size:int=Field(ge=0)
    sha256:ContentId
    captured_at:float=Field(ge=0)


class ArtifactCaptureState(CloudRecord):
    command:CaptureArtifact
    status:Literal['capturing','captured','available','failed','unknown']
    artifact:ArtifactRecord|None=None
    error_code:Label|None=None


class ArtifactSegment(CloudRecord):
    offset:int=Field(ge=0)
    size:int=Field(ge=0)
    sha256:ContentId


class ArtifactTransfer(CloudRecord):
    artifact:ArtifactRecord
    state:Literal['pending','uploading','uploaded','available']
    offset:int=Field(default=0,ge=0,exclude_if=lambda v:v==0)
    segments:list[ArtifactSegment]=Field(default_factory=list,max_length=4096,exclude_if=lambda v:not v)
    blob_identity:list[int]|None=Field(default=None,min_length=5,max_length=5)
