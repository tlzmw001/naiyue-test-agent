"""Canonical input identity and bounded transfer facts; no filesystem policy inference."""
import hashlib
from pathlib import Path
from typing import Literal
import re
import unicodedata

from pydantic import ConfigDict, Field, field_validator, model_validator
from .cloud_tasks import CloudRecord, ContentId, UUIDText
from .cloud_events import canonical_json
from .cloud_sources import SourcePolicy


def input_path(value):
    if (not value or len(value.encode('utf-8')) > 1024 or unicodedata.normalize('NFC',value)!=value
            or '\\' in value or any(ord(c)<32 or ord(c)==127 for c in value)):
        raise ValueError('invalid_input_path')
    parts=value.split('/')
    for part in parts:
        stem=part.split('.')[0].upper()
        if (part in ('','.','..') or part[-1:] in ('.',' ') or ':' in part
                or stem in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}
                or part.casefold() in {'.git','.venv','node_modules','__pycache__','.pytest_cache','.ssh','.aws','id_rsa','id_ed25519','id_dsa','id_ecdsa','.netrc','_netrc'}
                or any(c in '<>"|?*' for c in part) or part.casefold().endswith(('.p12','.pfx'))
                or part.casefold().startswith('.env')):
            raise ValueError('invalid_input_path')
    return value


def check_input_content(tail,chunk,*,first):
    content=tail+chunk
    if re.search(rb'-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----',content):
        from .cloud_tasks import CloudError
        raise CloudError('input_sensitive')
    if first and content.startswith(b'version https://git-lfs.github.com/spec/v1\n'):
        from .cloud_tasks import CloudError
        raise CloudError('input_lfs_pointer_unsupported')
    return content[-128:]


def distinct_paths(paths):
    seen=set()
    for path in paths:
        input_path(path)
        key=unicodedata.normalize('NFD',path).casefold()
        if key in seen:raise ValueError('input_path_collision')
        seen.add(key)
    for key in seen:
        if any('/'.join(key.split('/')[:i]) in seen for i in range(1,len(key.split('/')))):
            raise ValueError('input_path_parent_collision')
    # Parent spellings must also be portable (Dir/a and dir/b conflict).
    parents={}
    for path in paths:
        for i in range(1,len(path.split('/'))+1):
            prefix='/'.join(path.split('/')[:i]);key=unicodedata.normalize('NFD',prefix).casefold()
            if key in parents and parents[key]!=prefix:raise ValueError('input_path_collision')
            parents[key]=prefix
    return sorted(paths)


class InputLimits(CloudRecord):
    max_file_bytes:int=Field(default=64*1024*1024,gt=0)
    max_input_bytes:int=Field(default=128*1024*1024,gt=0)
    max_archive_bytes:int=Field(default=160*1024*1024,gt=0)
    max_files:int=Field(default=4096,gt=0)
    max_metadata_bytes:int=Field(default=1024*1024,gt=0)
    io_bytes:int=Field(default=256*1024,gt=0,le=1024*1024)
    max_pending:int=Field(default=8,gt=0)
    max_cache_bytes:int=Field(default=512*1024*1024,gt=0)
    gzip_level:int=Field(default=3,ge=0,le=9)
    upload_timeout_seconds:float=Field(default=120.0,gt=0,le=3600)
    chunk_bytes:int=Field(default=8*1024*1024,gt=0,le=64*1024*1024)
    max_segments:int=Field(default=4096,gt=0,le=4096)
    staging_ttl_seconds:float=Field(default=86400.0,gt=0)
    sweep_interval_seconds:float=Field(default=60.0,gt=0,le=3600)
    git_executable:str=Field(default='git',min_length=1)
    git_timeout_seconds:float=Field(default=30.0,gt=0,le=300)
    max_discovery_entries:int=Field(default=100000,gt=0)


class Selection(CloudRecord):
    model_config=ConfigDict(validate_by_name=True)
    schema_version:Literal[1,2]=Field(default=1,alias='schema')
    paths:list[str]=Field(min_length=1)
    executables:list[str]=Field(default_factory=list)
    policy:SourcePolicy|None=None

    @field_validator('paths','executables')
    @classmethod
    def paths_valid(cls,value):return distinct_paths(value)

    @model_validator(mode='after')
    def executable_subset(self):
        if not set(self.executables)<=set(self.paths):raise ValueError('unselected_executable')
        if (self.schema_version==2)!=(self.policy is not None):raise ValueError('source_policy_required')
        if self.policy is not None:
            distinct_paths(self.policy.include)
            distinct_paths(self.policy.exclude)
        return self

    @property
    def canonical(self):return canonical_json(self.model_dump(by_alias=True,exclude_none=True)).encode('utf-8')


class InputEntry(CloudRecord):
    path:str
    type:Literal['file','symlink']
    size:int=Field(ge=0)
    sha256:ContentId
    mode:Literal[420,493,511]
    target:str|None=None

    @field_validator('path')
    @classmethod
    def path_valid(cls,value):return input_path(value)

    @model_validator(mode='after')
    def kind_fields(self):
        if self.type=='file' and (self.target is not None or self.mode not in (420,493)):
            raise ValueError('invalid_file_entry')
        if self.type=='symlink' and (self.target is None or self.mode!=511):raise ValueError('invalid_link_entry')
        return self


class InputManifest(CloudRecord):
    model_config=ConfigDict(validate_by_name=True)
    schema_version:Literal[1]=Field(default=1,alias='schema')
    selection:dict[Literal['policy_sha256'],ContentId]
    entries:list[InputEntry]

    @model_validator(mode='after')
    def ordered(self):
        names=[e.path for e in self.entries]
        if set(self.selection)!={'policy_sha256'} or distinct_paths(names)!=names:raise ValueError('invalid_manifest')
        return self

    @property
    def canonical(self):return canonical_json(self.model_dump(by_alias=True,exclude_none=True)).encode('utf-8')
    @property
    def input_id(self):return hashlib.sha256(self.canonical).hexdigest()


class CapturedInput(CloudRecord):
    input_id:ContentId
    archive_sha256:ContentId
    size:int=Field(gt=0)
    codec:Literal['gzip','tar']
    directory:Path
    source_receipt:Path|None=None


class CreateTransfer(CloudRecord):
    command_id:UUIDText
    purpose:Literal['input']
    input_id:ContentId
    size:int=Field(gt=0)
    archive_sha256:ContentId
    codec:Literal['gzip','tar']


class InputSegment(CloudRecord):
    offset:int=Field(ge=0)
    size:int=Field(gt=0)
    sha256:ContentId


class InputTransfer(CreateTransfer):
    transfer_id:UUIDText
    state:Literal['pending','uploading','uploaded','committed','cancelled']
    offset:int=Field(ge=0)
    cache_hit:bool=False
    chunk_bytes:int=Field(default=8*1024*1024,gt=0,le=64*1024*1024)
    segments:list[InputSegment]=Field(default_factory=list,max_length=4096)
    archive_identity:list[int]|None=Field(default=None,min_length=5,max_length=5)
    updated_at:float=Field(default=0.0,ge=0)


class InputPackage(CloudRecord):
    input_id:ContentId
    archive_sha256:ContentId
    size:int=Field(gt=0)
    codec:Literal['gzip','tar']
    manifest:InputManifest
    selection:Selection
    archive_identity:list[int]|None=Field(default=None,min_length=5,max_length=5)


class CreateTask(CloudRecord):
    command_id:UUIDText
    project_id:UUIDText
    input_id:ContentId
    template_id:str=Field(min_length=1,max_length=128)
    title:str=Field(default='',max_length=1024)
