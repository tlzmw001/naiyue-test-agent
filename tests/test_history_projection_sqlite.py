"""Exercise the legacy JSON1 string behavior without changing installed SQLite."""

import json
import re

import pytest
from langchain_core.messages import HumanMessage, ToolMessage

from naiyue_test_agent.tools.history_settings import HistorySettings
from test_history_tool_storage import archive, query, save


def _legacy_json_extract(document, *paths):
    """The fixed paths used here follow the real SQLite 3.37.2 probe."""
    if document is None:
        return None
    root = json.loads(document)
    values = []
    for path in paths:
        value = root
        for part in re.findall(r'[A-Za-z_]\w*|\d+', path):
            try:
                value = value[int(part)] if isinstance(value, list) else value[part]
            except (KeyError, IndexError, TypeError):
                value = None
                break
        values.append(value)
    if len(values) > 1:
        return json.dumps(values, ensure_ascii=False)
    value, = values
    if isinstance(value, str):
        return value.split('\0', 1)[0]
    return json.dumps(value) if isinstance(value, (dict, list)) else value


def legacy(archive):
    store, _ = archive
    store.database.connection.create_function('json_extract', -1, _legacy_json_extract, deterministic=True)
    return store


@pytest.mark.parametrize('array_content', [False, True])
def test_legacy_json_text_search_and_chunks_preserve_nul(archive, array_content):
    store = legacy(archive)
    text = '前文\0AFTER🧪' * 100
    content = [{'type': 'text', 'text': text[:201]}, text[201:]] if array_content else text
    save(store, 'target', [HumanMessage(id='h', content=content)])
    settings = HistorySettings(read_chunk_chars=57)
    hit, = query(archive, settings, action='search', session_id='target', query='after')['items']
    assert hit['text_chars'] == len(text) and 'AFTER' in hit['content']
    chunks, offset = [], 0
    for _ in range(100):
        result = query(archive, settings, action='read', session_id='target', sequence=1,
                       revision=1, conversation_revision=0, offset=offset)
        item, = result['items']
        assert item['text_chars'] == len(text) and item['content']
        chunks.append(item['content'])
        offset = item['next_offset']
        if item['complete']:
            break
    assert ''.join(chunks) == text and offset == len(text) and not result['has_more']


def test_legacy_query_nul_does_not_match_only_its_prefix(archive):
    store = legacy(archive)
    save(store, 'target', [HumanMessage(id='unrelated', content='needle only')])
    request = dict(action='search', session_id='target', query='needle\0tail')
    assert query(archive, **request)['items'] == []
    save(store, 'target', [HumanMessage(id='match', content='needle\0tail')])
    hit, = query(archive, **request)['items']
    assert hit['message_id'] == 'match' and hit['content'] == 'needle\0tail'


def test_legacy_block_type_nul_does_not_become_text(archive):
    store = legacy(archive)
    save(store, 'target', [HumanMessage(id='h', content=[
        {'type': 'text\0reasoning', 'text': 'PRIVATE-MARKER'},
        {'type': 'text', 'text': 'safe\0AFTER'}])])
    item, = query(archive, action='read', session_id='target')['items']
    assert item['content'] == 'safe\0AFTER'
    assert query(archive, action='search', session_id='target', query='PRIVATE')['items'] == []


def test_legacy_tool_identity_and_name_keep_nul(archive):
    store = legacy(archive)
    save(store, 'target', [ToolMessage(id='t', content='ok', tool_call_id='call\0id', name='tool\0name')])
    item, = query(archive, action='read', session_id='target')['items']
    assert item['tool_call_id'] == 'call\0id' and item['name'] == 'tool\0name'
