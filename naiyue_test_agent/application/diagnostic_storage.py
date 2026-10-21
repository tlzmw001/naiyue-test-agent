"""Finite diagnostic groups; caller owns the SQLite lock and transaction."""

from datetime import datetime, timedelta, timezone
import json

from .errors import SessionPersistenceError


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def _now():
    return datetime.now(timezone.utc).isoformat()


def initialize(connection):
    if connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] >= 10:
        return
    with connection:
        connection.execute('BEGIN')
        connection.execute('''CREATE TABLE IF NOT EXISTS naiyue_diagnostic_runs(
            capture_id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, run_id TEXT NOT NULL,
            capture_state TEXT NOT NULL, closed_at TEXT, last_sequence INTEGER NOT NULL DEFAULT 0,
            event_bytes INTEGER NOT NULL DEFAULT 0, summary_bytes INTEGER NOT NULL,
            reserve_bytes INTEGER NOT NULL, summary_json TEXT NOT NULL, UNIQUE(session_id,run_id))''')
        connection.execute('''CREATE TABLE IF NOT EXISTS naiyue_diagnostic_events(
            session_id TEXT NOT NULL, run_id TEXT NOT NULL, sequence INTEGER NOT NULL,
            body_json TEXT NOT NULL, body_bytes INTEGER NOT NULL, PRIMARY KEY(session_id,run_id,sequence))''')
        connection.execute('CREATE INDEX IF NOT EXISTS naiyue_diagnostic_age ON naiyue_diagnostic_runs(capture_state,closed_at,capture_id)')
        connection.execute('UPDATE naiyue_store_info SET version=10')


def _allocated(connection):
    return connection.execute('''SELECT COALESCE(SUM(event_bytes + CASE WHEN capture_state='open'
        THEN reserve_bytes ELSE summary_bytes END),0) FROM naiyue_diagnostic_runs''').fetchone()[0]


def prune(connection, settings, *, now=None, required=0):
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=settings.retention_days)).isoformat()
    removed = 0
    while removed < settings.cleanup_batch_runs:
        row = connection.execute('''SELECT session_id,run_id,closed_at FROM naiyue_diagnostic_runs
            WHERE capture_state!='open' ORDER BY closed_at,capture_id LIMIT 1''').fetchone()
        if row is None or row['closed_at'] >= cutoff and _allocated(connection) + required <= settings.max_workspace_bytes:
            break
        key = (row['session_id'], row['run_id'])
        connection.execute('DELETE FROM naiyue_diagnostic_events WHERE session_id=? AND run_id=?', key)
        connection.execute('DELETE FROM naiyue_diagnostic_runs WHERE session_id=? AND run_id=?', key)
        removed += 1
    return {'removed_runs': removed, 'allocated_bytes': _allocated(connection)}


def _update(connection, row, summary, *, closed_at=None, event_bytes=None):
    body = _json(summary)
    amount = len(body.encode('utf-8'))
    if amount > row['reserve_bytes']:
        raise ValueError('diagnostic_summary_limit')
    connection.execute('''UPDATE naiyue_diagnostic_runs SET capture_state=?,closed_at=?,
        last_sequence=?,event_bytes=?,summary_bytes=?,summary_json=? WHERE session_id=? AND run_id=?''',
        (summary['capture_state'], closed_at, summary['last_sequence'],
         row['event_bytes'] if event_bytes is None else event_bytes, amount, body, row['session_id'], row['run_id']))


def prepare(connection, settings):
    # The workspace database has one owning process; existing open groups belong
    # to the previous owner. This changes only diagnostics, never run authority.
    for row in connection.execute("SELECT * FROM naiyue_diagnostic_runs WHERE capture_state='open'").fetchall():
        summary = json.loads(row['summary_json'])
        summary.update(capture_state='interrupted', capture_incomplete=True)
        summary['reason'] = summary['reason'] or 'missing_terminal'
        _update(connection, row, summary, closed_at=_now())
    return prune(connection, settings)


def _get(connection, key):
    return connection.execute('SELECT * FROM naiyue_diagnostic_runs WHERE session_id=? AND run_id=?', key).fetchone()


def _ensure(connection, record, settings):
    key = (record['session_id'], record['run_id'])
    row = _get(connection, key)
    if row is not None:
        return row
    if connection.execute('SELECT 1 FROM naiyue_sessions WHERE session_id=?', (key[0],)).fetchone() is None:
        return None
    prune(connection, settings, required=settings.summary_reserve_bytes)
    if _allocated(connection) + settings.summary_reserve_bytes > settings.max_workspace_bytes:
        return None
    started = record['event_type'] == 'run_started' and record['source'] is None
    summary = {'session_id': key[0], 'run_id': key[1], 'started_at': record['timestamp'] if started else None,
        'last_observed_at': record['timestamp'], 'finished_at': None, 'status': None, 'failure': None, 'duration_ms': None,
        'capture_state': 'open', 'capture_incomplete': not started, 'reason': None if started else 'missing_start',
        'dropped_events': 0, 'last_sequence': 0, 'event_count': 0}
    enabled = started and record['data'].get('http_observation_version') == 1
    summary['http_attempts'] = {'status': 'complete' if enabled else 'unavailable',
        'observed_attempts': 0 if enabled else None, 'response_headers': 0 if enabled else None,
        'unconfirmed': 0 if enabled else None}
    summary['_http_pending_models'] = 0
    body = _json(summary)
    connection.execute('''INSERT INTO naiyue_diagnostic_runs
        (session_id,run_id,capture_state,summary_bytes,reserve_bytes,summary_json) VALUES(?,?,'open',?,?,?)''',
        (*key, len(body.encode('utf-8')), settings.summary_reserve_bytes, body))
    return _get(connection, key)


