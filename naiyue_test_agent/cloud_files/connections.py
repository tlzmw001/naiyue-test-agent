"""One private file, normalized once into the existing profile execution path."""
from pydantic import Field,model_validator,RootModel
from naiyue_test_agent.contracts.cloud_tasks import CloudRecord,CloudError
from naiyue_test_agent.cloud_install.settings import RemoteUpdateSettings
from .client import CloudConnection
from .configuration import read_settings
from .profiles import CloudProfile,ProfileBook


class ConnectionEntry(CloudRecord):
    connection_id:str=Field(pattern=r'^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$')
    connection:CloudConnection=Field(repr=False)
    ssh:dict=Field(default_factory=dict)
    profiles:list[dict]=Field(min_length=1,max_length=64)
    remote_update:dict|None=None

    @model_validator(mode='after')
    def validate_entry(self):
        allowed={'tunnel','ssh_host','ssh_executable','connect_timeout_seconds','keepalive_seconds',
            'keepalive_count','poll_seconds','shutdown_seconds','reconnect_seconds'}
        if set(self.ssh)-allowed:raise ValueError('invalid_connection_ssh_fields')
        for value in self.profiles:
            if set(value)-{'profile_id','network_id','template_ids','default_template'}:
                raise ValueError('invalid_connection_profile_fields')
            self.profile(value)
        if self.remote_update is not None:
            if set(self.remote_update)-{'remote_python','remote_settings','timeout_seconds'}:
                raise ValueError('invalid_remote_update_fields')
            self.remote()
        return self

    def profile(self,value):
        return CloudProfile.model_validate(value|self.ssh|dict(connection=self.connection,connection_id=self.connection_id))

    def remote(self):
        if self.remote_update is None:raise CloudError('cloud_remote_update_not_configured',422)
        return RemoteUpdateSettings.model_validate(self.remote_update|{
            'ssh_host':self.ssh.get('ssh_host'),'ssh_executable':self.ssh.get('ssh_executable','ssh')})


class ConnectionBook(CloudRecord):
    connections:list[ConnectionEntry]=Field(min_length=1,max_length=64)

    @model_validator(mode='after')
    def unique(self):
        if len({entry.connection_id for entry in self.connections})!=len(self.connections):
            raise ValueError('duplicate_cloud_connection')
        self.profile_book()
        endpoints=[entry.connection.endpoint for entry in self.connections if entry.ssh.get('tunnel')=='owned']
        if len(set(endpoints))!=len(endpoints):raise ValueError('duplicate_cloud_endpoint')
        return self

    @classmethod
    def read(cls,path):return read_settings(path,cls)

    def profile_book(self):
        return ProfileBook(profiles=[entry.profile(value) for entry in self.connections for value in entry.profiles])

    def select(self,profile_id):
        book=self.profile_book()
        if profile_id is None:
            if len(book.profiles)!=1:raise CloudError('cloud_profile_required',422)
            return book.profiles[0]
        return book.require(profile_id)

    def remote(self,profile_id):
        selected=self.select(profile_id)
        return next(entry for entry in self.connections if entry.connection_id==selected.connection_id).remote()


class ClientSettings(RootModel[CloudConnection|ConnectionBook|RemoteUpdateSettings]):
    """Private CLI read boundary for both published 0.5.8 and unified files."""


def read_connection(path,profile_id=None):
    value=read_settings(path,ClientSettings).root
    if isinstance(value,ConnectionBook):return value.select(profile_id).connection_settings()
    if not isinstance(value,CloudConnection):raise CloudError('cloud_settings_invalid',422)
    if profile_id is not None:raise CloudError('cloud_profile_not_found',404)
    return value


def read_remote(path,profile_id=None):
    value=read_settings(path,ClientSettings).root
    if isinstance(value,ConnectionBook):return value.remote(profile_id)
    if not isinstance(value,RemoteUpdateSettings):raise CloudError('cloud_settings_invalid',422)
    if profile_id is not None:raise CloudError('cloud_profile_not_found',404)
    return value
