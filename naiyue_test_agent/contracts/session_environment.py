"""Persisted sandbox creation facts; no live clients or filesystem paths."""
import json
from pydantic import BaseModel,ConfigDict,Field
from typing import Annotated

Ident = Annotated[str,Field(pattern='^[0-9a-f]{32}$')]


class SandboxSessionSetup(BaseModel):
    model_config=ConfigDict(strict=True,extra='forbid',frozen=True)
    model_id: str = Field(min_length=1,max_length=128)
    include_untracked: list[str] = Field(max_length=100000)
    input_id: Ident
    binding_id: Ident

    def json_value(self):
        return json.dumps(self.model_dump(mode='json'),sort_keys=True)


def saved_setup(row):
    return SandboxSessionSetup.model_validate_json(row['execution_setup_json'])
