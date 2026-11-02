"""Opt-in local delegation settings; all endpoints remain in private profiles."""
from pathlib import Path
from pydantic import Field,field_validator
from naiyue_test_agent.contracts.cloud_tasks import CloudRecord
from naiyue_test_agent.contracts.cloud_inputs import InputLimits


class CloudTaskSettings(CloudRecord):
    profiles_file:Path|None=None
    max_preparations:int=Field(default=4,gt=0,le=32)
    max_commands:int=Field(default=16,gt=0,le=128)
    max_controls:int=Field(default=4,gt=0,le=32)
    runner_wait_seconds:float=Field(default=15.0,gt=0,le=60)
    runner_poll_seconds:float=Field(default=0.1,gt=0,le=5)
    max_observers:int=Field(default=8,gt=0,le=64)
    stream_seconds:float=Field(default=1800.0,gt=0,le=86400)
    stream_send_timeout_seconds:float=Field(default=15.0,gt=0,le=300)
    input_limits:InputLimits=Field(default_factory=InputLimits)

    @field_validator('profiles_file',mode='before')
    @classmethod
    def path(cls,value):
        if value is None:return None
        result=Path(value).expanduser()
        if not result.is_absolute():raise ValueError('cloud_profiles_absolute_path_required')
        return result
