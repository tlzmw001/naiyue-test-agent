"""Exact workspace edits should be all-or-nothing and observable to the model."""

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import Field

from naiyue_test_agent import application
from naiyue_test_agent.agent_runtime.tools import (
    create_session_edit_tool, create_session_read_tool,
)
from naiyue_test_agent.agent_runtime.tools import edit as edit_module
from naiyue_test_agent.agent_runtime.tools import write as write_module
from naiyue_test_agent.model_access import ModelCatalog


class RecordingFake(FakeMessagesListChatModel):
    seen: list[list[object]] = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, *args, **kwargs):
        self.seen.append(list(messages))
        return super()._generate(messages, *args, **kwargs)


def call(name, args, call_id):
    return {'name': name, 'args': args, 'id': call_id}


def make_app(monkeypatch, responses, tools=None):
    model = RecordingFake(responses=responses)
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    app = application.build_local_application(
        Path('synthetic.json'), tools=tools or [create_session_edit_tool()],
        system_prompt='', response_format=None,
    )
    return app, model


def tool_result(result, call_id):
    return next(message for message in result['messages']
                if isinstance(message, ToolMessage) and message.tool_call_id == call_id)


def test_edit_replaces_multiple_original_regions_and_returns_same_call_id(
    monkeypatch, tmp_path,
):
    target = tmp_path / 'case.md'
    target.write_text('alpha\nbeta\ngamma\n', encoding='utf-8')
    tool = create_session_edit_tool()
    assert set(tool.tool_call_schema.model_fields) == {'path', 'edits'}
    assert tool.metadata['naiyue_execution_mode'] == 'sequential'
    app, model = make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[call('edit', {
            'path': 'case.md', 'edits': [
                {'oldText': 'alpha', 'newText': 'ALPHA'},
                {'oldText': 'gamma', 'newText': 'GAMMA'},
            ],
        }, 'edit-1')]),
        AIMessage(content='done'),
    ])

    result = app.run(app.create_workspace_session('A', tmp_path), [('user', 'edit')])

    assert target.read_text(encoding='utf-8') == 'ALPHA\nbeta\nGAMMA\n'
    message = tool_result(result, 'edit-1')
    assert message.status == 'success'
    assert '2' in message.content
    assert 'ALPHA' not in message.content
    assert any(isinstance(item, ToolMessage) and item.tool_call_id == 'edit-1'
               for item in model.seen[1])


@pytest.mark.parametrize(('edits', 'reason'), [
    ([{'oldText': 'missing', 'newText': 'X'}], 'not found'),
    ([{'oldText': 'same', 'newText': 'X'}], 'unique'),
    ([{'oldText': 'alpha', 'newText': 'X'},
      {'oldText': 'lpha', 'newText': 'Y'}], 'overlap'),
    ([{'oldText': '', 'newText': 'X'}], 'empty'),
    ([{'oldText': 'alpha', 'newText': 'alpha'}], 'no change'),
])
def test_edit_invalid_replacement_does_not_change_file(
    monkeypatch, tmp_path, edits, reason,
):
    target = tmp_path / 'case.md'
    target.write_text('alpha same same\n', encoding='utf-8')
    app, model = make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[call('edit', {
            'path': 'case.md', 'edits': edits,
        }, 'invalid')]),
        AIMessage(content='done'),
    ])

    result = app.run(app.create_workspace_session('A', tmp_path), [('user', 'edit')])

    assert target.read_text(encoding='utf-8') == 'alpha same same\n'
    message = tool_result(result, 'invalid')
    assert message.status == 'error'
    assert reason in message.content.lower()
    assert any(isinstance(item, ToolMessage) and item.status == 'error'
               for item in model.seen[1])


def test_edit_preserves_bom_crlf_and_allows_deletion(monkeypatch, tmp_path):
    target = tmp_path / 'case.md'
    target.write_bytes(b'\xef\xbb\xbfalpha\r\nbeta\r\n')
    app, _ = make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[call('edit', {
            'path': 'case.md', 'edits': [
                {'oldText': 'alpha', 'newText': 'ALPHA'},
                {'oldText': 'beta', 'newText': ''},
            ],
        }, 'bom')]),
        AIMessage(content='done'),
    ])

    result = app.run(app.create_workspace_session('A', tmp_path), [('user', 'edit')])

    assert target.read_bytes() == b'\xef\xbb\xbfALPHA\r\n\r\n'
    assert tool_result(result, 'bom').status == 'success'


