"""Frozen portable policy and separate private local source evidence."""
from pathlib import Path
from typing import Literal
from pydantic import Field,field_validator
from .cloud_tasks import CloudRecord,ContentId


class SourceRule(CloudRecord):
    name:str=Field(min_length=1,max_length=2048)
    kind:Literal['ignore','attributes']
    content:str

    @field_validator('name')
    @classmethod
    def relative(cls,value):
        if value.startswith('/') or '\\' in value or ':' in value or any(p in ('','.','..') for p in value.split('/')):
            raise ValueError('source_rule_invalid')
        return value


class SourcePolicy(CloudRecord):
    kind:Literal['git']
    safety_version:Literal[1]=1
    include:list[str]=Field(default_factory=list)
    exclude:list[str]=Field(default_factory=list)
    rules:list[SourceRule]=Field(default_factory=list)


class SourceFact(CloudRecord):
    path:Path
    resolved:Path
    identity:list[int]|None
    sha256:ContentId|None


class SourceDirectory(CloudRecord):
    path:Path
    identity:list[int]
    members_sha256:ContentId


class SourceReceipt(CloudRecord):
    root:Path
    head:str|None
    config:dict[str,str]
    attributes:dict[str,dict[str,str]]
    deleted:list[str]
    submodules:list[str]
    excluded:list[str]
    facts:list[SourceFact]
    directories:list[SourceDirectory]
    git_commands:int=Field(ge=0)
    source_modes:dict[str,int]=Field(default_factory=dict)


class SourcePlan(CloudRecord):
    selection:dict
    receipt:SourceReceipt

    @field_validator('selection')
    @classmethod
    def valid_selection(cls,value):
        from .cloud_inputs import Selection
        return Selection.model_validate(value).model_dump(by_alias=True,exclude_none=True)
