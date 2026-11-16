"""Client tool blocks must agree with the canonical calls before summarization."""
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from naiyue_test_agent.agent_runtime.context_compaction import _summary_request, ContextCompactionError


def call_block(**changes):
    return {'type':'function_call','call_id':'c','name':'read','arguments':'{"path":"a"}'}|changes


def test_responses_history_serializes_client_call_once_without_mutating_or_reasoning():
    ai=AIMessage(content=[{'type':'reasoning','summary':[{'text':'PRIVATE_REASONING'}]},
        call_block(),{'type':'text','text':'visible'}],
        tool_calls=[{'id':'c','name':'read','args':{'path':'a'}}])
    from langchain_core.messages import ToolMessage
    messages=[HumanMessage(content='task'),ai,ToolMessage(content='file',tool_call_id='c')]
    before=ai.model_dump()
    value=_summary_request(messages)[1].content
    assert value.count('<tool_call ')==1 and 'visible' in value and 'file' in value
    assert 'PRIVATE_REASONING' not in value
    assert ai.model_dump()==before


@pytest.mark.parametrize('block',[call_block(name='write'),call_block(arguments='bad'),
    call_block(arguments=None),{'type':'function_call'}, {'type':'server_tool_use'}, {'type':'audio'}])
def test_inconsistent_or_unknown_blocks_still_reject(block):
    ai=AIMessage(content=[block],tool_calls=[{'id':'c','name':'read','args':{'path':'a'}}])
    from naiyue_test_agent.agent_runtime.context_compaction import _validate_removed_prefix
    with pytest.raises(ContextCompactionError,match='unsupported_summary_content'):
        _validate_removed_prefix([ai])


def test_non_ai_reasoning_is_not_silently_dropped():
    from naiyue_test_agent.agent_runtime.context_compaction import _validate_removed_prefix
    with pytest.raises(ContextCompactionError,match='unsupported_summary_content'):
        _validate_removed_prefix([HumanMessage(content=[{'type':'reasoning','summary':[]}])])


def test_anthropic_tool_use_history_serializes_once_and_preserves_original():
    from langchain_core.messages import ToolMessage
    ai = AIMessage(content=[{'type': 'text', 'text': 'reading'},
        {'type': 'tool_use', 'id': 'c', 'name': 'read', 'input': {'path': 'a'}}],
        tool_calls=[{'id': 'c', 'name': 'read', 'args': {'path': 'a'}}])
    before = ai.model_dump()
    from naiyue_test_agent.agent_runtime.context_compaction import _validate_removed_prefix
    messages = [HumanMessage(content='task'), ai, ToolMessage(content='file', tool_call_id='c')]
    _validate_removed_prefix(messages)
    text = _summary_request(messages)[1].content
    assert text.count('<tool_call ') == 1 and 'reading' in text and 'file' in text
    assert ai.model_dump() == before


@pytest.mark.parametrize('changes', [{'id': 'other'}, {'name': 'write'}, {'input': {'path': 'b'}}, {'input': None}])
def test_anthropic_conflicting_tool_use_still_rejects(changes):
    from naiyue_test_agent.agent_runtime.context_compaction import _validate_removed_prefix
    block = {'type': 'tool_use', 'id': 'c', 'name': 'read', 'input': {'path': 'a'}} | changes
    ai = AIMessage(content=[block], tool_calls=[{'id': 'c', 'name': 'read', 'args': {'path': 'a'}}])
    with pytest.raises(ContextCompactionError, match='unsupported_summary_content'):
        _validate_removed_prefix([ai])