def test_edit_large_file_with_match_crossing_chunk_keeps_untouched_bytes(
    monkeypatch, tmp_path,
):
    target = tmp_path / 'large.md'
    original = (b'\xef\xbb\xbf' + b'a' * (64 * 1024 - 5)
                + '甲old乙'.encode('utf-8') + b'\r\n'
                + b'z' * (16 * 1024 * 1024))
    target.write_bytes(original)
    target.chmod(0o640)
    app, model = make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[call('edit', {
            'path': 'large.md', 'edits': [
                {'oldText': '甲old乙', 'newText': '甲NEW乙'},
            ],
        }, 'large')]),
        AIMessage(content='done'),
    ])

    result = app.run(app.create_workspace_session('A', tmp_path), [('user', 'edit')])

    assert tool_result(result, 'large').status == 'success'
    assert target.read_bytes() == original.replace(
        '甲old乙'.encode('utf-8'), '甲NEW乙'.encode('utf-8'), 1,
    )
    assert target.stat().st_mode & 0o777 == 0o640
    assert any(isinstance(item, ToolMessage) and item.tool_call_id == 'large'
               for item in model.seen[1])


def test_edit_rejects_duplicate_in_different_chunks(monkeypatch, tmp_path):
    target = tmp_path / 'case.md'
    original = b'a' * (64 * 1024 - 2) + b'old' + b'z' * (64 * 1024) + b'old'
    target.write_bytes(original)
    app, _ = make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[call('edit', {
            'path': 'case.md', 'edits': [{'oldText': 'old', 'newText': 'new'}],
        }, 'duplicate')]),
        AIMessage(content='done'),
    ])

    result = app.run(app.create_workspace_session('A', tmp_path), [('user', 'edit')])

    assert tool_result(result, 'duplicate').status == 'error'
    assert 'unique' in tool_result(result, 'duplicate').content.lower()
    assert target.read_bytes() == original


def test_edit_rejects_invalid_utf8_split_across_chunks(monkeypatch, tmp_path):
    target = tmp_path / 'case.md'
    original = b'a' * (64 * 1024 - 1) + b'\xe2' + b'bad target'
    target.write_bytes(original)
    app, _ = make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[call('edit', {
            'path': 'case.md', 'edits': [{'oldText': 'target', 'newText': 'done'}],
        }, 'encoding-boundary')]),
        AIMessage(content='done'),
    ])

    result = app.run(app.create_workspace_session('A', tmp_path), [('user', 'edit')])

    assert tool_result(result, 'encoding-boundary').status == 'error'
    assert 'utf-8' in tool_result(result, 'encoding-boundary').content.lower()
    assert target.read_bytes() == original


@pytest.mark.parametrize('path', [
    '../outside.md', '.env', 'secret.pem', 'missing.md', 'directory', 'link.md',
])
def test_edit_rejects_unsafe_or_missing_files(monkeypatch, tmp_path, path):
    root = tmp_path / 'root'
    root.mkdir()
    (root / 'directory').mkdir()
    outside = tmp_path / 'outside.md'
    outside.write_text('outside', encoding='utf-8')
    (root / 'link.md').symlink_to(outside)
    app, _ = make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[call('edit', {
            'path': path, 'edits': [{'oldText': 'outside', 'newText': 'changed'}],
        }, 'denied')]),
        AIMessage(content='done'),
    ])

    result = app.run(app.create_workspace_session('A', root), [('user', 'edit')])

    assert tool_result(result, 'denied').status == 'error'
    assert outside.read_text(encoding='utf-8') == 'outside'


