"""An optional default location, never a filesystem authorization boundary."""
from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator


class ProjectsSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)
    default_parent_dir: Path | None = None

    @field_validator('default_parent_dir', mode='before')
    @classmethod
    def absolute_parent(cls, value):
        if value is None:
            return None
        if not isinstance(value, (str, Path)) or not str(value).strip() or '\0' in str(value):
            raise ValueError('Invalid default project parent directory')
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError('Default project parent directory must be absolute')
        return path
