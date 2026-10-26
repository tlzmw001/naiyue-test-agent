"""Visible reasoning is protocol data, independent of the answer and hidden signatures."""

from runtime_context_helpers import typed_context

from langchain_core.messages import AIMessage, AIMessageChunk
from naiyue_test_agent.web.serialization import message_view


def test_standard_reasoning_projection_excludes_signatures():
    message = AIMessage(content=[{'type': 'thinking', 'thinking': 'visible', 'signature': 'PRIVATE'},
                                 {'type': 'text', 'text': 'answer'}],
                        response_metadata={'model_provider': 'anthropic'})
    row = message_view(message)
    assert row['reasoning'] == 'visible' and row['content'] == 'answer'
    assert 'PRIVATE' not in str(row) and message.content[0]['signature'] == 'PRIVATE'


def test_completions_explicit_adapter_preserves_full_delta_and_history():
    from naiyue_test_agent.model_access.completions_reasoning import ReasoningChatOpenAI
    model = ReasoningChatOpenAI(model='synthetic', api_key='synthetic-not-a-credential')
    response = {'choices': [{'message': {'role': 'assistant', 'content': 'answer',
                  'reasoning_content': 'reason'}, 'finish_reason': 'stop'}], 'model': 'synthetic'}
    result = model._create_chat_result(response).generations[0].message
    assert result.additional_kwargs['reasoning_content'] == 'reason'
    assert message_view(result)['reasoning'] == 'reason'
    payload = model._get_request_payload([result])
    assert payload['messages'][0]['reasoning_content'] == 'reason'
    delta = model._convert_chunk_to_generation_chunk({'choices': [{'delta': {
        'role': 'assistant', 'reasoning_content': 'part'}}]}, AIMessageChunk, None)
    assert delta.message.additional_kwargs['reasoning_content'] == 'part'


def test_responses_summary_and_effort_survive_ui_selection(tmp_path):
    import json
    from naiyue_test_agent.model_access import load_model_catalog
    path = tmp_path / 'models.json'
    path.write_text(json.dumps({'schema_version': 1, 'default_model_id': 'm',
        'providers': {'p': {'kind': 'openai', 'api_key': 'synthetic-not-a-credential'}},
        'models': {'m': {'provider_id': 'p', 'model': 'synthetic', 'api': 'responses',
            'thinking': {'supported_levels': ['low', 'high'], 'default_level': 'low'},
            'request': {'reasoning_summary': 'auto'}}}}))
    catalog = load_model_catalog(path)
    assert catalog.get('m')._get_request_payload('q')['reasoning'] == {'effort': 'low', 'summary': 'auto'}
    assert catalog.with_thinking('m', 'high')._get_request_payload('q')['reasoning'] == {'effort': 'high', 'summary': 'auto'}


def test_reasoning_stream_has_independent_indices_and_filters_internal_calls():
    from naiyue_test_agent.agent_runtime.async_message_observation import _StreamState
    from naiyue_test_agent.agent_runtime.events import RunEventPublisher
    from naiyue_test_agent.agent_runtime.stream_events import SUMMARY_TAG
    events = []
    state = _StreamState(RunEventPublisher('session', 'run', [events.append]), typed_context())
    state.latest = {'active_reply_id': 'reply'}
    meta = {'langgraph_node': 'model', 'langgraph_step': 1}
    state.delta(AIMessageChunk(id='a', content=[{'type': 'reasoning', 'reasoning': 'one'}]), meta)
    state.delta(AIMessageChunk(id='a', content=[{'type': 'reasoning', 'reasoning': 'two'}]), meta)
    state.delta(AIMessageChunk(id='a', content='answer'), meta)
    state.delta(AIMessageChunk(id='internal', content=[{'type': 'reasoning', 'reasoning': 'private'}]), {**meta, 'tags': [SUMMARY_TAG]})
    reasoning = [e.data for e in events if e.event_type == 'message_reasoning_delta']
    assert [(r.text, r.index, r.reply_id) for r in reasoning] == [('one', 1, 'reply'), ('two', 2, 'reply')]
    assert [e.data.text for e in events if e.event_type == 'message_delta'] == ['answer']
