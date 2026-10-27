"""HistoryAPI operations with explicit dependencies and shared lifetime ownership."""
from naiyue_test_agent.agent_runtime.async_utils import await_settled, run_blocking

from naiyue_test_agent.application.subagents.subagent_history import subagent_records


class HistoryAPI:
    def __init__(self, owner, sessions, persistence, agent, catalog, registry, system_prompt, metadata, conversation, subagents):
        self._owner, self._sessions, self._persistence = owner, sessions, persistence
        self._agent, self._catalog, self._tool_registry = agent, catalog, registry
        self._system_prompt = system_prompt
        self._metadata, self._conversation, self._subagents = metadata, conversation, subagents

    async def messages(self, session_id):
        with self._owner.operation():
            thread = self._sessions.require(session_id).get_thread_id()
            snapshot = await self._agent.aget_state({'configurable': {'thread_id': thread}})
            return [m.model_copy(deep=True) for m in snapshot.values.get('messages', [])]

    async def image(self, session_id, message_id, index, *, source_revision, conversation_revision):
        from .image_history import read_image
        with self._owner.operation():
            row = await run_blocking(self._metadata.get, session_id)
            return await read_image(self, row, session_id, message_id, index,
                source_revision=source_revision, conversation_revision=conversation_revision)

    async def context(self, session_id):
        with self._owner.operation():
            from naiyue_test_agent.application.sessions.session_context import context_view
            history = None if self._sessions.has_session(session_id) else await self.session(session_id)
            return await context_view(session_id, sessions=self._sessions, agent=self._agent,
                catalog=self._catalog, tool_registry=self._tool_registry,
                system_prompt=self._system_prompt, history=history)

    async def subagents(self, session_id):
        with self._owner.operation():
            owned = {}
            if self._sessions.has_session(session_id):
                messages = await self.messages(session_id)
                owned = await self._sessions.require(session_id).subagents.asubagent_references()
            else:
                messages = (await self.session(session_id))['messages']
                owned = await run_blocking(self._subagents.get_subagent_references, session_id)
            from naiyue_test_agent.application.subagents.subagent_history import merge_registered_subagents
            records = merge_registered_subagents(subagent_records(session_id, messages, owned_instances=owned), owned)
            for row in records:
                if row['status'] == 'failed':
                    snapshot = await self._agent.aget_state({'configurable': {'thread_id': f'subagent:{row["agent_id"]}'}})
                    if snapshot.values.get('run_stopped'):
                        row['status'] = 'stopped'
            from naiyue_test_agent.application.subagents.subagent_queries import annotate
            active = self._sessions.require(session_id).describe_session()['run_id'] if self._sessions.has_session(session_id) else None
            return annotate(records, owned, self._sessions.get_registered_subagents(), self._catalog, active)

    async def subagent(self, session_id, agent_id):
        with self._owner.operation():
            record = next((row for row in await self.subagents(session_id) if row['agent_id'] == agent_id), None)
            if record is None:
                raise LookupError('Subagent does not belong to this session')
            # Same state schema and saver as the root; no role/configuration is required to read history.
            snapshot = await self._agent.aget_state({'configurable': {'thread_id': f'subagent:{agent_id}'}})
            messages = [m.model_copy(deep=True) for m in snapshot.values.get('messages', [])]
            if snapshot.values.get('run_stopped'):
                record['status'] = 'stopped'
            return {**record, 'messages': messages,
                    'available': bool((snapshot.config or {}).get('configurable', {}).get('checkpoint_id')),
                    'has_compaction': any(m.additional_kwargs.get('naiyue_compaction_summary') for m in messages)}

    async def page(self, session_id, *, agent_id=None, **query):
        with self._owner.operation():
            row = await run_blocking(self._metadata.get, session_id)
            return await run_blocking(self._conversation.read_chat_page, session_id,
                                      'subagent:' + agent_id if agent_id else row['thread_id'], **query)

    async def message(self, session_id, sequence, *, agent_id=None, **query):
        with self._owner.operation():
            row = await run_blocking(self._metadata.get, session_id)
            return await run_blocking(self._conversation.read_chat_message, session_id,
                                      'subagent:' + agent_id if agent_id else row['thread_id'], sequence, **query)

    async def reasoning(self, session_id, sequence, *, revision, agent_id, conversation_revision, offset, chars, max_bytes):
        with self._owner.operation():
            import json
            from langchain_core.messages import messages_from_dict
            from naiyue_test_agent.model_access.reasoning import visible_reasoning
            from naiyue_test_agent.storage.errors import SessionPersistenceError
            row = await run_blocking(self._metadata.get, session_id)
            thread = 'subagent:' + agent_id if agent_id else row['thread_id']
            records = await run_blocking(self._conversation.reasoning_body, session_id, thread, sequence, conversation_revision, max_bytes)
            if not records or records[0]['revision'] != revision or records[0]['body'] is None:
                raise SessionPersistenceError('history_cursor_stale')
            text = visible_reasoning(messages_from_dict([json.loads(records[0]['body'])])[0])
            if offset > len(text):
                raise SessionPersistenceError('history_invalid_offset')
            chunk = text[offset:offset + chars]
            return {'text': chunk, 'next_offset': offset + len(chunk), 'complete': offset + len(chunk) >= len(text)}

    async def file_change(self, session_id, sequence, *, revision, agent_id=None, conversation_revision=0, max_bytes):
        with self._owner.operation():
            row = await run_blocking(self._metadata.get, session_id)
            thread = 'subagent:' + agent_id if agent_id else row['thread_id']
            records = await run_blocking(self._conversation.file_change_body, session_id, thread, sequence, conversation_revision, max_bytes)
            if not records or records[0]['revision'] != revision or records[0]['artifact'] is None:
                from naiyue_test_agent.storage.errors import SessionPersistenceError
                raise SessionPersistenceError('history_cursor_stale')
            import json
            return json.loads(records[0]['artifact'])

    async def chat_subagents(self, session_id):
        with self._owner.operation():
            await run_blocking(self._metadata.get, session_id)
            from naiyue_test_agent.application.subagents.subagent_queries import annotate
            from naiyue_test_agent.application.subagents.subagent_history import merge_registered_subagents
            archived, registered = await run_blocking(self._conversation.subagent_snapshot, session_id)
            records = merge_registered_subagents(archived, registered)
            owned = await run_blocking(self._subagents.get_subagent_references, session_id)
            active = self._sessions.require(session_id).describe_session()['run_id'] if self._sessions.has_session(session_id) else None
            return annotate(records, owned, self._sessions.get_registered_subagents(), self._catalog, active)

    async def approval_seed(self, session_id):
        with self._owner.operation():
            identity = self._sessions.require(session_id).approval_seed_identity()
            if identity is None:
                return []
            thread_id, batch_id = identity
            return await run_blocking(self._conversation.read_approval_seed,
                                      session_id, thread_id, batch_id)

    async def session(self, session_id):
        with self._owner.operation():
            return await self._persistence.ahistory(session_id, self._agent)
