"""Private operator inputs; no credentials or prompts in the public report."""
from pathlib import Path
from pydantic import Field,field_validator
from naiyue_test_agent.contracts.cloud_tasks import CloudRecord
from naiyue_test_agent.contracts.cloud_inputs import InputLimits,input_path


class WorkflowSettings(CloudRecord):
    connection_file:Path
    profile_id:str|None=None
    source:Path
    selection_file:Path
    template_id:str=Field(min_length=1,max_length=128)
    model_id:str=Field(min_length=1,max_length=128)
    message:str=Field(min_length=1,max_length=262144)
    expected_report:str='acceptance.txt'
    rounds:int=Field(default=20,ge=1,le=100)
    task_count:int=Field(default=2,ge=1,le=2)
    timeout_seconds:float=Field(default=900.0,gt=0,le=7200)
    poll_seconds:float=Field(default=.5,ge=.05,le=30)
    input_limits:InputLimits=Field(default_factory=InputLimits)

    @field_validator('connection_file','source','selection_file')
    @classmethod
    def absolute(cls,value):return value.expanduser().absolute()

    @field_validator('message')
    @classmethod
    def marker(cls,value):
        if '{task_marker}' not in value:raise ValueError('task_marker_required')
        return value

    @field_validator('expected_report')
    @classmethod
    def report_path(cls,value):return input_path(value)
