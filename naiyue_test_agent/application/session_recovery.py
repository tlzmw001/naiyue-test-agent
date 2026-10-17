"""Saved history and local recovery through official checkpoint operations."""

import json
import logging
from hashlib import sha256
from uuid import uuid4

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langgraph.graph import END

from naiyue_test_agent.model_access import ModelConfigError
from naiyue_test_agent.tools.permissions import PermissionMechanismError
from .io_steps import IOCall
from .session_approval import pending_tool_approval
from .session_storage import SessionPersistenceError, STATES


logger = logging.getLogger(__name__)
APPROVAL_NODE = 'BatchApprovalMiddleware.after_model'


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


def _saved_approval(snapshot, messages):
    if snapshot.next != (APPROVAL_NODE,) or len(snapshot.tasks) != 1 or not messages:
        return False
    task = snapshot.tasks[0]
    if (task.name != APPROVAL_NODE or task.path != ('__pregel_pull', APPROVAL_NODE)
            or task.state or task.error or task.result is not None or len(task.interrupts) != 1):
        return False
    latest = messages[-1]
    if not isinstance(latest, AIMessage) or not latest.tool_calls:
        return False
    try:
        batch = snapshot.values['tool_execution']
        calls = batch['calls']
        if batch['batch_id'] != latest.id or len(calls) != len(latest.tool_calls):
            return False
        if any((saved['id'], saved['name'], saved['args']) != (call['id'], call['name'], call['args'])
               for saved, call in zip(calls, latest.tool_calls, strict=True)):
            return False
        pending = pending_tool_approval({**snapshot.values, '__interrupt__': task.interrupts})
        return bool(pending and pending['items'])
    except (KeyError, TypeError, ValueError, AttributeError, PermissionMechanismError):
        return False


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
    if _saved_approval(snapshot, messages):
        return _classification('pending_approval', 'saved_pre_tool_approval', missing)
    if (row['state'] == 'ready' and row['checkpoint_id'] == checkpoint and row['checkpoint_ns'] == ''
            and not snapshot.next and not snapshot.tasks and not missing):
        return _classification('ready', 'confirmed_checkpoint')
    reason = 'checkpoint_mismatch' if row['state'] == 'ready' else 'previous_run_unfinished'
    return _classification('interrupted_execution', reason, missing)


def history_steps(persistence, session_id, agent):
    row = yield IOCall(persistence.storage.get, (session_id,))
    value = {key: row[key] for key in ('session_id', 'state', 'model_id', 'workspace_root', 'permission_mode')}
    value.update(messages=[], active_tools=[], checkpoint_id=None, checkpoint_ns='',
                 confirmed_checkpoint_id=row['checkpoint_id'], confirmed_checkpoint_ns=row['checkpoint_ns'])
    config = {'configurable': {'thread_id': row['thread_id']}}
    try:
        snapshot = yield IOCall(agent.get_state, (config,), async_call=agent.aget_state)
    except Exception as error:
        logger.error('session_history_read_failed error_type=%s', type(error).__name__)
        value['recovery'] = _classification('unavailable', 'checkpoint_unreadable')
        return value
    recovery = classify_recovery(row, snapshot)
    if _identity_matches(row, snapshot):
        value['checkpoint_id'] = snapshot.config.get('configurable', {}).get('checkpoint_id')
        messages = snapshot.values.get('messages', []) if isinstance(snapshot.values, dict) else None
        if isinstance(messages, list) and all(isinstance(m, BaseMessage) for m in messages):
            value['messages'] = messages
    unavailable = None
    try:
        _, names = yield from persistence._configuration_steps(row)
        value['active_tools'] = list(names)
    except (ValueError, TypeError, ModelConfigError, SessionPersistenceError) as error:
        unavailable = 'saved_configuration_unavailable'
        logger.warning('session_history_configuration_unavailable error_type=%s', type(error).__name__)
    with persistence.sessions._lock:
        if session_id in persistence.sessions._sessions:
            unavailable = 'session_current_process'
    with persistence._lock:
        if (session_id in persistence._blocked or persistence._recovering == session_id
                or persistence._active and persistence._active[0] == session_id):
            unavailable = 'session_current_process'
    if unavailable:
        recovery = _classification('unavailable', unavailable, recovery['unconfirmed_tool_call_ids'])
    if recovery['kind'] == 'unavailable':
        logger.warning('session_history_unavailable reason=%s', recovery['reason'])
    elif recovery['kind'] in ('interrupted_execution', 'pending_approval') and value['checkpoint_id']:
        recovery['actions'] = (['restore_approval', 'continue'] if recovery['kind'] == 'pending_approval'
                               else ['continue'])
    value['recovery'] = recovery
    return value


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


def validate_saved_approval(persistence, row, snapshot, root, names):
    """Recheck local authority; never load file contents, review with AI or grant access."""
    from naiyue_test_agent.agent_runtime.tool_execution.permissions import prepare_permission_call
    try:
        if not isinstance(row['run_id'], str) or not row['run_id']:
            raise ValueError('Missing source run')
        batch = snapshot.values['tool_execution']
        settings = persistence.sessions._permission_settings
        settings.validate_mode(batch['mode'])
        if batch['mode'] not in ('Default', 'Auto') or batch.get('session_allow') != []:
            # Recursive tools also consume the batch's remembered scopes. A
            # saved nonempty scope must not survive merely by clearing memory.
            raise ValueError('Saved temporary authority cannot be restored')
        selected = batch['tool_names']
        if not isinstance(selected, list) or not set(selected).issubset(names):
            raise ValueError('Saved tools are unavailable')
        specs = {spec.tool.name: spec for spec in persistence.sessions._tool_registry.select(selected)}
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


