"""Two configured TCP service slots; credentials never belong in target URLs."""
from ipaddress import ip_address
import re
from typing import Literal
from urllib.parse import urlsplit
from pydantic import Field,model_validator,field_validator
from .cloud_tasks import CloudRecord,UUIDText


class ServiceTarget(CloudRecord):
    address:str=Field(min_length=1,max_length=2048)
    enabled:bool=True

    @model_validator(mode='after')
    def valid(self):
        if any(ord(c)<33 or ord(c)==127 for c in self.address):raise ValueError('invalid_service_target')
        value=urlsplit(self.address)
        if (value.scheme not in ('http','https','grpc','grpcs') or not value.hostname or value.username is not None
                or value.password is not None or value.query or value.fragment
                or not re.fullmatch(r'[a-zA-Z0-9_.:-]+',value.hostname)
                or value.hostname.startswith('-') or value.port is not None and not 1<=value.port<=65535
                or value.scheme in ('grpc','grpcs') and (value.port is None or value.path not in ('','/'))):
            raise ValueError('invalid_service_target')
        return self

    @property
    def host(self):return urlsplit(self.address).hostname
    @property
    def tls(self):return urlsplit(self.address).scheme in ('https','grpcs')
    @property
    def port(self):return urlsplit(self.address).port or (443 if self.tls else 80)
    @property
    def path(self):return urlsplit(self.address).path or '/'


class NetworkEndpoints(CloudRecord):
    bind_host:str
    http_port:int=Field(ge=1024,le=65535)
    grpc_port:int=Field(ge=1024,le=65535)

    @field_validator('bind_host')
    @classmethod
    def private_bridge(cls,value):
        address=ip_address(value)
        if address.version!=4 or not address.is_private or address.is_loopback or address.is_unspecified or address.is_multicast or address.is_link_local or address.is_reserved:
            raise ValueError('private_bridge_ipv4_required')
        return str(address)

    @model_validator(mode='after')
    def distinct(self):
        if self.http_port==self.grpc_port:raise ValueError('duplicate_service_port')
        return self


class NetworkChange(CloudRecord):
    command_id:UUIDText
    expected_revision:int=Field(ge=0)
    slot:Literal['http','grpc']
    target:ServiceTarget

    @model_validator(mode='after')
    def protocol(self):
        if not self.target.address.startswith(('http:','https:') if self.slot=='http' else ('grpc:','grpcs:')):
            raise ValueError('service_protocol_mismatch')
        return self


class NetworkFinish(CloudRecord):
    command_id:UUIDText


def service_context(state,endpoints):
    slots={}
    for slot,raw in state['slots'].items():
        target=ServiceTarget.model_validate(raw)
        if target.enabled:
            slots[slot]=dict(address=target.address,connect_host=endpoints.bind_host,
                connect_port=getattr(endpoints,slot+'_port'),server_name=target.host,tls=target.tls)
    return dict(revision=state['revision'],slots=slots)
