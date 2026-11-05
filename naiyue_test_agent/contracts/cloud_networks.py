"""Administrator-assigned TCP entries; credentials never belong in target URLs."""
from ipaddress import ip_address
import re
from typing import Annotated
from urllib.parse import urlsplit
from pydantic import Field,model_validator,field_validator
from .cloud_tasks import CloudRecord,UUIDText,CloudError

EntryId=Annotated[str,Field(pattern=r'^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$')]
ServicePort=Annotated[int,Field(ge=1024,le=65535)]


class ServiceTarget(CloudRecord):
    address:str=Field(min_length=1,max_length=2048)
    enabled:bool=True
    name:str=Field(default='',max_length=80,exclude_if=lambda value:not value)

    @field_validator('name')
    @classmethod
    def clean_name(cls,value):
        if any(ord(c)<32 or ord(c)==127 for c in value):raise ValueError('invalid_service_name')
        return value

    @model_validator(mode='after')
    def valid(self):
        if any(ord(c)<33 or ord(c)==127 for c in self.address):raise ValueError('invalid_service_target')
        value=urlsplit(self.address)
        if (value.scheme not in ('http','https','grpc','grpcs','tcp','tls') or not value.hostname or value.username is not None
                or value.password is not None or value.query or value.fragment
                or not re.fullmatch(r'[a-zA-Z0-9_.:-]+',value.hostname)
                or value.hostname.startswith('-') or value.port is not None and not 1<=value.port<=65535
                or value.scheme in ('grpc','grpcs','tcp','tls') and (value.port is None or value.path not in ('','/'))):
            raise ValueError('invalid_service_target')
        return self

    @property
    def host(self):return urlsplit(self.address).hostname
    @property
    def tls(self):return urlsplit(self.address).scheme in ('https','grpcs','tls')
    @property
    def port(self):return urlsplit(self.address).port or (443 if self.tls else 80)
    @property
    def path(self):return urlsplit(self.address).path or '/'


class NetworkEndpoints(CloudRecord):
    bind_host:str
    ports:dict[EntryId,ServicePort]=Field(min_length=1,max_length=64)

    @model_validator(mode='before')
    @classmethod
    def legacy_ports(cls,value):
        if isinstance(value,dict) and 'ports' not in value and 'http_port' in value and 'grpc_port' in value:
            value=dict(value)
            value['ports']={'http':value.pop('http_port'),'grpc':value.pop('grpc_port')}
        return value

    def extends(self,previous):
        return self.bind_host==previous.bind_host and all(self.ports.get(k)==v for k,v in previous.ports.items())

    def port(self,slot):
        if slot not in self.ports:raise CloudError('cloud_network_endpoint_missing',422)
        return self.ports[slot]

    @field_validator('bind_host')
    @classmethod
    def private_bridge(cls,value):
        address=ip_address(value)
        if address.version!=4 or not address.is_private or address.is_loopback or address.is_unspecified or address.is_multicast or address.is_link_local or address.is_reserved:
            raise ValueError('private_bridge_ipv4_required')
        return str(address)

    @model_validator(mode='after')
    def distinct(self):
        if len(set(self.ports.values()))!=len(self.ports):raise ValueError('duplicate_service_port')
        return self


class NetworkChange(CloudRecord):
    command_id:UUIDText
    expected_revision:int=Field(ge=0)
    slot:EntryId
    target:ServiceTarget



class NetworkFinish(CloudRecord):
    command_id:UUIDText


def service_context(state,endpoints):
    slots={}
    for slot,raw in state['slots'].items():
        target=ServiceTarget.model_validate(raw)
        if target.enabled:
            slots[slot]=dict(address=target.address,connect_host=endpoints.bind_host,
                connect_port=endpoints.port(slot),server_name=target.host,tls=target.tls,name=target.name or slot)
    return dict(revision=state['revision'],slots=slots)
