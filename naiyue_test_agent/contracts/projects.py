"""Project registry facts; no runtime objects or filesystem permission grants."""
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


class ProjectError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class LocalSource:
    path: Path


@dataclass(frozen=True)
class GitSource:
    url: str
    ref: str


@dataclass(frozen=True)
class RegisteredProject:
    project_id: str
    name: str
    source: LocalSource | GitSource
    database_path: Path
    lifecycle: Literal['pending', 'active', 'migrating', 'deleting'] = 'pending'
    trusted: bool = False
    availability: str | None = None
    nested_project_ids: tuple[str, ...] = ()
    revision: int = 1


@dataclass(frozen=True)
class ProjectExecutionFacts:
    project_id: str
    lifecycle: str
    trusted: bool
    source_kind: str
    root: Path | None
    path_key: str | None
    revision: int


@dataclass(frozen=True)
class ProjectOperation:
    operation_id: str
    action: str
    request: dict
    project_id: str
    stage: str
    facts: dict
    error_code: str | None
    result: dict | None
