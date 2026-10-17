"""Filename compatibility selects the review target once and never bypasses policy."""

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import ToolException

from naiyue_test_agent.tools.paths import prepare_file_subject, verify_file_target
from naiyue_test_agent.tools import create_session_read_tool
from test_read_tool import call_read
from test_permission_pipeline import build, call
from test_dynamic_tools import DynamicModel


@pytest.mark.parametrize('actual,supplied', [('note.txt', '@note.txt'), ('has space.txt', 'has\u00a0space.txt'),
    ('has space.txt', 'has\u3000space.txt'), ('Shot 1\u202fPM.txt', 'Shot 1 PM.txt'),
    ('cafe\u0301.txt', 'café.txt'), ('café.txt', 'cafe\u0301.txt'),
    ('Capture d’écran.txt', "Capture d'écran.txt"), ('cafe\u0301 d’écran.txt', "café d'écran.txt")])
def test_read_selects_an_existing_finite_filename_variant(tmp_path, actual, supplied):
    (tmp_path / actual).write_text('selected')
    result = call_read(create_session_read_tool(), {'path': supplied}, tmp_path)
    assert result.status == 'success' and result.content == 'selected'


def test_exact_name_wins_even_when_compatibility_target_exists(tmp_path):
    (tmp_path / '@note.txt').write_text('exact')
    (tmp_path / 'note.txt').write_text('variant')
    assert call_read(create_session_read_tool(), {'path': '@note.txt'}, tmp_path).content == 'exact'
    (tmp_path / '@directory').mkdir()
    (tmp_path / 'directory').write_text('PRIVATE_VARIANT')
    result = call_read(create_session_read_tool(), {'path': '@directory'}, tmp_path)
    assert result.status == 'error' and 'PRIVATE_VARIANT' not in result.content


def test_broken_exact_link_does_not_fall_back(tmp_path):
    (tmp_path / '@note.txt').symlink_to(tmp_path / 'missing')
    (tmp_path / 'note.txt').write_text('PRIVATE_VARIANT')
    result = call_read(create_session_read_tool(), {'path': '@note.txt'}, tmp_path)
    assert result.status == 'error' and 'PRIVATE_VARIANT' not in result.content


def test_failed_exact_path_probe_does_not_try_other_targets(tmp_path, monkeypatch):
    (tmp_path / 'note.txt').write_text('PRIVATE_VARIANT')
    original, inspected = Path.lstat, []
    def probe(path, *args, **kwargs):
        inspected.append(path)
        if path == tmp_path / '@note.txt':
            raise PermissionError('PRIVATE_FAILURE')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'lstat', probe)
    with pytest.raises(ToolException):
        prepare_file_subject('@note.txt', tmp_path, 'read')
    assert tmp_path / 'note.txt' not in inspected


@pytest.mark.parametrize('operation', ['write', 'list', 'search'])
def test_non_read_operations_do_not_guess_filenames(tmp_path, operation):
    (tmp_path / 'note.txt').write_text('original')
    subject = prepare_file_subject('@note.txt', tmp_path, operation)
    assert subject['requested'] == subject['resolved'] == str(tmp_path / '@note.txt')


def test_execution_rechecks_the_saved_exact_target_without_alias_search(tmp_path):
    selected = tmp_path / 'a b.txt'
    selected.write_text('original')
    subject = prepare_file_subject('a\u00a0b.txt', tmp_path, 'read')
    assert subject['requested'] == subject['resolved'] == str(selected)
    selected.unlink()
    (tmp_path / 'a\u00a0b.txt').write_text('PRIVATE_OTHER')
    with pytest.raises(ToolException):
        verify_file_target(subject)


@pytest.mark.parametrize('decision', ['deny', 'approve', 'reject', 'changed'])
def test_alias_permissions_use_selected_real_target_and_resume_never_reselects(monkeypatch, tmp_path, decision):
    root = tmp_path / 'workspace'
    root.mkdir()
    first, second = tmp_path / 'first.txt', tmp_path / 'second.txt'
    first.write_text('approved')
    second.write_text('PRIVATE_OTHER')
    link = root / 'note.txt'
    link.symlink_to(first)
    model = DynamicModel(responses=[AIMessage(content='', tool_calls=[call('@note.txt')]), AIMessage(content='done')])
    rules = {'deny': [{'tool': 'read', 'path': str(first)}], 'allow': [{'tool': 'read', 'path': str(tmp_path) + '/**'}]} if decision == 'deny' else {}
    app = build(monkeypatch, model, **rules)
    sid = app.create_workspace_session('A', root)
    opened, original = [], Path.open
    def record(path, *args, **kwargs):
        if path in (first, second):
            opened.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', record)
    paused = app.run(sid, [('user', 'read the file')])
    assert opened == []
    if decision == 'deny':
        result = paused
        assert not paused.get('approval_requests')
    else:
        assert paused['approval_requests'][0]['target'] == str(first)
        if decision == 'changed':
            link.unlink()
            link.symlink_to(second)
        result = app.resume_tool_approval(sid, ['reject' if decision == 'reject' else 'approve'])
    output = next(m for m in result['messages'] if isinstance(m, ToolMessage))
    assert output.tool_call_id == 'c1' and output.status == ('success' if decision == 'approve' else 'error')
    assert 'PRIVATE_OTHER' not in output.content
    assert opened == ([first] if decision == 'approve' else [])
    assert result['messages'][-1].content == 'done'


def test_hidden_compatibility_target_still_requires_approval(monkeypatch, tmp_path):
    (tmp_path / '.private').write_text('PRIVATE_BODY')
    app = build(monkeypatch, DynamicModel(responses=[AIMessage(content='', tool_calls=[call('@.private')])]))
    result = app.run(app.create_workspace_session('A', tmp_path), [('user', 'read')])
    assert result['approval_requests'][0]['target'] == str(tmp_path / '.private')
    assert not any(isinstance(m, ToolMessage) for m in result['messages'])
