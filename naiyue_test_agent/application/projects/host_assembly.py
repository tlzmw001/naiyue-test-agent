"""Assemble project resources against application-owned models, memory and admission."""
from contextlib import AsyncExitStack
import logging
from pathlib import Path

from naiyue_test_agent import application
from naiyue_test_agent.agent_runtime.async_utils import acquire_blocking, await_settled, run_blocking
from naiyue_test_agent.config_paths import get_data_dir
from naiyue_test_agent.execution_roots import Project
from naiyue_test_agent.storage.resources import open_storage
from naiyue_test_agent.application.lifetime import _register_cleanup, _enter_resource
from naiyue_test_agent.application.memory.memory_access import MemoryAccess
from naiyue_test_agent.application.observability.diagnostic_persistence import DiagnosticPersistence
from naiyue_test_agent.application.observability.model_usage_persistence import ModelUsagePersistence
from naiyue_test_agent.application.sessions.session_persistence import SessionPersistence
from .host import ProjectHost

logger = logging.getLogger(__name__)


async def assemble_host(*, catalog, parent, personal, admission, database_path, tools,
                        system_prompt, response_format, settings, initial_active_tools=None,
                        registered_project=None,
                        execution_authority=None, chats=False, storage_resources=None,
                        storage_stack=None, prepared_tools=None,environments=None):
    registry, initial = prepared_tools or prepare_tools(tools, settings, initial_active_tools)
    stack, resources, jobs = storage_stack or AsyncExitStack(), None, None
    try:
        options = {'storage_settings': settings.storage}
        if registered_project is not None: options['registered_project'] = registered_project
        if chats: options['chats'] = True
        owned = storage_resources or await _enter_resource(stack, 'storage', open_storage(database_path, **options))
        storage = owned.store
        if registered_project is not None:
            if execution_authority is None:
                raise ValueError('A registered host requires its execution authority')
            await run_blocking(storage.sessions.bind_project_execution, registered_project.project_id)
        binding = await run_blocking(storage.sessions.get_workspace_binding)
        project = (Project(binding['workspace_id'], getattr(registered_project.source,'path',None) if registered_project else
                   Path(binding['root_path']), storage.path) if binding else None)
        memory = None
        if database_path is not None and 'memory' in registry.names() and settings.memory.enabled:
            memory = await acquire_blocking(MemoryAccess, lambda item: run_blocking(item.close),
                storage, settings.memory, get_data_dir(settings.storage), personal=personal, personal_only=chats)
            memory_owner = memory
            async def close_memory(*args):
                await run_blocking(memory_owner.close)
            _register_cleanup(stack, 'memory', close_memory)
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
            data_dir=get_data_dir(settings.storage), project=project, admission=admission,
            execution_authority=execution_authority, fixed_execution=chats or registered_project is not None,
            environments=environments, image_input_settings=settings.image_inputs)
        usage = await _enter_resource(stack, 'usage', ModelUsagePersistence(storage, sessions.subscribe_events, settings.usage_persistence))
        diagnostics = await _enter_resource(stack, 'diagnostic', DiagnosticPersistence(storage, sessions.subscribe_events, settings.diagnostics))
        jobs = None
        if memory is not None and not chats:
            from naiyue_test_agent.application.memory.memory_jobs import MemoryJobs
            from naiyue_test_agent.application.observability.run_events import _log_event
            from naiyue_test_agent.agent_runtime.events import RunEventPublisher
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
            memory_jobs=jobs)
        sessions.bind_persistence(persistence)
        agent = sessions.assemble_runtime(owned.checkpointer, system_prompt=system_prompt, response_format=response_format,
            subagents=settings.subagents, subagent_settings=settings.tools.subagent)
        return ProjectHost(catalog, sessions, agent, registry, system_prompt=system_prompt,
            persistence=persistence, storage=storage, usage=usage, diagnostics=diagnostics, resources=stack,
            memory=memory, memory_jobs=jobs, parent=parent)
    except BaseException as primary:
        for name, resource in (('memory_jobs', jobs), ('mcp', resources)):
            if resource is not None:
                try:
                    await await_settled(resource.aclose())
                except BaseException as error:
                    logger.error('%s_cleanup_secondary_failed error_type=%s', name, type(error).__name__)
        await stack.__aexit__(type(primary), primary, primary.__traceback__)
        raise


def prepare_tools(tools, settings, initial_active_tools):
    registry = application.prepare_tool_registry(tools)
    from naiyue_test_agent.tools.subagent import subagent_selection
    initial = subagent_selection(registry.names() if initial_active_tools is None else initial_active_tools)
    if not any(server.enabled for server in settings.mcp.servers.values()):
        application.validate_permission_rules(settings.permissions, registry.select(registry.names()))
        registry.select(initial)
    return registry, initial
