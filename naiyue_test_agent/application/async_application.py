"""Native async application; producers survive presentation disconnects."""

import asyncio
import logging
from contextlib import contextmanager
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver

from naiyue_test_agent import application
from naiyue_test_agent.agent_runtime.async_utils import await_settled, run_blocking, acquire_blocking
from naiyue_test_agent.contracts.failures import safe_error_type
from naiyue_test_agent.agent_runtime.run_services import current_run_services
from .async_event_stream import AsyncEventStream
from .subagent_history import subagent_records


logger = logging.getLogger(__name__)


class AsyncApplication:
    def __init__(self, catalog, sessions, agent, tool_registry, *, system_prompt):
        self.default_model_id = catalog.default_model_id
        self._catalog, self._sessions, self._agent = catalog, sessions, agent
        self._tool_registry = tool_registry
        self._system_prompt = system_prompt
        self._loop = asyncio.get_running_loop()
        self._tasks = set()
        self._operations = {}
        self._close_task = None
        self._closed = self._closing = False

    def _check_open(self):
        if (asyncio.get_running_loop() is not self._loop or self._closed
                or self._closing and asyncio.current_task() not in self._operations.values()):
            raise RuntimeError('Async application must be open on its owning event loop')

    @contextmanager
    def _owned_operation(self):
        """Track operation settlement, without owning the caller's later work."""
        self._check_open()
        done = self._loop.create_future()
        self._operations[done] = asyncio.current_task()
        try:
            yield
        finally:
            self._operations.pop(done)
            done.set_result(None)

    @property
    def model_ids(self):
        return self._catalog.model_ids

    def get_thinking_options(self):
        self._check_open()
        return {identity: self._catalog.thinking_options(identity) for identity in self.model_ids}

    async def aselect_thinking(self, session_id, model_id, level):
        with self._owned_operation():
            await run_blocking(self._sessions.require(session_id).select_thinking, model_id, level)

    def get_registered_tools(self):
        self._check_open()
        return self._tool_registry.select(self._tool_registry.names())

    async def alist_background_tasks(self, session_id=None, *, workspace_root=None, limit=20, before=None):
        with self._owned_operation():
            return await self._sessions.background_tasks.list(session_id=session_id, workspace_root=workspace_root, limit=limit, before=before)

    async def aget_background_task_output(self, task_id, session_id=None, *, workspace_root=None):
        with self._owned_operation():
            return await self._sessions.background_tasks.output(task_id, session_id=session_id, workspace_root=workspace_root)

    async def astop_background_task(self, task_id, session_id=None, *, workspace_root=None):
        with self._owned_operation():
            return await self._sessions.background_tasks.stop(task_id, session_id=session_id, workspace_root=workspace_root)

    async def apreview_shell_outputs(self, root):
        from ..tools.output import preview_shell_outputs
        with self._owned_operation(), self._sessions.output_guard(root):
            return await run_blocking(preview_shell_outputs, root)

    async def aclear_shell_outputs(self, plan):
        from ..tools.output import clear_shell_outputs
        with self._owned_operation(), self._sessions.output_guard(plan.root):
            return await run_blocking(clear_shell_outputs, plan)

    async def aget_skills(self, workspace_root):
        with self._owned_operation():
            from .skills import discover_project_skills
            return await run_blocking(discover_project_skills, workspace_root)

    def get_session_control_options(self):
        self._check_open()
        return self._sessions.get_control_options()

    async def aregister_tool(self, spec):
        with self._owned_operation():
            self._sessions.register_tool(spec)

    def get_mcp_status(self):
        self._check_open()
        resources = self._sessions.mcp_resources
        return resources.server_status() if resources is not None else {'servers': []}

    async def arefresh_mcp(self, settings_path=None):
        with self._owned_operation():
            from naiyue_test_agent.mcp_client.settings import load_mcp_settings
            from naiyue_test_agent.mcp_client.refresh import refresh_mcp
            settings = await run_blocking(load_mcp_settings, settings_path)
            return await refresh_mcp(self._sessions.mcp_resources, self._tool_registry, settings,
                borrow_mcp=self._sessions.borrow_mcp, permission_settings=self._sessions._permission_settings)

    async def aregister_subagent(self, spec):
        with self._owned_operation():
            self._sessions.register_subagent(spec)

    def get_registered_subagents(self):
        self._check_open()
        return self._sessions.get_registered_subagents()

    def get_active_tools(self, session_id):
        self._check_open()
        return self._sessions.require(session_id).get_active_tools()

    async def aset_active_tools(self, session_id, names):
        with self._owned_operation():
            await run_blocking(self._sessions.require(session_id).set_active_tools, names)

    def get_permission_mode(self, session_id):
        self._check_open()
        return self._sessions.require(session_id).get_permission_mode()

    async def aset_permission_mode(self, session_id, mode):
        with self._owned_operation():
            await run_blocking(self._sessions.require(session_id).set_permission_mode, mode)

    def has_session(self, session_id):
        return self._sessions.has_session(session_id)

    def subscribe_events(self, handler, *, session_id=None):
        return self._sessions.subscribe_events(handler, session_id=session_id)

    def configure_error_classifiers(self, *, model_id=None, model=None, runtime=None, application=None):
        self._check_open()
        return self._sessions.configure_error_classifiers(
            model_id=model_id, model=model, runtime=runtime, application=application)

    async def acreate_session(self, model_id):
        with self._owned_operation():
            return await run_blocking(self._sessions.create_session, model_id)

    async def acreate_workspace_session(self, model_id, workspace_root):
        with self._owned_operation():
            return await run_blocking(self._sessions.create_workspace_session, model_id, workspace_root)

    async def aselect_model(self, session_id, model_id):
        with self._owned_operation():
            return await run_blocking(self._sessions.require(session_id).select_model, model_id)

    def steer(self, session_id, text):
        if not isinstance(text, str) or not text.strip():
            raise ValueError('A non-empty steering message is required')
        self._sessions.require(session_id).steer(text)

    def follow_up(self, session_id, text):
        if not isinstance(text, str) or not text.strip():
            raise ValueError('A non-empty follow-up message is required')
        self._sessions.require(session_id).follow_up(text)

    def request_stop(self, session_id, *, run_id=None, agent_path='root', agent_run_id=None, approval_batch_id=None):
        return self._sessions.require(session_id).request_scope_stop(run_id=run_id, agent_path=agent_path, agent_run_id=agent_run_id, approval_batch_id=approval_batch_id)

    def pending_tool_approvals(self, session_id):
        return self._sessions.require(session_id).pending_tool_approvals()

    def pending_subagent_approvals(self, session_id):
        self._check_open()
        return self._sessions.require(session_id).pending_subagent_approvals()

    def pending_approval_batches(self, session_id):
        self._check_open()
        return self._sessions.require(session_id).pending_approval_batches()

    def prepare_root_approval_submission(self, session_id, **submission):
        self._check_open()
        return self._sessions.require(session_id).prepare_root_approval_submission(**submission)

    async def asubmit_subagent_approval(self, session_id, *, approval_batch_id, decisions, origin_run_id, remember_approved=()):
        with self._owned_operation():
            return self._sessions.require(session_id).submit_subagent_approval(approval_batch_id=approval_batch_id, decisions=decisions, origin_run_id=origin_run_id, remember_approved=remember_approved)

    async def aget_messages(self, session_id):
        with self._owned_operation():
            thread = self._sessions.require(session_id).get_thread_id()
            snapshot = await self._agent.aget_state({'configurable': {'thread_id': thread}})
            return [m.model_copy(deep=True) for m in snapshot.values.get('messages', [])]

    async def aget_context_view(self, session_id):
        with self._owned_operation():
            from .session_context import context_view
            history = None if self.has_session(session_id) else await self.aget_session_history(session_id)
            return await context_view(session_id, sessions=self._sessions, agent=self._agent,
                catalog=self._catalog, tool_registry=self._tool_registry,
                system_prompt=self._system_prompt, history=history)

    async def aget_subagents(self, session_id):
        with self._owned_operation():
            owned = {}
            if self.has_session(session_id):
                messages = await self.aget_messages(session_id)
                owned = await self._sessions.require(session_id).asubagent_references()
            else:
                messages = (await self.aget_session_history(session_id))['messages']
                if getattr(self, '_storage', None) is not None:
                    owned = await run_blocking(self._storage.get_subagent_references, session_id)
            from .subagent_history import merge_registered_subagents
            records = merge_registered_subagents(subagent_records(session_id, messages, owned_instances=owned), owned)
            for row in records:
                if row['status'] == 'failed':
                    snapshot = await self._agent.aget_state({'configurable': {'thread_id': f'subagent:{row["agent_id"]}'}})
                    if snapshot.values.get('run_stopped'):
                        row['status'] = 'stopped'
            from .subagent_queries import annotate
            active = self._sessions.require(session_id).describe_session()['run_id'] if self.has_session(session_id) else None
            return annotate(records, owned, self.get_registered_subagents(), self._catalog, active)

    async def aget_subagent_history(self, session_id, agent_id):
        with self._owned_operation():
            record = next((row for row in await self.aget_subagents(session_id) if row['agent_id'] == agent_id), None)
            if record is None:
                raise LookupError('Subagent does not belong to this session')
            # Same state schema and saver as the root; no role/configuration is required to read history.
            snapshot = await self._agent.aget_state({'configurable': {'thread_id': f'subagent:{agent_id}'}})
            messages = [m.model_copy(deep=True) for m in snapshot.values.get('messages', [])]
            if snapshot.values.get('run_stopped'):
                record['status'] = 'stopped'
            return {**record, 'messages': messages,
                    'available': bool((snapshot.config or {}).get('configurable', {}).get('checkpoint_id')),
                    'has_compaction': any(m.additional_kwargs.get('naiyue_compaction_summary') for m in messages)}

    async def _execute(self, session_id, value, resume, run_id, max_concurrency, *, remember_approved=None, stop=False, compact=False, expected_conversation_revision=None):
        with self._owned_operation():
            if compact:
                return await self._sessions.acompact(self._agent, session_id, run_id=run_id)
            method = self._sessions.aresume_tool_approval if resume else self._sessions.arun
            return await method(self._agent, session_id, value, run_id=run_id, max_concurrency=max_concurrency,
                                **({'remember_approved': remember_approved, 'stop': stop} if resume else
                                   {'expected_conversation_revision': expected_conversation_revision}))

    async def arun(self, session_id, messages, *, max_concurrency=4, expected_conversation_revision=None):
        with self._owned_operation():
            return await self._execute(session_id, messages, False, None, max_concurrency, expected_conversation_revision=expected_conversation_revision)

    async def acompact_context(self, session_id):
        with self._owned_operation():
            return await self._execute(session_id, [], False, None, 4, compact=True)

    async def aresume_tool_approval(self, session_id, decisions, *, max_concurrency=4, remember_approved=None):
        with self._owned_operation():
            return await self._execute(session_id, decisions, True, None, max_concurrency, remember_approved=remember_approved)

    async def astop_tool_approval(self, session_id, *, approval_batch_id, origin_run_id, max_concurrency=4):
        with self._owned_operation():
            self._sessions.require(session_id).validate_root_approval_stop(approval_batch_id=approval_batch_id, origin_run_id=origin_run_id)
            return await self._execute(session_id, [], True, None, max_concurrency, stop=True)

    def _background_done(self, task):
        self._tasks.discard(task)
        if task.cancelled():
            logger.error('background_run_failed error_type=CancelledError')
        else:
            error = task.exception()
            if error is not None:
                logger.error('background_run_failed error_type=%s', safe_error_type(error))

    async def _stream(self, session_id, value, resume, max_concurrency, capacity, *, stop=False):
        self._check_open()
        run_id = uuid4().hex
        stream = AsyncEventStream(self.subscribe_events, session_id, run_id, capacity)

        async def produce():
            try:
                return await self._execute(session_id, value, resume, run_id, max_concurrency, stop=stop)
            finally:
                stream.finish()

        task = asyncio.create_task(produce())
        self._tasks.add(task)
        task.add_done_callback(self._background_done)
        try:
            async for event in stream:
                yield event
            await task
        finally:
            stream.close()

    async def astream_run(self, session_id, messages, *, max_concurrency=4, capacity=256):
        iterator = self._stream(session_id, messages, False, max_concurrency, capacity)
        try:
            async for event in iterator:
                yield event
        finally:
            await iterator.aclose()

    async def astream_tool_approval(self, session_id, decisions, *, max_concurrency=4, capacity=256):
        iterator = self._stream(session_id, decisions, True, max_concurrency, capacity)
        try:
            async for event in iterator:
                yield event
        finally:
            await iterator.aclose()

    async def astream_stop_tool_approval(self, session_id, *, approval_batch_id, origin_run_id, max_concurrency=4, capacity=256):
        self._check_open()
        self._sessions.require(session_id).validate_root_approval_stop(approval_batch_id=approval_batch_id, origin_run_id=origin_run_id)
        iterator = self._stream(session_id, [], True, max_concurrency, capacity, stop=True)
        try:
            async for event in iterator:
                yield event
        finally:
            await iterator.aclose()

    async def _close_resources(self):
        primary = None
        try:
            self._sessions.stop_loaded_sessions()
            background_error = None
            try:
                await self._sessions.background_tasks.aclose()
            except BaseException as error:
                background_error = error
            # Nested operations are covered by their outer operation. No new ingress
            # can start after closing; these signals settle only after required IO.
            try:
                if self._sessions.mcp_resources is not None:
                    await self._sessions.mcp_resources.aclose()
            except BaseException as error:
                if background_error is None:
                    raise
                logger.error('mcp_cleanup_secondary_failed error_type=%s', safe_error_type(error))
            finally:
                await asyncio.gather(*tuple(self._operations))
                results = await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
                for result in results:
                    if isinstance(result, BaseException):
                        logger.error('owned_run_failed error_type=%s', safe_error_type(result))
            if background_error is not None:
                raise background_error
        except BaseException as error:
            primary = error
            raise
        finally:
            try:
                await self._catalog.aclose()
            except BaseException as error:
                if primary is None:
                    raise
                logger.error('model_cleanup_secondary_failed error_type=%s', safe_error_type(error))
            finally:
                self._closed = True

    async def aclose(self):
        if asyncio.get_running_loop() is not self._loop:
            raise RuntimeError('Async application must close on its owning event loop')
        services = current_run_services()
        if services is not None and services.publisher is not None:
            raise RuntimeError('Cannot close the application from an active run')
        if asyncio.current_task() in self._operations.values() or asyncio.current_task() in self._tasks:
            raise RuntimeError('Cannot close the application from an active operation')
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._close_resources())
        await await_settled(self._close_task)


