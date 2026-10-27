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

    async def prepare_input(self, session_id, text, images, *, message_id):
        from langchain_core.messages import HumanMessage
        from naiyue_test_agent.application.conversation.image_inputs import normalize_input
        with self._owner.operation():
            if not images:
                return HumanMessage(content=text,id=message_id)
            if images:
                self._check_image_model(self._sessions.require(session_id))
            content = await run_blocking(normalize_input, text, images, self._sessions.image_input_settings)
            return HumanMessage(content=content, id=message_id)

    def _check_image_model(self, actor):
        from naiyue_test_agent.contracts.image_inputs import ImageInputError
        identity = actor.lifecycle.snapshot_for_run()['model_id']
        model = self._sessions._catalog.get(identity)
        if (getattr(model, 'profile', None) or {}).get('image_inputs') is not True:
            raise ImageInputError('model_image_input_unsupported')

    def steer(self, session_id, text):
        self._owner.check_controls_open()
        if not isinstance(text, str) or not text.strip():
            raise ValueError('A non-empty steering message is required')
        self._sessions.require(session_id).controls.steer(text)

    def follow_up(self, session_id, text):
        self._owner.check_controls_open()
        if not isinstance(text, str) or not text.strip():
            raise ValueError('A non-empty follow-up message is required')
        self._sessions.require(session_id).controls.follow_up(text)

    def queue_input(self, session_id, message, *, kind, replace_ids=()):
        from naiyue_test_agent.model_access.messages import content_text
        self._owner.check_controls_open()
        actor=self._sessions.require(session_id)
        if isinstance(message.content,list):
            self._check_image_model(actor)
        method=actor.controls.steer if kind=='steering' else actor.controls.follow_up
        method(content_text(message.content) + ('\n[图片已保留，可在 Web 待发送列表查看。]' if isinstance(message.content,list) else ''),content=message.content,replace_ids=replace_ids)

    def validate_pending(self, session_id, identities):
        self._owner.check_open()
        self._sessions.require(session_id).controls.validate_pending(identities)

    def pending_inputs(self, session_id):
        self._owner.check_open()
        return self._sessions.require(session_id).controls.pending_inputs()

    def pending_text(self,session_id,message_id):
        self._owner.check_open()
        return self._sessions.require(session_id).controls.pending_text(message_id)

    def pending_image(self,session_id,message_id,index):
        self._owner.check_open()
        return self._sessions.require(session_id).controls.pending_image(message_id,index)

    def remove_pending(self,session_id,message_id):
        self._owner.check_controls_open()
        self._sessions.require(session_id).controls.remove_pending(message_id)

    def stop(self, session_id, *, run_id=None, agent_path='root', agent_run_id=None, approval_batch_id=None):
        self._owner.check_controls_open()
        return self._sessions.require(session_id).controls.request_scope_stop(run_id=run_id, agent_path=agent_path, agent_run_id=agent_run_id, approval_batch_id=approval_batch_id)

    def pending_tools(self, session_id):
        self._owner.check_controls_open()
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
            actor = self._sessions.require(session_id)
            scope = actor.lifecycle.snapshot_for_run()
            root = scope['execution_root']
            approving = isinstance(decisions, list) and any(
                isinstance(choice, dict) and choice.get('decision') == 'approve' for choice in decisions)
            if root is not None and root.project_revision is not None and approving:
                facts = await run_blocking(root.authority.registry.execution_facts, scope['project_id'])
                if facts.revision != root.project_revision:
                    from naiyue_test_agent.application.errors import SessionApprovalExpiredError
                    raise SessionApprovalExpiredError('approval_expired: cancel the pending call and request a fresh review')
            if scope.get('execution_binding')=='sandbox' and approving:
                from .execution_binding import check_sandbox
                row=await run_blocking(self._metadata.get,session_id)
                await run_blocking(check_sandbox,self._sessions,row)
            self._owner.check_admission_open()
            return actor.approvals.submit_subagent_approval(approval_batch_id=approval_batch_id, decisions=decisions, origin_run_id=origin_run_id, remember_approved=remember_approved)

    async def _execute(self, session_id, value, resume, run_id, max_concurrency, *, remember_approved=None, stop=False, compact=False, expected_conversation_revision=None):
        with self._owner.operation():
            from naiyue_test_agent.agent_runtime.tool_execution.async_batch import validate_tool_concurrency
            validate_tool_concurrency(max_concurrency)
            if not resume and not compact:
                from langchain_core.messages import convert_to_messages
                value = convert_to_messages(value)
                from naiyue_test_agent.contracts.image_content import has_user_images
                if any(has_user_images(m.content) for m in value):
                    self._check_image_model(self._sessions.require(session_id))
            prepared = await self._sessions.prepare_admission(session_id, stop=stop)
            from .execution_binding import execution_use
            async with execution_use(self._sessions,prepared[1],stop=stop):
                await self._sessions.confirm_admission(prepared)
                self._owner.check_admission_open()
                if compact:
                    return await self._sessions.acompact(self._agent, session_id, run_id=run_id, prepared_admission=prepared)
                method = self._sessions.aresume_tool_approval if resume else self._sessions.arun
                return await method(self._agent, session_id, value, run_id=run_id, max_concurrency=max_concurrency, prepared_admission=prepared,
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
            await run_blocking(self._metadata.get, session_id)
            return await run_blocking(self._replies.action, session_id, request_id)

    async def regenerate(self, session_id, *, request_id, expected_reply_id, expected_conversation_revision):
        with self._owner.operation():
            from naiyue_test_agent.application.conversation.session_regeneration import regenerate
            return await regenerate(session_id, sessions=self._sessions, agent=self._agent, persistence=self._persistence, owner=self._owner,
                metadata=self._metadata, conversation=self._conversation, replies=self._replies, request_id=request_id, expected_reply_id=expected_reply_id,
                                    expected_conversation_revision=expected_conversation_revision)