def test_edit_rejects_invalid_utf8_without_overwriting(monkeypatch, tmp_path):
    target = tmp_path / 'bad.md'
    target.write_bytes(b'\xffalpha')
    app, _ = make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[call('edit', {
            'path': 'bad.md', 'edits': [{'oldText': 'alpha', 'newText': 'beta'}],
        }, 'encoding')]),
        AIMessage(content='done'),
    ])
    result = app.run(app.create_workspace_session('A', tmp_path), [('user', 'edit')])
    assert tool_result(result, 'encoding').status == 'error'
    assert target.read_bytes() == b'\xffalpha'


def test_edit_rejects_replacement_input_past_configured_byte_limit(monkeypatch, tmp_path):
    target = tmp_path / 'case.md'
    target.write_text('old', encoding='utf-8')
    monkeypatch.setenv('NAIYUE_WRITE_MAX_BYTES', '4')
    app, _ = make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[call('edit', {
            'path': 'case.md', 'edits': [{'oldText': 'old', 'newText': 'longer'}],
        }, 'limit')]),
        AIMessage(content='done'),
    ])
    result = app.run(app.create_workspace_session('A', tmp_path), [('user', 'edit')])
    assert tool_result(result, 'limit').status == 'error'
    assert 'limit' in tool_result(result, 'limit').content.lower()
    assert target.read_text(encoding='utf-8') == 'old'


def test_edit_rejects_file_changed_after_initial_read(monkeypatch, tmp_path):
    target = tmp_path / 'case.md'
    target.write_text('old', encoding='utf-8')
    real_scan = edit_module._scan_target

    def change_after_scan(target_path, old_parts):
        result = real_scan(target_path, old_parts)
        target.write_text('another writer', encoding='utf-8')
        return result

    monkeypatch.setattr(edit_module, '_scan_target', change_after_scan)
    app, model = make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[call('edit', {
            'path': 'case.md', 'edits': [{'oldText': 'old', 'newText': 'new'}],
        }, 'changed')]),
        AIMessage(content='done'),
    ])
    result = app.run(app.create_workspace_session('A', tmp_path), [('user', 'edit')])
    message = tool_result(result, 'changed')
    assert message.status == 'error'
    assert 'changed' in message.content.lower()
    assert target.read_text(encoding='utf-8') == 'another writer'
    assert any(isinstance(item, ToolMessage) and item.tool_call_id == 'changed'
               for item in model.seen[1])


def test_edit_detects_external_change_while_copying(monkeypatch, tmp_path):
    target = tmp_path / 'case.md'
    target.write_bytes(b'old' + b'a' * (128 * 1024))
    real_open = Path.open
    read_opens = 0

    class ChangeOnRead:
        def __init__(self, source):
            self.source = source

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.source.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.source, name)

        def read(self, size=-1):
            with real_open(target, 'r+b') as external:
                external.write(b'ALT')
            return self.source.read(size)

    def open_with_change(path, mode='r', *args, **kwargs):
        nonlocal read_opens
        opened = real_open(path, mode, *args, **kwargs)
        if path == target and mode == 'rb':
            read_opens += 1
            if read_opens == 2:
                return ChangeOnRead(opened)
        return opened

    monkeypatch.setattr(Path, 'open', open_with_change)
    with pytest.raises(write_module.ToolException, match='changed'):
        edit_module._edit(tmp_path, 'case.md', [
            edit_module.EditReplacement(oldText='old', newText='new'),
        ], 1024)
    assert target.read_bytes() == b'ALT' + b'a' * (128 * 1024)
    assert sorted(path.name for path in tmp_path.iterdir()) == ['case.md']


def test_edit_reserves_target_before_scan_against_concurrent_write(monkeypatch, tmp_path):
    target = tmp_path / 'case.md'
    target.write_text('old', encoding='utf-8')
    entered = Event()
    release = Event()
    real_scan = edit_module._scan_target

    def paused_scan(target_path, old_parts):
        entered.set()
        assert release.wait(5)
        return real_scan(target_path, old_parts)

    monkeypatch.setattr(edit_module, '_scan_target', paused_scan)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(edit_module._edit, tmp_path, 'case.md', [
            edit_module.EditReplacement(oldText='old', newText='new'),
        ], 1024)
        assert entered.wait(5)
        with pytest.raises(write_module.ToolException, match='busy'):
            write_module._write(tmp_path, 'case.md', 'other', 1024)
        release.set()
        assert first.result(timeout=5).startswith('edit replaced')
    assert target.read_text(encoding='utf-8') == 'new'


