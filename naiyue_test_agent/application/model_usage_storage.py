"""Finite invocation facts on the SessionStorage-owned SQLite connection."""

import logging
from datetime import datetime, timezone

from naiyue_test_agent.agent_runtime.events import ModelInvocationStartedData, ModelInvocationFinishedData


logger = logging.getLogger(__name__)

_METRICS = ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens')


def _usage_bucket():
    return {'recorded_calls': 0, 'status_counts': dict.fromkeys(
        ('started', 'returned', 'failed', 'cancelled', 'unconfirmed'), 0),
        'usage_status_counts': dict.fromkeys(('final', 'partial', 'unavailable'), 0),
        'totals': dict.fromkeys(_METRICS, 0), 'reported': dict.fromkeys(_METRICS, 0),
        'duration_total': 0, 'duration_reported': 0}


def _add_usage(bucket, row):
    bucket['recorded_calls'] += 1
    bucket['status_counts'][row['status']] += 1
    bucket['usage_status_counts'][row['usage_status']] += 1
    if row['duration_ms'] is not None:
        bucket['duration_total'] += row['duration_ms']
        bucket['duration_reported'] += 1
    for key in _METRICS:
        if row[key] is not None:
            bucket['totals'][key] += row[key]
            bucket['reported'][key] += 1


def _usage_view(bucket):
    return {key: bucket[key] for key in ('recorded_calls', 'status_counts', 'usage_status_counts')} | {
        'duration': {'known_sum_ms': str(bucket['duration_total']) if bucket['duration_reported'] else None,
            'reported_calls': bucket['duration_reported'],
            'unknown_calls': bucket['recorded_calls'] - bucket['duration_reported']},
        'metrics': {key: {'known_sum': str(bucket['totals'][key]) if bucket['reported'][key] else None,
            'reported_calls': bucket['reported'][key],
            'unknown_calls': bucket['recorded_calls'] - bucket['reported'][key]} for key in _METRICS}}


def read_summary(connection, session_id, *, model_limit, after_model=None):
    """The caller owns one read transaction; only finite fields leave SQLite."""
    from .errors import SessionPersistenceError
    if type(model_limit) is not int or not 1 <= model_limit <= 100:
        raise ValueError('Invalid usage model page size')
    if after_model is not None and (not isinstance(after_model, str) or not 1 <= len(after_model) <= 128):
        raise ValueError('Invalid usage model cursor')
    if connection.execute('SELECT 1 FROM naiyue_sessions WHERE session_id=?', (session_id,)).fetchone() is None:
        raise SessionPersistenceError('session_not_found')
    names = [r[0] for r in connection.execute('''SELECT DISTINCT model_id FROM naiyue_model_invocations
        WHERE session_id=? AND model_id IS NOT NULL AND (? IS NULL OR model_id>?)
        ORDER BY model_id LIMIT ?''', (session_id, after_model, after_model, model_limit + 1))]
    models = {name: _usage_bucket() for name in names[:model_limit]}
    unknown, total = _usage_bucket(), _usage_bucket()
    activities = {name: _usage_bucket() for name in (
        'root_agent', 'child_agent', 'summary', 'auto_fast', 'auto_detailed', 'memory', 'unattributed')}
    scopes = {name: _usage_bucket() for name in ('root', 'child')}
    for row in connection.execute('''SELECT model_id,purpose,agent_path,status,usage_status,
        input_tokens,output_tokens,cache_read_tokens,cache_write_tokens,duration_ms
        FROM naiyue_model_invocations WHERE session_id=?''', (session_id,)):
        scope = 'root' if row['agent_path'] == 'root' else 'child'
        activity = scope + '_agent' if row['purpose'] == 'agent' else row['purpose'] or 'unattributed'
        for bucket in (total, activities[activity], scopes[scope]):
            _add_usage(bucket, row)
        if row['model_id'] is None:
            _add_usage(unknown, row)
        elif row['model_id'] in models:
            _add_usage(models[row['model_id']], row)
    runs = incomplete = dropped = 0
    for row in connection.execute('''SELECT capture_incomplete,dropped_events
        FROM naiyue_model_usage_runs WHERE session_id=?''', (session_id,)):
        runs += 1
        incomplete += bool(row['capture_incomplete'])
        dropped += row['dropped_events']
    return {'session_id': session_id, 'as_of': datetime.now(timezone.utc).isoformat(), **_usage_view(total),
        'activities': [{'id': name, **_usage_view(bucket)} for name, bucket in activities.items() if bucket['recorded_calls']],
        'scopes': [{'id': name, **_usage_view(bucket)} for name, bucket in scopes.items() if bucket['recorded_calls']],
        'models': {'items': [{'model_id': name, **_usage_view(bucket)} for name, bucket in models.items()],
            'next_after': names[model_limit - 1] if len(names) > model_limit else None, 'unattributed': _usage_view(unknown)},
        'capture': {'recorded_runs': runs, 'incomplete_runs': incomplete, 'dropped_events': str(dropped)}}


