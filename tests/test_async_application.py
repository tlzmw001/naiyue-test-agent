"""The formal async application owns controls, approval and stream outcomes."""

import asyncio
import json
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field

from naiyue_test_agent import application
from naiyue_test_agent.tools import create_session_read_tool
from naiyue_test_agent.model_access import ModelCatalog


class StreamingToolModel(BaseChatModel):
    replies: list
    seen: list = Field(default_factory=list)
    entered: Any = None
    release: Any = None
    token_entered: Any = None
    token_release: Any = None

    @property
    def _llm_type(self):
        return 'native-async-stream-test'

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, *args, **kwargs):
        raise AssertionError('Synchronous model execution is forbidden')

    async def _next(self, messages):
        self.seen.append(messages)
        if len(self.seen) == 1 and self.entered is not None:
            self.entered.set()
            await self.release.wait()
        response = self.replies.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        response = await self._next(messages)
        return ChatResult(generations=[ChatGeneration(message=response.model_copy(deep=True))])

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        response = await self._next(messages)
        identity = response.id or uuid4().hex
        text = response.content
        if isinstance(text, list):
            yield ChatGenerationChunk(message=AIMessageChunk(content=text, id=identity))
        if isinstance(text, str) and text:
            split = max(1, len(text) // 2)
            yield ChatGenerationChunk(message=AIMessageChunk(content=text[:split], id=identity))
            if self.token_entered is not None:
                self.token_entered.set()
                await self.token_release.wait()
            yield ChatGenerationChunk(message=AIMessageChunk(content=text[split:], id=identity))
        chunks = [{'name': c['name'], 'args': json.dumps(c['args']), 'id': c['id'], 'index': i}
                  for i, c in enumerate(response.tool_calls)]
        yield ChatGenerationChunk(message=AIMessageChunk(content='', id=identity, tool_call_chunks=chunks,
            response_metadata=response.response_metadata, usage_metadata=response.usage_metadata))


async def make_app(monkeypatch, model, tools=None):
    from naiyue_test_agent.application.async_application import build_async_local_application
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    return await build_async_local_application('unused', tools=tools or [], system_prompt='', response_format=None)


def call(name, args, identity='t'):
    return AIMessage(content='', tool_calls=[{'name': name, 'args': args, 'id': identity}],
                     response_metadata={'finish_reason': 'tool_calls'})


def test_formal_arun_preserves_history_events_and_cleanup(monkeypatch):
    async def scenario():
        model = StreamingToolModel(replies=[AIMessage(content='first'), AIMessage(content='second')])
        app = await make_app(monkeypatch, model)
        events = []
        app.observability.subscribe(events.append)
        session = await app.sessions.create('A')
        try:
            await app.runs.start(session, [('user', 'one')])
            result = await app.runs.start(session, [('user', 'two')])
            assert [m.content for m in result['messages']] == ['one', 'first', 'two', 'second']
            ends = [e for e in events if e.event_type == 'run_finished']
            assert len(ends) == 2 and all(e.data.status == 'completed' for e in ends)
            assert ends[0].run_id != ends[1].run_id
            assert not app.sessions._sessions.require(session).describe_session()['busy']
        finally:
            await app.aclose()
    asyncio.run(scenario())


def test_waiting_model_accepts_steering_then_follow_up(monkeypatch):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        model = StreamingToolModel(replies=[AIMessage(content=x) for x in ('one', 'two', 'three')],
                                   entered=entered, release=release)
        app = await make_app(monkeypatch, model)
        session = await app.sessions.create('A')
        task = asyncio.create_task(app.runs.start(session, [('user', 'start')]))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            app.runs.steer(session, 'steering')
            app.runs.follow_up(session, 'follow-up')
            release.set()
            result = await task
            assert [m.content for m in result['messages']] == [
                'start', 'one', 'steering', 'two', 'follow-up', 'three']
            assert len(model.seen) == 3
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await app.aclose()
    asyncio.run(scenario())


def test_active_stop_returns_stopped(monkeypatch):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        model = StreamingToolModel(replies=[AIMessage(content='finished turn')], entered=entered, release=release)
        app = await make_app(monkeypatch, model)
        session = await app.sessions.create('A')
        events = []
        app.observability.subscribe(events.append)
        task = asyncio.create_task(app.runs.start(session, [('user', 'start')]))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            app.runs.follow_up(session, 'must not run')
            app.runs.stop(session)
            release.set()
            result = await task
            assert result['run_stopped'] is True and len(model.seen) == 1
            assert events[-1].data.status == 'stopped'
            assert events[-1].data.stop_mode == 'active'
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await app.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('decision', ['approve', 'reject'])
def test_async_approval_resume_preserves_tool_result(monkeypatch, tmp_path, decision):
    async def scenario():
        workspace = tmp_path / 'workspace'
        workspace.mkdir()
        outside = tmp_path / 'outside.txt'
        outside.write_text('outside value')
        model = StreamingToolModel(replies=[call('read', {'path': str(outside)}), AIMessage(content='after')])
        app = await make_app(monkeypatch, model, [create_session_read_tool()])
        session = await app.sessions.create_workspace('A', workspace)
        try:
            first = await app.runs.start(session, [('user', 'read')])
            assert len(first['approval_requests']) == 1
            assert not any(isinstance(m, ToolMessage) for m in first['messages'])
            result = await app.runs.resume_approval(session, [decision])
            message = next(m for m in result['messages'] if isinstance(m, ToolMessage))
            assert message.tool_call_id == 't'
            assert (message.status, message.content) == ('success', 'outside value') if decision == 'approve' else message.status == 'error'
            assert result['messages'][-1].content == 'after'
            assert app.runs.pending_tools(session) == []
        finally:
            await app.aclose()
    asyncio.run(scenario())


def test_delta_is_available_before_response_and_final_snapshot_calibrates(monkeypatch):
    async def scenario():
        from naiyue_test_agent.application.conversation.stream_projection import StreamingMessageProjection
        entered, release = asyncio.Event(), asyncio.Event()
        model = StreamingToolModel(replies=[AIMessage(content='hello')], token_entered=entered, token_release=release)
        app = await make_app(monkeypatch, model)
        session = await app.sessions.create('A')
        projection = StreamingMessageProjection()
        events, saw_delta = [], asyncio.Event()

        async def consume():
            async for event in app.runs.stream(session, [('user', 'start')]):
                events.append(event)
                projection(event)
                if event.event_type == 'message_delta':
                    saw_delta.set()

        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(entered.wait(), 5)
            await asyncio.wait_for(saw_delta.wait(), 5)
            assert not task.done()
            draft = next(row for row in projection.messages if row['type'] == 'ai')
            assert draft['content'] == 'he' and draft['provisional'] is True
            release.set()
            await task
            final = [row for row in projection.messages if row['type'] == 'ai']
            assert len(final) == 1 and final[0]['content'] == 'hello'
            assert final[0]['provisional'] is False
            assert events[-1].event_type == 'run_finished' and events[-1].data.status == 'completed'
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await app.aclose()
    asyncio.run(scenario())
