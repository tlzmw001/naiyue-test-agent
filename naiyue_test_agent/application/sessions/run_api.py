"""RunAPI operations with explicit dependencies and shared lifetime ownership."""
from naiyue_test_agent.agent_runtime.async_utils import await_settled, run_blocking

import asyncio
from uuid import uuid4
from naiyue_test_agent.application.observability.async_event_stream import AsyncEventStream


class RunAPI:
    def __init__(self, owner, sessions, agent, persistence, metadata, conversation, replies):
        self._owner, self._sessions, self._agent = owner, sessions, agent
        self._persistence = persistence
        self._metadata, self._conversation, self._replies = metadata, conversation, replies

    def steer(self, session_id, text):
        if not isinstance(text, str) or not text.strip():
            raise ValueError('A non-empty steering message is required')
        self._sessions.require(session_id).controls.steer(text)

    def follow_up(self, session_id, text):
        if not isinstance(text, str) or not text.strip():
            raise ValueError('A non-empty follow-up message is required')
        self._sessions.require(session_id).controls.follow_up(text)

    def stop(self, session_id, *, run_id=None, agent_path='root', agent_run_id=None, approval_batch_id=None):
        return self._sessions.require(session_id).controls.request_scope_stop(run_id=run_id, agent_path=agent_path, agent_run_id=agent_run_id, approval_batch_id=approval_batch_id)

    def pending_tools(self, session_id):
        return self._sessions.require(session_id).approvals.pending_tool_approvals()

    def pending_subagents(self, session_id):
        self._owner.check_open()
        return self._sessions.require(session_id).approvals.pending_subagent_approvals()

    def pending_approvals(self, session_id):
        self._owner.check_open()
        return self._sessions.require(session_id).approvals.pending_approval_batches()

    def prepare_approval(self, session_id, **submission):
        self._owner.check_open()
        return self._sessions.require(session_id).approvals.prepare_root_approval_submission(**submission)

    async def submit_subagent_approval(self, session_id, *, approval_batch_id, decisions, origin_run_id, remember_approved=()):
        with self._owner.operation():
            return self._sessions.require(session_id).approvals.submit_subagent_approval(approval_batch_id=approval_batch_id, decisions=decisions, origin_run_id=origin_run_id, remember_approved=remember_approved)

    async def _execute(self, session_id, value, resume, run_id, max_concurrency, *, remember_approved=None, stop=False, compact=False, expected_conversation_revision=None):
        with self._owner.operation():
            if compact:
                return await self._sessions.acompact(self._agent, session_id, run_id=run_id)
            method = self._sessions.aresume_tool_approval if resume else self._sessions.arun
            return await method(self._agent, session_id, value, run_id=run_id, max_concurrency=max_concurrency,
                                **({'remember_approved': remember_approved, 'stop': stop} if resume else
                                   {'expected_conversation_revision': expected_conversation_revision}))

    async def start(self, session_id, messages, *, max_concurrency=4, expected_conversation_revision=None):
        with self._owner.operation():
            return await self._execute(session_id, messages, False, None, max_concurrency, expected_conversation_revision=expected_conversation_revision)

    async def compact(self, session_id):
        with self._owner.operation():
            return await self._execute(session_id, [], False, None, 4, compact=True)

    async def resume_approval(self, session_id, decisions, *, max_concurrency=4, remember_approved=None):
        with self._owner.operation():
            return await self._execute(session_id, decisions, True, None, max_concurrency, remember_approved=remember_approved)

    async def stop_approval(self, session_id, *, approval_batch_id, origin_run_id, max_concurrency=4):
        with self._owner.operation():
            self._sessions.require(session_id).approvals.validate_root_approval_stop(approval_batch_id=approval_batch_id, origin_run_id=origin_run_id)
            return await self._execute(session_id, [], True, None, max_concurrency, stop=True)

    async def _stream(self, session_id, value, resume, max_concurrency, capacity, *, stop=False):
        self._owner.check_open()
        run_id = uuid4().hex
        stream = AsyncEventStream(self._sessions.subscribe_events, session_id, run_id, capacity)

        async def produce():
            try:
                return await self._execute(session_id, value, resume, run_id, max_concurrency, stop=stop)
            finally:
                stream.finish()

        task = asyncio.create_task(produce())
        self._owner._tasks.add(task)
        task.add_done_callback(self._owner.background_done)
        try:
            async for event in stream:
                yield event
            await task
        finally:
            stream.close()

    async def stream(self, session_id, messages, *, max_concurrency=4, capacity=256):
        iterator = self._stream(session_id, messages, False, max_concurrency, capacity)
        try:
            async for event in iterator:
                yield event
        finally:
            await iterator.aclose()

    async def stream_approval(self, session_id, decisions, *, max_concurrency=4, capacity=256):
        iterator = self._stream(session_id, decisions, True, max_concurrency, capacity)
        try:
            async for event in iterator:
                yield event
        finally:
            await iterator.aclose()

    async def stream_stop_approval(self, session_id, *, approval_batch_id, origin_run_id, max_concurrency=4, capacity=256):
        self._owner.check_open()
        self._sessions.require(session_id).approvals.validate_root_approval_stop(approval_batch_id=approval_batch_id, origin_run_id=origin_run_id)
        iterator = self._stream(session_id, [], True, max_concurrency, capacity, stop=True)
        try:
            async for event in iterator:
                yield event
        finally:
            await iterator.aclose()

    async def regeneration_action(self, session_id, request_id):
        with self._owner.operation():
            return await run_blocking(self._replies.action, session_id, request_id)

    async def regenerate(self, session_id, *, request_id, expected_reply_id, expected_conversation_revision):
        with self._owner.operation():
            from naiyue_test_agent.application.conversation.session_regeneration import regenerate
            return await regenerate(session_id, sessions=self._sessions, agent=self._agent, persistence=self._persistence,
                metadata=self._metadata, conversation=self._conversation, replies=self._replies, request_id=request_id, expected_reply_id=expected_reply_id,
                                    expected_conversation_revision=expected_conversation_revision)
