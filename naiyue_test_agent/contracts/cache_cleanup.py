from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

PATH = '/api/v1/maintenance/cache-cleanup'
CATEGORIES = ('tool_outputs', 'cloud_inputs')
SCOPE = 'registered_projects_and_persistent_chats'


class CleanupRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    request_id: str
    categories: list[Literal['tool_outputs', 'cloud_inputs']] = Field(min_length=1, max_length=2)

    @field_validator('request_id')
    @classmethod
    def canonical_id(cls, value):
        if str(UUID(value)) != value:
            raise ValueError('invalid request ID')
        return value

    @field_validator('categories')
    @classmethod
    def category_order(cls, values):
        if values != [category for category in CATEGORIES if category in values]:
            raise ValueError('categories must be unique and ordered')
        return values
