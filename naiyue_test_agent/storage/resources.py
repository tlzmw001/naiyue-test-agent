"""Two connections to one store, with explicit file and RAM opening profiles."""
from contextlib import asynccontextmanager
from dataclasses import dataclass
import logging
from functools import partial
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from naiyue_test_agent.agent_runtime.async_utils import acquire_blocking, await_settled, run_blocking
from .project_store import ProjectStore
from naiyue_test_agent.application.errors import SessionPersistenceError


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StorageResources:
    store: ProjectStore
    checkpointer: AsyncSqliteSaver


@asynccontextmanager
async def open_storage(database_path, *, expected_project=None):
    if expected_project is not None and (
            database_path is None or Path(database_path).expanduser().absolute() != expected_project.database_path):
        raise SessionPersistenceError('workspace_database_mismatch')
    factory, args = (ProjectStore.in_memory, ()) if database_path is None else (ProjectStore, (database_path,))
    if expected_project is not None:
        factory = partial(ProjectStore, require_existing=True)
    store = await acquire_blocking(factory, lambda item: run_blocking(item.close), *args)
    connection, primary = None, None
    try:
        database = store.database
        if expected_project is not None:
            binding = await run_blocking(store.sessions.get_workspace_binding)
            if binding != {'workspace_id': expected_project.project_id, 'root_path': str(expected_project.canonical_root)}:
                raise SessionPersistenceError('workspace_database_mismatch')
        target = database.uri if database_path is None else store.path
        if expected_project is not None:
            target = store.path.as_uri() + '?mode=rw'
        connection = aiosqlite.connect(target, uri=database_path is None or expected_project is not None)
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
