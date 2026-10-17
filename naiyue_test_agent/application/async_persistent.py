"""Own official async checkpoints and the existing private metadata storage."""

import logging
from contextlib import asynccontextmanager

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph._internal._constants import CONFIG_KEY_DURABILITY

from naiyue_test_agent import application
from naiyue_test_agent.agent_runtime.async_utils import run_blocking, await_settled, acquire_blocking
from .async_application import AsyncApplication
from .session_persistence import SessionPersistence
from .session_storage import SessionStorage


logger = logging.getLogger(__name__)


class AsyncPersistentApplication(AsyncApplication):
    async def arecover_session(self, session_id, *, checkpoint_id, action):
        self._check_open()
        return await self._sessions._persistence.arecover(session_id, self._agent, checkpoint_id=checkpoint_id, action=action)

    async def aget_session_history(self, session_id):
        self._check_open()
        return await self._sessions._persistence.ahistory(session_id, self._agent)

    async def arestore_session(self, session_id):
        self._check_open()
        return await self._sessions._persistence.arestore(session_id, self._agent)

    async def alist_sessions(self):
        self._check_open()
        return await run_blocking(self._sessions._persistence.storage.list_sessions)


@asynccontextmanager
async def open_async_persistent_application(config_path, *, database_path, tools, system_prompt, response_format, initial_active_tools=None, settings=None, workspace_binding=None):
    settings = settings if settings is not None else application.load_tool_settings()
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
        await run_blocking(catalog._prepare_async_clients)
        async with AsyncSqliteSaver.from_conn_string(str(storage.path)) as saver:
            sessions = application.SessionService(catalog, registry, initial_active_tools=initial, permission_settings=settings.permissions, progress_settings=settings.progress)
            sessions._persistence = SessionPersistence(storage, sessions)
            agent = application._assemble_application(catalog, sessions, saver,
                tool_registry=registry, system_prompt=system_prompt, response_format=response_format)
            agent = agent.with_config({'configurable': {CONFIG_KEY_DURABILITY: 'sync'}})
            app = AsyncPersistentApplication(catalog, sessions, agent, registry)
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
