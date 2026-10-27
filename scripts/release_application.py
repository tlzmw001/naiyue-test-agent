"""Disposable release scenarios use the real service and explicit project API."""
from contextlib import asynccontextmanager

from naiyue_test_agent.application.service_application import open_service_application
from naiyue_test_agent.agent_runtime.async_utils import run_blocking
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.storage.project_store import ProjectStore


@asynccontextmanager
async def open_smoke_host(config_path, *, workspace, data_dir, tools, system_prompt,
        response_format, database_path=None, settings=None, initial_active_tools=None, workspace_binding=None):
    settings = settings if settings is not None else AppSettings()
    settings = settings.model_copy(update={'storage': settings.storage.model_copy(update={'data_dir': data_dir})})
    if workspace_binding is not None and not database_path.exists():
        # Construct the requested legacy input, not a second product factory.
        def seed():
            with ProjectStore(database_path) as store:
                store.sessions.bind_workspace(workspace_binding['workspace_id'], workspace_binding['root_path'])
        await run_blocking(seed)
    async with open_service_application(config_path, tools=tools, system_prompt=system_prompt,
            response_format=response_format, initial_active_tools=initial_active_tools, settings=settings) as root:
        if workspace_binding is not None:
            result, = await root.projects.import_legacy([database_path])
            if result['error'] is not None:
                raise RuntimeError(result['error'])
        project = await root.projects.add_local(workspace, trusted=True, operation_id='release-project')
        yield await root.projects.open(project.project_id)
