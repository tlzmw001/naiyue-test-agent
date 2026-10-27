"""Validate stored recovery ownership without choosing a recovery action."""

import json
from .errors import SessionPersistenceError
from .queries import STATES


def recovery_record(row, snapshot):
    """Validate persisted recovery ownership before trusting its saved phase."""
    encoded = (snapshot.metadata or {}).get('naiyue_recovery')
    if encoded is None:
        return None
    if not isinstance(encoded, str):
        raise SessionPersistenceError('invalid_recovery_record')
    try:
        record = json.loads(encoded)
        keys = {'version', 'recovery_id', 'session_id', 'thread_id', 'source_checkpoint_id',
                'source_namespace', 'original_state', 'pre_tool_approval', 'phase'}
        valid = (isinstance(record, dict) and set(record) == keys and type(record['version']) is int
                 and record['version'] == 1 and record['session_id'] == row['session_id']
                 and record['thread_id'] == row['thread_id'] and record['source_namespace'] == ''
                 and isinstance(record['recovery_id'], str) and len(record['recovery_id']) == 32
                 and all(char in '0123456789abcdef' for char in record['recovery_id'])
                 and isinstance(record['source_checkpoint_id'], str) and bool(record['source_checkpoint_id'])
                 and record['original_state'] in STATES and type(record['pre_tool_approval']) is bool
                 and record['phase'] in ('cleared', 'paired', 'finished'))
    except (ValueError, TypeError, KeyError):
        valid = False
    if not valid:
        raise SessionPersistenceError('invalid_recovery_record')
    return record
