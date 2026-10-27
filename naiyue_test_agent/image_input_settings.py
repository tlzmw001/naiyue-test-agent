"""Central limits for user supplied images; Read retains its own budgets."""
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ImageInputSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)
    max_images_per_message: int = Field(default=4, gt=0)
    max_input_bytes: int = Field(default=10*1024*1024, gt=0)
    max_total_input_bytes: int = Field(default=20*1024*1024, gt=0)
    max_pixels: int = Field(default=40_000_000, gt=0)
    max_dimension: int = Field(default=4096, gt=0)
    max_base64_bytes: int = Field(default=4_718_592, gt=0)
    max_total_base64_bytes: int = Field(default=12*1024*1024, gt=0)
    max_request_bytes: int = Field(default=28*1024*1024, gt=0)
    max_in_flight_requests: int = Field(default=2, gt=0)
    max_pending_image_messages: int = Field(default=16, gt=0)
    max_pending_image_bytes: int = Field(default=32*1024*1024, gt=0)

    @model_validator(mode='after')
    def budgets(self):
        if self.max_input_bytes > self.max_total_input_bytes:
            raise ValueError('Single image input exceeds combined budget')
        if self.max_request_bytes < 4*((self.max_total_input_bytes+2)//3)+32768*4+4096:
            raise ValueError('Image request budget cannot contain input and bounded metadata')
        return self
