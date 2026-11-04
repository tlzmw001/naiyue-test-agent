"""Trusted release inputs and explicit owner/operator update commands."""
from pathlib import PurePosixPath
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from .cloud_tasks import CloudRecord, ContentId, TemplateReceipt, UUIDText, TaskId


class ReleaseArtifact(CloudRecord):
    filename: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,199}$')
    url: str = Field(max_length=4096)
    size: int = Field(gt=0, le=2**34)
    sha256: ContentId

    @field_validator('url')
    @classmethod
    def trusted_url(cls, value):
        p=urlsplit(value)
        if (p.scheme!='https' or not p.hostname or p.username is not None or p.password is not None
                or p.query or p.fragment or any(ord(c)<33 or ord(c)==127 for c in value)
                or '..' in PurePosixPath(p.path).parts):
            raise ValueError('invalid_release_url')
        return value


class CloudRelease(CloudRecord):
    release_id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$')
    agent_version: str = Field(pattern=r'^[0-9][A-Za-z0-9_.+!-]{0,79}$')
    source_commit: str = Field(pattern=r'^(?:[0-9a-f]{40}|[0-9a-f]{64})$')
    min_coordinator_version: str|None = None
    coordinator_protocol: int = Field(ge=1)
    runner_protocol: int = Field(ge=1)
    state_schema: int = Field(ge=1)
    min_connector_protocol: int = Field(ge=1)
    required_capabilities: list[str] = Field(default_factory=list, max_length=64)
    wheels: dict[str,ReleaseArtifact] = Field(min_length=1,max_length=4)
    kit: ReleaseArtifact
    kit_version: str = Field(min_length=1,max_length=80)
    test_lock: ReleaseArtifact
    base_image: str = Field(pattern=r'^[a-z0-9][a-z0-9/_.:-]*@sha256:[0-9a-f]{64}$')

    @field_validator('agent_version','kit_version','min_coordinator_version')
    @classmethod
    def versions(cls,value):
        if value is not None:
            from packaging.version import Version
            Version(value)
        return value

    @model_validator(mode='after')
    def platforms(self):
        if set(self.wheels)-{'linux-arm64','linux-x86_64','macos-arm64','windows-x86_64'}:
            raise ValueError('unsupported_release_platform')
        if len(self.required_capabilities)!=len(set(self.required_capabilities)) or any(not re.fullmatch(r'[a-z][a-z0-9:_-]{0,79}',x) for x in self.required_capabilities):
            raise ValueError('invalid_release_capabilities')
        return self


class RegisterRelease(CloudRecord):
    command_id: UUIDText
    release: CloudRelease
    template: TemplateReceipt


class ActivateRelease(CloudRecord):
    command_id: UUIDText
    release_id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$')
    template_id: str = Field(pattern=r'^[a-z0-9][a-z0-9_.-]{0,127}$')
    expected_revision: int = Field(ge=0)


class MaintenanceCommand(CloudRecord):
    command_id: UUIDText
    action: Literal['enter','leave']


class UpgradeTask(CloudRecord):
    command_id: UUIDText
    release_id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$')
    expected_revision: int = Field(ge=1)
    expected_generation: int = Field(ge=1)


class BackupRequest(CloudRecord):
    command_id: UUIDText
    task_id: TaskId
    generation: int = Field(ge=1)
    reservation_sha256: ContentId


class UpgradeBackup(BackupRequest):
    manifest_sha256: ContentId
    files: int = Field(ge=0)
    bytes: int = Field(ge=0)


class UpgradeCancellation(BackupRequest):
    container_name: str = Field(pattern=r'^nyt-upgrade-[0-9a-f]{32}$')
    observed_container_id: str|None = Field(default=None,pattern=r'^[0-9a-f]{64}$')


class ReleaseIndex(CloudRecord):
    schema_version: int = Field(ge=1,le=1)
    releases: dict[str,ReleaseArtifact] = Field(max_length=10000)
    channels: dict[str,str] = Field(default_factory=dict,max_length=32)

    @field_validator('releases')
    @classmethod
    def release_names(cls,value):
        if any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',key) for key in value):raise ValueError('invalid_release_id')
        return value

    @model_validator(mode='after')
    def channel_targets(self):
        if any(not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}',name) or target not in self.releases for name,target in self.channels.items()):
            raise ValueError('invalid_release_channel')
        return self
