"""Single-file immutable artifacts; raw bytes never travel in JSON/control frames."""
from typing import Literal
from pydantic import Field,field_validator
from .cloud_tasks import CloudRecord,UUIDText,TaskId,ContentId,Label
from .cloud_inputs import input_path


class ArtifactLimits(CloudRecord):
    max_file_bytes:int=Field(default=64*1024*1024,gt=0)
    max_cache_bytes:int=Field(default=512*1024*1024,gt=0)
    max_artifacts:int=Field(default=128,gt=0)
    io_bytes:int=Field(default=256*1024,gt=0,le=1024*1024)
    transfer_timeout_seconds:float=Field(default=120.0,gt=0,le=3600)


class CaptureArtifact(CloudRecord):
    command_id:UUIDText
    artifact_id:UUIDText
    relative_name:str

    @field_validator('relative_name')
    @classmethod
    def safe_name(cls,value):return input_path(value)


class CaptureRequest(CloudRecord):
    command_id:UUIDText
    expected_revision:int=Field(ge=1)
    expected_generation:int=Field(ge=1)
    relative_name:str

    @field_validator('relative_name')
    @classmethod
    def safe_name(cls,value):return input_path(value)


class ArtifactRecord(CaptureArtifact):
    task_id:TaskId
    project_id:UUIDText
    input_id:ContentId
    generation:int=Field(ge=1)
    size:int=Field(ge=0)
    sha256:ContentId
    captured_at:float=Field(ge=0)


class ArtifactCaptureState(CloudRecord):
    command:CaptureArtifact
    status:Literal['capturing','captured','available','failed','unknown']
    artifact:ArtifactRecord|None=None
    error_code:Label|None=None


class ArtifactTransfer(CloudRecord):
    artifact:ArtifactRecord
    state:Literal['pending','uploading','uploaded','available']
    blob_identity:list[int]|None=Field(default=None,min_length=5,max_length=5)
