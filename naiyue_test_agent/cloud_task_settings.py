"""Opt-in local delegation settings; all endpoints remain in private profiles."""
from pathlib import Path
from pydantic import Field,field_validator,model_validator
from naiyue_test_agent.contracts.cloud_tasks import CloudRecord
from naiyue_test_agent.contracts.cloud_inputs import InputLimits
from naiyue_test_agent.contracts.cloud_receive import ReceiveLimits
from naiyue_test_agent.contracts.cloud_results import ResultLimits


class CloudTaskSettings(CloudRecord):
    profiles_file:Path|None=None
    connections_file:Path|None=None
    network_state_file:Path|None=None
    max_preparations:int=Field(default=4,gt=0,le=32)
    download_link_seconds:int=Field(default=300,gt=0,le=3600)
    max_download_links:int=Field(default=64,gt=0,le=128)
    queue_poll_seconds:float=Field(default=1.0,gt=0,le=60)
    max_commands:int=Field(default=16,gt=0,le=128)
    max_controls:int=Field(default=4,gt=0,le=32)
    runner_wait_seconds:float=Field(default=15.0,gt=0,le=60)
    runner_poll_seconds:float=Field(default=0.1,gt=0,le=5)
    max_observers:int=Field(default=8,gt=0,le=64)
    stream_seconds:float=Field(default=1800.0,gt=0,le=86400)
    stream_send_timeout_seconds:float=Field(default=15.0,gt=0,le=300)
    input_limits:InputLimits=Field(default_factory=InputLimits)
    receive_limits:ReceiveLimits=Field(default_factory=ReceiveLimits)
    result_limits:ResultLimits=Field(default_factory=ResultLimits)

    @model_validator(mode='after')
    def connection_source(self):
        if self.profiles_file is not None and self.connections_file is not None:raise ValueError('cloud_connection_config_conflict')
        if self.network_state_file is not None:
            state=self.network_state_file.resolve()
            if not state.name.endswith('.networks.json'):raise ValueError('cloud_network_state_path_invalid')
            for path in (self.profiles_file,self.connections_file):
                if path is not None and str(path.resolve()).casefold()==str(state).casefold():
                    raise ValueError('cloud_network_state_config_conflict')
        return self

    @field_validator('profiles_file','connections_file','network_state_file',mode='before')
    @classmethod
    def path(cls,value):
        if value is None:return None
        result=Path(value).expanduser()
        if not result.is_absolute():raise ValueError('cloud_profiles_absolute_path_required')
        return result
