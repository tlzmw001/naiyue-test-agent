"""Explicit root/host fixtures for component tests, not the project protocol.

Registration/service integration tests use open_service_application directly.
These fixtures borrow that root's real models/resources and the sole host
assembler while allowing tests to select an isolated legacy database.
"""
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
import logging

from naiyue_test_agent import application
from naiyue_test_agent.application.service_application import open_service_application
from naiyue_test_agent.application.projects.host_assembly import assemble_host, prepare_tools
from naiyue_test_agent.application.lifetime import _enter_resource, _register_cleanup
from naiyue_test_agent.storage.resources import open_storage
from naiyue_test_agent.agent_runtime.async_utils import run_blocking

logger = logging.getLogger(__name__)


@dataclass
class ComponentApplication:
    root: object
    host: object
    resources: AsyncExitStack

    async def aclose(self):
        try:
            await self.root.aclose()
        finally:
            await self.resources.aclose()


async def build_component_application(config_path, *, tools, system_prompt, response_format,
        database_path=None, initial_active_tools=None, settings=None, workspace_binding=None,
        expected_project=None):
    settings = settings if settings is not None else application.load_settings()
    prepared = prepare_tools(tools, settings, initial_active_tools)
    if workspace_binding is not None and expected_project is not None:
        raise ValueError('Project verification and binding are mutually exclusive')
    if workspace_binding is not None and database_path is None:
        raise ValueError('Temporary application cannot bind a project')
    resources, storage_stack = AsyncExitStack(), AsyncExitStack()
    try:
        owned = await _enter_resource(storage_stack, 'storage', open_storage(database_path,
            expected_project=expected_project, storage_settings=settings.storage))
        if workspace_binding is not None:
            await run_blocking(owned.store.sessions.bind_workspace, workspace_binding['workspace_id'], workspace_binding['root_path'])
        root = await resources.enter_async_context(open_service_application(config_path, tools=tools,
            system_prompt=system_prompt, response_format=response_format, settings=settings,
            initial_active_tools=initial_active_tools))
        manager = root.projects._hosts
        options = await manager._host_options()
        host = await assemble_host(database_path=database_path,
            storage_resources=owned, storage_stack=storage_stack, prepared_tools=prepared, **options)
        root._lifetime._children.append(host._lifetime)
        _register_cleanup(root._lifetime._resources, 'component_storage', storage_stack.__aexit__)
        return ComponentApplication(root, host, resources)
    except BaseException:
        # Before a host is registered, the root's opened resource is its catalog;
        # preserve the component cleanup diagnostic and the primary exception.
        for name, stack in (('storage', storage_stack), ('model', resources)):
            try:
                await stack.aclose()
            except BaseException as error:
                logger.error('%s_cleanup_secondary_failed error_type=%s', name, type(error).__name__)
        raise


@asynccontextmanager
async def open_component_application(*args, **kwargs):
    fixture = await build_component_application(*args, **kwargs)
    primary = None
    try:
        yield fixture
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            await fixture.aclose()
        except BaseException as error:
            if primary is None:
                raise
            logger.error('component_cleanup_secondary_failed error_type=%s', type(error).__name__)
