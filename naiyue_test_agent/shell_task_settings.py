"""Application policy for Bash and PowerShell execution deadlines."""

from pydantic import BaseModel, ConfigDict, Field


class ShellTaskSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)

    max_execution_seconds: int = Field(default=0, ge=0)
    foreground_wait_seconds: int = Field(default=120, gt=0)
    max_background_archive_bytes: int = Field(default=67108864, gt=0)
    max_notice_items: int = Field(default=20, gt=0)
    max_notice_bytes: int = Field(default=8192, gt=0)
