"""Bounded diagnostic facts. Never tool feedback, exception text or retry policy."""
import json
import logging
import math
import re

logger = logging.getLogger(__name__)


def safe_log(write, message, *args):
    """A failed optional log cannot replace the operation's result or exception."""
    try:
        write(message, *args, stacklevel=2)
        return True
    except Exception as error:
        try:
            logger.error('diagnostic_log_failed diagnostic=%s', diagnostic_text(error=error))
        except Exception:
            return False  # Both observation sinks failed; execution still owns its outcome.
        return False


_LABELS = frozenset(('event', 'session_id', 'run_id', 'project_id', 'binding_id', 'operation_id',
    'request_id', 'input_id', 'result_id', 'task_id', 'tool_call_id', 'tool_name', 'server',
    'agent_path', 'agent_run_id', 'parent_tool_call_id', 'origin_run_id', 'phase', 'stage',
    'action', 'kind', 'operation', 'command', 'transport', 'termination', 'status', 'state',
    'code', 'error_type', 'cause_type', 'sqlite_errorname', 'stderr_category', 'category',
    'reason', 'outcome', 'boundary', 'mode', 'stop_mode', 'http_capture_status', 'model_id',
    'model_call_id', 'invocation_id', 'purpose', 'control_id', 'trigger', 'message_id',
    'message_type', 'visibility', 'related_operation_id', 'attempt_id', 'stop_reason'))
_NUMBERS = frozenset(('errno', 'winerror', 'sqlite_errorcode', 'exit_code', 'taskkill_exit_code',
    'pid', 'duration_ms', 'timeout_seconds', 'stderr_bytes', 'stderr_retained_bytes', 'offset',
    'saved_bytes', 'observed_bytes', 'sequence', 'leaf_count', 'read_bytes', 'http_status',
    'input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens', 'retry_attempts',
    'approved_count', 'rejected_count', 'response_headers_ms', 'before_estimate', 'after_estimate',
    'original_message_count', 'removed_message_count', 'preserved_message_count', 'count'))
_FLAGS = frozenset(('stderr_present', 'stderr_truncated', 'leaves_truncated', 'collector_stopped',
    'thread_alive', 'process_exited', 'mutates', 'manual_review_required', 'side_effect_started'))


def _safe_fields(fields):
    result = {}
    for name, value in fields.items():
        if value is None and name in _LABELS | _NUMBERS | _FLAGS:
            result[name] = None
        elif name in _LABELS and type(value) is str and re.fullmatch(r'[A-Za-z0-9_.:/-]{1,128}', value):
            result[name] = value
        elif name in _NUMBERS and type(value) in (int, float) and -2**63 < value < 2**63 and math.isfinite(value):
            result[name] = value
        elif name in _FLAGS and type(value) is bool:
            result[name] = value
        elif name == 'leaves' and type(value) is list:
            result[name] = [_safe_fields(item) for item in value[:8] if type(item) is dict]
    return result


def _error_fields(error):
    result = {'error_type': type(error).__name__}
    for name in ('errno', 'winerror', 'sqlite_errorcode', 'sqlite_errorname'):
        try:
            value = getattr(error, name, None)
        except Exception:
            continue  # Diagnostic properties must not replace the original failure.
        if name == 'sqlite_errorname':
            if type(value) is str and re.fullmatch(r'SQLITE_[A-Z0-9_]{1,60}', value):
                result[name] = value
        elif type(value) is int:
            result[name] = value
    return result


def diagnostic_text(*, error=None, **facts):
    """Only allowlisted scalar metadata; no str(error), argv, env or payload."""
    try:
        if error is not None:
            facts.update(_error_fields(error))
            if isinstance(error, BaseExceptionGroup):
                pending, leaves, visited = [error], [], 0
                while pending and len(leaves) < 8 and visited < 32:
                    member = pending.pop(); visited += 1
                    if isinstance(member, BaseExceptionGroup):
                        pending.extend(reversed(member.exceptions[:8]))
                        if len(member.exceptions) > 8:
                            facts['leaves_truncated'] = True
                    else:
                        leaves.append(_error_fields(member))
                facts.update(leaves=leaves, leaf_count=len(leaves))
                facts['leaves_truncated'] = facts.get('leaves_truncated', False) or bool(pending)
        return json.dumps(_safe_fields(facts), ensure_ascii=True, sort_keys=True, separators=(',', ':'))
    except Exception:
        return '{"diagnostic_projection_failed":true}'


def stderr_facts(data, total=None):
    """A matching text hint is diagnostic evidence, not a definitive root cause."""
    total = len(data) if total is None else total
    sample = bytes(data[:8192]).lower()
    categories = (
        ('authentication_failed', (b'permission denied (publickey', b'authentication failed')),
        ('host_key_failed', (b'host key verification failed', b'remote host identification has changed')),
        ('name_resolution_failed', (b'could not resolve host', b'could not resolve hostname', b'name or service not known')),
        ('connection_refused', (b'connection refused',)),
        ('connection_timeout', (b'connection timed out', b'operation timed out')),
        ('disk_full', (b'no space left on device',)),
        ('permission_denied', (b'permission denied', b'access is denied')),
        ('not_found', (b'no such file or directory', b'command not found')),
    )
    category = next((name for name, patterns in categories if any(p in sample for p in patterns)),
                    'unclassified' if total else 'empty')
    return dict(stderr_category=category, stderr_bytes=total, stderr_retained_bytes=len(sample),
        stderr_present=bool(total), stderr_truncated=total > len(sample))


def command_kind(args, family):
    # These are labels, never an argv rendering (arguments may contain credentials).
    if not args:
        return 'unknown'
    if family == 'docker':
        names = {('info',), ('context', 'inspect'), ('container', 'ls'), ('container', 'inspect'),
            ('container', 'exec'), ('container', 'create'), ('container', 'start'), ('container', 'stop'),
            ('container', 'rm'), ('volume', 'ls'), ('volume', 'inspect'), ('volume', 'create'),
            ('volume', 'rm'), ('image', 'inspect')}
        candidate = tuple(args[:1] if args[0] == 'info' else args[:2])
        return '.'.join(candidate) if candidate in names else 'other'
    return args[0] if args[0] in ('init', 'fetch', 'bundle', 'fast-import', 'cat-file', 'rev-parse',
        'update-ref', 'ls-tree', 'status', 'diff', 'merge', 'merge-base', 'read-tree', 'write-tree',
        'commit-tree', 'update-index', 'hash-object', 'check-ignore', 'ls-files', 'config', 'checkout',
        'show', 'for-each-ref', 'symbolic-ref', 'rev-list', 'cherry-pick') else 'other'


class SafeDiagnosticFormatter(logging.Formatter):
    """Render existing structured events without dumping arbitrary LogRecord extras."""
    def __init__(self):
        super().__init__('%(asctime)s %(levelname)s %(name)s %(message)s')

    def format(self, record):
        rendered = super().format(record)
        for name in ('application_event', 'session_event', 'tool_error'):
            fields = getattr(record, name, None)
            if type(fields) is dict:
                rendered += ' ' + name + '=' + diagnostic_text(**fields)
        return rendered
