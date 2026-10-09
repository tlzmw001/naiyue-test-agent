"""Opt-in, approved local Bash execution without filesystem or network isolation."""

import logging
import os
import signal
import subprocess
import sys
from pathlib import Path
from threading import Thread
from time import monotonic

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import ToolException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from naiyue_test_agent.agent_runtime.tool_approval import (
    ApprovalPolicy, ApprovalReview, OneUseApprovalGrants, attach_approval,
)

from .bash_output import ArchiveResult, BashOutputArchive
from ..run_control import current_run_control


logger = logging.getLogger(__name__)
_DEFAULT_TIMEOUT = 60
_MAX_TIMEOUT = 300
_OUTPUT_BYTES = 4096


class BashOutcomeUnknown(RuntimeError):
    """A terminated or lost command may have already changed external state."""

    def __init__(self, message: str, *, partial_stdout: str = '',
                 partial_stderr: str = '', output_truncated: bool = False,
                 output_paths: dict[str, str] | None = None,
                 archive_complete: bool = False, archive_reason: str | None = None):
        super().__init__(message)
        self.partial_stdout = partial_stdout
        self.partial_stderr = partial_stderr
        self.output_truncated = output_truncated
        self.output_paths = output_paths or {}
        self.archive_complete = archive_complete
        self.archive_reason = archive_reason


BashApprovalGrants = OneUseApprovalGrants


class BashInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')

    command: str = Field(min_length=1, max_length=8192)
    timeout: int | None = Field(default=None, gt=0, le=_MAX_TIMEOUT)


class SessionBashInput(BashInput):
    model_config = ConfigDict(strict=True, extra='forbid', arbitrary_types_allowed=True)

    runtime: ToolRuntime[dict, dict]


def bash_args_valid(args: object) -> bool:
    if not isinstance(args, dict):
        return False
    try:
        BashInput.model_validate(args)
    except ValidationError:
        return False
    return True


def bash_requires_approval(request) -> bool:
    """Pause valid commands; let ToolNode return invalid-argument errors to the model."""
    return bash_args_valid(request.tool_call.get('args'))


def _review_bash(root: Path, args: dict) -> ApprovalReview | None:
    if not bash_args_valid(args):
        return None
    command = args['command']
    return ApprovalReview(target=command, grant_value=(command, root), workspace=root)


def _capture(source, buffer: bytearray, truncated: list[bool], errors: list[str],
             archive: BashOutputArchive, stream: str) -> None:
    try:
        while chunk := source.read(8192):
            archive.record(stream, chunk)
            buffer.extend(chunk)
            if len(buffer) > _OUTPUT_BYTES:
                del buffer[:-_OUTPUT_BYTES]
                truncated[0] = True
    except OSError as error:
        errors.append(type(error).__name__)
        logger.warning('bash_output_read_failed error_type=%s', type(error).__name__)
    finally:
        try:
            source.close()
        except OSError as error:
            errors.append(type(error).__name__)
            logger.warning('bash_output_close_failed error_type=%s', type(error).__name__)


def _kill_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    if process.poll() is None:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.error('bash_reap_timed_out')


def _decode_output_tail(buffer: bytearray, truncated: bool) -> str:
    data = bytes(buffer)
    if truncated:
        while data and data[0] & 0xc0 == 0x80:
            data = data[1:]
    return data.decode('utf-8', errors='replace')


def _unknown_with_output(message: str, stdout: bytearray, stderr: bytearray,
                         out_truncated: list[bool],
                         err_truncated: list[bool],
                         archived: ArchiveResult) -> BashOutcomeUnknown:
    return BashOutcomeUnknown(
        message,
        partial_stdout=_decode_output_tail(stdout, out_truncated[0]),
        partial_stderr=_decode_output_tail(stderr, err_truncated[0]),
        output_truncated=out_truncated[0] or err_truncated[0],
        output_paths=archived.paths,
        archive_complete=archived.complete,
        archive_reason=archived.reason,
    )


