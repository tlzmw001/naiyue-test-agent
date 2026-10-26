"""Reusable templates are configuration; selected execution policies are flat facts."""
import logging
from pathlib import Path
import tomllib
from pydantic import BaseModel,ConfigDict,Field,field_serializer,field_validator,model_validator
from .sandbox_settings import SandboxSettings
from .contracts.sandbox import SandboxError
from .contracts.session_environment import ProfileId,SandboxProfileSelection,profile_name


class SandboxProfile(BaseModel):
    model_config=ConfigDict(strict=True,extra='forbid',frozen=True,hide_input_in_errors=True)
    id: ProfileId
    name: str = Field(min_length=1,max_length=120)
    image: str
    _name=field_validator('name')(profile_name)

    @field_validator('image')
    @classmethod
    def image_reference(cls,value):
        return SandboxSettings(image=value).image


class SandboxLifecycleSettings(BaseModel):
    model_config=ConfigDict(strict=True,extra='forbid',frozen=True,hide_input_in_errors=True)
    max_running: int = Field(default=2,ge=1,le=1024)
    max_retained: int = Field(default=10,ge=1,le=10000)
    idle_stop_seconds: int = Field(default=1800,ge=0,le=31536000)
    sweep_seconds: int = Field(default=30,ge=1,le=3600)

    @model_validator(mode='after')
    def check_limits(self):
        if self.max_running>self.max_retained:raise ValueError('Running limit exceeds retained limit')
        return self


class SandboxConfiguration(SandboxSettings):
    image: None = Field(default=None,exclude=True)
    default_profile: ProfileId | None = None
    profiles: tuple[SandboxProfile,...] = ()
    lifecycle: SandboxLifecycleSettings = Field(default_factory=SandboxLifecycleSettings)

    @model_validator(mode='before')
    @classmethod
    def normalize_templates(cls,value):
        if isinstance(value,SandboxSettings):value=value.model_dump(mode='python')
        if not isinstance(value,dict):return value
        raw=dict(value)
        legacy=raw.pop('image',None)
        if legacy is not None:
            if 'profiles' in raw or 'default_profile' in raw:
                raise ValueError('Legacy image cannot be combined with environment profiles')
            raw.update(default_profile='default',profiles=(SandboxProfile(
                id='default',name='默认沙箱',image=legacy),))
        elif isinstance(raw.get('profiles'),dict):
            profiles=[]
            for key,item in raw['profiles'].items():
                if not isinstance(item,dict) or set(item)-{'name','image'}:
                    raise ValueError('Environment profiles accept only name and image')
                profiles.append(SandboxProfile(id=key,**item))
            raw['profiles']=tuple(profiles)
        return raw

    @model_validator(mode='after')
    def check_default(self):
        keys=[profile.id for profile in self.profiles]
        if len(keys)!=len(set(keys)) or (self.profiles and self.default_profile not in keys
                or not self.profiles and self.default_profile is not None):
            raise ValueError('Default environment profile must identify a configured template')
        return self

    @field_serializer('profiles')
    def serialize_profiles(self,profiles):
        return {profile.id:profile.model_dump(exclude={'id'}) for profile in profiles}

    def select(self,profile_id=None):
        key=self.default_profile if profile_id is None else profile_id
        profile=next((profile for profile in self.profiles if profile.id==key),None)
        if profile is None:
            raise SandboxError('env_image_required' if key is None else 'env_profile_unavailable')
        settings=SandboxSettings.model_validate(dict(
            self.model_dump(mode='python',include=set(SandboxSettings.model_fields)),image=profile.image))
        return SandboxProfileSelection.create(profile.id,profile.name,settings)

    def execution_settings(self):
        if self.profiles:return self.select().settings
        return SandboxSettings.model_validate(self.model_dump(mode='python',include=set(SandboxSettings.model_fields)))

    def catalog(self):
        return dict(default_profile=self.default_profile,profiles=[dict(profile_id=p.id,name=p.name,
            revision=self.select(p.id).revision) for p in self.profiles])


def load_sandbox_configuration(path):
    """Explicit refresh reads only sandbox; a missing file is not an empty catalog."""
    try:
        with Path(path).open('rb') as stream:
            raw=tomllib.load(stream)
        return SandboxConfiguration.model_validate(raw.get('sandbox',{}))
    except (OSError,UnicodeError,ValueError) as error:
        logging.getLogger(__name__).warning('sandbox_refresh_settings_invalid error_type=%s',type(error).__name__)
        raise SandboxError('env_configuration_invalid') from None