def initialize(connection):
    if connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] < 9:
        with connection:
            connection.execute('BEGIN')
            connection.execute('''CREATE TABLE naiyue_model_usage_runs(
                session_id TEXT NOT NULL, run_id TEXT NOT NULL,
                capture_incomplete INTEGER NOT NULL DEFAULT 0, reason TEXT,
                dropped_events INTEGER NOT NULL DEFAULT 0, recording_closed_at TEXT,
                PRIMARY KEY(session_id,run_id))''')
            connection.execute('''CREATE TABLE naiyue_model_invocations(
                invocation_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, run_id TEXT NOT NULL,
                agent_path TEXT NOT NULL, agent_id TEXT, agent_run_id TEXT,
                parent_run_id TEXT, parent_tool_call_id TEXT,
                model_id TEXT, purpose TEXT, started_at TEXT, finished_at TEXT,
                status TEXT NOT NULL, usage_status TEXT NOT NULL DEFAULT 'unavailable',
                input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER,
                cache_write_tokens INTEGER, error_type TEXT)''')
            connection.execute('CREATE INDEX naiyue_usage_session ON naiyue_model_invocations(session_id,run_id)')
            connection.execute("CREATE INDEX naiyue_usage_pending ON naiyue_model_invocations(status) WHERE status='started'")
            connection.execute('UPDATE naiyue_store_info SET version=9')
    with connection:
        connection.execute('BEGIN')
        connection.execute('''INSERT OR IGNORE INTO naiyue_model_usage_runs
            (session_id,run_id,capture_incomplete,reason)
            SELECT session_id,run_id,1,'interrupted_execution' FROM naiyue_sessions
            WHERE state='running' AND run_id IS NOT NULL''')
        connection.execute("UPDATE naiyue_model_invocations SET status='unconfirmed' WHERE status='started'")
        connection.execute('''UPDATE naiyue_model_usage_runs SET capture_incomplete=1,
            reason=COALESCE(reason,'interrupted_execution'),recording_closed_at=?
            WHERE recording_closed_at IS NULL''', (datetime.now(timezone.utc).isoformat(),))


def _run(connection, session_id, run_id):
    if connection.execute('SELECT 1 FROM naiyue_sessions WHERE session_id=?', (session_id,)).fetchone() is None:
        raise ValueError('usage_session_missing')
    connection.execute('INSERT OR IGNORE INTO naiyue_model_usage_runs(session_id,run_id) VALUES(?,?)',
                       (session_id, run_id))


def migrate_duration(connection):
    """Run after all existing schema12 owners have initialized their tables."""
    if connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] >= 13:
        return
    with connection:
        connection.execute('BEGIN')
        connection.execute('''ALTER TABLE naiyue_model_invocations ADD COLUMN duration_ms INTEGER
            CHECK(duration_ms IS NULL OR (typeof(duration_ms)='integer' AND duration_ms>=0))''')
        connection.execute('UPDATE naiyue_store_info SET version=13')


