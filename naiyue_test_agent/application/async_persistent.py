"""Own official async checkpoints and the existing private metadata storage."""

import logging
from contextlib import asynccontextmanager

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from naiyue_test_agent import application
from naiyue_test_agent.agent_runtime.async_utils import run_blocking, await_settled, acquire_blocking
from .async_application import AsyncApplication
from .session_persistence import SessionPersistence
from .session_storage import SessionStorage
from .model_usage_persistence import ModelUsagePersistence
from .diagnostic_persistence import DiagnosticPersistence


logger = logging.getLogger(__name__)


class AsyncPersistentApplication(AsyncApplication):
    def __init__(self, catalog, sessions, agent, tool_registry, *, system_prompt, persistence, storage, usage, diagnostics):
        super().__init__(catalog, sessions, agent, tool_registry, system_prompt=system_prompt)
        self._persistence, self._storage = persistence, storage
        self._usage = usage
        self._diagnostics = diagnostics

    async def _close_resources(self):
        try:
            await super()._close_resources()
        finally:
            try:
                await self._usage.aclose()
            finally:
                await self._diagnostics.aclose()

    async def aprepare_session(self, session_id):
        with self._owned_operation():
            await await_settled(self._persistence.aprepare(session_id, self._agent))
            return await self.aget_session_status(session_id)

    async def aget_session_status(self, session_id):
        with self._owned_operation():
            return await self._persistence.astatus(session_id)

    async def aget_model_usage(self, session_id, *, model_limit, after_model=None):
        with self._owned_operation():
            return await run_blocking(self._storage.read_model_usage, session_id,
                model_limit=model_limit, after_model=after_model)

    async def alist_diagnostic_runs(self, session_id, *, limit=50, before=None):
        with self._owned_operation():
            return await run_blocking(self._storage.read_diagnostic_runs, session_id, limit=limit, before=before)

    async def aget_diagnostic_events(self, session_id, run_id, *, limit=50, after=0):
        with self._owned_operation():
            return await run_blocking(self._storage.read_diagnostic_events, session_id, run_id, limit=limit, after=after)

    async def aget_chat_page(self, session_id, *, agent_id=None, **query):
        with self._owned_operation():
            storage = self._storage
            row = await run_blocking(storage.get, session_id)
            return await run_blocking(storage.read_chat_page, session_id,
                                      'subagent:' + agent_id if agent_id else row['thread_id'], **query)

    async def aget_chat_message(self, session_id, sequence, *, agent_id=None, **query):
        with self._owned_operation():
            storage = self._storage
            row = await run_blocking(storage.get, session_id)
            return await run_blocking(storage.read_chat_message, session_id,
                                      'subagent:' + agent_id if agent_id else row['thread_id'], sequence, **query)

    async def aget_chat_subagents(self, session_id):
        with self._owned_operation():
            return await run_blocking(self._storage.chat_subagents, session_id)

    async def aget_approval_seed(self, session_id):
        with self._owned_operation():
            identity = self._sessions.require(session_id).approval_seed_identity()
            if identity is None:
                return []
            thread_id, batch_id = identity
            return await run_blocking(self._storage.read_approval_seed,
                                      session_id, thread_id, batch_id)

    async def aupdate_session_presentation(self, session_id, **changes):
        with self._owned_operation():
            await run_blocking(self._storage.update_presentation, session_id, **changes)

    async def areorder_sessions(self, workspace_root, identities):
        with self._owned_operation():
            await run_blocking(self._storage.reorder, workspace_root, identities)

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
            return await run_blocking(self._storage.list_sessions)

    async def alist_workspace_sessions(self, workspace_root):
        with self._owned_operation():
            return await run_blocking(self._storage.list_workspace_sessions, workspace_root)


@asynccontextmanager
async def open_async_persistent_application(config_path, *, database_path, tools, system_prompt, response_format, initial_active_tools=None, settings=None, workspace_binding=None):
    settings = settings if settings is not None else application.load_settings()
    registry = application.prepare_tool_registry(tools)
    application.validate_permission_rules(settings.permissions, registry.select(registry.names()))
    initial = registry.names() if initial_active_tools is None else initial_active_tools
    registry.select(initial)
    storage, catalog, app = None, None, None
    primary = None
    try:
        storage = await acquire_blocking(SessionStorage, lambda item: run_blocking(item.close), database_path)
        if workspace_binding is not None:
            await run_blocking(storage.bind_workspace, workspace_binding['workspace_id'], workspace_binding['root_path'])
        catalog = await acquire_blocking(application.load_model_catalog, lambda item: item.aclose(), config_path)
        await run_blocking(catalog.prepare_async_clients)
        async with AsyncSqliteSaver.from_conn_string(str(storage.path)) as saver:
            # Diagnostics can write at run_started, before the first graph read.
            # Finish the official WAL/schema initialization before subscribing.
            await saver.setup()
            sessions = application.SessionService(catalog, registry, initial_active_tools=initial, permission_settings=settings.permissions, progress_settings=settings.progress,
                max_active_runs=settings.max_active_runs)
            async with (ModelUsagePersistence(storage, sessions.subscribe_events, settings.usage_persistence) as usage,
                        DiagnosticPersistence(storage, sessions.subscribe_events, settings.diagnostics) as diagnostics):
                persistence = SessionPersistence(storage, sessions, catalog=catalog, tool_registry=registry,
                    permission_settings=settings.permissions, usage_persistence=usage)
                sessions.bind_persistence(persistence)
                agent = sessions.assemble_runtime(saver, system_prompt=system_prompt, response_format=response_format,
                    subagents=settings.subagents, subagent_settings=settings.tools.subagent)
                app = AsyncPersistentApplication(catalog, sessions, agent, registry, system_prompt=system_prompt,
                    persistence=persistence, storage=storage, usage=usage, diagnostics=diagnostics)
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
    except BaseException as error:
        if primary is not None and primary is not error:
            logger.error('saver_cleanup_secondary_failed error_type=%s', type(error).__name__)
            raise primary
        primary = error
        raise
    finally:
        if catalog is not None and app is None:
            try:
                await await_settled(catalog.aclose())
            except Exception as error:
                if primary is None:
                    raise
                logger.error('model_cleanup_secondary_failed error_type=%s', type(error).__name__)
        if storage is not None:
            try:
                await run_blocking(storage.close)
            except Exception as error:
                if primary is None:
                    raise
                logger.error('storage_cleanup_secondary_failed error_type=%s', type(error).__name__)
