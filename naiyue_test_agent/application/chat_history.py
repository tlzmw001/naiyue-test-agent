"""Collect saved chat facts without running the graph or observing token events."""

import json
import logging
from dataclasses import dataclass
from hashlib import sha256

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage, message_to_dict

from naiyue_test_agent.agent_runtime.async_utils import run_blocking
from .errors import SessionPersistenceError
from .subagent_history import subagent_records


logger = logging.getLogger(__name__)
HISTORY_PAGE_SIZE = 64


@dataclass(frozen=True)
class ChatUpdate:
    thread_id: str
    checkpoint_ns: str
    agent_path: str
    expected_revision: int
    checkpoint_id: str
    messages: tuple
    stopped: bool = False
    boundaries: tuple = ()


def _checkpoint(config, thread_id):
    values = (config or {}).get('configurable', {})
    if values.get('thread_id') != thread_id or values.get('checkpoint_ns', '') != '':
        raise SessionPersistenceError('chat_history_identity_mismatch')
    return values.get('checkpoint_id')


async def _new_snapshots(agent, config, latest, boundary):
    """Walk only the selected ancestry, using bounded official history pages."""
    expected = _checkpoint(latest.config, config['configurable']['thread_id'])
    if not expected:
        if boundary or latest.values.get('messages'):
            raise SessionPersistenceError('chat_history_source_missing')
        return []
    if expected == boundary:
        return []
    snapshots, before = [], None
    while expected != boundary:
        page = [snapshot async for snapshot in agent.aget_state_history(
            config, before=before, limit=HISTORY_PAGE_SIZE)]
        if not page:
            raise SessionPersistenceError('chat_history_source_missing')
        for snapshot in page:
            identity = _checkpoint(snapshot.config, config['configurable']['thread_id'])
            if identity != expected:
                continue  # Saved siblings are not part of the selected branch.
            snapshots.append(snapshot)
            expected = (_checkpoint(snapshot.parent_config, config['configurable']['thread_id'])
                        if snapshot.parent_config else None)
            if expected == boundary:
                break
            if expected is None:
                raise SessionPersistenceError('chat_history_source_missing')
        next_before = page[-1].config
        if next_before == before:
            raise SessionPersistenceError('chat_history_source_missing')
        before = next_before
    return list(reversed(snapshots))


def _messages(snapshot):
    messages = snapshot.values.get('messages', [])
    if not isinstance(messages, list):
        raise SessionPersistenceError('chat_history_invalid_messages')
    identities = set()
    for message in messages:
        if not isinstance(message, BaseMessage) or not isinstance(message.id, str) or not message.id or message.id in identities:
            raise SessionPersistenceError('chat_history_invalid_messages')
        identities.add(message.id)
    return messages


async def collect_chat_updates(agent, storage, session_id, *, snapshot=None, previous=()):
    """Return candidates; only the storage owner's finish transaction commits them.

    `previous` carries pre-recovery candidates across the three state updates;
    its boundary avoids recollecting those checkpoints before the final commit.
    """
    try:
        row = await run_blocking(storage.get, session_id)
        heads = {item['thread_id']: item for item in await run_blocking(storage.chat_heads, session_id)}
        prior = {item.thread_id: item for item in previous}
        scopes = {row['thread_id']: 'root'}
        updates = []
        queue = list(scopes.items())
        for thread_id, agent_path in queue:
            config = {'configurable': {'thread_id': thread_id, 'checkpoint_ns': ''}}
            latest = snapshot if thread_id == row['thread_id'] and snapshot is not None else await agent.aget_state(config)
            identity = _checkpoint(latest.config, thread_id)
            head = heads.get(thread_id)
            old = prior.get(thread_id)
            boundary = old.checkpoint_id if old else head['checkpoint_id'] if head else None
            snapshots = await _new_snapshots(agent, config, latest, boundary)
            if not identity:
                continue  # A refused/unstarted delegation has no child checkpoint.
            candidates = {item['message_id']: item for item in old.messages} if old else {}
            boundaries = {}
            for saved in [*snapshots, latest]:
                source = _checkpoint(saved.config, thread_id)
                messages = _messages(saved)
                owner = None
                call_replies = {}
                for message in messages:
                    if not isinstance(message, ToolMessage):
                        owner = message if isinstance(message, AIMessage) else None
                    reply_id = message.additional_kwargs.get('naiyue_reply_id')
                    if isinstance(message, AIMessage):
                        call_replies.update({c['id']: reply_id for c in message.tool_calls})
                    if isinstance(message, ToolMessage):
                        reply_id = call_replies.get(message.tool_call_id)
                    if (reply_id and isinstance(message, HumanMessage)
                            and message.additional_kwargs.get('naiyue_input_kind') != 'steering'):
                        boundaries.setdefault(reply_id, dict(reply_id=reply_id, input_message_id=message.id,
                            source_checkpoint_id=(_checkpoint(saved.parent_config, thread_id) if saved.parent_config else None)))
                    parent = (owner.id if isinstance(message, ToolMessage) and owner and
                              any(call['id'] == message.tool_call_id for call in owner.tool_calls) else None)
                    body = json.dumps(message_to_dict(message), ensure_ascii=False, sort_keys=True,
                                      separators=(',', ':'), allow_nan=False)
                    digest = sha256(body.encode('utf-8')).hexdigest()
                    if message.id in candidates and candidates[message.id]['body_hash'] == digest:
                        continue
                    candidates[message.id] = {
                        'message_id': message.id, 'body_json': body, 'reply_id': reply_id,
                        'body_hash': digest,
                        'kind': 'context_summary' if message.additional_kwargs.get('naiyue_compaction_summary') is True else 'message',
                        'source_checkpoint_id': source,
                        'message_type': message.type, 'parent_message_id': parent,
                    }
                if agent_path == 'root':
                    for child in subagent_records(session_id, messages):
                        key = 'subagent:' + child['agent_id']
                        if key not in scopes:
                            scopes[key] = child['agent_path']
                            queue.append((key, child['agent_path']))
            updates.append(ChatUpdate(thread_id, '', agent_path, old.expected_revision if old else
                                      head['revision'] if head else 0, identity, tuple(candidates.values()),
                                      latest.values.get('run_stopped') is True, tuple(boundaries.values())))
        return tuple(updates)
    except Exception as error:
        logger.error('chat_history_collect_failed error_type=%s', type(error).__name__)
        if isinstance(error, SessionPersistenceError):
            raise
        raise SessionPersistenceError('chat_history_unavailable') from error
