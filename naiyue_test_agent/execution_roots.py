"""Project identity and frozen execution location; neither implies tool permission."""

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Literal


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Project:
    project_id: str
    canonical_root: Path
    database_path: Path


@dataclass(frozen=True)
class ExecutionRoot:
    path: Path
    kind: Literal['local', 'worktree'] = 'local'
    branch: str | None = None

    def __post_init__(self):
        if not isinstance(self.path, Path) or not self.path.is_absolute() or '..' in self.path.parts or '\0' in str(self.path):
            raise ValueError('Execution path must be canonical and absolute')
        if self.kind not in ('local', 'worktree'):
            raise ValueError('Invalid execution root kind')


class ExecutionRootUnavailable(RuntimeError):
    code = 'execution_root_unavailable'

    def __init__(self, reason):
        self.reason = reason
        super().__init__(f'{self.code}: {reason}')

    def public(self):
        return {'code': self.code, 'reason': self.reason}


def validate_execution_root(root, project_id=None):
    """Only entry admission checks; history and already-running tasks do not call this."""
    if root is None:
        return
    try:
        root.path.stat()
    except FileNotFoundError as error:
        raise ExecutionRootUnavailable('missing') from error
    except NotADirectoryError as error:
        raise ExecutionRootUnavailable('not_directory') from error
    except OSError as error:
        logger.error('execution_root_check_failed error_type=%s', type(error).__name__)
        raise ExecutionRootUnavailable('wrong_project') from error
    if not root.path.is_dir():
        raise ExecutionRootUnavailable('not_directory')
    # Managed worktree registration is implemented by the later worktree feature.
    if root.kind == 'worktree':
        raise ExecutionRootUnavailable('not_registered')
    if project_id is not None:
        from .workspaces import _read_marker
        try:
            identity = _read_marker(root.path)
        except (OSError, ValueError) as error:
            logger.warning('execution_identity_unavailable error_type=%s', type(error).__name__)
            raise ExecutionRootUnavailable('wrong_project') from error
        if identity != project_id:
            raise ExecutionRootUnavailable('wrong_project')


def execution_error(root, project_id=None):
    try:
        validate_execution_root(root, project_id)
    except ExecutionRootUnavailable as error:
        return error.public()
    return None
