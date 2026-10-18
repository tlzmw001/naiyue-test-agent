"""Saved history and local recovery through official checkpoint operations."""

import json
import logging
from hashlib import sha256

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from naiyue_test_agent.tools.permissions import PermissionMechanismError
from .session_approval import pending_tool_approval, is_pre_tool_approval
from .session_storage import SessionPersistenceError, STATES


logger = logging.getLogger(__name__)


def _classification(kind, reason, missing=()):
    return {'kind': kind, 'reason': reason, 'actions': [],
            'unconfirmed_tool_call_ids': list(missing)}


def _message_facts(messages):
    """Pair within each assistant batch; call IDs may recur in later batches."""
    missing, identities, calls, results = [], set(), {}, set()
    invalid = False
    for message in messages:
        if not message.id or message.id in identities:
            invalid = True
        identities.add(message.id)
        if isinstance(message, ToolMessage):
            identity = message.tool_call_id
            if identity not in calls or identity in results:
                invalid = True
            results.add(identity)
        else:
            unpaired = [identity for identity in calls if identity not in results]
            missing.extend(unpaired)
            if unpaired:
                invalid = True  # A new non-tool message overtook an unfinished batch.
            calls, results = {}, set()
            if isinstance(message, AIMessage):
                for call in message.tool_calls:
                    identity = call.get('id')
                    if not isinstance(identity, str) or not identity or identity in calls:
                        invalid = True
                        continue
                    calls[identity] = call
    missing.extend(identity for identity in calls if identity not in results)
    return list(dict.fromkeys(missing)), invalid


def _identity_matches(row, snapshot):
    if not isinstance(snapshot.config, dict):
        return False
    config = snapshot.config.get('configurable', {})
    if not isinstance(config, dict):
        return False
    return config.get('thread_id') == row['thread_id'] and config.get('checkpoint_ns', '') == ''



def classify_recovery(row, snapshot):
    """Describe saved state; never authorize, repair or install a session."""
    if not _identity_matches(row, snapshot):
        return _classification('unavailable', 'checkpoint_identity_mismatch')
    if not isinstance(snapshot.values, dict):
        return _classification('unavailable', 'invalid_history')
    messages = snapshot.values.get('messages', [])
    if not isinstance(messages, list) or any(not isinstance(m, BaseMessage) for m in messages):
        return _classification('unavailable', 'invalid_history')
    missing, invalid = _message_facts(messages)
    if invalid:
        return _classification('unavailable', 'message_pairing_ambiguous', missing)
    checkpoint = snapshot.config.get('configurable', {}).get('checkpoint_id')
    if not checkpoint:
        never_run = (row['state'] == 'ready' and row['run_id'] is None and row['checkpoint_id'] is None
                     and not messages and not snapshot.next and not snapshot.tasks)
        return _classification('ready' if never_run else 'unavailable',
                               'never_run' if never_run else 'checkpoint_missing', missing)
    if (snapshot.metadata or {}).get('naiyue_recovery') is not None:
        try:
            record = recovery_record(row, snapshot)
        except SessionPersistenceError:
            return _classification('unavailable', 'invalid_recovery_record', missing)
        if not (record['phase'] == 'finished' and row['state'] == 'ready'
                and row['checkpoint_id'] == checkpoint and not snapshot.next and not snapshot.tasks and not missing):
            return _classification('interrupted_execution', 'recovery_incomplete', missing)
    if is_pre_tool_approval(snapshot, messages):
        return _classification('pending_approval', 'saved_pre_tool_approval', missing)
    if (row['state'] == 'ready' and row['checkpoint_id'] == checkpoint and row['checkpoint_ns'] == ''
            and not snapshot.next and not snapshot.tasks and not missing):
        return _classification('ready', 'confirmed_checkpoint')
    reason = 'checkpoint_mismatch' if row['state'] == 'ready' else 'previous_run_unfinished'
    return _classification('interrupted_execution', reason, missing)




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