def save_event(connection, record, settings):
    row = _ensure(connection, record, settings)
    if row is None:
        return False
    if row['capture_state'] != 'open' or record['sequence'] <= row['last_sequence']:
        return True
    summary = json.loads(row['summary_json'])
    summary.update(last_sequence=record['sequence'], last_observed_at=record['timestamp'])
    body = _json(record)
    amount, reason = len(body.encode('utf-8')), None
    if row['event_bytes'] + amount > settings.max_run_bytes - row['reserve_bytes']:
        reason = 'run_budget'
    else:
        # Existing open groups cannot be evicted during this short transaction.
        if _allocated(connection) + amount > settings.max_workspace_bytes:
            prune(connection, settings, required=amount)
        if _allocated(connection) + amount > settings.max_workspace_bytes:
            reason = 'workspace_budget'
    if reason is not None:
        summary.update(capture_incomplete=True, dropped_events=summary['dropped_events'] + 1)
        summary['reason'] = summary['reason'] or reason
        _update(connection, row, summary)
    else:
        connection.execute('INSERT INTO naiyue_diagnostic_events VALUES(?,?,?,?,?)',
                           (row['session_id'], row['run_id'], record['sequence'], body, amount))
        summary['event_count'] += 1
        http = summary.get('http_attempts')
        if http is not None and http['status'] != 'unavailable':
            kind, data = record['event_type'], record['data']
            if kind == 'model_http_attempt_started':
                http['observed_attempts'] += 1
            elif kind == 'model_http_attempt_finished':
                http[data['status']] += 1
            elif kind == 'model_call_started':
                summary['_http_pending_models'] += 1
            elif kind == 'model_call_finished':
                summary['_http_pending_models'] -= 1
                if data.get('http_capture_status') != 'complete':
                    http['status'] = 'partial'
            elif kind == 'run_finished' and data.get('http_capture_status') == 'incomplete':
                http['status'] = 'partial'
        _update(connection, row, summary, event_bytes=row['event_bytes'] + amount)
    return True


def finish_run(connection, record, settings, *, reason=None, dropped_events=0):
    row = _ensure(connection, record, settings)
    if row is None:
        return False
    if row['capture_state'] != 'open':
        return True
    terminal = record['event_type'] == 'run_finished' and record['source'] is None
    if terminal:
        save_event(connection, record, settings)
        row = _get(connection, (record['session_id'], record['run_id']))
    summary = json.loads(row['summary_json'])
    summary.update(capture_state='closed' if terminal else 'interrupted',
        capture_incomplete=summary['capture_incomplete'] or reason is not None or not terminal,
        dropped_events=summary['dropped_events'] + dropped_events)
    summary['reason'] = reason or summary['reason'] or (None if terminal else 'missing_terminal')
    if terminal:
        summary.update(status=record['data'].get('status'), failure=record['data'].get('failure'),
                       finished_at=record['timestamp'], duration_ms=record['data'].get('duration_ms'))
    _update(connection, row, summary, closed_at=_now())
    prune(connection, settings)
    return True


def _summary(row):
    summary = {'duration_ms': None, **json.loads(row['summary_json'])}
    http = summary.setdefault('http_attempts', {'status': 'unavailable', 'observed_attempts': None,
        'response_headers': None, 'unconfirmed': None})
    pending = summary.pop('_http_pending_models', 0)
    if http['status'] != 'unavailable' and (summary['capture_incomplete'] or pending
            or http['observed_attempts'] != http['response_headers'] + http['unconfirmed']):
        http['status'] = 'partial'
    return {**summary, 'capture_id': row['capture_id'], 'payload_bytes': row['event_bytes'] + row['summary_bytes']}


def _check_query(connection, session_id, limit):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Invalid diagnostic page size')
    if connection.execute('SELECT 1 FROM naiyue_sessions WHERE session_id=?', (session_id,)).fetchone() is None:
        raise SessionPersistenceError('session_not_found')


def read_runs(connection, session_id, *, limit=50, before=None):
    _check_query(connection, session_id, limit)
    if before is not None and (type(before) is not int or not 1 <= before <= 2**63 - 1):
        raise ValueError('Invalid diagnostic run cursor')
    rows = connection.execute('''SELECT * FROM naiyue_diagnostic_runs WHERE session_id=?
        AND (? IS NULL OR capture_id<?) ORDER BY capture_id DESC LIMIT ?''',
        (session_id, before, before, limit + 1)).fetchall()
    return {'session_id': session_id, 'runs': [_summary(row) for row in rows[:limit]],
            'next_before': rows[limit - 1]['capture_id'] if len(rows) > limit else None}


def read_events(connection, session_id, run_id, *, limit=50, after=0):
    _check_query(connection, session_id, limit)
    if type(after) is not int or not 0 <= after <= 2**63 - 1:
        raise ValueError('Invalid diagnostic event cursor')
    row = _get(connection, (session_id, run_id))
    if row is None:
        raise SessionPersistenceError('diagnostic_run_not_found')
    rows = connection.execute('''SELECT sequence,body_json FROM naiyue_diagnostic_events
        WHERE session_id=? AND run_id=? AND sequence>? ORDER BY sequence LIMIT ?''',
        (session_id, run_id, after, limit + 1)).fetchall()
    return {'session_id': session_id, 'run_id': run_id, 'summary': _summary(row),
            'events': [json.loads(item['body_json']) for item in rows[:limit]],
            'next_after': rows[limit - 1]['sequence'] if len(rows) > limit else None}
