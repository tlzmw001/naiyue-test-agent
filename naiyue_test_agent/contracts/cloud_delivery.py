"""Explicit local code delivery, separate from receiving and analysing materials."""
from typing import Literal
from pydantic import Field,model_validator
from .cloud_tasks import CloudRecord,UUIDText
from .cloud_local_tasks import SessionId


class DeliveryRequest(CloudRecord):
    command_id:UUIDText
    local_session_id:SessionId
    receive_id:UUIDText
    action:Literal['export','branch','apply']
    destination_handle:UUIDText
    name:str=Field(default='',max_length=128)

    @model_validator(mode='after')
    def name_required(self):
        if self.action in ('export','branch') and not self.name:raise ValueError('delivery_name_required')
        if self.action=='apply' and self.name:raise ValueError('delivery_name_unexpected')
        return self


class DeliveryChange(CloudRecord):
    path:str
    operation:Literal['add','modify','delete']


class DeliveryRecord(CloudRecord):
    command_id:UUIDText
    local_project_id:UUIDText
    local_session_id:SessionId
    receive_id:UUIDText
    action:Literal['export','branch','apply']
    name:str=''
    phase:Literal['preparing','prepared','executing','completed','partial','failed','unknown']
    changes:list[DeliveryChange]=Field(default_factory=list)
    conflicts:list[str]=Field(default_factory=list)
    error_code:str|None=None
    branch_head:str|None=None


class DeliveryConfirm(CloudRecord):
    confirm:Literal[True]
