"""In-process custody of approved Shell operations, independent of root runs."""

import asyncio
from contextlib import contextmanager
from contextvars import Context
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import logging
import json
import hashlib
from pathlib import Path
from threading import RLock
from uuid import uuid4

from langchain_core.tools import ToolException

from ..agent_runtime.async_utils import await_settled
from ..tools.process_execution import ProcessCapture, ForegroundResult
from ..tools.processes import ProcessCleanupError
from ..tools.background_tasks import BackgroundNoticeSnapshot
from ..shell_task_settings import ShellTaskSettings
from ..execution_roots import ExecutionRoot


logger = logging.getLogger(__name__)


class TaskQueryError(ToolException):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _now():
    return datetime.now(timezone.utc).isoformat()


@dataclass
class _Operation:
    task_id: str
    session_id: str
    root: Path
    archive_root: Path
    run_id: str
    call_id: str
    name: str
    command: str
    request: object = field(repr=False)
    settings: object = field(repr=False)
    capture: ProcessCapture = field(repr=False)
    background: bool = True
    project_id: str | None = None
    execution_root: ExecutionRoot | None = None
    execution_origin: float | None = None
    exited: bool = False
    outcome: object = field(default=None, repr=False)
    archived: object = field(default=None, repr=False)
    error: BaseException | None = field(default=None, repr=False)
    notice_version: int = 0
    status: str = 'starting'
    created_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None
    termination: str | None = None
    exit_code: int | None = None
    output_complete: bool = False
    cleanup_confirmed: bool = False
    error_type: str | None = None
    requested: bool = False
    stop_reason: str | None = None
    worker: asyncio.Task | None = field(default=None, repr=False)