def test_edit_temp_write_failure_keeps_original(monkeypatch, tmp_path):
    target = tmp_path / 'case.md'
    target.write_text('old', encoding='utf-8')

    def fail_temp(*_args, **_kwargs):
        raise OSError('PRIVATE_DISK_DETAIL')

    monkeypatch.setattr(edit_module.tempfile, 'NamedTemporaryFile', fail_temp)
    with pytest.raises(write_module.ToolException, match='I/O'):
        edit_module._edit(tmp_path, 'case.md', [
            edit_module.EditReplacement(oldText='old', newText='new'),
        ], 1024)
    assert target.read_text(encoding='utf-8') == 'old'


def test_edit_uses_session_workspace_even_when_model_forges_runtime(
    monkeypatch, tmp_path,
):
    first = tmp_path / 'first'
    second = tmp_path / 'second'
    first.mkdir()
    second.mkdir()
    (first / 'case.md').write_text('old', encoding='utf-8')
    (second / 'case.md').write_text('old', encoding='utf-8')
    app, _ = make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[call('edit', {
            'path': 'case.md',
            'edits': [{'oldText': 'old', 'newText': 'new'}],
            'runtime': {'context': {'workspace_root': str(second)}},
        }, 'forged')]),
        AIMessage(content='done'),
    ])

    result = app.run(app.create_workspace_session('A', first), [('user', 'edit')])

    assert tool_result(result, 'forged').status == 'success'
    assert (first / 'case.md').read_text(encoding='utf-8') == 'new'
    assert (second / 'case.md').read_text(encoding='utf-8') == 'old'


def test_read_edit_read_batch_observes_order(monkeypatch, tmp_path):
    target = tmp_path / 'case.md'
    target.write_text('before', encoding='utf-8')
    app, _ = make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[
            call('read', {'path': 'case.md'}, 'read-before'),
            call('edit', {'path': 'case.md', 'edits': [
                {'oldText': 'before', 'newText': 'after'},
            ]}, 'edit-middle'),
            call('read', {'path': 'case.md'}, 'read-after'),
        ]),
        AIMessage(content='done'),
    ], [create_session_read_tool(), create_session_edit_tool()])

    result = app.run(app.create_workspace_session('A', tmp_path), [('user', 'edit')])

    assert tool_result(result, 'read-before').content == 'before'
    assert tool_result(result, 'edit-middle').status == 'success'
    assert tool_result(result, 'read-after').content == 'after'


def test_edit_commit_unknown_quarantines_session_and_redacts_details(
    monkeypatch, tmp_path, caplog,
):
    target = tmp_path / 'case.md'
    target.write_text('old', encoding='utf-8')
    app, model = make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[call('edit', {
            'path': 'case.md', 'edits': [{'oldText': 'old', 'newText': 'PRIVATE_NEW'}],
        }, 'unknown')]),
        AIMessage(content='must not continue'),
    ])
    session = app.create_workspace_session('A', tmp_path)

    def unknown_replace(_source, _target):
        raise OSError('PRIVATE_IO_DETAIL')

    monkeypatch.setattr(write_module.os, 'replace', unknown_replace)
    with caplog.at_level(logging.INFO), pytest.raises(write_module.WriteOutcomeUnknown):
        app.run(session, [('user', 'edit')])

    assert target.read_text(encoding='utf-8') == 'old'
    assert len(model.seen) == 1
    assert 'PRIVATE_IO_DETAIL' not in caplog.text
    assert 'PRIVATE_NEW' not in caplog.text
    events = [record.write_event for record in caplog.records
              if hasattr(record, 'write_event')]
    assert [event['event'] for event in events] == [
        'edit_outcome_unknown', 'session_manual_review_required',
    ]
    assert events[0]['tool_call_id'] == 'unknown'
    assert events[0]['run_id'] == events[1]['run_id']
    with pytest.raises(RuntimeError, match='manual review'):
        app.run(session, [('user', 'again')])
