"""Core acceptance consumes actual formal App state, not model self-reports."""

import asyncio

from langchain_core.messages import AIMessage
import pytest

from naiyue_test_agent.model_access import ModelCatalog
from scripts.provider_acceptance_budget import RequestBudget
from scripts.provider_acceptance_cases import CaseEnvironment
from scripts import provider_acceptance_core as core, provider_acceptance_cases as shared
from test_async_application import StreamingToolModel, call


class AcceptanceModel(StreamingToolModel):
    # Match the real adapter field the probe limits; no shared test helper change.
    max_tokens: int = 1024


def test_text_read_core_uses_formal_app_and_requires_actual_read(monkeypatch, tmp_path):
    model = AcceptanceModel(replies=[call('read', {'path': 'seed.txt'}), AIMessage(content='FIXTURE')])
    monkeypatch.setattr(core, 'nonce', lambda: 'FIXTURE')
    monkeypatch.setattr(shared, 'load_model_catalog', lambda _: ModelCatalog('fixture', {'fixture': model}))
    monkeypatch.setattr(shared, 'guard_catalog', lambda *_: None)
    env = CaseEnvironment('unused', 'fixture', tmp_path, RequestBudget(2))
    assert asyncio.run(core.read_text(env))['paired_read'] is True
    assert len(model.seen) == 2
    assert not (tmp_path / 'work' / 'vision.png').exists()


def test_read_acceptance_handles_valid_complete_page_metadata(monkeypatch, tmp_path):
    model = AcceptanceModel(replies=[call('read', {'path': 'seed.txt', 'offset': 1, 'limit': 2000}),
                                    AIMessage(content='FIXTURE')])
    monkeypatch.setattr(core, 'nonce', lambda: 'FIXTURE')
    monkeypatch.setattr(shared, 'load_model_catalog', lambda _: ModelCatalog('fixture', {'fixture': model}))
    monkeypatch.setattr(shared, 'guard_catalog', lambda *_: None)
    assert asyncio.run(core.read_text(CaseEnvironment('unused', 'fixture', tmp_path, RequestBudget(2))))['paired_read']


def test_read_case_rejects_correct_answer_without_tool_call(monkeypatch, tmp_path):
    model = AcceptanceModel(replies=[AIMessage(content='FIXTURE')])
    monkeypatch.setattr(core, 'nonce', lambda: 'FIXTURE')
    monkeypatch.setattr(shared, 'load_model_catalog', lambda _: ModelCatalog('fixture', {'fixture': model}))
    monkeypatch.setattr(shared, 'guard_catalog', lambda *_: None)
    with pytest.raises(AssertionError, match='expected_tool_sequence'):
        asyncio.run(core.read_text(CaseEnvironment('unused', 'fixture', tmp_path, RequestBudget(2))))


@pytest.mark.parametrize('case', ['approve', 'reject', 'no_ask', 'deny_rule', 'feedback'])
def test_permission_cases_follow_real_pipeline(monkeypatch, tmp_path, case):
    if case == 'approve':
        first = AIMessage(content='', tool_calls=[
            {'name': 'read', 'args': {'path': str(tmp_path / 'allowed.txt')}, 'id': 'allow'},
            {'name': 'read', 'args': {'path': str(tmp_path / 'outside.txt')}, 'id': 'ask'}])
        answer = 'APPROVED_VALUE'
    else:
        paths = {'reject': str(tmp_path / 'outside.txt'), 'no_ask': str(tmp_path / 'outside.txt'),
                 'deny_rule': 'blocked.txt', 'feedback': 'absent.txt'}
        first = call('read', {'path': paths[case]})
        answer = {'reject': 'DENIED', 'no_ask': 'NOASK_DONE', 'deny_rule': 'DENY_DONE', 'feedback': 'ERROR_SEEN'}[case]
    model = AcceptanceModel(replies=[first, AIMessage(content=answer)])
    monkeypatch.setattr(shared, 'load_model_catalog', lambda _: ModelCatalog('fixture', {'fixture': model}))
    monkeypatch.setattr(shared, 'guard_catalog', lambda *_: None)
    env = CaseEnvironment('unused', 'fixture', tmp_path, RequestBudget(2))
    assert asyncio.run(getattr(core, case)(env))
    assert len(model.seen) == 2


def test_file_flow_runs_native_search_and_real_write_edit(monkeypatch, tmp_path):
    monkeypatch.setattr(core, 'nonce', lambda: 'AFTER')
    calls = [('ls', {'path': '.'}), ('find', {'pattern': '*.txt', 'path': '.'}),
             ('grep', {'pattern': 'SEARCH_MARKER', 'path': '.'}),
             ('write', {'path': 'nested/output.txt', 'content': 'BEFORE'}),
             ('edit', {'path': 'nested/output.txt', 'edits': [{'oldText': 'BEFORE', 'newText': 'AFTER'}]}),
             ('read', {'path': 'nested/output.txt'})]
    model = AcceptanceModel(replies=[call(name, args, f'c{i}') for i, (name, args) in enumerate(calls)] + [AIMessage(content='AFTER')])
    monkeypatch.setattr(shared, 'load_model_catalog', lambda _: ModelCatalog('fixture', {'fixture': model}))
    monkeypatch.setattr(shared, 'guard_catalog', lambda *_: None)
    assert asyncio.run(core.files(CaseEnvironment('unused', 'fixture', tmp_path, RequestBudget(7))))['file_tools'] == 6


@pytest.mark.parametrize('case,answer', [('no_ask', 'NOASK_DONE'), ('deny_rule', 'DENY_DONE')])
def test_permission_case_does_not_misreport_unrelated_file_error(monkeypatch, tmp_path, case, answer):
    model = AcceptanceModel(replies=[call('read', {'path': 'wrong_missing.txt'}), AIMessage(content=answer)])
    monkeypatch.setattr(shared, 'load_model_catalog', lambda _: ModelCatalog('fixture', {'fixture': model}))
    monkeypatch.setattr(shared, 'guard_catalog', lambda *_: None)
    with pytest.raises(AssertionError, match='fixture_call_arguments'):
        asyncio.run(getattr(core, case)(CaseEnvironment('unused', 'fixture', tmp_path, RequestBudget(2))))
