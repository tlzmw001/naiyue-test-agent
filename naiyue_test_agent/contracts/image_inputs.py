"""Validated upload envelope and safe public image errors."""
from pydantic import BaseModel, ConfigDict, Field


class ImageUpload(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)
    client_id: str = Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_-]+$')
    name: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(pattern=r'^image/(png|jpeg|webp|gif|bmp)$')
    base64: str = Field(min_length=1, repr=False)


class ImageInputError(ValueError):
    def __init__(self, code, *, status=None):
        self.code, self.status = code, status if status is not None else 413 if code == 'image_input_too_large' else 422
        super().__init__(code)
