"""Shared process-tool output ownership for Bash, PowerShell and direct CLI calls."""

import asyncio
import logging
from time import monotonic

from langchain_core.tools import ToolException

from .output import ShellOutputArchive
from .output_text import OutputAccumulator
from .processes import ProcessCleanupError
from .progress import ToolProgress

logger = logging.getLogger(__name__)


def format_process_outcome(outcome, output, archived, *, label):
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
        raise ToolException(f'{label} {reason}. Inspect side effects before retrying; '
                            f'the command was not automatically retried.\n{text}')
    if outcome.returncode != 0:
        raise ToolException(text)
    return text


async def execute_process_request(request, runtime, *, name, label, settings, runner, output_settings, progress_settings):
    output = OutputAccumulator(settings)
    archive = ShellOutputArchive(request.cwd, settings, output_settings)
    progress = ToolProgress(runtime, progress_settings)
    observed_bytes = 0
    started = monotonic()
    def record(chunk):
        output.feed(chunk)
        archive.record(chunk, activate=output.truncated or output.transformed)
    async def consume(_stream, chunk):
        nonlocal observed_bytes
        # HostProcessRunner owns this consumer and already-started disk IO.
        await asyncio.to_thread(record, chunk)
        observed_bytes += len(chunk)
        progress.report(phase='process_output', completed=observed_bytes, unit='bytes', text=output.text)
    def finish(incomplete=False):
        output.finish()
        archive.activate(output.truncated or output.transformed)
        if incomplete:
            archive.mark_incomplete('capture_incomplete')
        return archive.finish(transformed=output.transformed)
    async def finish_owned(incomplete=False):
        finishing = asyncio.create_task(asyncio.to_thread(finish, incomplete))
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
    try:
        progress.report(phase='process_starting', activity='preparing', completed=0, unit='bytes')
        outcome = await runner.run(request, consume, runtime.context.get('_run_control'))
    except BaseException as error:
        if isinstance(error, ProcessCleanupError) and error.consumer_task is not None:
            # Closing under an unsettled writer would break resource ownership.
            async def deferred():
                try:
                    await finish_owned(True)
                except Exception as failure:
                    logger.error('shell_deferred_archive_failed error_type=%s', type(failure).__name__)
            error.consumer_task.add_done_callback(lambda _task: asyncio.create_task(deferred()))
        else:
            try:
                await finish_owned(True)
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
    archived = await finish_owned(not outcome.output_complete)
    progress.report(phase='capture_cleanup', activity='settling', completed=observed_bytes, unit='bytes', text=output.text)
    logger.info('shell_completed', extra={'shell_event': {
        'event': 'shell_completed', 'shell': name, 'run_id': runtime.context.get('run_id'),
        'tool_call_id': runtime.tool_call_id, 'exit_code': outcome.returncode,
        'termination': outcome.termination, 'truncated': output.truncated,
        'output_complete': outcome.output_complete, 'archive_complete': archived.complete,
        'elapsed_ms': int((monotonic() - started) * 1000),
    }})
    try:
        return format_process_outcome(outcome, output, archived, label=label)
    finally:
        progress.flush()