def save_event(connection, event):
    data, source = event.data, event.source
    if not ((event.event_type == 'model_call_started' and type(data) is ModelInvocationStartedData)
            or (event.event_type == 'model_call_finished' and type(data) is ModelInvocationFinishedData)):
        raise ValueError('usage_event_invalid')
    scope = (event.session_id, event.run_id, source.agent_path if source else 'root',
             source.agent_id if source else None, source.agent_run_id if source else None,
             source.parent_run_id if source else None, source.parent_tool_call_id if source else None)
    previous = connection.execute('SELECT * FROM naiyue_model_invocations WHERE invocation_id=?',
                                  (data.invocation_id,)).fetchone()
    names = ('session_id', 'run_id', 'agent_path', 'agent_id', 'agent_run_id', 'parent_run_id', 'parent_tool_call_id')
    if previous is not None and tuple(previous[key] for key in names) != scope:
        raise ValueError('usage_identity_conflict')
    _run(connection, event.session_id, event.run_id)
    if previous is None:
        connection.execute('''INSERT INTO naiyue_model_invocations
            (invocation_id,session_id,run_id,agent_path,agent_id,agent_run_id,parent_run_id,parent_tool_call_id,status)
            VALUES(?,?,?,?,?,?,?,?,?)''', (data.invocation_id, *scope, 'started'))
    if type(data) is ModelInvocationStartedData:
        if previous is not None and previous['started_at'] is not None:
            if (previous['model_id'], previous['purpose']) != (data.model_id, data.purpose):
                raise ValueError('usage_identity_conflict')
            return
        connection.execute('''UPDATE naiyue_model_invocations SET model_id=?,purpose=?,started_at=?
            WHERE invocation_id=?''', (data.model_id, data.purpose, event.timestamp, data.invocation_id))
        return
    counts = []
    for key in ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens'):
        value = getattr(data, key)
        if value is not None and value > 2**63 - 1:
            logger.warning('model_usage_storage_count_invalid field=%s', key)
            value = None
        counts.append(value)
    usage_status = data.usage_status if any(value is not None for value in counts) else 'unavailable'
    terminal = (data.status, usage_status, *counts, data.error_type, data.duration_ms)
    fields = ('status', 'usage_status', 'input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens', 'error_type', 'duration_ms')
    if previous is not None and previous['finished_at'] is not None:
        if tuple(previous[key] for key in fields) != terminal:
            raise ValueError('usage_terminal_conflict')
        return
    connection.execute('''UPDATE naiyue_model_invocations SET status=?,usage_status=?,input_tokens=?,
        output_tokens=?,cache_read_tokens=?,cache_write_tokens=?,error_type=?,duration_ms=?,finished_at=? WHERE invocation_id=?''',
        (*terminal, event.timestamp, data.invocation_id))
    if previous is None or previous['started_at'] is None:
        connection.execute('''UPDATE naiyue_model_usage_runs SET capture_incomplete=1,reason=COALESCE(reason,'missing_start')
            WHERE session_id=? AND run_id=?''', (event.session_id, event.run_id))


def finish_run(connection, session_id, run_id, *, reason=None, dropped_events=0):
    _run(connection, session_id, run_id)
    pending = connection.execute('''SELECT 1 FROM naiyue_model_invocations
        WHERE session_id=? AND run_id=? AND status='started' LIMIT 1''', (session_id, run_id)).fetchone()
    reason = reason or ('unfinished_invocation' if pending else None)
    connection.execute('''UPDATE naiyue_model_invocations SET status='unconfirmed'
        WHERE session_id=? AND run_id=? AND status='started' ''', (session_id, run_id))
    connection.execute('''UPDATE naiyue_model_usage_runs SET capture_incomplete=MAX(capture_incomplete,?),
        reason=COALESCE(reason,?),dropped_events=MAX(dropped_events,?),
        recording_closed_at=COALESCE(recording_closed_at,?) WHERE session_id=? AND run_id=?''',
        (int(reason is not None), reason, dropped_events, datetime.now(timezone.utc).isoformat(), session_id, run_id))
    row = connection.execute('SELECT * FROM naiyue_model_usage_runs WHERE session_id=? AND run_id=?',
                             (session_id, run_id)).fetchone()
    return {'capture_incomplete': bool(row['capture_incomplete']), 'reason': row['reason'], 'dropped_events': row['dropped_events']}
