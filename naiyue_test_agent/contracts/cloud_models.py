"""Bounded private deployment policy and secret-free task model profile."""
import ipaddress
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel,ConfigDict,Field,TypeAdapter,field_validator

from .cloud_tasks import CloudRecord,ContentId,Label


class ModelProxySettings(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True)
    config_path:Path
    upstreams:dict[str,str]=Field(default_factory=dict)
    max_request_bytes:int=Field(default=32*1024*1024,ge=1024,le=256*1024*1024)
    max_response_bytes:int=Field(default=128*1024*1024,ge=1024,le=1024*1024*1024)
    error_bytes:int=Field(default=65536,ge=1024,le=1048576)
    chunk_bytes:int=Field(default=65536,ge=1024,le=1048576)
    per_task:int=Field(default=4,ge=1,le=128)
    total:int=Field(default=16,ge=1,le=1024)
    body_timeout_seconds:float=Field(default=30,gt=0,le=3600)
    max_duration_seconds:float=Field(default=3600,gt=0,le=86400)

    @field_validator('config_path')
    @classmethod
    def explicit_file(cls,path):return path.expanduser().absolute()

    @field_validator('upstreams')
    @classmethod
    def service_roots(cls,values):
        for value in values.values():validate_model_upstream(value)
        return values


def validate_model_upstream(value):
    try:
        p=urlsplit(value);port=p.port
        try:address=ipaddress.ip_address(p.hostname or '')
        except ValueError:address=None
        if (not p.hostname or p.scheme not in ('http','https') or p.username or p.password
                or p.query or p.fragment or '\\' in value or any(ord(c)<33 for c in value)
                or port==0 or any(part in ('.','..') for part in p.path.split('/'))
                or (p.scheme=='http' and (address is None or not (address.is_private or address.is_loopback)
                    or address.is_unspecified or address.is_multicast))):raise ValueError()
    except ValueError:raise ValueError('invalid_model_upstream') from None
    return value.rstrip('/')


class RunnerModelProfile(CloudRecord):
    profile_id:ContentId
    configuration:dict

    @field_validator('configuration')
    @classmethod
    def public_only(cls,value):
        if set(value)!={'schema_version','default_model_id','providers','models'}:raise ValueError('invalid_model_profile')
        if not isinstance(value['providers'],dict) or any(set(p)!={'kind'} for p in value['providers'].values()):
            raise ValueError('private_model_configuration')
        if not isinstance(value['models'],dict) or not 0<len(value['models'])<=128:
            raise ValueError('model_catalog_too_large')
        for model_id in value['models']:TypeAdapter(Label).validate_python(model_id)
        return value