def recovery_steps(persistence, session_id, agent, checkpoint_id, action):
    if action not in ('continue', 'restore_approval'):
        raise SessionPersistenceError('invalid_recovery_action')
    row = yield IOCall(persistence.storage.get, (session_id,))
    root, names = yield from persistence._configuration_steps(row)
    binding = yield IOCall(persistence.storage.get_workspace_binding)
    if binding and str(root) != binding['root_path']:
        raise SessionPersistenceError('workspace_mismatch')
    config = {'configurable': {'thread_id': row['thread_id']}}
    try:
        snapshot = yield IOCall(agent.get_state, (config,), async_call=agent.aget_state)
    except Exception as error:
        logger.error('session_recovery_read_failed error_type=%s', type(error).__name__)
        raise SessionPersistenceError('checkpoint_unreadable') from error
    if not _identity_matches(row, snapshot):
        raise SessionPersistenceError('checkpoint_mismatch')
    actual = snapshot.config['configurable'].get('checkpoint_id')
    if not actual or checkpoint_id != actual:
        raise SessionPersistenceError('checkpoint_mismatch')
    classification = classify_recovery(row, snapshot)
    if classification['kind'] not in ('interrupted_execution', 'pending_approval'):
        raise SessionPersistenceError('session_recovery_blocked')
    if action == 'restore_approval':
        if classification['kind'] != 'pending_approval':
            raise SessionPersistenceError('session_recovery_blocked')
        pending = yield IOCall(validate_saved_approval, (persistence, row, snapshot, root, names))
        try:
            yield IOCall(persistence.storage.finish_run, (session_id, 'interrupted', actual, '', 'interrupted'))
            persistence._install_session(row, root, names, recovering=True, pending=pending)
        except Exception as error:
            logger.error('session_approval_restore_save_failed error_type=%s', type(error).__name__)
            raise SessionPersistenceError('session_save_unconfirmed') from error
        return {'session_id': session_id, 'checkpoint_id': actual, 'checkpoint_ns': '', 'state': 'interrupted'}
    record = recovery_record(row, snapshot)
    if record is not None:
        source_config = {'configurable': {'thread_id': row['thread_id'], 'checkpoint_ns': '',
                                         'checkpoint_id': record['source_checkpoint_id']}}
        try:
            source = yield IOCall(agent.get_state, (source_config,), async_call=agent.aget_state)
        except Exception as error:
            logger.error('session_recovery_source_read_failed error_type=%s', type(error).__name__)
            raise SessionPersistenceError('checkpoint_unreadable') from error
        if (not _identity_matches(row, source) or not source.created_at
                or source.config['configurable'].get('checkpoint_id') != record['source_checkpoint_id']
                or (source.metadata or {}).get('naiyue_recovery') is not None
                or record['pre_tool_approval'] and not _saved_approval(source, source.values.get('messages', []))):
            raise SessionPersistenceError('invalid_recovery_record')
    record = record or {
        'version': 1, 'recovery_id': uuid4().hex, 'session_id': session_id, 'thread_id': row['thread_id'],
        'source_checkpoint_id': actual, 'source_namespace': '', 'original_state': row['state'],
        'pre_tool_approval': classification['kind'] == 'pending_approval', 'phase': None,
    }
    try:
        phases = ('cleared', 'paired', 'finished')
        start = phases.index(record['phase']) + 1 if record['phase'] else 0
        for phase in phases[start:]:
            values = build_recovery_update(snapshot, record) if phase == 'paired' else None
            node = 'tools' if phase == 'paired' else END
            record = {**record, 'phase': phase}
            marked = {**snapshot.config, 'metadata': {'naiyue_recovery': json.dumps(record, sort_keys=True)}}
            updated = yield IOCall(agent.update_state, (marked, values), {'as_node': node}, async_call=agent.aupdate_state)
            snapshot = yield IOCall(agent.get_state, (config,), async_call=agent.aget_state)
            if (not _identity_matches(row, snapshot) or snapshot.config != updated
                    or recovery_record(row, snapshot) != record):
                raise SessionPersistenceError('session_save_unconfirmed')
        missing, invalid = _message_facts(snapshot.values.get('messages', []))
        if (snapshot.next or snapshot.tasks or missing or invalid or snapshot.values.get('tool_execution') != {}
                or snapshot.values.get('run_stopped') is not False):
            raise SessionPersistenceError('session_save_unconfirmed')
        actual = snapshot.config['configurable']['checkpoint_id']
        yield IOCall(persistence.storage.finish_run, (session_id, 'ready', actual, '', 'recovered'))
        persistence._install_session(row, root, names, recovering=True)
    except Exception as error:
        logger.error('session_recovery_save_failed error_type=%s', type(error).__name__)
        raise SessionPersistenceError('session_save_unconfirmed') from error
    return {'session_id': session_id, 'checkpoint_id': actual, 'checkpoint_ns': '',
            'recovery_id': record['recovery_id'], 'state': 'ready'}
