"""One manual operation with bounded scope, work and observable results."""
import json
import logging
import sqlite3
import time

from naiyue_test_agent.contracts.cache_cleanup import CATEGORIES, SCOPE
from naiyue_test_agent.contracts.projects import ProjectError
from naiyue_test_agent.storage.cache_cleanup_files import CleanupIssue, error_code
from naiyue_test_agent.storage.errors import SessionPersistenceError
from .cache_cleanup import scan

logger = logging.getLogger(__name__)
COUNTS = ('scopes_visited', 'units_examined', 'units_deleted', 'files_deleted',
          'bytes_deleted', 'units_retained', 'units_skipped', 'units_failed')


class StopCleanup(Exception):
    pass


class CacheCleanupBusy(Exception):
    pass


class CacheCleanupFrameTooSmall(Exception):
    pass


class CleanupRun:
    scope_errors = (CleanupIssue, OSError, sqlite3.Error, ProjectError, SessionPersistenceError, ValueError)

    def __init__(self, request_id, categories, settings, result_limit):
        self.request_id, self.categories = request_id, categories
        self.started = time.time_ns()
        self.deadline = time.monotonic() + settings.max_operation_seconds
        self.remaining = settings.max_scan_entries
        self.limit = result_limit
        self.details_size = 0
        self.details = []
        self.reason = None
        self.fatal = False
        self.closing = lambda: False
        self.category = categories[0]
        self.kind, self.scope_id = 'service', None
        self.policies = {category: getattr(settings, option) for category, option in
                         zip(CATEGORIES, ('tool_output_retention_days', 'cloud_input_retention_days'))
                         if category in categories}
        self.cutoffs = {category: self.started - days * 86_400_000_000_000
                        for category, days in self.policies.items()}
        self.stats = {category: dict.fromkeys(COUNTS, 0) for category in categories}

    def select(self, category, kind, scope_id=None):
        self.category, self.kind, self.scope_id = category, kind, scope_id

    @property
    def counts(self):
        return self.stats[self.category]

    @property
    def cutoff(self):
        return self.cutoffs[self.category]

    def check(self, entries=1, *, path=None, unit=None):
        self.remaining -= entries
        reason = ('service_closing' if self.closing() else
                  'scan_budget' if self.remaining < 0 else
                  'time_budget' if time.monotonic() >= self.deadline else
                  'result_budget' if self.details_size + 2048 + len(json.dumps(str(path or ''), ensure_ascii=False).encode()) +
                  len(json.dumps(str(unit or ''), ensure_ascii=False).encode()) > self.limit else None)
        if reason:
            self.reason = reason
            raise StopCleanup

    def deleted(self, size):
        self.counts['files_deleted'] += 1
        self.counts['bytes_deleted'] += size

    def issue(self, path, step, error, unit=None):
        code = error_code(error)
        skipped = isinstance(error, CleanupIssue) and error.skipped or code == 'already_missing'
        detail = dict(category=self.category, scope_kind=self.kind, path=str(path), step=step,
                      outcome='skipped' if skipped else 'unconfirmed' if step == 'sync' else 'failed',
                      code=code, reason=str(error) if isinstance(error, CleanupIssue) else
                      ('文件或目录已不存在' if code == 'already_missing' else '无法验证或处理受管缓存'),
                      hint='请核对目录状态、占用与访问权限后重新清理。')
        if self.scope_id is not None:
            detail['project_id' if self.kind == 'project' else 'session_id'] = self.scope_id
        if unit is not None:
            detail['unit_id'] = unit
        for name in ('errno', 'winerror'):
            value = getattr(error, name, None)
            if value is not None:
                detail[name] = value
        encoded_size = len(json.dumps(detail, ensure_ascii=False, separators=(',', ':')).encode()) + 1
        if self.details_size + encoded_size + 1280 > self.limit:
            self.reason = 'result_budget'
            raise StopCleanup
        self.counts['units_skipped' if skipped else 'units_failed'] += 1
        self.details.append(detail)
        self.details_size += encoded_size
        logger.info('cache_cleanup_issue code=%s step=%s type=%s', code, step, type(error).__name__)

    def result(self):
        totals = {key: sum(data[key] for data in self.stats.values()) for key in COUNTS}
        def serialized(counts):
            return counts | {'bytes_deleted': str(counts['bytes_deleted'])}
        return dict(request_id=self.request_id, scope=SCOPE, started_at_ns=str(self.started),
                    finished_at_ns=str(time.time_ns()), status='failed' if self.fatal else
                    'partial' if self.details or self.reason else 'completed',
                    enumeration_complete=self.reason is None, stop_reason=self.reason,
                    policies=[dict(id=category, retention_days=days, cutoff_ns=str(self.cutoffs[category]))
                              for category, days in self.policies.items()],
                    totals=serialized(totals), categories={category: serialized(data)
                    for category, data in self.stats.items()}, details=self.details)


class CacheCleanup:
    def __init__(self, owner, hosts, settings):
        self.owner, self.hosts, self.settings = owner, hosts, settings
        self.running = False

    def policies(self):
        return dict(scope=SCOPE, categories=[
            dict(id='tool_outputs', retention_days=self.settings.tool_output_retention_days, available=True),
            dict(id='cloud_inputs', retention_days=self.settings.cloud_input_retention_days, available=True)])

    async def execute(self, request_id, categories, *, result_limit_bytes, service_closing=None):
        if self.running:
            raise CacheCleanupBusy
        run = CleanupRun(request_id, categories, self.settings, result_limit_bytes)
        run.reason = 'result_budget'
        envelope = dict(state='finished', request_id=request_id, http_status=200, result=run.result())
        if len(json.dumps(envelope, separators=(',', ':')).encode()) > result_limit_bytes:
            raise CacheCleanupFrameTooSmall
        run.reason = None
        run.closing = lambda: self.owner._closing or (service_closing is not None and service_closing())
        self.running = True
        try:
            with self.owner.operation():
                await scan(self.hosts, run)
        except StopCleanup:
            return run.result()
        except Exception as error:
            run.fatal = True
            run.reason = 'root_unavailable' if isinstance(error, run.scope_errors) else 'internal_error'
            run.select(run.category, 'service')
            try:
                run.issue(self.hosts.projects.data_dir, 'enumerate', error)
            except StopCleanup:
                logger.error('cache_cleanup_root_failed error_type=%s', type(error).__name__)
        finally:
            self.running = False
        return run.result()
