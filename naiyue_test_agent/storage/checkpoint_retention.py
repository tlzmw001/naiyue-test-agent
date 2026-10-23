"""Optional SQLite maintenance while the finishing root still owns admission."""

import json
import logging
from time import perf_counter

from langgraph.channels.delta import DeltaChannel
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from naiyue_test_agent.agent_runtime.async_utils import run_blocking
from .queries import read_rows, session_row, subagent_references, chat_heads
from naiyue_test_agent.application.sessions.session_recovery import recovery_record


logger = logging.getLogger(__name__)


class RetentionSkipped(Exception):
    """A known safety boundary; its reason is an application-owned constant."""


def _ownership(connection):
    owners = {}
    for row in connection.execute('''SELECT session_id,thread_id FROM naiyue_sessions
            UNION ALL SELECT session_id,thread_id FROM naiyue_chat_sync
            UNION ALL SELECT session_id,child_thread_id FROM naiyue_subagent_instances'''):
        owners.setdefault(row['thread_id'], set()).add(row['session_id'])
    for row in connection.execute('SELECT session_id,old_thread_id,new_thread_id,obsolete_children FROM naiyue_regenerations'):
        children = json.loads(row['obsolete_children'])
        if not isinstance(children, list) or any(not isinstance(item, str) or not item for item in children):
            raise RetentionSkipped('ownership_invalid')
        for thread in (row['old_thread_id'], row['new_thread_id'], *children):
            owners.setdefault(thread, set()).add(row['session_id'])
    return owners


def _inventory(storage, session_id, run_id, settings):
    with storage.database.transaction() as connection:
        row = session_row(connection, session_id)
        if row['state'] != 'ready' or row['run_id'] != run_id or not row['checkpoint_id']:
            raise RetentionSkipped('session_not_confirmed')
        heads = chat_heads(connection, session_id)
        by_thread = {head['thread_id']: head for head in heads}
        root = by_thread.get(row['thread_id'])
        if not root or root['checkpoint_id'] != row['checkpoint_id']:
            raise RetentionSkipped('archive_not_current')
        if any(head['checkpoint_ns'] != '' for head in heads):
            raise RetentionSkipped('namespace_unsupported')
        actions = read_rows(connection, 'SELECT * FROM naiyue_regenerations WHERE session_id=?', (session_id,))
        retired = set()
        for action in actions:
            if action['status'] in ('preparing', 'activated'):
                raise RetentionSkipped('regeneration_unfinished')
            if action['status'] == 'prepare_failed':
                retired.add(action['new_thread_id'])
            elif action['status'] in ('completed', 'failed', 'interrupted', 'recovered'):
                retired.add(action['old_thread_id'])
                children = json.loads(action['obsolete_children'])
                if not isinstance(children, list) or any(not isinstance(item, str) or not item for item in children):
                    raise RetentionSkipped('ownership_invalid')
                retired.update(children)
            else:
                raise RetentionSkipped('regeneration_status_unknown')
        scopes = []
        ordered = [row['thread_id'], *sorted(set(by_thread) - {row['thread_id']}), *sorted(retired - set(by_thread))]
        for thread in ordered:
            threshold = settings.trigger_checkpoints if thread in by_thread else 1
            count = connection.execute('''SELECT COUNT(*) FROM
                (SELECT 1 FROM checkpoints WHERE thread_id=? AND checkpoint_ns='' LIMIT ?)''',
                (thread, threshold)).fetchone()[0]
            if count >= threshold:
                scopes.append(thread)
        boundary = read_rows(connection, '''SELECT * FROM naiyue_reply_boundaries WHERE session_id=? AND thread_id=?
            ORDER BY input_sequence DESC LIMIT 1''', (session_id, row['thread_id'])) if scopes else []
        registered = subagent_references(connection, session_id) if scopes else {}
        return dict(row=row, heads=heads, live=by_thread, retired=retired - set(by_thread),
                    owners=_ownership(connection) if scopes else {},
                    scopes=scopes, boundary=boundary[0] if boundary else None, registered=registered)


def _weight(storage, key):
    with storage.database.transaction() as connection:
        record = connection.execute('''SELECT length(checkpoint)+COALESCE(length(metadata),0)+
            COALESCE((SELECT SUM(length(value)) FROM writes w WHERE w.thread_id=c.thread_id
                AND w.checkpoint_ns=c.checkpoint_ns AND w.checkpoint_id=c.checkpoint_id),0)
            FROM checkpoints c WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?''', key).fetchone()
        if record is None:
            raise RetentionSkipped('protected_checkpoint_missing')
        return record[0]


async def _protected(agent, storage, inventory, settings):
    row = inventory['row']
    protected = {(head['thread_id'], head['checkpoint_ns'], head['checkpoint_id']) for head in inventory['heads']}
    protected.update((item['child_thread_id'], item['checkpoint_ns'], item['checkpoint_id'])
        for item in inventory['registered'].values() if item['checkpoint_id'] is not None)
    protected.add((row['thread_id'], row['checkpoint_ns'], row['checkpoint_id']))
    boundary = inventory['boundary']
    if boundary and boundary['source_checkpoint_id']:
        protected.add((boundary['source_thread_id'] or boundary['thread_id'], '', boundary['source_checkpoint_id']))
    queue = list(protected)
    inspected, read_bytes = set(), 0
    for key in queue:
        if key in inspected:
            continue
        if inventory['owners'].get(key[0]) != {row['session_id']}:
            raise RetentionSkipped('protected_owner_unconfirmed')
        read_bytes += await run_blocking(_weight, storage, key)
        if read_bytes > settings.max_delete_bytes:
            raise RetentionSkipped('inspection_budget')
        thread, namespace, identity = key
        saved = await agent.checkpointer.aget_tuple({'configurable': {
            'thread_id': thread, 'checkpoint_ns': namespace, 'checkpoint_id': identity}})
        if saved is None or saved.checkpoint.get('v') != 4:
            raise RetentionSkipped('checkpoint_format_unsupported')
        record = recovery_record({**row, 'thread_id': thread}, saved)
        if record:
            source = (thread, record['source_namespace'], record['source_checkpoint_id'])
            if source not in protected:
                protected.add(source)
                queue.append(source)
        inspected.add(key)
    return protected


