"""Formal application streaming filters summary text and confirms state events."""

import asyncio

import pytest
from langchain_core.messages import AIMessage
from pydantic import Field

from naiyue_test_agent.application.conversation.stream_projection import StreamingMessageProjection
from naiyue_test_agent.agent_runtime.context_compaction import SUMMARY_MARKER
from test_async_application import StreamingToolModel, make_app
from test_async_context_chain import model, history, overflow_error


class ContextStreamModel(StreamingToolModel):
    summary_seen: list = Field(default_factory=list)

    async def _next(self, messages):
        if str(messages[0].content).startswith('Summarize completed'):
            self.summary_seen.append(messages)
            return AIMessage(content='SUMMARY_INTERNAL_TEXT', response_metadata={'finish_reason': 'stop'})
        return await super()._next(messages)


@pytest.mark.parametrize('overflow', [False, True])
def test_summary_delta_is_hidden_and_state_operations_confirm(monkeypatch, overflow):
    async def scenario():
        chosen = ContextStreamModel(replies=([overflow_error()] if overflow else []) + [AIMessage(content='answer')],
            metadata=model(window=2000 if overflow else 500, reserve=20 if overflow else 450).metadata)
        app = await make_app(monkeypatch, chosen)
        session = await app.acreate_session('A')
        events, projection = [], StreamingMessageProjection()
        try:
            async for event in app.astream_run(session, history()):
                events.append(event)
                projection(event)
            assert len(chosen.summary_seen) == 1
            assert len(chosen.seen) == (2 if overflow else 1)
            assert not any('SUMMARY_INTERNAL_TEXT' in e.data.text for e in events if e.event_type == 'message_delta')
            compaction = next(e for e in events if e.event_type == 'compaction_finished')
            assert compaction.data.status == 'applied'
            if overflow:
                recovery = next(e for e in events if e.event_type == 'context_recovery_finished')
                assert recovery.data.status == 'recovered'
            snapshots = await app.aget_messages(session)
            assert snapshots[0].additional_kwargs[SUMMARY_MARKER] is True
            assert not any(m.id == 'old-user' for m in snapshots)
            assert any(row['id'] == 'old-user' for row in projection.messages)
            assert not any(row.get('is_context_summary') for row in projection.messages)
            assert projection.messages[-1]['content'] == 'answer'
            assert events[-1].data.status == 'completed'
        finally:
            await app.aclose()
    asyncio.run(scenario())
