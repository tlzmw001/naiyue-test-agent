"""Default user paths shared by model, tool, and application configuration."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator


def get_agent_dir() -> Path:
    return Path.home() / '.naiyue-test-agent'


def get_models_path() -> Path:
    return get_agent_dir() / 'models.json'


class StorageSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)

    data_dir: Path | None = None

    @field_validator('data_dir', mode='before')
    @classmethod
    def absolute_data_directory(cls, value):
        if value is None:
            return None
        if not isinstance(value, (str, Path)) or not str(value).strip() or '\0' in str(value):
            raise ValueError('Invalid storage data directory')
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError('Storage data directory must be absolute')
        return path


def get_data_dir(settings: StorageSettings) -> Path:
    return settings.data_dir if settings.data_dir is not None else get_agent_dir() / 'data'
