"""Shared process-tool output ownership for Bash, PowerShell and direct CLI calls."""

import asyncio
import json
import logging
from dataclasses import dataclass
from contextvars import Context
from threading import RLock
from time import monotonic

from langchain_core.tools import ToolException

from .output import ShellOutputArchive, require_archive_root
from .output_text import OutputAccumulator
from .processes import ProcessCleanupError
from .progress import ToolProgress

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ForegroundResult:
    outcome: object
    capture: object
    archived: object
    stop_reason: str | None = None


def format_process_outcome(outcome, output, archived, *, label, stop_reason=None):
    text = (f'exit_code={outcome.returncode}; truncated={str(output.truncated).lower()}; '
            f'output_complete={str(outcome.output_complete).lower()}; lossy={str(output.lossy).lower()}\n'
            f'observed_lines={output.observed_lines}; observed_text_bytes={output.observed_bytes}; '
            f'returned_lines={output.start_line}-{output.observed_lines}; '
            f'partial_line={str(output.partial_line).lower()}; '
            f'truncation_reason={",".join(sorted(output.reasons)) or "none"}\n'
            f'output:\n{output.text}')
    if archived.paths or archived.reason is not None:
        text += (f'\narchive_complete={str(archived.complete).lower()}; '
                 f'archive_saved_bytes={archived.saved_bytes}')
        text += ''.join(f'; {kind}_path={path}' for kind, path in archived.paths.items())
        if archived.reason:
            text += f'; archive_reason={archived.reason}; saved prefix and returned tail may have a gap'
        readable = archived.paths.get('text') or (archived.paths.get('raw')
            if not output.transformed and archived.complete else None)
        if archived.reason in ('write_error', 'read_error'):
            readable = None
        if readable:
            text += f'\nRead text with read(path="{readable}", offset=1).'
        else:
            text += '\nRaw archive is diagnostic bytes; no complete UTF-8 text view is available.'
    if outcome.termination != 'exited':
        reason = 'timed out' if outcome.termination == 'timeout' else 'was stopped by the user'
        if outcome.termination == 'user_stop' and stop_reason in ('output_limit', 'archive_error'):
            reason = ('was stopped at the background archive limit' if stop_reason == 'output_limit'
                      else 'was stopped because its background archive could not be written')
        raise ToolException(f'{label} {reason}. Inspect side effects before retrying; '
                            f'the command was not automatically retried.\n{text}')
    if outcome.returncode != 0:
        raise ToolException(text)
    return text


class ProcessCapture:
    """Own bounded text and file writers, without a run's runtime or publisher."""

    def __init__(self, root, settings, *, background=False):
        self.output = OutputAccumulator(settings)
        self.archive = ShellOutputArchive(root, settings)
        self.background = background
        self.closed = False
        self.observed_raw_bytes = 0
        self._lock = RLock()

    def record(self, chunk):
        with self._lock:
            self.observed_raw_bytes += len(chunk)
            self.output.feed(chunk)
            self.archive.record(chunk, activate=self.background or self.output.truncated or self.output.transformed)
            if self.background:
                return self.archive.blocking_reason or ('command_limit' if self.archive.limit_reached else None)

    def promote(self, limit):
        with self._lock:
            self.background = True
            return self.archive.promote(limit)

    def finish(self, incomplete=False):
        with self._lock:
            self.output.finish()
            self.archive.activate(self.background or self.output.truncated or self.output.transformed)
            if incomplete:
                self.archive.mark_incomplete('capture_incomplete')
            saved = self.archive.finish(transformed=self.output.transformed)
            self.closed = self.archive.writers_closed
            return saved

    def snapshot(self):
        with self._lock:
            saved = self.archive.snapshot()
            return {'output': self.output.text, 'truncated': self.output.truncated,
                'observed_raw_bytes': self.observed_raw_bytes,
                'lossy': self.output.lossy, 'observed_text_bytes': self.output.observed_bytes,
                'archive_paths': saved.paths, 'archive_saved_bytes': saved.saved_bytes,
                'archive_complete': self.closed and saved.complete, 'archive_reason': saved.reason}

    async def finish_owned(self, incomplete=False):
        finishing = asyncio.create_task(asyncio.to_thread(self.finish, incomplete))
        cancellation = None
        while True:
            try:
                archived = await asyncio.shield(finishing)
                break
            except asyncio.CancelledError as signal:
                cancellation = cancellation or signal
        if cancellation is not None:
            raise cancellation
        return archived

    def defer_finish(self, consumer):
        async def deferred():
            try:
                await self.finish_owned(True)
            except Exception as failure:
                logger.error('shell_deferred_archive_failed error_type=%s', type(failure).__name__)
        consumer.add_done_callback(lambda _task: asyncio.create_task(deferred(), context=Context()), context=Context())


