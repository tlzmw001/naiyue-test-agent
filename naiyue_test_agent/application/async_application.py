"""Native async application; producers survive presentation disconnects."""

import asyncio
import logging
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
    def __init__(self, catalog, sessions, agent, tool_registry):
        self.default_model_id = catalog.default_model_id
        self._catalog, self._sessions, self._agent = catalog, sessions, agent
        self._tool_registry = tool_registry
        self._loop = asyncio.get_running_loop()
        self._tasks = set()
        self._closed = self._closing = False

    def _check_open(self):
        if asyncio.get_running_loop() is not self._loop or self._closed or self._closing:
            raise RuntimeError('Async application must be open on its owning event loop')

    @property
    def model_ids(self):
        return self._catalog.model_ids

    def get_thinking_options(self):
        self._check_open()
        return {identity: self._catalog.thinking_options(identity) for identity in self.model_ids}

    async def aselect_thinking(self, session_id, model_id, level):
        self._check_open()
        await run_blocking(self._sessions.select_thinking, session_id, model_id, level)

    def get_registered_tools(self):
        self._check_open()
        return self._tool_registry.select(self._tool_registry.names())

    async def aget_skills(self, workspace_root):
        self._check_open()
        from .skills import discover_project_skills
        return await run_blocking(discover_project_skills, workspace_root)

    def get_session_control_options(self):
        self._check_open()
        permissions = self._sessions._permission_settings
        modes = ['Default', 'NoAsk', 'Auto', 'BypassApprovals']
        if permissions.auto is None:
            modes.remove('Auto')
        return {'default_active_tools': list(self._sessions._initial_active_tools),
                'default_permission_mode': permissions.mode, 'permission_modes': modes}

    async def aregister_tool(self, spec):
        self._check_open()
        self._sessions.register_tool(spec)

    async def aregister_subagent(self, spec):
        self._check_open()
        self._sessions.register_subagent(spec)

    def get_registered_subagents(self):
        self._check_open()
        return self._sessions.get_registered_subagents()

    def get_active_tools(self, session_id):
        self._check_open()
        return self._sessions.get_active_tools(session_id)

    async def aset_active_tools(self, session_id, names):
        self._check_open()
        await run_blocking(self._sessions.set_active_tools, session_id, names)

    def get_permission_mode(self, session_id):
        self._check_open()
        return self._sessions.get_permission_mode(session_id)

    async def aset_permission_mode(self, session_id, mode):
        self._check_open()
        await run_blocking(self._sessions.set_permission_mode, session_id, mode)

    def has_session(self, session_id):
        return self._sessions.has_session(session_id)

    async def aget_session_view(self, session_id):
        self._check_open()
        return {**self._sessions.describe_session(session_id),
                'messages': await self.aget_messages(session_id)}

    def subscribe_events(self, handler):
        return self._sessions.subscribe_events(handler)

    def configure_error_classifiers(self, *, model_id=None, model=None, runtime=None, application=None):
        self._check_open()
        return self._sessions.configure_error_classifiers(
            model_id=model_id, model=model, runtime=runtime, application=application)

    async def acreate_session(self, model_id):
        self._check_open()
        return await run_blocking(self._sessions.create_session, model_id)

    async def acreate_workspace_session(self, model_id, workspace_root):
        self._check_open()
        return await run_blocking(self._sessions.create_workspace_session, model_id, workspace_root)

    async def aselect_model(self, session_id, model_id):
        self._check_open()
        return await run_blocking(self._sessions.select_model, session_id, model_id)

    def steer(self, session_id, text):
        self._sessions.steer(session_id, text)

    def follow_up(self, session_id, text):
        self._sessions.follow_up(session_id, text)

    def request_stop(self, session_id, *, run_id=None, agent_path='root'):
        return self._sessions.request_stop(session_id, run_id=run_id, agent_path=agent_path)

    def pending_tool_approvals(self, session_id):
        return self._sessions.pending_tool_approvals(session_id)

    def pending_subagent_approvals(self, session_id):
        self._check_open()
        return self._sessions.pending_subagent_approvals(session_id)

    def pending_approval_batches(self, session_id):
        self._check_open()
        return self._sessions.pending_approval_batches(session_id)

    def prepare_root_approval_submission(self, session_id, **submission):
        self._check_open()
        return self._sessions.prepare_root_approval_submission(session_id, **submission)

    async def asubmit_subagent_approval(self, session_id, *, approval_batch_id, decisions, origin_run_id, remember_approved=()):
        self._check_open()
        return self._sessions.submit_subagent_approval(session_id, approval_batch_id=approval_batch_id,
            decisions=decisions, origin_run_id=origin_run_id, remember_approved=remember_approved)

    async def aget_messages(self, session_id):
        self._check_open()
        with self._sessions._lock:
            thread = self._sessions._sessions[session_id]['thread_id']
        snapshot = await self._agent.aget_state({'configurable': {'thread_id': thread}})
        return [m.model_copy(deep=True) for m in snapshot.values.get('messages', [])]

    async def aget_context_view(self, session_id):
        self._check_open()
        from .session_context import context_view
        return await context_view(self, session_id)

    async def aget_subagents(self, session_id):
        self._check_open()
        if self.has_session(session_id):
            messages = await self.aget_messages(session_id)
        else:
            messages = (await self._sessions._persistence.ahistory(session_id, self._agent))['messages']
        records = subagent_records(session_id, messages)
        for row in records:
            if row['status'] == 'failed':
                snapshot = await self._agent.aget_state({'configurable': {'thread_id': f'subagent:{row["agent_id"]}'}})
                if snapshot.values.get('run_stopped'):
                    row['status'] = 'stopped'
        return records

    async def aget_subagent_history(self, session_id, agent_id):
        self._check_open()
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

    async def _execute(self, session_id, value, resume, run_id, max_concurrency, *, remember_approved=None, stop=False, compact=False):
        self._check_open()
        task = asyncio.current_task()
        already_owned = task in self._tasks
        self._tasks.add(task)
        try:
            if compact:
                return await self._sessions.acompact(self._agent, session_id, run_id=run_id)
            method = self._sessions.aresume_tool_approval if resume else self._sessions.arun
            return await method(self._agent, session_id, value, run_id=run_id, max_concurrency=max_concurrency,
                                **({'remember_approved': remember_approved, 'stop': stop} if resume else {}))
        finally:
            if not already_owned:
                self._tasks.discard(task)

    async def arun(self, session_id, messages, *, max_concurrency=4):
        self._check_open()
        return await self._execute(session_id, messages, False, None, max_concurrency)

    async def acompact_context(self, session_id):
        self._check_open()
        return await self._execute(session_id, [], False, None, 4, compact=True)

    async def aresume_tool_approval(self, session_id, decisions, *, max_concurrency=4, remember_approved=None):
        self._check_open()
        return await self._execute(session_id, decisions, True, None, max_concurrency, remember_approved=remember_approved)

    async def astop_tool_approval(self, session_id, *, approval_batch_id, origin_run_id, max_concurrency=4):
        self._check_open()
        self._sessions.validate_root_approval_stop(session_id, approval_batch_id=approval_batch_id, origin_run_id=origin_run_id)
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
        self._sessions.validate_root_approval_stop(session_id, approval_batch_id=approval_batch_id, origin_run_id=origin_run_id)
        iterator = self._stream(session_id, [], True, max_concurrency, capacity, stop=True)
        try:
            async for event in iterator:
                yield event
        finally:
            await iterator.aclose()

    async def _close_resources(self):
        for session_id in tuple(self._sessions._sessions):
            self._sessions._request_scope_stop(session_id, reason='close')
        results = await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                logger.error('owned_run_failed error_type=%s', safe_error_type(result))
        await self._catalog.aclose()

    async def aclose(self):
        if self._closed:
            return
        services = current_run_services()
        if services is not None and services.publisher is not None:
            raise RuntimeError('Cannot close the application from an active run')
        self._check_open()
        self._closing = True
        try:
            await await_settled(self._close_resources())
        finally:
            self._closed = True


async def build_async_local_application(config_path, *, tools, system_prompt, response_format, initial_active_tools=None, settings=None):
    settings = settings if settings is not None else application.load_tool_settings()
    registry = application.prepare_tool_registry(tools)
    application.validate_permission_rules(settings.permissions, registry.select(registry.names()))
    initial = registry.names() if initial_active_tools is None else initial_active_tools
    registry.select(initial)
    catalog = await acquire_blocking(application.load_model_catalog, lambda item: item.aclose(), config_path)
    try:
        await run_blocking(catalog._prepare_async_clients)
        sessions = application.SessionService(catalog, registry, initial_active_tools=initial, permission_settings=settings.permissions, progress_settings=settings.progress)
        agent = application._assemble_application(catalog, sessions, InMemorySaver(),
            tool_registry=registry, system_prompt=system_prompt, response_format=response_format,
            subagents=settings.subagents, subagent_settings=settings.tools.subagent)
        return AsyncApplication(catalog, sessions, agent, registry)
    except BaseException:
        try:
            await await_settled(catalog.aclose())
        except BaseException as error:
            logger.error('model_cleanup_secondary_failed error_type=%s', safe_error_type(error))
        raise
