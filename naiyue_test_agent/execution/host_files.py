"""Host file provider reuses the existing safe IO and bounded output algorithms."""
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import sys

from naiyue_test_agent.contracts.environment import EnvironmentRef, FileResource
from naiyue_test_agent.execution_roots import ExecutionRoot, ExecutionRootUnavailable, validate_execution_root


@dataclass(frozen=True)
class HostFileSystem:
    execution_root: ExecutionRoot
    project_id: str | None
    owner_session_id: str | None = field(init=False)
    environment: EnvironmentRef = field(init=False)

    def __init__(self, execution_root, project_id, *, session_id=None):
        if not isinstance(execution_root, ExecutionRoot) or execution_root.kind != 'local':
            raise ExecutionRootUnavailable('not_registered')
        if execution_root.binding == 'project_local' and (project_id is None or execution_root.authority is None):
            raise ExecutionRootUnavailable('not_registered')
        platform = 'windows' if sys.platform == 'win32' else 'macos' if sys.platform == 'darwin' else 'linux'
        binding_id = ('host:project:' + project_id if execution_root.binding == 'project_local' else
            'host:local:' + hashlib.sha256(os.path.normcase(str(execution_root.path)).encode('utf-8')).hexdigest())
        object.__setattr__(self, 'execution_root', execution_root)
        object.__setattr__(self, 'project_id', project_id)
        object.__setattr__(self, 'owner_session_id', session_id)
        object.__setattr__(self, 'environment', EnvironmentRef(binding_id, 'host', project_id,
            str(execution_root.path), platform, execution_root.project_revision))

    def validate(self):
        try:
            validate_execution_root(self.execution_root, self.project_id)
        except ExecutionRootUnavailable as error:
            from langchain_core.tools import ToolException
            raise ToolException(str(error)) from error

    def prepare_target(self, path, operation):
        self.validate()
        from naiyue_test_agent.tools.paths import prepare_file_subject
        return prepare_file_subject(path, self.execution_root.path, operation)

    def inspect_candidate(self, path, operation):
        # This is part of an already admitted list/search, not a new execution.
        from naiyue_test_agent.tools.paths import prepare_file_subject
        return prepare_file_subject(path, self.execution_root.path, operation)

    def verify_target(self, subject):
        self.validate()
        from naiyue_test_agent.tools.paths import verify_file_target
        return str(verify_file_target(subject))

    def read(self, subject, offset, limit, *, settings, control, progress):
        from naiyue_test_agent.tools.read import _read
        return _read(Path(self.verify_target(subject)), offset, limit,
                     settings=settings, control=control, progress=progress)

    def write(self, subject, content, *, settings, control, progress, read_allowed):
        from naiyue_test_agent.tools.write import _write
        target = self.verify_target(subject)
        artifact = {}
        text = _write(self.execution_root.path, target, content, settings.max_input_bytes,
            subject=subject, control=control, progress=progress, feedback_artifact=artifact,
            feedback_settings=settings, feedback_read_allowed=read_allowed, feedback_path=subject['args']['path'])
        return text, artifact

    def edit(self, subject, edits, *, settings, control, progress):
        from naiyue_test_agent.tools.edit import _edit
        return _edit(self.execution_root.path, self.verify_target(subject), edits, settings,
                     subject=subject, control=control, progress=progress)

    def list(self, subject, limit, access, *, settings, progress):
        from naiyue_test_agent.tools.ls import _list_directory
        return _list_directory(Path(self.verify_target(subject)), limit, access, settings=settings, progress=progress)

    async def find(self, subject, pattern, limit, access, *, settings, process_settings, progress, timeout):
        from naiyue_test_agent.tools.find import _find_files, HostProcessRunner
        return await _find_files(Path(self.verify_target(subject)), pattern, limit, access,
            settings=settings, runner=HostProcessRunner(process_settings), progress=progress, timeout=timeout)

    async def grep(self, subject, pattern, glob, ignore_case, literal, context_lines, limit, access,
                   *, settings, process_settings, progress, timeout):
        from naiyue_test_agent.tools.grep import _grep_files, HostProcessRunner
        return await _grep_files(Path(self.verify_target(subject)), pattern, glob, ignore_case,
            literal, context_lines, limit, access, settings=settings,
            runner=HostProcessRunner(process_settings), progress=progress, timeout=timeout)

    def resource_info(self, path):
        target = Path(path)
        # lstat preserves an unreadable target as an error, rather than missing.
        try:
            target.lstat()
        except (FileNotFoundError, NotADirectoryError):
            return FileResource(False, False, 'missing')
        link = target.is_symlink()
        return FileResource(target.exists(), link,
            'file' if target.is_file() else 'directory' if target.is_dir() else 'other')

    def resolve_resource(self, path):
        return str(Path(path).resolve(strict=True))

    def list_resources(self, path):
        return tuple(str(item) for item in Path(path).iterdir())

    def read_resource(self, path, max_bytes):
        with Path(path).open('rb') as source:
            return source.read(max_bytes + 1)
