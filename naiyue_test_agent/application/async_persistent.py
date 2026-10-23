"""Context-managed disk-profile factory for the composed application."""
import logging
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


@asynccontextmanager
async def open_async_persistent_application(config_path, *, database_path, tools, system_prompt, response_format, initial_active_tools=None, settings=None, workspace_binding=None, expected_project=None):
    from .assembly import build_application
    app = await build_application(config_path, database_path=database_path, tools=tools,
        system_prompt=system_prompt, response_format=response_format, initial_active_tools=initial_active_tools,
        settings=settings, workspace_binding=workspace_binding, expected_project=expected_project)
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
