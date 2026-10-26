"""Two connections to one store, with explicit file and RAM opening profiles."""
from contextlib import asynccontextmanager
from dataclasses import dataclass
import logging
from functools import partial
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from naiyue_test_agent.agent_runtime.async_utils import acquire_blocking, await_settled, run_blocking
from naiyue_test_agent.config_paths import StorageSettings
from .project_store import ProjectStore
from naiyue_test_agent.storage.errors import SessionPersistenceError


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StorageResources:
    store: ProjectStore
    checkpointer: AsyncSqliteSaver


@asynccontextmanager
async def open_storage(database_path, *, expected_project=None, registered_project=None, chats=False,
                       storage_settings=None):
    settings = storage_settings if storage_settings is not None else StorageSettings()
    if sum((expected_project is not None, registered_project is not None, chats)) > 1:
        raise ValueError('Storage opening profiles are mutually exclusive')
    if expected_project is not None and (
            database_path is None or Path(database_path).expanduser().absolute() != expected_project.database_path):
        raise SessionPersistenceError('workspace_database_mismatch')
    factory, args = (ProjectStore.in_memory, ()) if database_path is None else (ProjectStore, (database_path,))
    if expected_project is not None:
        factory = partial(ProjectStore, require_existing=True)
    if registered_project is not None:
        if Path(database_path) != registered_project.database_path:
            raise SessionPersistenceError('workspace_database_mismatch')
        factory = partial(ProjectStore, require_existing=True, runtime_profile=registered_project.project_id)
    elif chats:
        factory = partial(ProjectStore, runtime_profile='chats')
    store = await acquire_blocking(factory, lambda item: run_blocking(item.close), *args,
                                  busy_timeout_ms=settings.busy_timeout_ms)
    connection, primary = None, None
    try:
        database = store.database
        if registered_project is not None:
            store.sessions.execution_location = getattr(registered_project.source,'path',None)
        if expected_project is not None:
            binding = await run_blocking(store.sessions.get_workspace_binding)
            if binding != {'workspace_id': expected_project.project_id, 'root_path': str(expected_project.canonical_root)}:
                raise SessionPersistenceError('workspace_database_mismatch')
        target = database.uri if database_path is None else store.path
        if expected_project is not None or registered_project is not None:
            target = store.path.as_uri() + '?mode=rw'
        connection = aiosqlite.connect(target, timeout=settings.busy_timeout_ms / 1000,
            uri=database_path is None or expected_project is not None or registered_project is not None)
        # Retain the connection owner before opening: cancellation must still close it.
        await await_settled(connection)
        saver = AsyncSqliteSaver(connection)
        await saver.setup()
        yield StorageResources(store, saver)
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            if connection is not None:
                await await_settled(connection.close())
        except BaseException as error:
            if primary is None:
                primary = error
                raise
            logger.error('saver_cleanup_secondary_failed error_type=%s', type(error).__name__)
        finally:
            try:
                await run_blocking(store.close)
            except BaseException as error:
                if primary is None:
                    raise
                logger.error('storage_cleanup_secondary_failed error_type=%s', type(error).__name__)
