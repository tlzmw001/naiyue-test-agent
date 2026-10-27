"""Serializable environment facts and typed file capability; no provider clients."""
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from .run_context import RuntimeContext
    from .process import ProcessPort
    from .run_ports import TaskQueries


class ResourceBoundaryError(ValueError):
    """A resource is outside its execution root; environment health is separate."""


@dataclass(frozen=True)
class EnvironmentRef:
    binding_id: str
    kind: Literal['host', 'sandbox']
    project_id: str | None
    root: str
    platform: Literal['windows', 'macos', 'linux']
    project_revision: int | None = None

    def __post_init__(self):
        if (not isinstance(self.binding_id, str) or not self.binding_id
                or self.kind not in ('host', 'sandbox') or self.platform not in ('windows', 'macos', 'linux')
                or not isinstance(self.root, str) or not self.root or '\0' in self.root
                or self.project_id is not None and (not isinstance(self.project_id, str) or not self.project_id)
                or self.project_revision is not None and (type(self.project_revision) is not int or self.project_revision < 1)):
            raise ValueError('Invalid execution environment reference')
        path = self.path(self.root)
        if not path.is_absolute() or '..' in path.parts:
            raise ValueError('Invalid execution environment root')

    def public(self):
        return asdict(self)

    def path(self, value):
        return (PureWindowsPath if self.platform == 'windows' else PurePosixPath)(value)


@dataclass(frozen=True)
class FileResource:
    exists: bool
    is_link: bool
    kind: Literal['file', 'directory', 'missing', 'other']


@dataclass(frozen=True)
class PreparedExecution:
    environment: EnvironmentRef | None = None
    files: 'FileSystemPort | None' = None
    processes: 'ProcessPort | None' = None
    task_queries: 'TaskQueries | None' = None
    project_revision: int | None = None


class FileAccess(Protocol):
    context: 'RuntimeContext'
    tool_call_id: str | None


class FileSystemPort(Protocol):
    environment: EnvironmentRef
    owner_session_id: str | None

    def validate(self) -> None: ...
    def prepare_target(self, path: str, operation: str) -> dict: ...
    def inspect_candidate(self, path: str, operation: str) -> dict: ...
    def verify_target(self, subject: dict) -> str: ...
    def read(self, subject: dict, offset, limit, *, settings, control, progress): ...
    def write(self, subject: dict, content: str, *, settings, control, progress, read_allowed: bool): ...
    def edit(self, subject: dict, edits, *, settings, control, progress): ...
    def list(self, subject: dict, limit: int, access: FileAccess, *, settings, progress): ...
    async def find(self, subject: dict, pattern: str, limit: int, access: FileAccess,
                   *, settings, process_settings, progress, timeout): ...
    async def grep(self, subject: dict, pattern: str, glob, ignore_case: bool, literal: bool,
                   context_lines: int, limit: int, access: FileAccess,
                   *, settings, process_settings, progress, timeout): ...
    def resource_info(self, path: str) -> FileResource: ...
    def resolve_resource(self, path: str) -> str: ...
    def list_resources(self, path: str) -> tuple[str, ...]: ...
    def read_resource(self, path: str, max_bytes: int) -> bytes: ...