def _run_host_command(command: str, root: Path, timeout: int) -> tuple[int, str, str, bool, ArchiveResult]:
    try:
        archive = BashOutputArchive(root)
    except ValueError as error:
        logger.warning('bash_archive_config_invalid error_type=%s', type(error).__name__)
        raise ToolException('Bash output limit configuration is invalid.') from error
    shell = os.environ.get('NAIYUE_BASH_SHELL', '/bin/bash')
    env = {
        'PATH': f'{Path(sys.executable).parent}:/usr/bin:/bin:/usr/sbin:/sbin',
        'HOME': str(root), 'TMPDIR': str(root), 'LANG': 'en_US.UTF-8',
    }
    control = current_run_control()
    if control is not None and control.requested:
        raise ToolException('Bash was not started because the user stopped this run.')
    try:
        process = subprocess.Popen(
            [shell, '-c', command], cwd=root, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        )
    except OSError as error:
        logger.warning('bash_start_failed error_type=%s', type(error).__name__)
        raise ToolException('Bash could not start. Check the configured shell.') from error

    stdout = bytearray()
    stderr = bytearray()
    out_truncated = [False]
    err_truncated = [False]
    capture_errors: list[str] = []
    readers = [
        Thread(target=_capture, args=(process.stdout, stdout, out_truncated,
                                      capture_errors, archive, 'stdout'), daemon=True),
        Thread(target=_capture, args=(process.stderr, stderr, err_truncated,
                                      capture_errors, archive, 'stderr'), daemon=True),
    ]
    for reader in readers:
        reader.start()
    cancelled = False
    try:
        if control is None:
            exit_code = process.wait(timeout=timeout)
        else:
            deadline = monotonic() + timeout
            while process.poll() is None:
                if control.requested:
                    cancelled = True
                    _kill_group(process)
                    break
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(process.args, timeout)
                control.signal.wait(min(0.1, remaining))
            exit_code = process.returncode
        for reader in readers:
            reader.join(timeout=2)
        if any(reader.is_alive() for reader in readers):
            _kill_group(process)
            for reader in readers:
                reader.join(timeout=2)
            archive.mark_incomplete('capture_incomplete')
            raise _unknown_with_output(
                'Bash output streams did not close; inspect side effects.',
                stdout, stderr, out_truncated, err_truncated, archive.finish(),
            )
        if capture_errors:
            archive.mark_incomplete('capture_incomplete')
            raise _unknown_with_output(
                'Bash output was incomplete; inspect side effects.',
                stdout, stderr, out_truncated, err_truncated, archive.finish(),
            )
    except subprocess.TimeoutExpired as error:
        _kill_group(process)
        for reader in readers:
            reader.join(timeout=2)
        if any(reader.is_alive() for reader in readers) or capture_errors:
            archive.mark_incomplete('capture_incomplete')
        raise _unknown_with_output(
            'Bash timed out; inspect side effects before retrying.',
            stdout, stderr, out_truncated, err_truncated, archive.finish(),
        ) from error
    if cancelled:
        raise _unknown_with_output(
            'Bash was stopped by the user; inspect side effects before retrying.',
            stdout, stderr, out_truncated, err_truncated, archive.finish(),
        )
    return (exit_code, _decode_output_tail(stdout, out_truncated[0]),
            _decode_output_tail(stderr, err_truncated[0]),
            out_truncated[0] or err_truncated[0], archive.finish())


def _archive_feedback(archived: ArchiveResult) -> str:
    if not archived.paths and archived.reason is None:
        return ''
    fields = [f'archive_complete={str(archived.complete).lower()}']
    fields.extend(f'{stream}_path={path}' for stream, path in archived.paths.items())
    if archived.reason is not None:
        fields.append(f'archive_reason={archived.reason}')
    return '\n' + '; '.join(fields)


def create_session_bash_tool():
    """Create a host-shell tool that requires an exact application approval grant."""

    @tool('bash', args_schema=SessionBashInput)
    def bash(command: str, runtime: ToolRuntime[dict, dict],
             timeout: int | None = None) -> str:
        """Run an approved local Bash command with current user access.

        The selected workspace is only the working directory, not a sandbox.
        Each command needs approval. Timeout defaults to 60 seconds and is at
        most 300 seconds. Only the last 4096 bytes of each output stream are
        returned. Longer output is saved under naiyue-agent-output/ in the
        workspace when possible; use read with the returned relative path to
        page through UTF-8 text. Archive limits are configurable; incomplete
        archives are marked explicitly.
        """
        context = runtime.context
        root = context.get('workspace_root') if isinstance(context, dict) else None
        grants = context.get('approval_grants', {}).get('bash') if isinstance(context, dict) else None
        if not isinstance(root, Path) or not root.is_absolute() or not root.is_dir():
            raise ToolException('Bash has no valid session workspace.')
        if not isinstance(grants, BashApprovalGrants) or not grants.consume(
            runtime.tool_call_id, command, root,
        ):
            raise ToolException('Bash command was not approved for this tool call.')
        started = monotonic()
        try:
            exit_code, stdout, stderr, truncated, archived = _run_host_command(
                command, root, timeout or _DEFAULT_TIMEOUT,
            )
        except BashOutcomeUnknown:
            logger.error('bash_outcome_unknown', extra={'bash_event': {
                'event': 'bash_outcome_unknown', 'run_id': context.get('run_id'),
                'tool_call_id': runtime.tool_call_id,
            }})
            raise
        logger.info('bash_completed', extra={'bash_event': {
            'event': 'bash_completed', 'run_id': context.get('run_id'),
            'tool_call_id': runtime.tool_call_id, 'exit_code': exit_code,
            'truncated': truncated, 'elapsed_ms': int((monotonic() - started) * 1000),
            'archive_complete': archived.complete if truncated else None,
        }})
        result = (f'exit_code={exit_code}; truncated={str(truncated).lower()}\n'
                  f'stdout:\n{stdout}\nstderr:\n{stderr}'
                  f'{_archive_feedback(archived)}')
        if exit_code != 0:
            raise ToolException(result)
        return result

    bash.handle_validation_error = ('Invalid bash arguments. Provide a non-empty command '
                                    'and optional timeout from 1 to 300 seconds.')
    bash.handle_tool_error = lambda error: str(error)
    bash.metadata = {'naiyue_execution_mode': 'sequential'}
    return attach_approval(bash, ApprovalPolicy(
        when=bash_requires_approval,
        description='Run a local command with current user access',
        review=_review_bash,
        grant_factory=BashApprovalGrants,
    ))
