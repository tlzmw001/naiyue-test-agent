"""Immutable result identities; transfer completeness is distinct from business success."""
import hashlib,unicodedata
from typing import Literal
from pydantic import Field,field_validator,model_validator
from .cloud_tasks import CloudRecord,UUIDText,TaskId,ContentId,Label
from .cloud_inputs import InputEntry,input_path
from .cloud_artifacts import ArtifactRecord
from .cloud_events import canonical_json


def result_path(value):
    if (not value or len(value.encode())>1024 or unicodedata.normalize('NFC',value)!=value or '\\' in value
            or any(ord(c)<32 or ord(c)==127 for c in value) or any(p in ('','.','..') for p in value.split('/'))
            or any(p.casefold()=='.git' for p in value.split('/'))):raise ValueError('invalid_result_path')
    return value


class ResultLimits(CloudRecord):
    output_directory:str='.nyt-output'
    max_patch_bytes:int=Field(default=64*1024*1024,gt=0)
    max_metadata_bytes:int=Field(default=1024*1024,gt=0)
    max_files:int=Field(default=4096,gt=0)
    max_artifacts:int=Field(default=30,ge=0,le=126)
    max_result_bytes:int=Field(default=256*1024*1024,gt=0)
    max_results:int=Field(default=64,gt=0,le=1024)
    git_executable:str=Field(default='git',min_length=1)
    git_timeout_seconds:float=Field(default=30,gt=0,le=300)
    _directory=field_validator('output_directory')(input_path)


class ResultPlan(CloudRecord):
    result_id:UUIDText
    source_command_id:UUIDText
    source_run_id:TaskId
    task_id:TaskId
    project_id:UUIDText
    input_id:ContentId
    source_generation:int=Field(ge=1)
    output_directory:str
    _directory=field_validator('output_directory')(input_path)


class ResultBoundary(CloudRecord):
    kind:Literal['run','recovery']
    epoch:UUIDText
    seq:int=Field(ge=1)
    outcome:Literal['completed','stopped','failed','interrupted','unconfirmed']


class OutputEntry(InputEntry):
    @field_validator('path')
    @classmethod
    def path_valid(cls,value):return result_path(value)


class OutputManifest(CloudRecord):
    input_id:ContentId
    policy_sha256:ContentId
    entries:list[OutputEntry]

    @model_validator(mode='after')
    def ordered(self):
        paths=[e.path for e in self.entries]
        if sorted(set(paths))!=paths:raise ValueError('invalid_output_manifest')
        names=set(paths)
        if any('/'.join(p.split('/')[:n]) in names for p in names for n in range(1,len(p.split('/')))):
            raise ValueError('result_path_parent_collision')
        return self

    @property
    def canonical(self):return canonical_json(self.model_dump(exclude_none=True)).encode()
    @property
    def sha256(self):return hashlib.sha256(self.canonical).hexdigest()


class ResultFile(CloudRecord):
    role:Literal['manifest','patch','artifact']
    artifact:ArtifactRecord


class ResultBundle(CloudRecord):
    plan:ResultPlan
    boundary:ResultBoundary
    generation:int=Field(ge=1)
    captured_at:float=Field(ge=0)
    files:list[ResultFile]=Field(min_length=2,max_length=128)

    @model_validator(mode='after')
    def slots(self):
        if [f.role for f in self.files].count('manifest')!=1 or [f.role for f in self.files].count('patch')!=1:
            raise ValueError('result_required_slots')
        ids=[f.artifact.artifact_id for f in self.files];names=[(f.role,f.artifact.relative_name) for f in self.files]
        if len(set(ids))!=len(ids) or len(set(names))!=len(names):raise ValueError('result_slot_collision')
        for item in self.files:
            a=item.artifact
            if (a.command_id!=self.plan.result_id or a.task_id!=self.plan.task_id or a.project_id!=self.plan.project_id
                    or a.input_id!=self.plan.input_id or a.generation!=self.generation or a.captured_at!=self.captured_at):
                raise ValueError('result_slot_identity')
        return self


class ResultRecord(CloudRecord):
    plan:ResultPlan
    phase:Literal['waiting_terminal','pending','capturing','verifying','available','failed','unknown','abandoned']
    revision:int=Field(default=1,ge=1)
    boundary:ResultBoundary|None=None
    bundle:ResultBundle|None=None
    error_code:Label|None=None
    attempt:int=Field(default=0,ge=0)
    generation:int|None=Field(default=None,ge=1)


class ResultAction(CloudRecord):
    command_id:UUIDText
    expected_revision:int=Field(ge=1)
