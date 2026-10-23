"""Responses starts with an empty response-id chunk; later chunks get run IDs."""

from runtime_context_helpers import typed_context

import asyncio

from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk

from naiyue_test_agent import application
from naiyue_test_agent.application.conversation.stream_projection import StreamingMessageProjection
from test_async_application import StreamingToolModel
from native_application_helpers import async_test


class ResponsesIdentityModel(StreamingToolModel):
    async def _astream(self, messages, **kwargs):
        yield ChatGenerationChunk(message=AIMessageChunk(content='', id='resp_synthetic',
            response_metadata={'id': 'resp_synthetic', 'model_provider': 'openai'}))
        yield ChatGenerationChunk(message=AIMessageChunk(content='HELLO', id=None))
        yield ChatGenerationChunk(message=AIMessageChunk(content=' WORLD', id=None))
        yield ChatGenerationChunk(message=AIMessageChunk(content='', id=None,
            response_metadata={'finish_reason': 'stop'}))


@async_test
async def test_responses_empty_first_chunk_anchors_deltas_to_saved_message(monkeypatch):
    async def scenario():
        from naiyue_test_agent.model_access import ModelCatalog
        from native_application_helpers import build_application
        model = ResponsesIdentityModel(replies=[])
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('fixture', {'fixture': model}))
        app = await build_application('unused', tools=[], system_prompt='', response_format=None)
        try:
            sid = await app.sessions.create('fixture')
            events, projection = [], StreamingMessageProjection()
            async for event in app.runs.stream(sid, [('user', 'test')]):
                events.append(event)
                projection(event)
            saved = await app.history.messages(sid)
            assert saved[-1].id == 'resp_synthetic' and saved[-1].content == 'HELLO WORLD'
            deltas = [e for e in events if e.event_type == 'message_delta']
            assert deltas and {e.data.message_id for e in deltas} == {saved[-1].id}
            visible = [row for row in projection.messages if row['type'] == 'ai']
            assert len(visible) == 1 and not visible[0]['provisional']
        finally:
            await app.aclose()
    await scenario()


def test_content_only_deltas_keep_empty_response_anchor_and_original_final_snapshot():
    from naiyue_test_agent.agent_runtime.async_message_observation import _StreamState
    from naiyue_test_agent.agent_runtime.events import RunEventPublisher
    from langchain_core.messages import AIMessage
    from test_content_projection import IMAGE
    from naiyue_test_agent.web.serialization import project_event_for_web
    import json
    events = []
    stream = _StreamState(RunEventPublisher('s', 'r', (events.append,)), typed_context())
    metadata = {'langgraph_node': 'model', 'langgraph_checkpoint_ns': '', 'langgraph_step': 1}
    stream.delta(AIMessageChunk(content='', id='resp_projection',
                               response_metadata={'id': 'resp_projection'}), metadata)
    stream.delta(AIMessageChunk(content=[IMAGE], id='run-id'), metadata)
    stream.delta(AIMessageChunk(content=['ignore string block'], id='run-id'), metadata)
    stream.delta(AIMessageChunk(content=[{'type': ['opaque']}], id='run-id'), metadata)
    stream.delta(AIMessageChunk(content=[{'type': 'text', 'text': '真实'}], id='run-id'), metadata)
    stream.delta(AIMessageChunk(content='文字', id='run-id'), metadata)
    deltas = [e for e in events if e.event_type == 'message_delta']
    assert [(e.data.message_id, e.data.text, e.data.index) for e in deltas] == [
        ('resp_projection', '真实', 1), ('resp_projection', '文字', 2)]
    final = AIMessage(id='resp_projection', content=[{'type': 'text', 'text': '真实文字'}, IMAGE])
    stream.state([final])
    completed = [e for e in events if e.event_type == 'message_completed']
    assert len(completed) == 1 and completed[0].data.message_id == 'resp_projection'
    assert json.loads(completed[0].data.snapshot_json)['content'][-1] == IMAGE
    projected = project_event_for_web(completed[0])
    assert 'PRIVATE_PIXELS' not in projected.data.snapshot_json
    assert not stream.drafts
    assert [e.event_type for e in events] == ['message_delta', 'message_delta', 'message_completed']
    assert [e.sequence for e in events] == [1, 2, 3]
