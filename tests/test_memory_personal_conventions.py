"""Personal rules are discovered on demand through the same Wiki tool."""
import time

import pytest

from memory_wiki_helpers import record
from naiyue_test_agent.application.memory.memory_wiki_context import prepare_wiki_parts
from naiyue_test_agent.contracts.memory import MemoryReadError
from naiyue_test_agent.storage.memory_sql.memory_wiki_writes import WikiWriter
from test_memory_readonly import memory
from test_memory_wiki_pagination import decode_content

TOPIC = 'working_agreements'


def setup(memory, *, body_chars=30):
    storage, access, workspace, _ = memory
    now = time.time()
    storage.memory.prepare_wiki(now=now)
    personal = access._personal_store.repository()
    personal.prepare_wiki(now=now)
    page = WikiWriter.new_page(TOPIC, '个人测试约定', '')
    with personal.wiki_edit('personal', None, now=now, capacity=30) as writer:
        writer.apply(pages=[page], memories=[record(page['page_id'], scope='personal', workspace_id=None,
            body=('完整正文' * body_chars) + str(i), evidence_at=now - i, expires_at=now + 86400,
            created_at=now-i, updated_at=now-i) for i in range(4)])
    return access, personal, workspace, page


def view(access, workspace, action, **args):
    return access.query_wiki('caller', workspace, dict(action=action, scope='personal', **args))


def test_personal_page_returns_four_complete_bodies_on_demand(memory):
    access, personal, workspace, page = setup(memory)
    before = personal.database.connection.total_changes
    parts = prepare_wiki_parts(access, 'caller', workspace, allowed=lambda _: True)
    assert '完整正文' not in str(parts)
    directory, = decode_content(view(access, workspace, 'list_pages', topic=TOPIC))['pages']
    assert directory['page_id'] == page['page_id'] and directory['scope'] == 'personal'
    entries = decode_content(view(access, workspace, 'read_page', page_id=page['page_id']))['entries']
    assert len(entries) == 4
    assert all(row['entry_complete'] and row['evidence_at'] for row in entries)
    assert personal.database.connection.total_changes == before


@pytest.mark.parametrize('body_chars', [50, 120, 180, 250])
def test_personal_page_paginates_complete_entries_without_automatic_search(memory, body_chars):
    access, personal, workspace, page = setup(memory, body_chars=body_chars)
    access.settings = access.settings.model_copy(update={'wiki_read_tokens': 950})
    before = personal.database.connection.total_changes
    bodies, cursor = {}, None
    for step in range(40):
        result = view(access, workspace, 'read_page', page_id=page['page_id'], cursor=cursor)
        assert result['budget']['estimated_tokens'] <= 950
        for row in decode_content(result)['entries']:
            mid = row['memory_id']
            assert row['offset'] == len(bodies.get(mid, ''))
            bodies[mid] = bodies.get(mid, '') + row['body']
        if not result['has_more']:
            break
        assert result['next_cursor'] and result['next_cursor'] != cursor
        cursor = result['next_cursor']
    else:
        pytest.fail('personal page pagination did not finish')
    assert step > 0 and len(bodies) == 4
    assert set(bodies.values()) == {('完整正文' * body_chars) + str(i) for i in range(4)}
    assert personal.database.connection.total_changes == before


def test_zero_navigation_budget_does_not_open_personal_storage(memory, monkeypatch):
    _, access, workspace, _ = memory
    access.settings = access.settings.model_copy(update={'wiki_navigation_tokens': 0})

    def forbidden(*args, **kwargs):
        pytest.fail('disabled navigation must not query')

    monkeypatch.setattr(access, 'query_wiki', forbidden)
    assert prepare_wiki_parts(access, 'caller', workspace, allowed=lambda _: True) == []


def test_page_cursor_rejects_a_newer_page_version(memory):
    access, personal, workspace, page = setup(memory, body_chars=250)
    access.settings = access.settings.model_copy(update={'wiki_read_tokens': 950})
    first = view(access, workspace, 'read_page', page_id=page['page_id'])
    assert first['has_more']
    with personal.wiki_edit('personal', None, now=time.time(), capacity=30) as writer:
        writer.apply(pages=[dict(writer.page(page['page_id']), expected_version=1, title='更正页标题')])
    with pytest.raises(MemoryReadError, match='memory_cursor_stale'):
        view(access, workspace, 'read_page', page_id=page['page_id'], cursor=first['next_cursor'])


def test_tiny_navigation_budget_does_not_trigger_personal_search(memory, monkeypatch):
    access, _, workspace, _ = setup(memory)
    access.settings = access.settings.model_copy(update={'wiki_navigation_tokens': 1})
    actions = []
    original = access.query_wiki

    def observe(*args, **kwargs):
        actions.append(args[2]['action'])
        return original(*args, **kwargs)

    monkeypatch.setattr(access, 'query_wiki', observe)
    assert prepare_wiki_parts(access, 'caller', workspace, allowed=lambda _: True) == []
    assert actions == ['list_topics', 'list_topics']


def test_explicit_personal_read_reports_version_change(memory):
    access, _, workspace, page = setup(memory)
    entry = decode_content(view(access, workspace, 'read_page', page_id=page['page_id']))['entries'][0]
    with pytest.raises(MemoryReadError, match='memory_version_changed'):
        view(access, workspace, 'read', memory_id=entry['memory_id'], version=entry['version']+1)
