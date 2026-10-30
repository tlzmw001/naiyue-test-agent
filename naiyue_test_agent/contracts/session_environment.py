"""Persisted sandbox creation facts; no live clients or filesystem paths."""
import json
import hashlib
from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator
from typing import Annotated
from naiyue_test_agent.sandbox_settings import SandboxSettings

Ident = Annotated[str,Field(pattern='^[0-9a-f]{32}$')]
ProfileId = Annotated[str,Field(pattern='^[a-z][a-z0-9_-]{0,63}$')]


def profile_name(value):
    if not value.strip() or any(ord(char)<32 or ord(char)==127 for char in value):
        raise ValueError('Invalid environment profile name')
    return value


def selection_revision(profile_id,name,settings):
    value=dict(profile_id=profile_id,name=name,settings=settings.model_dump(mode='json'))
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


class SandboxProfileSelection(BaseModel):
    model_config=ConfigDict(strict=True,extra='forbid',frozen=True,hide_input_in_errors=True)
    profile_id: ProfileId
    name: str = Field(min_length=1,max_length=120)
    revision: str = Field(pattern='^[0-9a-f]{64}$')
    settings: SandboxSettings
    _name=field_validator('name')(profile_name)

    @model_validator(mode='after')
    def check_revision(self):
        if self.settings.image is None or self.revision!=selection_revision(self.profile_id,self.name,self.settings):
            raise ValueError('Environment selection fingerprint does not match')
        return self

    @classmethod
    def create(cls,profile_id,name,settings):
        return cls(profile_id=profile_id,name=name,settings=settings,
            revision=selection_revision(profile_id,name,settings))

    @classmethod
    def from_record(cls,record):
        fields=(record.profile_id,record.profile_name,record.profile_revision)
        if any(value is not None for value in fields):
            if any(value is None for value in fields):raise ValueError('Incomplete environment profile identity')
            return cls(profile_id=record.profile_id,name=record.profile_name,
                revision=record.profile_revision,settings=SandboxSettings.model_validate(record.settings))
        settings=SandboxSettings.model_validate(dict(record.settings,image=record.settings.get('image') or record.image_id))
        return cls.create('legacy','旧版沙箱',settings)


class SandboxProfileReference(BaseModel):
    model_config=ConfigDict(strict=True,extra='forbid',frozen=True,hide_input_in_errors=True)
    profile_id: ProfileId
    revision: str = Field(pattern='^[0-9a-f]{64}$')


class SandboxSessionSetup(BaseModel):
    model_config=ConfigDict(strict=True,extra='forbid',frozen=True)
    model_id: str = Field(min_length=1,max_length=128)
    include_untracked: list[str] = Field(max_length=100000)
    input_id: Ident
    binding_id: Ident
    selection: SandboxProfileSelection | None = None
    requested_profile: SandboxProfileReference | None = None


def saved_setup(row):
    return SandboxSessionSetup.model_validate_json(row['execution_setup_json'])