def build_recovery_update(snapshot, record):
    """Add only missing results; leave actual messages and artifacts untouched."""
    messages = snapshot.values.get('messages', [])
    if not isinstance(messages, list) or any(not isinstance(message, BaseMessage) for message in messages):
        raise SessionPersistenceError('invalid_history')
    _, invalid = _message_facts(messages)
    if invalid:
        raise SessionPersistenceError('message_pairing_ambiguous')
    owner = next((message for message in reversed(messages) if isinstance(message, AIMessage)), None)
    returned = {message.tool_call_id for message in messages[messages.index(owner) + 1:]
                if isinstance(message, ToolMessage)} if owner is not None else set()
    identities = {message.id for message in messages}
    additions = []
    for call in owner.tool_calls if owner is not None else ():
        if call['id'] in returned:
            continue
        identity = 'recovery-' + sha256(json.dumps([owner.id, call['id']]).encode()).hexdigest()
        if identity in identities:
            raise SessionPersistenceError('message_pairing_ambiguous')
        cancelled = record['pre_tool_approval']
        reason = 'cancelled_before_approval' if cancelled else 'result_unconfirmed'
        content = ('The previous tool batch was cancelled before approval. It was not executed.' if cancelled else
                   'The previous run ended before a result was recorded. The operation may already have taken effect. '
                   'Inspect the current state before retrying; it was not automatically replayed.')
        additions.append(ToolMessage(id=identity, name=call['name'], tool_call_id=call['id'], status='error',
            content=content, additional_kwargs={'naiyue_recovery': {'recovery_id': record['recovery_id'], 'reason': reason}}))
    return {'messages': additions, 'tool_execution': {}, 'run_stopped': False}


def validate_saved_approval(settings, registry, row, snapshot, root, names):
    """Recheck local authority; never load file contents, review with AI or grant access."""
    from naiyue_test_agent.agent_runtime.tool_execution.permissions import prepare_permission_call
    try:
        if not isinstance(row['run_id'], str) or not row['run_id']:
            raise ValueError('Missing source run')
        batch = snapshot.values['tool_execution']
        settings.validate_mode(batch['mode'])
        if batch['mode'] not in ('Default', 'Auto') or batch.get('session_allow') != []:
            # Recursive tools also consume the batch's remembered scopes. A
            # saved nonempty scope must not survive merely by clearing memory.
            raise ValueError('Saved temporary authority cannot be restored')
        selected = batch['tool_names']
        if not isinstance(selected, list) or not set(selected).issubset(names):
            raise ValueError('Saved tools are unavailable')
        specs = {spec.tool.name: spec for spec in registry.select(selected)}
        context = {'session_id': row['session_id'], 'agent_path': 'root', 'workspace_root': root}
        for saved in batch['calls']:
            current = prepare_permission_call(saved, specs[saved['name']], context, settings, [])
            if saved.get('validation') != 'valid' or current['validation'] != 'valid':
                raise ValueError('Arguments or target changed')
            # Resource exemptions depend on Skill discovery. Do not resurrect
            # old scopes or read Skill bodies during recovery: prove Allow anew.
            old_subject = {k: v for k, v in saved['subject'].items() if k != 'resource_roots'}
            new_subject = {k: v for k, v in current['subject'].items() if k != 'resource_roots'}
            if old_subject != new_subject:
                raise ValueError('Reviewed subject changed')
            decision = saved.get('decision')
            if (decision not in ('allow', 'ask', 'deny')
                    or decision == 'allow' and current['decision'] != 'allow'
                    or decision == 'ask' and current['decision'] == 'deny'
                    or decision == 'deny' and current['decision'] != 'deny'):
                raise ValueError('Local authority changed')
        pending = pending_tool_approval({**snapshot.values, '__interrupt__': snapshot.tasks[0].interrupts})
        pending['requested_run_id'] = row['run_id']
        return pending
    except (ValueError, TypeError, KeyError, AttributeError, PermissionMechanismError) as error:
        logger.warning('session_approval_revalidation_failed error_type=%s', type(error).__name__)
        raise SessionPersistenceError('approval_restore_unavailable') from error
