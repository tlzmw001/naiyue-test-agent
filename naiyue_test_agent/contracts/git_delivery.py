"""Host delivery commands, never arbitrary paths, refspecs or shell strings."""
import re
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator


class GitDeliveryError(RuntimeError):
    def __init__(self,code):self.code=code;super().__init__(code)


def branch_name(value):
    if (not isinstance(value,str) or not 1<=len(value)<=200 or value.startswith(('-', 'refs/'))
            or value=='HEAD' or re.search(r'[\x00-\x20\x7f~^:?*\[\\]',value)
            or '..' in value or '@{' in value):
        raise GitDeliveryError('git_delivery_invalid_branch')
    return value


class DeliveryCommand(BaseModel):
    model_config=ConfigDict(strict=True,extra='forbid',frozen=True)
    operation_id:str=Field(pattern='^[0-9a-f]{32}$')
    action:Literal['branch','merge','discard']
    branch:str|None=None

    @field_validator('branch')
    @classmethod
    def validate_branch(cls,value):
        if value is None:return value
        try:return branch_name(value)
        except GitDeliveryError:raise ValueError('Invalid delivery branch') from None

    @model_validator(mode='after')
    def fields_for_action(self):
        if (self.action=='branch')!=(self.branch is not None):raise ValueError('Branch required for action')
        return self