async def execute_process_request(request, runtime, *, name, label, settings, runner, progress_settings):
    capture = ProcessCapture(require_archive_root(runtime.context.scope.archive_root, runtime.context.scope.identity.execution_path), settings)
    output = capture.output
    progress = ToolProgress(runtime, progress_settings)
    observed_bytes = 0
    started = monotonic()
    async def consume(_stream, chunk):
        nonlocal observed_bytes
        await asyncio.to_thread(capture.record, chunk)
        observed_bytes += len(chunk)
        progress.report(phase='process_output', completed=observed_bytes, unit='bytes', text=output.text)
    try:
        progress.report(phase='process_starting', activity='preparing', completed=0, unit='bytes')
        outcome = await runner.run(request, consume, runtime.context.scope.control)
    except BaseException as error:
        if isinstance(error, ProcessCleanupError) and error.consumer_task is not None:
            # Closing under an unsettled writer would break resource ownership.
            capture.defer_finish(error.consumer_task)
        else:
            try:
                await capture.finish_owned(True)
            except asyncio.CancelledError:
                if isinstance(error, (ProcessCleanupError, asyncio.CancelledError)):
                    raise error
                raise
        progress.report(phase='capture_cleanup', activity='settling')
        progress.flush()
        if isinstance(error, OSError):
            logger.warning('shell_process_failed shell=%s error_type=%s', name, type(error).__name__)
            raise ToolException(f'{label} could not start or capture output. Check the configured shell; inspect any side effects before retrying.') from error
        raise
    archived = await capture.finish_owned(not outcome.output_complete)
    progress.report(phase='capture_cleanup', activity='settling', completed=observed_bytes, unit='bytes', text=output.text)
    log_process_completed(outcome, capture, archived, runtime, name, started)
    try:
        return format_process_outcome(outcome, output, archived, label=label)
    finally:
        progress.flush()


def log_process_completed(outcome, capture, archived, runtime, name, started):
    logger.info('shell_completed', extra={'shell_event': {
        'event': 'shell_completed', 'shell': name, 'run_id': runtime.context.scope.identity.run_id,
        'tool_call_id': runtime.tool_call_id, 'exit_code': outcome.returncode,
        'termination': outcome.termination, 'truncated': capture.output.truncated,
        'output_complete': outcome.output_complete, 'archive_complete': archived.complete,
        'elapsed_ms': int((monotonic() - started) * 1000),
    }})


async def execute_managed_foreground(port, scope, request, runtime, *, command, name,
                                     label, settings, runner, task_settings, progress_settings):
    progress = ToolProgress(runtime, progress_settings)
    started = monotonic()
    def report(count, text):
        progress.report(phase='process_output', completed=count, unit='bytes', text=text)
    progress.report(phase='process_starting', activity='preparing', completed=0, unit='bytes')
    try:
        result = await port.foreground(**scope, run_id=runtime.context.scope.identity.run_id,
            tool_call_id=runtime.tool_call_id, agent_path=runtime.context.scope.identity.agent_path,
            command=command, name=name, request=request, settings=settings, runner=runner,
            control=runtime.context.scope.control, wait_seconds=task_settings.foreground_wait_seconds,
            background_limit=task_settings.max_background_archive_bytes, on_progress=report)
        if isinstance(result, dict):
            # Handoff has already committed. Never deliver to this run again.
            return json.dumps({**result, 'query': 'Use task_list/task_output; task_stop stops this task. '
                'running confirms process creation, not service readiness.'}, ensure_ascii=False)
        log_process_completed(result.outcome, result.capture, result.archived, runtime, name, started)
        progress.report(phase='capture_cleanup', activity='settling',
            completed=result.capture.observed_raw_bytes, unit='bytes', text=result.capture.output.text)
        progress.flush()
        return format_process_outcome(result.outcome, result.capture.output, result.archived,
            label=label, stop_reason=result.stop_reason)
    except OSError as error:
        logger.warning('shell_process_failed shell=%s error_type=%s', name, type(error).__name__)
        raise ToolException(f'{label} could not start or capture output. Check the configured shell; inspect any side effects before retrying.') from error
