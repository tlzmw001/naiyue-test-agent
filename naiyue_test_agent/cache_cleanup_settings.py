from pydantic import BaseModel, ConfigDict, Field


class CacheCleanupSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)

    tool_output_retention_days: int = Field(default=7, gt=0)
    cloud_input_retention_days: int = Field(default=3, gt=0)
    max_scan_entries: int = Field(default=100000, gt=0)
    max_operation_seconds: float = Field(default=60, gt=0, allow_inf_nan=False)
