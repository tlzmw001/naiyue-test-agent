"""Reuse edit/feedback algorithms in protected staging, then commit by directory FD."""
from contextlib import ExitStack
import hashlib
import logging
import os
from pathlib import Path
import stat
import tempfile

from langchain_core.tools import ToolException
from naiyue_test_agent.tools.control import check_tool_stop
from naiyue_test_agent.tools.paths import prepare_file_subject
from naiyue_test_agent.tools.write import _write, _reserve_target, WriteOutcomeUnknown, WRITE_CHANGED
from naiyue_test_agent.tools.edit import _edit
from naiyue_test_agent.execution.git_snapshot import file_identity

logger = logging.getLogger(__name__)


def copy_source(fd, output, control):
    before = file_identity(os.fstat(fd))
    digest = hashlib.sha256()
    with os.fdopen(os.dup(fd),'rb') as source:
        while chunk := source.read(65536):
            check_tool_stop(control, 'File operation stopped before commit.')
            digest.update(chunk)
            if output is not None:
                output.write(chunk)
    if file_identity(os.fstat(fd)) != before:
        raise ToolException(WRITE_CHANGED)
    return before, digest.digest()


def mutate(files, subject, *, content=None, edits=None, settings, control, progress, read_allowed=True):
    files.check_subject(subject)
    workspace = files.workspace
    owner = files.owner
    stage_root = workspace.physical.parent/'.envd-stage'
    stage_root.mkdir(mode=0o700, exist_ok=True)
    info = stage_root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ToolException('Protected file staging is unavailable.')
    if stage_root.stat().st_dev != workspace.identity.st_dev:
        raise ToolException('File staging must share the workspace filesystem.')
    with _reserve_target(Path(subject['resolved'])), ExitStack() as stack:
        check_tool_stop(control, 'File operation stopped before commit.')
        target = stack.enter_context(workspace.open(subject['requested'], missing=True))
        workspace.check(target,subject)
        if target.info is None and edits is not None:
            raise ToolException('Edit target is missing.')
        directory = Path(stack.enter_context(tempfile.TemporaryDirectory(dir=stage_root)))
        staged = directory/target.relative
        staged.parent.mkdir(parents=True,exist_ok=True)
        original = None
        if target.fd is not None:
            with staged.open('wb') as output:
                original = copy_source(target.fd,output,control)
            staged.chmod(stat.S_IMODE(target.info.st_mode) & 0o777)
        prepared = prepare_file_subject(str(staged),directory,'write')
        prepared.update(args=subject.get('args',{'path':target.relative}),path_field='path',
                        tool_name='edit' if edits is not None else 'write')
        if edits is not None:
            result = _edit(directory,str(staged),edits,settings,subject=prepared,control=control,progress=progress)
        else:
            artifact = {}
            text = _write(directory,str(staged),content,settings.max_input_bytes,subject=prepared,
                control=control,progress=progress,feedback_artifact=artifact,feedback_settings=settings,
                feedback_read_allowed=read_allowed,feedback_path=subject['args']['path'])
            result = text,artifact
        # Parent creation is part of the mutation; it is not performed during approval.
        current = stack.enter_context(workspace.open(subject['requested'],missing=True,create_parents=True,owner=owner))
        workspace.check(current,subject)
        if original is not None:
            if current.fd is None or copy_source(current.fd,None,control) != original:
                raise ToolException(WRITE_CHANGED)
        elif current.fd is not None:
            raise ToolException(WRITE_CHANGED)
        with staged.open('rb') as output:
            if owner is not None:
                os.fchown(output.fileno(),*owner)
            os.fsync(output.fileno())
        check_tool_stop(control, 'File operation stopped before commit.')
        source_parent = os.open(staged.parent,os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.replace(staged.name,current.name,src_dir_fd=source_parent,dst_dir_fd=current.parent)
            os.fsync(current.parent)
        except OSError as error:
            logger.error('envd_file_commit_unknown error_type=%s',type(error).__name__)
            raise WriteOutcomeUnknown('File write outcome is unknown; inspect before another write.') from error
        finally:
            os.close(source_parent)
        return result
