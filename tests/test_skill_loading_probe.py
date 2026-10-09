"""The live probe must check tool execution rather than a guessed final marker."""

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from scripts.probe_skill_loading import check_skill_trace
from skill_helpers import write_skill


def _trace(root):
    skill = write_skill(root, name='source-check')
    reference = skill.parent / 'references/source.txt'
    reference.parent.mkdir()
    reference.write_text('SOURCE_AGENTS_PROBE', encoding='utf-8')
    calls = [{'name': 'read', 'args': {'path': str(path)}, 'id': f'call-{index}'}
             for index, path in enumerate((skill, reference))]
    return [AIMessage(content='', tool_calls=calls),
            ToolMessage(content='instructions', tool_call_id='call-0'),
            ToolMessage(content='SOURCE_AGENTS_PROBE', tool_call_id='call-1'),
            AIMessage(content='SOURCE_AGENTS_PROBE')]


def test_valid_probe_trace_proves_instruction_and_reference_reads(tmp_path):
    assert check_skill_trace(_trace(tmp_path), tmp_path, 'agents') == 2


@pytest.mark.parametrize('fault', ['guess', 'id', 'error', 'wrong_source'])
def test_probe_rejects_incomplete_or_incorrect_tool_evidence(tmp_path, fault):
    messages = _trace(tmp_path)
    if fault == 'guess':
        messages = [messages[-1]]
    elif fault == 'id':
        messages[1].tool_call_id = 'different'
    elif fault == 'error':
        messages[2].status = 'error'
    else:
        messages[0].tool_calls[0]['args']['path'] = str(tmp_path / '.claude/skills/source-check/SKILL.md')
    with pytest.raises(AssertionError):
        check_skill_trace(messages, tmp_path, 'agents')
