"""One application assembly; only the SQLite opening profile differs."""
from contextlib import AsyncExitStack
import logging
from pathlib import Path

from naiyue_test_agent import application
from naiyue_test_agent.agent_runtime.async_utils import acquire_blocking, await_settled, run_blocking
from naiyue_test_agent.config_paths import get_data_dir
from naiyue_test_agent.execution_roots import Project
from naiyue_test_agent.storage.resources import open_storage
from .async_persistent import AsyncPersistentApplication
from .memory.memory_access import MemoryAccess
from .observability.diagnostic_persistence import DiagnosticPersistence
from .observability.model_usage_persistence import ModelUsagePersistence
from .sessions.session_persistence import SessionPersistence


logger = logging.getLogger(__name__)


def _register_cleanup(stack, name, close):
    """Keep the first failure while still closing every acquired resource."""
    async def exit_resource(error_type, primary, traceback):
        try:
            await await_settled(close(error_type, primary, traceback))
        except BaseException as error:
            if primary is None:
                raise
            logger.error('%s_cleanup_secondary_failed error_type=%s', name, type(error).__name__)
        return False
    stack.push_async_exit(exit_resource)


async def _enter_resource(stack, name, manager):
    resource = await manager.__aenter__()
    _register_cleanup(stack, name, manager.__aexit__)
    return resource


async def build_application(config_path, *, database_path, tools, system_prompt, response_format,
                            initial_active_tools=None, settings=None, workspace_binding=None):
    settings = settings if settings is not None else application.load_settings()
    registry = application.prepare_tool_registry(tools)
    initial = registry.names() if initial_active_tools is None else initial_active_tools
    from naiyue_test_agent.tools.subagent import subagent_selection
    initial = subagent_selection(initial)
    if not any(server.enabled for server in settings.mcp.servers.values()):
        application.validate_permission_rules(settings.permissions, registry.select(registry.names()))
        registry.select(initial)
    stack = AsyncExitStack()
    catalog, resources, jobs = None, None, None
    try:
        owned = await _enter_resource(stack, 'storage', open_storage(database_path))
        storage = owned.store
        if workspace_binding is not None:
            if database_path is None:
                raise ValueError('Temporary application cannot bind a project')
            await run_blocking(storage.sessions.bind_workspace, workspace_binding['workspace_id'], workspace_binding['root_path'])
        project_binding = await run_blocking(storage.sessions.get_workspace_binding)
        memory = None
        if database_path is not None and 'memory' in registry.names() and settings.memory.enabled:
            memory = await acquire_blocking(MemoryAccess, lambda item: run_blocking(item.close),
                                           storage, settings.memory, get_data_dir(settings.storage))
            async def close_memory(*args):
                await run_blocking(memory_owner.close)
            memory_owner = memory
            _register_cleanup(stack, 'memory', close_memory)
        catalog = await acquire_blocking(application.load_model_catalog, lambda item: item.aclose(), config_path)
        await run_blocking(catalog.prepare_async_clients)
        from naiyue_test_agent.mcp_client.resources import MCPSessionResources
        resources = MCPSessionResources(settings.mcp, settings.network, retry_settings=settings.tool_retry)
        from naiyue_test_agent.mcp_client.tools import register_mcp_tools
        def borrow_mcp(session_id, agent_path, server_id):
            return sessions.borrow_mcp(session_id, agent_path, server_id)
        await register_mcp_tools(registry, resources, borrow_mcp=borrow_mcp)
        application.validate_permission_rules(settings.permissions, registry.select(registry.names()))
        registry.select(initial)
        sessions = application.SessionService(catalog, registry, initial_active_tools=initial,
            permission_settings=settings.permissions, progress_settings=settings.progress,
            max_active_runs=settings.max_active_runs, mcp_resources=resources,
            http_attempt_limit=settings.diagnostics.max_pending_events, shell_task_settings=settings.shell_tasks,
            data_dir=get_data_dir(settings.storage),
            project=(Project(project_binding['workspace_id'], Path(project_binding['root_path']), storage.path)
                     if project_binding else None))
        usage = await _enter_resource(stack, 'usage', ModelUsagePersistence(storage, sessions.subscribe_events, settings.usage_persistence))
        diagnostics = await _enter_resource(stack, 'diagnostic', DiagnosticPersistence(storage, sessions.subscribe_events, settings.diagnostics))
        jobs = None
        if memory is not None:
            from .memory.memory_jobs import MemoryJobs
            from .observability.run_events import _log_event
            from ..agent_runtime.events import RunEventPublisher
            try:
                jobs = MemoryJobs(memory, catalog, permission_settings=settings.permissions, flush_usage=usage.aflush_run,
                    publisher_factory=lambda sid, rid: RunEventPublisher(sid, rid, (_log_event, usage.accept, diagnostics.accept)))
            except ValueError:
                logger.warning('memory_model_unavailable')
                await run_blocking(memory.close)
                memory = None
        sessions.memory_jobs = jobs
        sessions.task_queries.memory = jobs
        persistence = SessionPersistence(storage, sessions, catalog=catalog, tool_registry=registry,
            permission_settings=settings.permissions, usage_persistence=usage,
            title_max_chars=settings.session_title_max_chars, checkpoint_settings=settings.checkpoint_retention,
            memory=memory, memory_jobs=jobs)
        sessions.bind_persistence(persistence)
        agent = sessions.assemble_runtime(owned.checkpointer, system_prompt=system_prompt, response_format=response_format,
            subagents=settings.subagents, subagent_settings=settings.tools.subagent)
        persistence.bind_memory_agent(agent)
        app = AsyncPersistentApplication(catalog, sessions, agent, registry, system_prompt=system_prompt,
            persistence=persistence, storage=storage, usage=usage, diagnostics=diagnostics, resources=stack)
        return app
    except BaseException as primary:
        if jobs is not None:
            try:
                await await_settled(jobs.aclose())
            except BaseException as error:
                logger.error('memory_jobs_cleanup_secondary_failed error_type=%s', type(error).__name__)
        if resources is not None:
            try:
                await await_settled(resources.aclose())
            except BaseException as error:
                logger.error('mcp_cleanup_secondary_failed error_type=%s', type(error).__name__)
        if catalog is not None:
            try:
                await await_settled(catalog.aclose())
            except BaseException as error:
                logger.error('model_cleanup_secondary_failed error_type=%s', type(error).__name__)
        await stack.__aexit__(type(primary), primary, primary.__traceback__)
        raise