def _candidates(storage, inventory, eligible, protected, settings):
    selected, payload = [], 0
    with storage.database.transaction() as connection:
        for thread in eligible:
            if inventory['owners'].get(thread) != {inventory['row']['session_id']}:
                logger.warning('checkpoint_retention_skipped reason=foreign_owner')
                continue
            kept_count = sum(key[0] == thread for key in protected)
            records = connection.execute('''SELECT checkpoint_id,bytes FROM
                (SELECT checkpoint_id,length(checkpoint)+COALESCE(length(metadata),0)+
                COALESCE((SELECT SUM(length(value)) FROM writes w WHERE w.thread_id=c.thread_id
                    AND w.checkpoint_ns=c.checkpoint_ns AND w.checkpoint_id=c.checkpoint_id),0) AS bytes
                FROM checkpoints c WHERE thread_id=? AND checkpoint_ns='')
                WHERE bytes<=? ORDER BY checkpoint_id LIMIT ?''',
                (thread, settings.max_delete_bytes, settings.max_delete_checkpoints + kept_count)).fetchall()
            for record in records:
                key = (thread, '', record['checkpoint_id'])
                if key in protected:
                    continue
                if record['bytes'] > settings.max_delete_bytes - payload:
                    continue
                selected.append((key, record['bytes']))
                payload += record['bytes']
                if len(selected) == settings.max_delete_checkpoints:
                    return selected
        return selected


def _commit(storage, inventory, candidates, protected):
    row = inventory['row']
    with storage.database.transaction(write=True) as connection:
        current = session_row(connection, row['session_id'])
        if any(current[key] != row[key] for key in ('state', 'run_id', 'thread_id', 'checkpoint_id', 'conversation_revision')):
            raise RetentionSkipped('session_changed')
        heads = chat_heads(connection, row['session_id'])
        if heads != inventory['heads']:
            raise RetentionSkipped('archive_changed')
        if subagent_references(connection, row['session_id']) != inventory['registered']:
            raise RetentionSkipped('registry_changed')
        owners = _ownership(connection)
        checked = set()
        for key, _ in candidates:
            thread, _, _ = key
            if thread not in checked:
                if owners.get(thread) != {row['session_id']}:
                    raise RetentionSkipped('ownership_changed')
                checked.add(thread)
            if key in protected:
                raise RetentionSkipped('protected_candidate')
            connection.execute('DELETE FROM writes WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?', key)
            if connection.execute('DELETE FROM checkpoints WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?', key).rowcount != 1:
                raise RetentionSkipped('candidate_changed')
    return dict(deleted_checkpoints=len(candidates), deleted_bytes=sum(size for _, size in candidates), reason='pruned')


async def prune_after_finish(agent, storage, session_id, run_id, settings, *, snapshot):
    """Required saving has committed; the owning run has not been released yet."""
    empty = dict(deleted_checkpoints=0, deleted_bytes=0)
    if not settings.enabled:
        return {**empty, 'reason': 'disabled'}
    started = perf_counter()
    try:
        if (not isinstance(agent.checkpointer, AsyncSqliteSaver)
                or any(isinstance(channel, DeltaChannel) for channel in agent.channels.values())):
            raise RetentionSkipped('backend_or_channel_unsupported')
        if snapshot is None or snapshot.next or snapshot.tasks:
            raise RetentionSkipped('root_unfinished')
        inventory = await run_blocking(_inventory, storage, session_id, run_id, settings)
        if not inventory['scopes']:
            return {**empty, 'reason': 'below_threshold'}
        protected = await _protected(agent, storage, inventory, settings)
        eligible = []
        for thread in inventory['scopes']:
            if thread in inventory['retired']:
                eligible.append(thread)
                continue
            saved = snapshot if thread == inventory['row']['thread_id'] else await agent.aget_state(
                {'configurable': {'thread_id': thread, 'checkpoint_ns': ''}})
            identity = (saved.config or {}).get('configurable', {}).get('checkpoint_id')
            if saved.next or saved.tasks or identity != inventory['live'][thread]['checkpoint_id']:
                logger.warning('checkpoint_retention_skipped reason=thread_unfinished')
                continue
            eligible.append(thread)
        candidates = await run_blocking(_candidates, storage, inventory, eligible, protected, settings)
        if not candidates:
            return {**empty, 'reason': 'protected_or_budget'}
        result = await run_blocking(_commit, storage, inventory, candidates, protected)
        logger.info('checkpoint_retention_finished checkpoints=%s bytes=%s duration_ms=%.3f',
                    result['deleted_checkpoints'], result['deleted_bytes'], (perf_counter() - started) * 1000)
        return result
    except RetentionSkipped as error:
        logger.warning('checkpoint_retention_skipped reason=%s', str(error))
        return {**empty, 'reason': str(error)}