async def build_async_local_application(config_path, *, tools, system_prompt, response_format, initial_active_tools=None, settings=None):
    settings = settings if settings is not None else application.load_settings()
    registry = application.prepare_tool_registry(tools)
    initial = registry.names() if initial_active_tools is None else initial_active_tools
    from naiyue_test_agent.tools.subagent import subagent_selection
    initial = subagent_selection(initial)
    if not any(server.enabled for server in settings.mcp.servers.values()):
        application.validate_permission_rules(settings.permissions, registry.select(registry.names()))
        registry.select(initial)
    catalog = await acquire_blocking(application.load_model_catalog, lambda item: item.aclose(), config_path)
    resources = None
    try:
        await run_blocking(catalog.prepare_async_clients)
        from naiyue_test_agent.mcp_client.resources import MCPSessionResources
        resources = MCPSessionResources(settings.mcp, settings.network, retry_settings=settings.tool_retry)
        from naiyue_test_agent.mcp_client.tools import register_mcp_tools
        # Definitions precede SessionService; execution starts only after assembly.
        def borrow_mcp(session_id, agent_path, server_id):
            return sessions.borrow_mcp(session_id, agent_path, server_id)
        await register_mcp_tools(registry, resources, borrow_mcp=borrow_mcp)
        application.validate_permission_rules(settings.permissions, registry.select(registry.names()))
        registry.select(initial)
        sessions = application.SessionService(catalog, registry, initial_active_tools=initial, permission_settings=settings.permissions, progress_settings=settings.progress,
            max_active_runs=settings.max_active_runs, mcp_resources=resources,
            http_attempt_limit=settings.diagnostics.max_pending_events, shell_task_settings=settings.shell_tasks)
        agent = sessions.assemble_runtime(InMemorySaver(), system_prompt=system_prompt, response_format=response_format,
            subagents=settings.subagents, subagent_settings=settings.tools.subagent)
        return AsyncApplication(catalog, sessions, agent, registry, system_prompt=system_prompt)
    except BaseException:
        if resources is not None:
            try:
                await await_settled(resources.aclose())
            except BaseException as error:
                logger.error('mcp_cleanup_secondary_failed error_type=%s', safe_error_type(error))
        try:
            await await_settled(catalog.aclose())
        except BaseException as error:
            logger.error('model_cleanup_secondary_failed error_type=%s', safe_error_type(error))
        raise
