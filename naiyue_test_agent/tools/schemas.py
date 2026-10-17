"""Derive tool schemas without exposing the trusted runtime to the model."""

from typing import Annotated, Any

from langchain.tools import ToolRuntime
from pydantic import BaseModel, ConfigDict, Field, create_model


ExecutionTimeout = Annotated[int | None, Field(ge=0, description=(
    'Optional execution timeout in seconds. Omit, null, or 0 for no time limit; '
    'a positive integer limits this execution.'))]


def build_tool_schemas(
    name: str, base: type[BaseModel], **field_definitions: Any,
) -> tuple[type[BaseModel], type[BaseModel]]:
    public = create_model(name, __base__=base, **field_definitions)
    execution = create_model('Session' + name, __base__=public,
        __config__=ConfigDict(arbitrary_types_allowed=True),
        runtime=(ToolRuntime[dict, dict], ...))
    return public, execution
