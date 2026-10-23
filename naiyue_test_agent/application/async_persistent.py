"""Own official async checkpoints and the existing private metadata storage."""

import logging
from contextlib import asynccontextmanager

from naiyue_test_agent.agent_runtime.async_utils import run_blocking, await_settled
from .async_application import AsyncApplication


logger = logging.getLogger(__name__)


class AsyncPersistentApplication(AsyncApplication):
    def __init__(self, catalog, sessions, agent, tool_registry, *, system_prompt, persistence, storage, usage, diagnostics, resources):
        super().__init__(catalog, sessions, agent, tool_registry, system_prompt=system_prompt)
        self._persistence, self._storage = persistence, storage
        self._usage = usage
        self._diagnostics = diagnostics
        self._resources = resources

    async def _close_resources(self):
        primary = None
        try:
            await super()._close_resources()
        except BaseException as error:
            primary = error
            raise
        finally:
            try:
                await self._resources.aclose()
            except BaseException as error:
                if primary is None:
                    raise
                logger.error('application_storage_cleanup_secondary_failed error_type=%s', type(error).__name__)

    async def aprepare_session(self, session_id):
        with self._owned_operation():
            await await_settled(self._persistence.aprepare(session_id, self._agent))
            return await self.aget_session_status(session_id)

    async def aget_session_status(self, session_id):
        with self._owned_operation():
            return await self._persistence.astatus(session_id)

    async def aget_regeneration_action(self, session_id, request_id):
        with self._owned_operation():
            from .conversation.reply_storage import action
            return await run_blocking(self._storage.replies.action, session_id, request_id)

    async def aregenerate_last_reply(self, session_id, *, request_id, expected_reply_id, expected_conversation_revision):
        with self._owned_operation():
            from .conversation.session_regeneration import regenerate
            return await regenerate(self, session_id, request_id=request_id, expected_reply_id=expected_reply_id,
                                    expected_conversation_revision=expected_conversation_revision)

    async def aget_model_usage(self, session_id, *, model_limit, after_model=None):
        with self._owned_operation():
            return await run_blocking(self._storage.usage.read_model_usage, session_id,
                model_limit=model_limit, after_model=after_model)

    async def alist_diagnostic_runs(self, session_id, *, limit=50, before=None):
        with self._owned_operation():
            return await run_blocking(self._storage.diagnostics.read_diagnostic_runs, session_id, limit=limit, before=before)

    async def aget_diagnostic_events(self, session_id, run_id, *, limit=50, after=0):
        with self._owned_operation():
            return await run_blocking(self._storage.diagnostics.read_diagnostic_events, session_id, run_id, limit=limit, after=after)

    async def aget_chat_page(self, session_id, *, agent_id=None, **query):
        with self._owned_operation():
            storage = self._storage
            row = await run_blocking(storage.sessions.get, session_id)
            return await run_blocking(storage.conversation.read_chat_page, session_id,
                                      'subagent:' + agent_id if agent_id else row['thread_id'], **query)

    async def aget_chat_message(self, session_id, sequence, *, agent_id=None, **query):
        with self._owned_operation():
            storage = self._storage
            row = await run_blocking(storage.sessions.get, session_id)
            return await run_blocking(storage.conversation.read_chat_message, session_id,
                                      'subagent:' + agent_id if agent_id else row['thread_id'], sequence, **query)

    async def aget_message_reasoning(self, session_id, sequence, *, revision, agent_id, conversation_revision, offset, chars, max_bytes):
        with self._owned_operation():
            import json
            from langchain_core.messages import messages_from_dict
            from naiyue_test_agent.model_access.reasoning import visible_reasoning
            from .errors import SessionPersistenceError
            row = await run_blocking(self._storage.sessions.get, session_id)
            thread = 'subagent:' + agent_id if agent_id else row['thread_id']
            records = await run_blocking(self._storage.conversation.reasoning_body, session_id, thread, sequence, conversation_revision, max_bytes)
            if not records or records[0]['revision'] != revision or records[0]['body'] is None:
                raise SessionPersistenceError('history_cursor_stale')
            text = visible_reasoning(messages_from_dict([json.loads(records[0]['body'])])[0])
            if offset > len(text):
                raise SessionPersistenceError('history_invalid_offset')
            chunk = text[offset:offset + chars]
            return {'text': chunk, 'next_offset': offset + len(chunk), 'complete': offset + len(chunk) >= len(text)}

    async def aget_file_change(self, session_id, sequence, *, revision, agent_id=None, conversation_revision=0, max_bytes):
        with self._owned_operation():
            row = await run_blocking(self._storage.sessions.get, session_id)
            thread = 'subagent:' + agent_id if agent_id else row['thread_id']
            records = await run_blocking(self._storage.conversation.file_change_body, session_id, thread, sequence, conversation_revision, max_bytes)
            if not records or records[0]['revision'] != revision or records[0]['artifact'] is None:
                from .errors import SessionPersistenceError
                raise SessionPersistenceError('history_cursor_stale')
            import json
            return json.loads(records[0]['artifact'])

    async def aget_chat_subagents(self, session_id):
        with self._owned_operation():
            from .subagents.subagent_queries import annotate
            records = await run_blocking(self._storage.conversation.chat_subagents, session_id)
            owned = await run_blocking(self._storage.subagents.get_subagent_references, session_id)
            active = self._sessions.require(session_id).describe_session()['run_id'] if self.has_session(session_id) else None
            return annotate(records, owned, self.get_registered_subagents(), self._catalog, active)

    async def aget_approval_seed(self, session_id):
        with self._owned_operation():
            identity = self._sessions.require(session_id).approval_seed_identity()
            if identity is None:
                return []
            thread_id, batch_id = identity
            return await run_blocking(self._storage.conversation.read_approval_seed,
                                      session_id, thread_id, batch_id)

    async def aupdate_session_presentation(self, session_id, **changes):
        with self._owned_operation():
            await run_blocking(self._storage.sessions.update_presentation, session_id, **changes)

    async def areorder_sessions(self, workspace_root, identities):
        with self._owned_operation():
            await run_blocking(self._storage.sessions.reorder, workspace_root, identities)

    async def adelete_session(self, session_id):
        with self._owned_operation():
            await await_settled(self._persistence.adelete(session_id, self._agent))

    async def arecover_session(self, session_id, *, checkpoint_id, action):
        with self._owned_operation():
            return await self._persistence.arecover(session_id, self._agent, checkpoint_id=checkpoint_id, action=action)

    async def aget_session_history(self, session_id):
        with self._owned_operation():
            return await self._persistence.ahistory(session_id, self._agent)

    async def arestore_session(self, session_id):
        with self._owned_operation():
            return await self._persistence.arestore(session_id, self._agent)

    async def alist_sessions(self):
        with self._owned_operation():
            return await run_blocking(self._storage.sessions.list_sessions)

    async def alist_workspace_sessions(self, workspace_root):
        with self._owned_operation():
            return await run_blocking(self._storage.sessions.list_workspace_sessions, workspace_root)


@asynccontextmanager
async def open_async_persistent_application(config_path, *, database_path, tools, system_prompt, response_format, initial_active_tools=None, settings=None, workspace_binding=None):
    from .assembly import build_application
    app = await build_application(config_path, database_path=database_path, tools=tools,
        system_prompt=system_prompt, response_format=response_format, initial_active_tools=initial_active_tools,
        settings=settings, workspace_binding=workspace_binding)
    primary = None
    try:
        yield app
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            await app.aclose()
        except BaseException as error:
            if primary is None:
                raise
            logger.error('application_cleanup_secondary_failed error_type=%s', type(error).__name__)