class BackgroundProcessManager:
    def __init__(self, settings=None):
        self._notice_settings = (settings or ShellTaskSettings()).model_copy(deep=True)
        self._notice_version = 0
        self._pending_notices = {}
        self._loop = None
        self._lock = RLock()
        self._entries = {}
        self._calls = {}
        self._closing = False
        self._deleting = set()
        self._cleaning = set()

    def _check_loop(self):
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        if loop is not self._loop:
            raise RuntimeError('Background tasks require their owning application loop')

    @staticmethod
    def _public(item):
        return {'task_id': item.task_id, 'session_id': item.session_id,
            'workspace_root': str(item.root), 'origin_run_id': item.run_id,
            'tool_call_id': item.call_id, 'shell': item.name, 'command_preview': item.command[:200],
            'status': item.status, 'created_at': item.created_at, 'started_at': item.started_at,
            'finished_at': item.finished_at, 'max_execution_seconds': item.request.timeout_seconds,
            'exit_code': item.exit_code, 'termination': item.termination,
            'output_complete': item.output_complete, 'cleanup_confirmed': item.cleanup_confirmed,
            'error_type': item.error_type}

    async def submit(self, *, session_id, workspace_root, archive_root, run_id, tool_call_id, agent_path,
                     command, name, request, settings, runner, control, project_id=None, execution_root=None):
        item = self._register(session_id=session_id, workspace_root=workspace_root, archive_root=archive_root, run_id=run_id,
            tool_call_id=tool_call_id, agent_path=agent_path, command=command, name=name,
            request=request, settings=settings, runner=runner, control=control, background=True, project_id=project_id, execution_root=execution_root)
        return self._public(item)

    def _register(self, *, session_id, workspace_root, archive_root, run_id, tool_call_id, agent_path,
                  command, name, request, settings, runner, control, background, project_id=None, execution_root=None):
        self._check_loop()
        if agent_path != 'root':
            raise ToolException('Only the root agent can start background commands.')
        if (not all(isinstance(value, str) and value for value in (session_id, run_id, tool_call_id))
                or not isinstance(workspace_root, Path) or not workspace_root.is_absolute()
                or request.cwd != workspace_root):
            raise ToolException('Background task scope is unavailable.')
        from ..tools.output import require_archive_root
        from ..tools.permissions import require_execution_directory
        require_execution_directory({'workspace_root': workspace_root, 'project_id': project_id, 'execution_root': execution_root})
        archive_root = require_archive_root({'archive_root': archive_root, 'workspace_root': workspace_root})
        with self._lock:
            if self._closing or session_id in self._deleting or archive_root in self._cleaning:
                raise ToolException('Background task admission is closed.')
            if control is not None and control.requested:
                raise ToolException('Background command was stopped before handoff; not executed.')
            key = (run_id, tool_call_id)
            existing = self._entries.get(self._calls.get(key))
            if existing is not None:
                if (existing.session_id != session_id or existing.request != request or existing.archive_root != archive_root
                        or existing.settings != settings or existing.name != name or existing.command != command
                        or existing.project_id != project_id or existing.execution_root != execution_root):
                    raise ToolException('Background call identity conflicts with an existing operation.')
                return existing
            frozen = deepcopy(request)
            frozen_settings = settings.model_copy(deep=True)
            item = _Operation(uuid4().hex, session_id, workspace_root, archive_root, run_id, tool_call_id,
                name, command, frozen, frozen_settings, ProcessCapture(archive_root, frozen_settings, background=background), background=background, project_id=project_id, execution_root=execution_root)
            self._entries[item.task_id] = item
            self._calls[key] = item.task_id
            try:
                item.worker = self._loop.create_task(self._drive(item, runner), context=Context())
            except BaseException:
                self._entries.pop(item.task_id)
                self._calls.pop(key)
                raise
            # Registration and handoff contain no suspension point. Once this
            # receipt is returned, only the application owns the process worker.
            return item

    async def foreground(self, *, wait_seconds, background_limit, on_progress, **scope):
        """The waiting caller observes its run; the worker never owns that caller."""
        control = scope['control']
        item = self._register(**scope, background=False)
        if item.background:
            return self._public(item)
        observed = -1
        try:
            while not item.worker.done():
                if control is not None and control.requested:
                    self._request_stop(item, 'user_stop')
                current = await await_settled(asyncio.to_thread(item.capture.snapshot))
                if current['observed_raw_bytes'] != observed:
                    observed = current['observed_raw_bytes']
                    on_progress(observed, current['output'])
                if self._can_promote(item, wait_seconds):
                    # Disk work must settle even if the caller is cancelled here.
                    reason = await await_settled(asyncio.to_thread(item.capture.promote, background_limit))
                    if control is not None and control.requested:
                        self._request_stop(item, 'user_stop')
                    if self._can_promote(item, wait_seconds):
                        item.background = True
                        if reason is not None:
                            self._request_stop(item, 'output_limit' if reason == 'command_limit' else 'archive_error')
                        return self._public(item)
                await asyncio.wait((item.worker,), timeout=.05)
            if item.error is not None:
                raise item.error
            return ForegroundResult(item.outcome, item.capture, item.archived, item.stop_reason)
        except BaseException as error:
            self._request_stop(item, 'user_stop')
            try:
                await await_settled(item.worker)
            except BaseException as cleanup:
                logger.error('foreground_wait_cleanup_failed error_type=%s', type(cleanup).__name__)
            raise error
        finally:
            with self._lock:
                item.error = None  # The caller's traceback can contain its runtime/publisher.
                if not item.background:
                    if self._unsettled(item):
                        item.background = True  # Unconfirmed custody must remain visible.
                        self._queue_notice(item)
                    else:
                        # Ordinary completed calls have no background history to retain.
                        self._entries.pop(item.task_id, None)
                        self._calls.pop((item.run_id, item.call_id), None)

    def _can_promote(self, item, wait_seconds):
        if item.background or item.requested or item.exited or item.worker.done() or item.execution_origin is None:
            return False
        elapsed = self._loop.time() - item.execution_origin
        seconds = item.request.timeout_seconds
        return elapsed >= wait_seconds and (seconds is None or seconds > max(wait_seconds, elapsed))

    def _started(self, item):
        with self._lock:
            item.started_at = _now()
            if not item.requested:
                item.status = 'running'

    def _request_stop(self, item, reason):
        with self._lock:
            if item.status in ('finished', 'lost'):
                return
            if item.stop_reason is None:
                item.stop_reason = reason
            item.requested = True
            item.status = 'stopping'

    async def _drive(self, item, runner):
        outcome = None
        async def consume(_stream, chunk):
            reason = await asyncio.to_thread(item.capture.record, chunk)
            if reason is not None:
                item.capture.archive.mark_incomplete(reason)
                self._request_stop(item, 'output_limit' if reason == 'command_limit' else 'archive_error')
        try:
            def execution_started(origin):
                item.execution_origin = origin
            def exited():
                item.exited = True
            req = replace(item.request, on_started=lambda: self._started(item),
                on_execution_started=execution_started, on_exited=exited)
            outcome = await runner.run(req, consume, item)
            with self._lock:
                item.outcome = outcome
                item.exit_code = outcome.returncode
                item.output_complete = outcome.output_complete
                item.termination = item.stop_reason if outcome.termination == 'user_stop' else outcome.termination
            item.archived = await item.capture.finish_owned(not outcome.output_complete)
            with self._lock:
                item.cleanup_confirmed = (outcome.process_reaped and outcome.pipes_closed
                    and outcome.consumer_settled and item.capture.closed)
                item.status = 'finished' if item.cleanup_confirmed else 'lost'
        except BaseException as error:
            item.error = error
            logger.error('background_process_failed task_id=%s error_type=%s', item.task_id, type(error).__name__)
            uncertain = isinstance(error, (ProcessCleanupError, asyncio.CancelledError))
            if isinstance(error, ProcessCleanupError) and error.consumer_task is not None:
                item.capture.defer_finish(error.consumer_task)
            else:
                try:
                    await item.capture.finish_owned(True)
                except BaseException as failure:
                    uncertain = True
                    logger.error('background_capture_cleanup_failed task_id=%s error_type=%s', item.task_id, type(failure).__name__)
            with self._lock:
                item.error_type = type(error).__name__
                item.cleanup_confirmed = not uncertain and item.capture.closed
                item.status = 'finished' if item.cleanup_confirmed else 'lost'
                if outcome is None:
                    item.termination = 'cleanup_unconfirmed' if not item.cleanup_confirmed else (
                        'start_failed' if item.started_at is None else 'capture_failed')
        finally:
            with self._lock:
                item.finished_at = _now()
                if item.background:
                    item.error = None
                    self._queue_notice(item)

    def _queue_notice(self, item):
        # Both callers already hold the registry lock after recording terminal facts.
        if item.notice_version or item.status not in ('finished', 'lost'):
            return
        self._notice_version += 1
        item.notice_version = self._notice_version
        self._pending_notices.setdefault(item.session_id, {})[item.task_id] = item.notice_version

    def notice_snapshot(self, *, session_id, workspace_root, run_id=None):
        header = '\n\nBackground Shell task updates (application status; use task_output for results):\n'
        versions, lines = [], []
        size = len(header.encode('utf-8'))
        with self._lock:
            for identity, version in self._pending_notices.get(session_id, {}).items():
                item = self._entries[identity]
                if item.root != workspace_root:
                    continue
                line = json.dumps({'task_id': identity, 'status': item.status,
                    'exit_code': item.exit_code, 'termination': item.termination}, separators=(',', ':')) + '\n'
                needed = len(line.encode('utf-8'))
                if len(versions) >= self._notice_settings.max_notice_items or size + needed > self._notice_settings.max_notice_bytes:
                    if not versions:
                        logger.warning('background_notice_budget_too_small')
                    break
                versions.append((identity, version))
                lines.append(line)
                size += needed
        return BackgroundNoticeSnapshot(session_id, workspace_root, run_id, tuple(versions),
            header + ''.join(lines) if versions else '')

    def acknowledge_notices(self, snapshot, *, session_id, workspace_root, run_id):
        if (not isinstance(snapshot, BackgroundNoticeSnapshot) or snapshot.session_id != session_id
                or snapshot.workspace_root != workspace_root or snapshot.run_id != run_id):
            raise ValueError('Background notice scope mismatch')
        with self._lock:
            pending = self._pending_notices.get(session_id, {})
            for identity, version in snapshot.versions:
                item = self._entries.get(identity)
                if item is not None and item.root == workspace_root and pending.get(identity) == version:
                    pending.pop(identity)
            if not pending:
                self._pending_notices.pop(session_id, None)

    def _find(self, task_id, session_id=None, workspace_root=None):
        item = self._entries.get(task_id)
        if item is None or not item.background:
            raise TaskQueryError('task_not_found', 'Background task not found.')
        if ((session_id is not None and item.session_id != session_id)
                or (workspace_root is not None and item.root != workspace_root)):
            raise TaskQueryError('task_not_found', 'Background task scope mismatch.')
        return item

    async def list(self, *, session_id=None, workspace_root=None, limit=20, before=None):
        self._check_loop()
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ToolException('Task list limit must be an integer from 1 to 100.')
        with self._lock:
            rows = [item for item in reversed(tuple(self._entries.values()))
                    if item.background and (session_id is None or item.session_id == session_id)
                    and (workspace_root is None or item.root == workspace_root)]
            metadata = [self._public(item) for item in rows]
            summary = {'active_count': sum(item.status in ('starting', 'running', 'stopping') for item in rows),
                'terminal_revision': max((item.notice_version for item in rows), default=0),
                'revision': hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest()}
            if before is not None:
                position = next((i for i, item in enumerate(rows) if item.task_id == before), None)
                if position is None:
                    raise TaskQueryError('task_cursor_invalid', 'Invalid task list cursor.')
                rows = rows[position + 1:]
            page = rows[:limit]
            return {**summary, 'items': [self._public(item) for item in page],
                    'next_before': page[-1].task_id if len(rows) > limit else None}

    async def output(self, task_id, *, session_id=None, workspace_root=None):
        self._check_loop()
        with self._lock:
            item = self._find(task_id, session_id, workspace_root)
            row = self._public(item)
        view = await asyncio.to_thread(item.capture.snapshot)
        available = await asyncio.to_thread(item.capture.archive.available)
        row.update(view, output_available=available)
        if not available:
            row['output_unavailable'] = True
        return row

    async def stop(self, task_id, *, session_id=None, workspace_root=None):
        self._check_loop()
        with self._lock:
            item = self._find(task_id, session_id, workspace_root)
            self._request_stop(item, 'user_stop')
        await await_settled(item.worker)
        return self._public(item)

    def _unsettled(self, item):
        return not item.cleanup_confirmed or not item.capture.closed

    @contextmanager
    def deletion_guard(self, session_id):
        with self._lock:
            if session_id in self._deleting or any(item.session_id == session_id and self._unsettled(item)
                    for item in self._entries.values()):
                raise ToolException('Session has active background tasks or unconfirmed cleanup.')
            self._deleting.add(session_id)
        try:
            yield
        finally:
            with self._lock:
                self._deleting.remove(session_id)

    def forget_session(self, session_id):
        with self._lock:
            for identity, item in tuple(self._entries.items()):
                if item.session_id == session_id:
                    if self._unsettled(item):
                        raise ToolException('Session has active background tasks.')
                    self._entries.pop(identity)
                    self._calls.pop((item.run_id, item.call_id))
            self._pending_notices.pop(session_id, None)

    @contextmanager
    def output_guard(self, root):
        with self._lock:
            if root in self._cleaning or any(item.archive_root == root and self._unsettled(item)
                    for item in self._entries.values()):
                raise ValueError('Workspace has active background tasks or unconfirmed cleanup')
            self._cleaning.add(root)
        try:
            yield
        finally:
            with self._lock:
                self._cleaning.remove(root)

    def is_cleaning(self, root):
        with self._lock:
            return root in self._cleaning

    async def aclose(self):
        self._check_loop()
        with self._lock:
            self._closing = True
            entries = tuple(self._entries.values())
            for item in entries:
                self._request_stop(item, 'app_close')
        for item in entries:
            await await_settled(item.worker)
        if any(self._unsettled(item) for item in entries):
            raise ProcessCleanupError('Background task cleanup could not be confirmed.')
