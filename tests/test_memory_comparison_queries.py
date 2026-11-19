"""Wiki comparison uses authorized, complete page reads instead of old lexical discovery."""
import time

import pytest

from naiyue_test_agent.application.memory.memory_organization_commit import commit_group
from naiyue_test_agent.application.memory.memory_organization_reads import Ledger
from naiyue_test_agent.application.memory.memory_wiki_protocol import (
    EXTRACT_PROMPT, ORGANIZE_PROMPT, OrganizationResult,
)
from naiyue_test_agent.contracts.memory import MemoryReadError
from test_memory_lifecycle import source
from test_memory_readonly import memory
from test_memory_wiki_mutations import args, call, setup
from test_memory_wiki_pagination import decode_content

TOPIC = 'working_agreements'


def test_discovery_never_uses_expired_item_as_current(memory):
    storage, access, _ = setup(memory)
    now = time.time()
    at = now - 31 * 86400
    old = call(memory, args(sources=[source(access, 'caller', at=at)]), now=at)['item']
    listing = call(memory, dict(action='list_pages', scope='workspace', topic=TOPIC), now=now)
    assert listing['complete'] and decode_content(listing)['pages'] == []
    row = storage.database.connection.execute('SELECT memory_id,expires_at FROM naiyue_memories').fetchone()
    assert row['memory_id'] == old['memory_id'] and row['expires_at'] < now


def test_empty_directory_still_checks_read_permission(memory):
    _, access, project = setup(memory)
    ledger = Ledger('workspace', TOPIC)
    with pytest.raises(MemoryReadError, match='memory_permission_denied'):
        ledger.read(access, 'caller', project, dict(action='list_pages', topic=TOPIC), lambda _: False)
    assert not ledger.directories and not ledger.old


def test_organization_commit_preserves_binding_gate(memory):
    storage, access, _ = setup(memory)
    before = storage.database.connection.total_changes
    ledger = Ledger('workspace', TOPIC)
    with pytest.raises(MemoryReadError, match='memory_scope_mismatch'):
        commit_group(access, 'caller', 'other-project',
                     [(ledger, {}, dict(step='finish', decisions=[]))], lambda _: True)
    assert storage.database.connection.total_changes == before


def test_explicit_search_keeps_anchor_and_excludes_inactive(memory):
    _, access, _ = setup(memory)
    ref = source(access, 'caller')
    request = args(sources=[ref])
    request['proposal'].update(subject='case-one', body='TC-99 本地回归系统Python')
    wanted = call(memory, request)['item']
    request = args(sources=[ref])
    request['proposal'].update(subject='case-two', body='TC-98 本地回归系统Python')
    call(memory, request)
    query = dict(action='search', scope='workspace', query='本地回归 TC-99')
    row, = call(memory, query)['items']
    assert row['memory_id'] == wanted['memory_id']
    call(memory, dict(action='disable', scope='workspace', memory_id=wanted['memory_id'],
        expected_version=1, sources=[ref], reason='停用'))
    assert call(memory, query)['items'] == []


def test_directory_discovery_is_not_permission_to_read_page(memory):
    _, access, project = setup(memory)
    now = time.time()
    old = call(memory, args(sources=[source(access, 'caller', at=now-1)]), now=now)['item']
    ledger = Ledger('workspace', TOPIC)
    allowed = lambda request: request['action'] != 'read_page'
    ledger.read(access, 'caller', project, dict(action='list_pages', topic=TOPIC), allowed)
    with pytest.raises(MemoryReadError, match='memory_permission_denied'):
        ledger.read(access, 'caller', project, dict(action='read_page', page_id=old['page_id']), allowed)
    assert not ledger.old


def test_insufficient_read_budget_cannot_mean_empty_success(memory):
    _, access, project = setup(memory)
    now = time.time()
    call(memory, args(sources=[source(access, 'caller', at=now-1)]), now=now)
    access.settings = access.settings.model_copy(update={'max_output_bytes': 64})
    ledger = Ledger('workspace', TOPIC)
    with pytest.raises(MemoryReadError, match='memory_budget_too_small'):
        ledger.read(access, 'caller', project, dict(action='list_pages', topic=TOPIC), lambda _: True)
    assert not ledger.directories


def test_forgotten_source_rejected_by_wiki_storage_gate(memory):
    _, access, _ = setup(memory)
    ref = source(access, 'caller')
    old = call(memory, args(sources=[ref]))['item']
    call(memory, dict(action='forget', scope='workspace', memory_id=old['memory_id'],
        expected_version=1, sources=[ref], reason='用户遗忘'))
    with pytest.raises(MemoryReadError, match='memory_source_suppressed'):
        call(memory, args(sources=[ref]))


def test_organization_schema_binds_read_scope_in_code():
    schema = OrganizationResult.model_json_schema()
    assert schema['$defs']['BoundListPages']['additionalProperties'] is False
    assert 'scope' not in schema['$defs']['BoundListPages']['properties']
    assert 'code binds this work unit scope' in ORGANIZE_PROMPT


def test_extraction_scope_fallback_is_explicit_in_current_prompt():
    text = EXTRACT_PROMPT.format(overview_chars=1200, max_proposals=8)
    assert 'Scope workspace by default' in text
    assert 'not by keywords' in text and 'those same E IDs' in text
