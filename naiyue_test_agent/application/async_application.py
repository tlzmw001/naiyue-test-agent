"""Native async application; producers survive presentation disconnects."""

import asyncio
import logging
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver

from naiyue_test_agent import application
from naiyue_test_agent.agent_runtime.async_utils import await_settled, run_blocking, acquire_blocking
from naiyue_test_agent.agent_runtime.events import current_run_event_publisher, safe_error_type
from naiyue_test_agent.agent_runtime.tool_approval import approval_policies_for_tools
from .async_event_stream import AsyncEventStream


logger = logging.getLogger(__name__)


class AsyncApplication:
    def __init__(self, catalog, sessions, agent):
        self.default_model_id = catalog.default_model_id
        self._catalog, self._sessions, self._agent = catalog, sessions, agent
        self._loop = asyncio.get_running_loop()
        self._tasks = set()
        self._closed = self._closing = False

    def _check_open(self):
        if asyncio.get_running_loop() is not self._loop or self._closed or self._closing:
            raise RuntimeError('Async application must be open on its owning event loop')

    @property
    def model_ids(self):
        return self._catalog.model_ids

    def has_session(self, session_id):
        return self._sessions.has_session(session_id)

    async def aget_session_view(self, session_id):
        self._check_open()
        return {**self._sessions.describe_session(session_id),
                'messages': await self.aget_messages(session_id)}

    def subscribe_events(self, handler):
        return self._sessions.subscribe_events(handler)

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

    def request_stop(self, session_id, *, run_id=None):
        return self._sessions.request_stop(session_id, run_id=run_id)

    def pending_tool_approvals(self, session_id):
        return self._sessions.pending_tool_approvals(session_id)

    async def aget_messages(self, session_id):
        self._check_open()
        with self._sessions._lock:
            thread = self._sessions._sessions[session_id]['thread_id']
        snapshot = await self._agent.aget_state({'configurable': {'thread_id': thread}})
        return [m.model_copy(deep=True) for m in snapshot.values.get('messages', [])]

    async def _execute(self, session_id, value, resume, run_id, max_concurrency):
        task = asyncio.current_task()
        already_owned = task in self._tasks
        self._tasks.add(task)
        try:
            method = self._sessions.aresume_tool_approval if resume else self._sessions.arun
            return await method(self._agent, session_id, value, run_id=run_id, max_concurrency=max_concurrency)
        finally:
            if not already_owned:
                self._tasks.discard(task)

    async def arun(self, session_id, messages, *, max_concurrency=4):
        self._check_open()
        return await self._execute(session_id, messages, False, None, max_concurrency)

    async def aresume_tool_approval(self, session_id, decisions, *, max_concurrency=4):
        self._check_open()
        return await self._execute(session_id, decisions, True, None, max_concurrency)

    def _background_done(self, task):
        self._tasks.discard(task)
        if task.cancelled():
            logger.error('background_run_failed error_type=CancelledError')
        else:
            error = task.exception()
            if error is not None:
                logger.error('background_run_failed error_type=%s', safe_error_type(error))

    async def _stream(self, session_id, value, resume, max_concurrency, capacity):
        self._check_open()
        run_id = uuid4().hex
        stream = AsyncEventStream(self.subscribe_events, session_id, run_id, capacity)

        async def produce():
            try:
                return await self._execute(session_id, value, resume, run_id, max_concurrency)
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

    async def _close_resources(self):
        results = await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                logger.error('owned_run_failed error_type=%s', safe_error_type(result))
        await self._catalog.aclose()

    async def aclose(self):
        if self._closed:
            return
        if current_run_event_publisher() is not None:
            raise RuntimeError('Cannot close the application from an active run')
        self._check_open()
        self._closing = True
        try:
            await await_settled(self._close_resources())
        finally:
            self._closed = True


async def build_async_local_application(config_path, *, tools, system_prompt, response_format):
    catalog = await acquire_blocking(application.load_model_catalog, lambda item: item.aclose(), config_path)
    try:
        await run_blocking(catalog._prepare_async_clients)
        sessions = application.SessionService(catalog, approval_policies_for_tools(tools))
        agent = application._assemble_application(catalog, sessions, InMemorySaver(),
            tools=tools, system_prompt=system_prompt, response_format=response_format)
        return AsyncApplication(catalog, sessions, agent)
    except BaseException:
        try:
            await await_settled(catalog.aclose())
        except BaseException as error:
            logger.error('model_cleanup_secondary_failed error_type=%s', safe_error_type(error))
        raise
