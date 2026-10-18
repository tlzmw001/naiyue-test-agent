"""Native Responses text blocks must be valid text summaries."""

import asyncio
import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage

from naiyue_test_agent.agent_runtime.context_compaction import _acompact_messages, ContextCompactionError


def summarize(content):
    model = FakeMessagesListChatModel(responses=[AIMessage(content=content, response_metadata={'finish_reason': 'stop'})],
        metadata={'naiyue_model_input': {'api': 'responses'},
            'naiyue_model_limits': {'context_window_tokens': 128000, 'max_output_tokens': 1024},
            'naiyue_compaction_reserve': {'tokens': 2000}, 'naiyue_compaction_keep_recent': {'tokens': 20000},
            'naiyue_compaction_summary_output': {'tokens': 256}})
    messages = [HumanMessage(content='Old task. ' * 1000), AIMessage(content='done'),
                HumanMessage(content='Recent task'), AIMessage(content='recent')]
    return asyncio.run(_acompact_messages(messages, model, model_id='synthetic', context={},
        system_messages=[], tools=[], force=True, trigger='manual'))


@pytest.mark.parametrize('content', ['A concise summary.', [{'type': 'text', 'text': 'A concise summary.'}],
    [{'type': 'reasoning', 'summary': []}, {'type': 'text', 'text': 'A concise summary.'}]])
def test_native_summary_text_and_blocks_produce_same_saved_text(content):
    result = summarize(content)
    assert result['messages'][1].content.endswith('A concise summary.')
    assert result['messages'][1].additional_kwargs['naiyue_compaction_summary']


def test_unknown_or_image_only_summary_content_is_rejected():
    with pytest.raises(ContextCompactionError, match='summary_invalid'):
        summarize([{'type': 'image_url', 'image_url': {'url': 'synthetic'}}])
