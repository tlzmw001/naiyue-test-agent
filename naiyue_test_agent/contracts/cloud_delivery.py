"""Explicit local code delivery, separate from receiving and analysing materials."""
from typing import Literal
from pydantic import Field,model_validator
from .cloud_tasks import CloudRecord,UUIDText
from .cloud_local_tasks import SessionId


class DeliveryRequest(CloudRecord):
    command_id:UUIDText
    local_session_id:SessionId
    receive_id:UUIDText
    action:Literal['export','branch','apply','files']
    destination_handle:UUIDText
    destination_path:str|None=Field(default=None,max_length=4096)
    expected_directory:str|None=Field(default=None,max_length=64)
    name:str=Field(default='',max_length=128)

    @model_validator(mode='after')
    def name_required(self):
        if self.action in ('export','branch') and not self.name:raise ValueError('delivery_name_required')
        if self.action=='apply' and self.name:raise ValueError('delivery_name_unexpected')
        if self.action=='files':
            if not self.destination_path or not self.expected_directory:raise ValueError('delivery_destination_required')
        elif self.destination_path is not None or self.expected_directory is not None:raise ValueError('delivery_destination_unexpected')
        return self


class DeliveryChange(CloudRecord):
    path:str
    operation:Literal['add','modify','delete']


class DeliveryRecord(CloudRecord):
    command_id:UUIDText
    local_project_id:UUIDText
    local_session_id:SessionId
    receive_id:UUIDText
    action:Literal['export','branch','apply','files']
    name:str=''
    phase:Literal['preparing','prepared','executing','completed','partial','failed','unknown']
    changes:list[DeliveryChange]=Field(default_factory=list)
    conflicts:list[str]=Field(default_factory=list)
    error_code:str|None=None
    branch_head:str|None=None
    force:bool=False
    force_allowed:bool=False
    base_result_id:str|None=None
    destination_path:str|None=None
    done:list[str]=Field(default_factory=list)


class DeliveryConfirm(CloudRecord):
    confirm:Literal[True]
    force:bool=False
