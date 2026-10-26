"""Host-only delivery budgets; no credentials or container configuration."""
from pydantic import BaseModel,ConfigDict,Field


class DeliverySettings(BaseModel):
    model_config=ConfigDict(strict=True,extra='forbid',frozen=True)
    max_commits:int=Field(default=256,ge=1,le=10000)
    max_operations_per_project:int=Field(default=1024,ge=1,le=100000)
