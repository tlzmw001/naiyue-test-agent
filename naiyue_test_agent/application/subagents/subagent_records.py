"""Application-owned continuation admission and required instance saving."""

from datetime import datetime, timezone
from hashlib import sha256
import asyncio
import json

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import ToolException

from naiyue_test_agent.agent_runtime.async_utils import run_blocking, await_settled
from naiyue_test_agent.agent_runtime.subagents import subagent_result_envelope


def role_fingerprint(spec):
    contract = [spec.name, spec.system_prompt, list(spec.tool_names), spec.model_id, spec.max_model_calls]
    return sha256(json.dumps(contract, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


async def _get(owner, identity):
    return await run_blocking(owner._persistence.storage.subagents.get_subagent, owner.session_id, identity)


async def _save(owner, record, expected_version):
    await run_blocking(owner._persistence.storage.subagents.save_subagent, record, expected_version)


def _clean(snapshot, thread_id):
    config = (snapshot.config or {}).get('configurable', {})
    messages = snapshot.values.get('messages', [])
    if (config.get('thread_id') != thread_id or config.get('checkpoint_ns', '') != ''
            or not config.get('checkpoint_id') or snapshot.next or snapshot.tasks or snapshot.interrupts
            or snapshot.values.get('run_stopped') or not messages
            or not isinstance(messages[-1], AIMessage) or messages[-1].tool_calls):
        raise ToolException('Subagent checkpoint is not confirmed for continuation.')
    pending = set()
    for message in messages:
        if isinstance(message, AIMessage):
            if pending:
                raise ToolException('Subagent history has unconfirmed tool results.')
            pending.update(call['id'] for call in message.tool_calls)
        elif isinstance(message, ToolMessage):
            if message.tool_call_id not in pending:
                raise ToolException('Subagent history has unconfirmed tool results.')
            pending.remove(message.tool_call_id)
        elif pending:
            raise ToolException('Subagent history has unconfirmed tool results.')
    if pending:
        raise ToolException('Subagent history has unconfirmed tool results.')
    return config


async def admit(owner, graph, instance, spec):
    if len(subagent_result_envelope(instance).encode('utf-8')) > owner._subagent_result_settings.max_result_bytes:
        raise ToolException('Subagent result budget cannot hold its instance metadata.')
    previous = await _get(owner, instance['agent_id'])
    from naiyue_test_agent.tools.permissions import require_execution_directory
    from naiyue_test_agent.storage.execution_storage import root_from_row
    saved_root = root_from_row(previous, child=True) if previous is not None else instance['execution_root']
    saved_project = previous['project_id'] if previous is not None else instance['project_id']
    require_execution_directory(saved_root, saved_project)
    if saved_root != instance['execution_root'] or saved_project != instance['project_id']:
        raise ToolException('Subagent execution binding changed; the saved instance was not rebound.')
    if instance['continuing']:
        if previous is None:
            raise ToolException('Unknown or legacy subagent instance; create a new instance.')
        if (previous['session_id'] != owner.session_id or previous['agent'] != spec.name
                or previous['agent_path'] != instance['agent_path']
                or previous['child_thread_id'] != instance['child_thread_id']):
            raise ToolException('Subagent instance does not match this session and role.')
        if previous['role_fingerprint'] != role_fingerprint(spec):
            raise ToolException('Subagent role configuration changed; create a new instance.')
        if previous['state'] == 'retired':
            raise ToolException('Subagent instance was retired by reply regeneration; create a new instance.')
        allowed_pending = previous['state'] == 'pending_parent' and previous['last_parent_run_id'] == instance['parent_run_id']
        if previous['state'] != 'ready' and not allowed_pending:
            raise ToolException('Subagent instance cannot continue from its current state.')
        snapshot = await graph.aget_state({'configurable': {'thread_id': instance['child_thread_id']}})
        config = _clean(snapshot, instance['child_thread_id'])
        if any(config.get(key, '') != previous[key] for key in ('checkpoint_ns', 'checkpoint_id')):
            raise ToolException('Subagent checkpoint changed after its confirmed save.')
        instance['model_id'] = previous['model_id']
    elif previous is not None:
        raise ToolException('Subagent call has already been admitted; replay is not allowed.')
    owner._catalog.get(instance['model_id'])
    row = await run_blocking(owner._persistence.storage.sessions.get, owner.session_id)
    actual_root = str(instance['workspace_root']) if instance['workspace_root'] is not None else None
    if row['workspace_root'] != actual_root:
        raise ToolException('Subagent workspace binding changed.')
    with owner._lock:
        owner._require_subagent(instance)
        if owner._run.stop_requested:
            raise ToolException('Subagent was not started because its owning run is stopping.')
    now = _now()
    record = dict(session_id=owner.session_id, agent_id=instance['agent_id'], agent_path=instance['agent_path'],
        child_thread_id=instance['child_thread_id'], agent=spec.name, role_fingerprint=role_fingerprint(spec),
        model_id=instance['model_id'], last_task=instance['task'], state='running',
        project_id=saved_project, execution_root=str(saved_root.path) if saved_root else None,
        execution_root_kind=saved_root.kind if saved_root else None,
        version=previous['version'] + 1 if previous else 1,
        checkpoint_ns=previous['checkpoint_ns'] if previous else '', checkpoint_id=previous['checkpoint_id'] if previous else None,
        last_delegation_id=instance['delegation_id'], last_parent_run_id=instance['parent_run_id'],
        last_parent_batch_id=instance['parent_batch_id'], last_parent_tool_call_id=instance['parent_tool_call_id'],
        last_parent_reply_id=instance['parent_reply_id'], created_at=previous['created_at'] if previous else now, updated_at=now)
    claim = asyncio.create_task(_save(owner, record, previous['version'] if previous else None))
    try:
        await await_settled(claim)
    finally:
        # Cancellation drains the database writer. Publish ownership even when
        # the caller is cancelled after a successful write, so settle can undo it.
        if claim.done() and not claim.cancelled() and claim.exception() is None:
            instance.update(record=record, previous_record=previous)


async def settle(owner, instance):
    record = instance.get('record')
    if record is None:
        return
    if not instance.get('input_started'):
        previous = instance['previous_record']
        await run_blocking(owner._persistence.storage.subagents.cancel_subagent_claim, record, previous)
        return
    updated = {**record, 'version': record['version'] + 1, 'updated_at': _now(),
               'state': 'stopped' if instance['status'] == 'stopped' else 'failed'}
    if instance['status'] == 'completed':
        config = _clean(instance['final_snapshot'], instance['child_thread_id'])
        updated.update(state='pending_parent', checkpoint_id=config['checkpoint_id'], checkpoint_ns=config.get('checkpoint_ns', ''))
    await _save(owner, updated, record['version'])
    instance['record'] = updated

