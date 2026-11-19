"""Conversation text does not start automatic searches; explicit query literals are preserved."""
import pytest
from langchain_core.messages import HumanMessage

from naiyue_test_agent.application.memory.memory_jobs import MemoryJobs
from naiyue_test_agent.contracts.memory import MemoryReadError
from naiyue_test_agent.memory_settings import MemorySettings
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.tools.memory_wiki_actions import compile_wiki_request
from pydantic import ValidationError
from test_async_application import StreamingToolModel
from test_memory_readonly import memory
from test_memory_wiki_pagination import seed_wiki


@pytest.mark.parametrize('text', [
    '跑一下本地回归，另外看看 TC-99 的页面。',
    '如果在 CI，先保持原样。不要沿用本地代理。',
    '请检查 /repo/a.b/test.py，另外排查 TC-99。',
    '继续吧',
    '查看“a;b”以及(x;y)。',
    '检查配置。\n```\nsynthetic code\n```\n检查报告。',
    '检查 TC-99。' + '后文'*1500,
    '1. 检查 TC-99。\n- 检查配置。',
    '检查 https://example.com/api?case=TC-99&page=2。检查报告。',
    '检查 `func(`。另外检查 TC-99。',
    '检查配置。\n````\n```python\n代码\n```\n````\n检查报告。',
    'go\non',
])
def test_current_navigation_leaves_literal_input_for_the_model(memory, monkeypatch, text):
    storage, access, _, _ = seed_wiki(memory)
    model = StreamingToolModel(replies=[])
    jobs = MemoryJobs(access, ModelCatalog('A', {'A': model}))
    root = storage.sessions.get('caller')['workspace_root']
    actions = []
    original = access.query_wiki

    def trace(*args, **kwargs):
        actions.append(args[2]['action'])
        return original(*args, **kwargs)

    monkeypatch.setattr(access, 'query_wiki', trace)
    message = HumanMessage(content=text)
    result = jobs.prepare('caller', root, [message], model, preview=True)
    assert result and actions == ['list_topics', 'list_topics']
    assert message.content == text and not model.seen


@pytest.mark.parametrize('literal', [
    'https://example.com/api?case=TC-99&page=2', '/repo/a.b/test.py',
    'a;b / (x;y)', '不要沿用本地代理',
])
def test_explicit_search_keeps_one_opaque_literal(literal):
    envelope = {'request': dict(action='search', scope='workspace', query=literal)}
    assert compile_wiki_request(envelope)['query'] == literal


def test_explicit_search_refuses_oversized_query_instead_of_cutting(memory):
    _, access, project, _ = seed_wiki(memory)
    access.settings = access.settings.model_copy(update={'max_query_chars': 64})
    query = '限' * 65
    with pytest.raises(MemoryReadError, match='^memory_query_too_complex$'):
        access.query_wiki('caller', project, dict(action='search', scope='workspace', query=query))


def test_retired_automatic_query_limits_are_explicitly_rejected():
    for change in ({'recall_query_limit': True}, {'recall_query_limit': 9}, {'recall_scan_chars': 1999}):
        with pytest.raises(ValidationError, match='Extra inputs are not permitted'):
            MemorySettings(**change)
